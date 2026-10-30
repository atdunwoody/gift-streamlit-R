"""Selected-segment scenarios, optional maximum depth, and UI state isolation."""
import copy
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
import habitat_engine as engine
from bankfull_regressions import (BANKFULL_OPTIONS, BIEGER_NATIONAL, BIEGER_REGIONAL,
    CASTRO_JACKSON, CHAMP, CUSTOM, KM2_PER_MI2, default_plot_settings, plot_export,
    plot_reach, predict_bankfull)
from test_app import champ_app, send_map_selection
from test_on_demand import counted_solves


@pytest.fixture
def reach():
    network, _ = engine.load_dataset(ROOT / "data")
    return next(r for r in network["features"] if r["id"] == 23428544)


def test_published_regression_values_and_castro_usgs_units():
    # Bieger Table 3: at 1 km2 the returned SI dimension is the intercept.
    assert predict_bankfull(1., "width", BIEGER_NATIONAL) == 2.70
    assert predict_bankfull(1., "depth", BIEGER_NATIONAL) == .30
    assert predict_bankfull(1., "width", BIEGER_REGIONAL) == 1.11
    assert predict_bankfull(1., "depth", BIEGER_REGIONAL) == .07
    # Table 3 rounds the coefficients; Table 4 retains the underlying fits.
    assert predict_bankfull(1000., "depth", BIEGER_NATIONAL) == pytest.approx(1.32, abs=.02)
    assert predict_bankfull(1000., "depth", BIEGER_REGIONAL) == pytest.approx(.69, abs=.02)
    # USGS official NSS response: 100 mi2 -> 65.0 ft width, 2.79 ft mean depth.
    reference = json.loads((ROOT / "tests/regression_reference/castro_jackson_usgs.json").read_text())
    results = reference[0]["regressionRegions"][0]["results"]
    for dimension, code in (("width", "BFWDTH"), ("depth", "BFDPTH")):
        expected = next(r for r in results if r["code"] == code)
        assert expected["unit"]["abbr"] == "ft"
        assert predict_bankfull(100*KM2_PER_MI2, dimension, CASTRO_JACKSON) == pytest.approx(
            expected["value"]*.3048, rel=.002)


@pytest.mark.parametrize("dimension", ["width", "depth"])
@pytest.mark.parametrize("source", [BIEGER_NATIONAL, BIEGER_REGIONAL, CASTRO_JACKSON, CUSTOM])
def test_dimensions_are_independent_and_alternative_omits_maximum(reach, monkeypatch, dimension, source):
    original = copy.deepcopy(reach)
    choices = default_plot_settings(reach)
    choices[dimension+"_source"] = source
    choices["custom_"+dimension] = 20. if dimension == "width" else .9
    adjusted = plot_reach(reach, choices)
    unchanged = "bfDepth" if dimension == "width" else "bfWidth"
    assert adjusted[unchanged] == reach[unchanged]
    assert adjusted["maxDepth"] is None
    assert reach == original
    calls = []
    actual = engine.avg_hydraulics
    def record(*args, **kwargs):
        calls.append(kwargs)
        return actual(*args, **kwargs)
    monkeypatch.setattr(engine, "avg_hydraulics", record)
    hyd = engine.calculate_hydraulics(engine.hydraulic_parameters(adjusted))
    assert "max_bankfull_depth" not in calls[0]
    assert hyd.attrs["shape_factor"] == pytest.approx(adjusted["bfWidth"]/adjusted["bfDepth"]/100)
    # R's JSON transport rounds each returned column independently.
    np.testing.assert_allclose(hyd.Q, hyd.Ai*hyd.Ui, rtol=5e-14)


def test_slope_adjustment_preserves_geometry_and_scales_discharge(reach):
    choices = default_plot_settings(reach)
    baseline = engine.calculate_hydraulics(engine.hydraulic_parameters(reach))
    choices["slope"] = 4*reach["slope"]
    adjusted = plot_reach(reach, choices)
    hyd = engine.calculate_hydraulics(engine.hydraulic_parameters(adjusted))
    assert adjusted["maxDepth"] == reach["maxDepth"]
    np.testing.assert_allclose(hyd[["Ai", "Wi", "di"]], baseline[["Ai", "Wi", "di"]])
    np.testing.assert_allclose(hyd[["Q", "Ui"]], 2*baseline[["Q", "Ui"]])


def test_invalid_shape_and_export_provenance(reach):
    choices = default_plot_settings(reach)
    choices.update(depth_source=CUSTOM, custom_depth=.001)
    with pytest.raises(ValueError, match="shape factor"):
        plot_reach(reach, choices)
    choices["custom_depth"] = .9
    adjusted = plot_reach(reach, choices)
    result = plot_export(pd.DataFrame({"q": [1.]}), adjusted)
    assert result.plot_bankfull_depth_source.iloc[0] == CUSTOM
    assert result.plot_mean_bankfull_depth_m.iloc[0] == .9
    assert result.plot_max_bankfull_depth_m.isna().all()
    assert result.plot_slope_m_per_m.iloc[0] == reach["slope"]


def test_ui_network_regressions_apply_to_every_reach_and_reuse_physics(counted_solves, monkeypatch):
    import runtime_data
    network, curves = engine.load_dataset(ROOT / "data")
    ids = {23428544, 23428378}
    network = dict(network, features=[r for r in network["features"] if r["id"] in ids])
    monkeypatch.setattr(runtime_data, "load_runtime_dataset", lambda config: (copy.deepcopy(network), curves))
    at = champ_app().run()
    assert not at.exception and counted_solves == []
    assert at.selectbox(key="network_width_source").value == CHAMP
    at.selectbox(key="network_depth_source").set_value(BIEGER_NATIONAL).run()
    assert not at.exception and not at.error
    assert len(counted_solves) == 2
    for args, kwargs in counted_solves:
        assert "max_bankfull_depth" not in kwargs
        assert any(args[2] == pytest.approx(predict_bankfull(r["da"], "depth", BIEGER_NATIONAL))
                   for r in network["features"])
    at.selectbox(key="stage").set_value("steelhead_spawning").run()
    assert len(counted_solves) == 2
    at.selectbox(key="network_width_source").set_value(CASTRO_JACKSON).run()
    assert not at.exception and len(counted_solves) == 4
    for args, kwargs in counted_solves[-2:]:
        assert any(args[1] == pytest.approx(predict_bankfull(r["da"], "width", CASTRO_JACKSON))
                   for r in network["features"])
    at.selectbox(key="network_width_source").set_value(CHAMP).run()
    assert len(counted_solves) == 4


def test_ui_custom_inputs_persist_and_never_change_network_map(counted_solves):
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    send_map_selection(at, 23428544)
    default_depth = at.number_input(key="plot-23428544-custom_depth").value
    before_map = at.get("deck_gl_json_chart")[0].proto.json
    for kind, value in (("depth", .9), ("width", 35.), ("slope", .005), ("d84", 90.)):
        at.number_input(key=f"plot-23428544-custom_{kind}").set_value(value).run()
        assert not at.exception and not at.error
    assert at.get("deck_gl_json_chart")[0].proto.json == before_map
    assert counted_solves[-1][0][-1] == 90.
    assert counted_solves[-1][0][0] == .005
    assert "max_bankfull_depth" not in counted_solves[-1][1]
    send_map_selection(at, 23428378)
    assert at.number_input(key="plot-23428378-custom_depth").value is not None
    send_map_selection(at, 23428544)
    for kind, value in (("depth", .9), ("width", 35.), ("slope", .005), ("d84", 90.)):
        assert at.number_input(key=f"plot-23428544-custom_{kind}").value == value
    at.number_input(key="plot-23428544-custom_depth").set_value(.001).run()
    assert not at.exception and any("shape factor" in r.value for r in at.error)
    before_reset = len(counted_solves)
    at.button(key="reset-plot-23428544").click().run()
    assert not at.exception and not at.error
    assert at.number_input(key="plot-23428544-custom_depth").value == pytest.approx(default_depth)
    assert len(counted_solves) == before_reset  # Original solution is reused.


def test_ui_custom_dimensions_can_supply_missing_champ_inputs():
    st.cache_data.clear()
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    send_map_selection(at, 23429096)
    for kind, value in (("width", 4.), ("depth", .3)):
        at.number_input(key=f"plot-23429096-custom_{kind}").set_value(value).run()
    assert not at.exception and not at.error
    assert any(p.proto.id.endswith("-flow-wua") for p in at.get("plotly_chart"))


