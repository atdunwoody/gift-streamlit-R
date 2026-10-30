"""Check the active spawning curves and HSI/WUA values through the app UI."""
import base64
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bankfull_regressions import CASTRO_JACKSON, CHAMP
from habitat_engine import aggregate_period, compute_monthly, load_dataset
from network_scenarios import build_network_solution, full_curve_summary
from presentation import COMPARISON_FIELDS
from test_app import send_plot_selection
from test_network_options import small_network


def map_values(at):
    deck = json.loads(at.get("deck_gl_json_chart")[0].proto.json)
    streams = next(layer for layer in deck["layers"] if layer["id"] == "streams")
    return {row["comid"]: float(row["value"].replace(",", ""))
            for row in streams["data"] if row["value"] != "Unavailable"}


def plot_values(values):
    """Decode Plotly's typed arrays as well as ordinary JSON arrays."""
    if isinstance(values, dict):
        return np.frombuffer(base64.b64decode(values["bdata"]), dtype=values["dtype"])
    return values


def test_spawning_uses_only_stream_river_csvs_and_legacy_selection_migrates(small_network):
    _, curves = load_dataset(ROOT / "data")
    by_id = {curve["id"]: curve for curve in curves}
    assert "chinook_spawning_large_river" not in by_id
    curve = by_id["chinook_spawning_stream_river"]
    assert curve["label"] == "Chinook - Spawning"
    assert by_id["salmonid_general_sensitivity"]["label"] == "General salmonid"
    for kind in ("depth", "velocity", "substrate"):
        source = pd.read_csv(ROOT / "inputs/curves" / f"{curve['id']}_{kind}.csv")
        np.testing.assert_array_equal(curve[kind], source.to_numpy())
    st.cache_data.clear()
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30)
    at.session_state.stage = "chinook_spawning_large_river"
    at.run()
    assert not at.exception and not at.error
    assert at.selectbox(key="stage").value == "chinook_spawning_stream_river"
    assert "Chinook - Spawning" in at.selectbox(key="stage").options
    assert not any("large river" in name.lower() for name in at.selectbox(key="stage").options)
    assert at.selectbox(key="start_month").value == 8
    assert at.selectbox(key="end_month").value == 9


def test_defaults_and_mean_map_follow_selected_metric(small_network):
    st.cache_data.clear()
    network, curves = small_network
    modeled, physical = build_network_solution(network, CHAMP, CASTRO_JACKSON)
    expected = full_curve_summary(modeled, physical, curves[0])
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not at.exception and not at.error
    assert at.selectbox(key="network_width_source").value == CHAMP
    assert at.selectbox(key="network_depth_source").value == CASTRO_JACKSON
    assert at.button_group(key="map_metric").options == ["HSI", "WUA"]
    assert "map_metric" not in {widget.key for widget in at.selectbox}
    elements = list(at.main)
    map_index = next(i for i, element in enumerate(elements) if element.type == "deck_gl_json_chart")
    for key in ("network_width_source", "network_depth_source", "map_metric", "mean_hsi"):
        assert next(i for i, element in enumerate(elements) if getattr(element, "key", None) == key) < map_index
    at.button_group(key="map_metric").set_value("wua").run()
    assert at.toggle(key="mean_hsi").label == "Map mean WUA"
    at.toggle(key="mean_hsi").set_value(True).run()
    for metric in ("wua", "hsi", "wua"):
        at.button_group(key="map_metric").set_value(metric).run()
        assert not at.exception and not at.error
        assert at.toggle(key="mean_hsi").value
        assert at.toggle(key="mean_hsi").label == f"Map mean {metric.upper()}"
        assert at.selectbox(key="start_month").disabled
        assert at.selectbox(key="end_month").disabled
        for comid, value in map_values(at).items():
            assert value == pytest.approx(expected.at[comid, metric], abs=.00051)
    at.toggle(key="mean_hsi").set_value(False).run()
    assert at.button_group(key="map_metric").value == "wua"
    assert not at.selectbox(key="start_month").disabled


def test_all_comparisons_switch_values_and_substrate_points_select_reaches(small_network):
    st.cache_data.clear()
    network, curves = small_network
    modeled, _ = build_network_solution(network, CHAMP, CASTRO_JACKSON)
    expected = aggregate_period(compute_monthly(modeled["features"], curves[0]), [7, 8, 9])
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    original_map = at.get("deck_gl_json_chart")[0].proto.json
    at.toggle(key="comparison_wua").set_value(True).run()
    assert not at.exception and not at.error
    assert at.get("deck_gl_json_chart")[0].proto.json == original_map
    assert at.button_group(key="map_metric").value == "hsi"
    for comparison in COMPARISON_FIELDS:
        at.session_state.comparison_tabs = comparison
        at.run()
        assert not at.exception and not at.error
        chart = next(p.proto for p in at.get("plotly_chart")
                     if p.proto.id.endswith(f"-comparison-{comparison}"))
        spec = json.loads(chart.spec)
        assert spec["layout"]["yaxis"]["title"]["text"] == "WUA (m²/m)"
        trace = spec["data"][0]
        for x, y, (_, comid) in zip(plot_values(trace["x"]), plot_values(trace["y"]), trace["customdata"]):
            assert y == pytest.approx(expected.at[comid, "wua"])
            if comparison == "Substrate suitability":
                assert x == pytest.approx(expected.at[comid, "s"])
    comid = int(trace["customdata"][0][1])
    send_plot_selection(at, "Substrate suitability", comid)
    assert not at.exception and at.session_state.selected_id == comid
    assert at.session_state.view_focus == comid
    at.toggle(key="comparison_wua").set_value(False).run()
    chart = next(p.proto for p in at.get("plotly_chart")
                 if p.proto.id.endswith("-comparison-Substrate suitability"))
    spec = json.loads(chart.spec)
    assert spec["layout"]["yaxis"]["title"]["text"] == "HSI"
    for y, (_, comid) in zip(plot_values(spec["data"][0]["y"]), spec["data"][0]["customdata"]):
        assert y == pytest.approx(expected.at[comid, "hsi"])
