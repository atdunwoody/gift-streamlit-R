"""Scientific and UI checks for network scenarios and local plot overrides."""
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
from bankfull_regressions import (CHAMP, CUSTOM, BIEGER_NATIONAL, BIEGER_REGIONAL,
                                 NETWORK_BANKFULL_OPTIONS, apply_plot_overrides, default_overrides,
                                 plot_export, predict_bankfull)
from network_scenarios import build_network_solution, full_curve_mean, full_curve_summary
from presentation import (TEMP_PALETTE, TEMP_STOPS, channel_geometry_figure,
                          legend_gradient, rgb)
from test_app import champ_app, send_map_selection
from test_on_demand import counted_solves


@pytest.fixture
def small_network(monkeypatch):
    import runtime_data
    network, curves = engine.load_dataset(ROOT / "data")
    ids = {23428544, 23428378}
    network = dict(network, features=[r for r in network["features"] if r["id"] in ids])
    monkeypatch.setattr(runtime_data, "load_runtime_dataset", lambda config: (copy.deepcopy(network), curves))
    return network, curves


@pytest.mark.parametrize("width_source", NETWORK_BANKFULL_OPTIONS)
@pytest.mark.parametrize("depth_source", NETWORK_BANKFULL_OPTIONS)
def test_network_geometry_independent_sources_and_no_input_mutation(small_network, width_source, depth_source):
    network, _ = small_network
    before = copy.deepcopy(network)
    result, hyd = build_network_solution(network, width_source, depth_source)
    assert network == before
    assert len(hyd) == len(network["features"])
    for original, reach in zip(network["features"], result["features"]):
        for kind, source, field in (("width", width_source, "bfWidth"), ("depth", depth_source, "bfDepth")):
            expected = original[field] if source == CHAMP else predict_bankfull(original["da"], kind, source)
            assert reach[field] == pytest.approx(expected)
        assert reach["d84"] == original["d84"] and reach["slope"] == original["slope"]
        assert (reach["maxDepth"] is not None) == (width_source == depth_source == CHAMP)
        assert reach["modelStatus"] == "ok"
        assert reach["qMax"] == pytest.approx(hyd[reach["id"]][-1, 0])


def test_bad_geometry_is_unavailable_and_local_overrides_can_recover(small_network):
    network, _ = small_network
    network["features"][0].update(da=None, bfWidth=None, bfDepth=None, maxDepth=None)
    result, hyd = build_network_solution(network, BIEGER_NATIONAL, BIEGER_REGIONAL)
    reach = result["features"][0]
    assert reach["id"] not in hyd
    assert reach["qMax"] is None and reach["bfWidth"] is None and reach["bfDepth"] is None
    overrides = default_overrides(reach)
    overrides.update(override_width=True, custom_width=4., override_depth=True, custom_depth=.4)
    recovered = apply_plot_overrides(reach, overrides)
    assert not engine.calculate_hydraulics(engine.hydraulic_parameters(recovered)).empty


def test_d84_override_changes_resistance_and_substrate_without_mutating_network(small_network):
    network, curves = small_network
    reach = next(r for r in network["features"] if r["id"] == 23428544)
    before = copy.deepcopy(reach)
    choices = default_overrides(reach)
    choices.update(override_d84=True, custom_d84=30.)
    adjusted = apply_plot_overrides(reach, choices)
    original_hyd = engine.calculate_hydraulics(engine.hydraulic_parameters(reach))
    adjusted_hyd = engine.calculate_hydraulics(engine.hydraulic_parameters(adjusted))
    assert adjusted_hyd.Q.iloc[-1] > original_hyd.Q.iloc[-1]
    assert adjusted["maxDepth"] == reach["maxDepth"]
    monthly = engine.compute_monthly([engine.reach_with_hydraulics(adjusted, adjusted_hyd)], curves[0])
    assert monthly.s.iloc[0] == engine.substrate_score(30., curves[0])
    assert monthly.s.iloc[0] != engine.substrate_score(reach["d84"], curves[0])
    export = plot_export(monthly, adjusted)
    assert export.plot_D84_mm.eq(30.).all()
    assert reach == before


def test_full_flow_mean_has_known_value_for_constant_suitability(small_network):
    network, curves = small_network
    result, hyd = build_network_solution(network)
    constant = dict(curves[0], depth=[[0., 1.], [1000., 1.]],
                    velocity=[[0., 1.], [1000., 1.]], substrate=[[0., 1.e6, .4]])
    summary = full_curve_summary(result, hyd, constant)
    np.testing.assert_allclose(summary.hsi, .4, atol=1e-14)
    assert summary.validCount.eq(201).all()
    export = engine.network_export(result["features"], summary, constant["id"], [7, 8, 9])
    assert "HSI_full_curve_mean" in export and "HSI_median" not in export
    assert export.map_aggregation.eq("full_curve_mean").all()
    for reach in result["features"]:
        row = summary.loc[reach["id"]]
        assert row.qMin == hyd[reach["id"]][0, 0]
        assert row.qMax == hyd[reach["id"]][-1, 0]


def test_flow_mean_sampling_convergence(small_network):
    network, curves = small_network
    result, hyd = build_network_solution(network, BIEGER_NATIONAL, BIEGER_NATIONAL)
    for reach in result["features"]:
        for curve in (curves[0], next(c for c in curves if c["id"] == "steelhead_spawning")):
            coarse = full_curve_mean(reach, hyd[reach["id"]], curve)
            fine = full_curve_mean(reach, hyd[reach["id"]], curve, samples=1001)
            assert abs(coarse["hsi"]-fine["hsi"]) < .003


def test_cross_section_is_the_model_grid_and_updates_for_alternative_geometry(small_network):
    from gift_habitat.hydraulics import _cross_section_grid
    network, _ = small_network
    for sources in ((CHAMP, CHAMP), (BIEGER_NATIONAL, BIEGER_REGIONAL)):
        result, _ = build_network_solution(network, *sources)
        reach = result["features"][0]
        fig = channel_geometry_figure(reach)
        x, y, _ = _cross_section_grid(reach["bfWidth"], reach["bfDepth"], reach["shapeUsed"])
        # The plot now uses R's exact grid; allow cross-language rounding.
        np.testing.assert_allclose(fig.data[0].x, x, rtol=2e-12, atol=2e-12)
        np.testing.assert_allclose(fig.data[0].y, y-5*reach["bfDepth"])
        assert fig.data[1].x[-1] == reach["bfWidth"]
        assert fig.data[1].y == (0, 0)


def test_temperature_tan_position_matches_map_and_legend():
    assert TEMP_STOPS[TEMP_PALETTE.index([242, 225, 190])] == .8
    assert rgb(.8, 1., TEMP_PALETTE)[:3] == [242, 225, 190]
    assert "80%" in legend_gradient(TEMP_PALETTE)
    assert rgb(.90, 1., TEMP_PALETTE) != rgb(.96, 1., TEMP_PALETTE)


def test_mean_map_toggle_is_off_by_default_and_ignores_selected_months(small_network, counted_solves):
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not at.exception and not at.toggle(key="mean_hsi").value
    assert len(counted_solves) == 2  # Default CHaMP width / Castro and Jackson depth.
    at.button_group(key="map_metric").set_value("wua").run()
    at.toggle(key="mean_hsi").set_value(True).run()
    assert not at.exception and not at.error
    assert len(counted_solves) == 2
    before = at.get("deck_gl_json_chart")[0].proto.json
    assert "modeled flows" in before
    assert any("201 equally spaced" in c.value for c in at.caption)
    assert at.selectbox(key="start_month").disabled
    assert at.selectbox(key="end_month").disabled
    assert at.radio(key="period_mode").disabled
    # Programmatic changes to inactive dates must not alter the mean map.
    at.session_state.start_month = 1
    at.run()
    assert not at.exception
    assert at.get("deck_gl_json_chart")[0].proto.json == before
    assert len(counted_solves) == 2
    at.selectbox(key="stage").set_value("steelhead_spawning").run()
    assert not at.exception and len(counted_solves) == 2
    at.toggle(key="mean_hsi").set_value(False).run()
    assert "months" in at.get("deck_gl_json_chart")[0].proto.json
    assert at.button_group(key="map_metric").value == "wua"


def test_plot_override_survives_global_regression_change(small_network, counted_solves):
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    send_map_selection(at, 23428544)
    at.number_input(key="plot-23428544-custom_width").set_value(30.).run()
    at.selectbox(key="network_depth_source").set_value(BIEGER_NATIONAL).run()
    assert not at.exception and not at.error
    assert at.session_state._plot_choices[23428544]["override_width"]
    assert at.number_input(key="plot-23428544-custom_width").value == 30.
    assert any(m.label == "Plot bankfull width (m)" and m.value == "30.000" for m in at.metric)
    assert at.selectbox(key="network_width_source").value == CHAMP


def test_sidebar_curve_dialog_and_lazy_geometry_tab(small_network):
    st.cache_data.clear()
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert any(c.value == "Geomorphic Instream Flow Tool results across the Upper Grande Ronde basin." for c in at.caption)
    at.button(key="view_suitability_curves").click().run()
    assert not at.exception
    assert len([p for p in at.get("plotly_chart") if "-dialog-" in p.proto.id]) == 3
    at.run()
    send_map_selection(at, 23428544)
    at.session_state.flow_tabs = "Channel Geometry"
    at.run()
    assert not at.exception
    charts = [p.proto.id for p in at.get("plotly_chart")]
    assert any(k.endswith("-channel-geometry") for k in charts)
    assert not any(k.endswith("-flow-wua") or k.endswith("-flow-hsi") for k in charts)


def test_outside_bankfull_diagnostics_collapsed_and_layer_labels(small_network):
    st.cache_data.clear()
    at = champ_app().run()
    at.selectbox(key="stage").set_value("steelhead_spawning").run()
    send_map_selection(at, 23428544)
    assert not at.exception
    diagnostics = next(e for e in at.expander if e.label == "GIFT inputs and unavailable-result diagnostics")
    assert not diagnostics.proto.expanded
    assert at.radio(key="network_layer").options == ["GIFT habitat", "Temperature Suitability", "Provisional Priority"]
    at.radio(key="network_layer").set_value("Provisional Priority").run()
    assert any(c.value == "Provisional Model stream segment restoration priority" for c in at.info)
    at.radio(key="network_layer").set_value("Temperature Suitability").run()
    assert any("NoRWeST mean August stream temperature" in i.value for i in at.info)
