"""Check CHaMP overview startup, cache reuse, fresh inputs, and numerical parity."""
import copy
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import habitat_engine as engine
import prepare_data
import runtime_data
import tools.prepare_network as preparation
from test_app import champ_app, send_map_selection
from test_data_preparation import configured_inputs


@pytest.fixture
def counted_solves(monkeypatch):
    st.cache_data.clear()
    calls = []
    original = engine.avg_hydraulics
    def counted(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)
    def no_bulk(*args, **kwargs):
        raise AssertionError("Interactive app must not build network hydraulics")
    monkeypatch.setattr(engine, "avg_hydraulics", counted)
    monkeypatch.setattr(preparation, "avg_hydraulics", no_bulk)
    monkeypatch.setattr(prepare_data, "ensure_prepared_data", no_bulk)
    yield calls
    st.cache_data.clear()


def test_startup_and_clicks_use_only_selected_hydraulics(counted_solves):
    at = champ_app().run()
    assert not at.exception and not at.error
    assert counted_solves == []
    assert at.session_state.selected_id is None
    assert at.get("deck_gl_json_chart")
    for comid, expected in ((23428378, 1), (23428390, 2), (23428378, 2)):
        send_map_selection(at, comid)
        assert not at.exception
        assert len(counted_solves) == expected
    at.selectbox(key="stage").set_value("steelhead_spawning").run()
    at.selectbox(key="end_month").set_value(7).run()
    at.radio(key="flow_units").set_value("m³/s").run()
    assert not at.exception
    assert len(counted_solves) == 2


@pytest.mark.parametrize("snapshot", ["missing", "corrupt", "no_native_files"])
def test_startup_never_depends_on_native_files(tmp_path, monkeypatch, counted_solves, snapshot):
    config = copy.deepcopy(prepare_data.load_settings())
    config.DATA_DIRECTORY = tmp_path / "overview"
    if snapshot != "missing":
        config.DATA_DIRECTORY.mkdir()
        for filename in ("network.json", "curves.json", "build-info.json"):
            shutil.copy2(ROOT / "data" / filename, config.DATA_DIRECTORY / filename)
        if snapshot == "corrupt":
            (config.DATA_DIRECTORY / "network.json").write_text("corrupt")
    monkeypatch.setattr(prepare_data, "load_settings", lambda: copy.deepcopy(config))
    at = champ_app().run()
    assert not at.exception and not at.error
    assert counted_solves == []
    assert not (config.DATA_DIRECTORY / "hydraulics").exists()
    send_map_selection(at, 23428378)
    assert not at.exception
    assert len(counted_solves) == 1
    assert any(p.proto.id.endswith("-flow-wua") for p in at.get("plotly_chart"))
    if snapshot == "missing":
        assert not config.DATA_DIRECTORY.exists()


def test_changed_reach_updates_map_and_inputs_without_bulk_build(configured_inputs, monkeypatch, counted_solves):
    config = configured_inputs
    monkeypatch.setattr(prepare_data, "load_settings", lambda: copy.deepcopy(config))
    at = champ_app().run()
    assert not at.exception and counted_solves == []
    assert any("2 segments await habitat calculation" in item.value for item in at.info)
    send_map_selection(at, 10)
    assert not at.exception and len(counted_solves) == 1
    assert set(at.session_state._evaluated_reaches) == {10}
    deck = json.loads(at.get("deck_gl_json_chart")[0].proto.json)
    streams = next(layer for layer in deck["layers"] if layer["id"] == "streams")["data"]
    assert next(row for row in streams if row["comid"] == 10)["value"] != "Unavailable"
    assert next(row for row in streams if row["comid"] == 20)["value"] == "Unavailable"
    config.NETWORK_FIELDS["bankfull_width"] = "alternative_width_ft"
    at.run()  # Full reruns automatically verify source files and field mappings.
    assert not at.exception and len(counted_solves) == 2
    assert at.session_state._evaluated_reaches[10]["bfWidth"] == pytest.approx(12.)
    assert not config.DATA_DIRECTORY.exists()


def test_sessions_with_different_evaluated_reaches_keep_their_own_overview(configured_inputs, monkeypatch, counted_solves):
    config = configured_inputs
    monkeypatch.setattr(prepare_data, "load_settings", lambda: copy.deepcopy(config))
    first = champ_app().run()
    second = champ_app().run()
    send_map_selection(first, 10)
    send_map_selection(second, 20)
    for app, expected in ((first, 10), (second, 20)):
        assert not app.exception
        chart = next(p.proto for p in app.get("plotly_chart")
                     if p.proto.id.endswith("-comparison-Temperature suitability"))
        points = json.loads(chart.spec)["data"][0]["customdata"]
        assert {int(row[1]) for row in points} == {expected}
    assert len(counted_solves) == 2


def test_clicked_hydraulics_match_monthly_overview():
    network, curves = engine.load_dataset(ROOT / "data")
    reaches = [network["features"][0], next(r for r in network["features"] if (r.get("shapeRaw") or 0) > .7)]
    for reach in reaches:
        hyd = engine.calculate_hydraulics(engine.hydraulic_parameters(reach))
        evaluated = engine.reach_with_hydraulics(reach, hyd)
        assert [row[4] for row in evaluated["months"]] == [row[4] for row in reach["months"]]
        for actual, expected in zip(evaluated["months"], reach["months"]):
            np.testing.assert_allclose(
                np.array(actual[:4], dtype=float), np.array(expected[:4], dtype=float),
                rtol=2e-9, atol=2e-11, equal_nan=True)
        native = engine.native_habitat(ROOT / "unused", reach, curves[0], hydraulics=hyd)
        assert len(native) == 981
        np.testing.assert_allclose(native.q, hyd.Q)
