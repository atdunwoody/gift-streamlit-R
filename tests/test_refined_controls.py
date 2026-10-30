"""Editable geometry, optional maximum depth, and consistent full-curve mean UI."""
import copy
import hashlib
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
from bankfull_regressions import (BIEGER_NATIONAL, CASTRO_JACKSON, CHAMP, apply_plot_overrides,
                                 default_overrides, network_reach, plot_export)
from curve_presets import gift_shiny_default
from network_scenarios import build_network_solution, full_curve_mean
from presentation import flow_figure
from test_app import send_map_selection
from test_network_options import small_network
from test_on_demand import counted_solves


def test_supplied_gift_defaults_are_loaded_without_changing_csv_values():
    reference = json.loads((ROOT/'tests/regression_reference/gift_shiny_defaults.json').read_text())
    curve = gift_shiny_default()
    assert curve['available'] and curve['label'] == 'GIFT Shiny Default'
    for kind, data in reference['files'].items():
        path = ROOT/'inputs/curves'/data['filename']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == data['sha256']
        frame = pd.read_csv(path)
        assert len(frame) == data['rows']
        np.testing.assert_array_equal(frame.to_numpy(), curve[kind])


@pytest.mark.parametrize('depth_source', [CHAMP, BIEGER_NATIONAL])
def test_explicit_maximum_depth_controls_plot_shape_and_can_be_cleared(small_network, depth_source):
    network, _ = small_network
    network, _ = build_network_solution(network, CHAMP, depth_source)
    reach = next(r for r in network['features'] if r['id'] == 23428544)
    original = copy.deepcopy(reach)
    choices = default_overrides(reach)
    assert choices['custom_max_depth'] is None
    automatic = apply_plot_overrides(reach, choices)
    assert automatic['maxDepth'] == reach['maxDepth']
    choices['custom_max_depth'] = 2.
    adjusted = apply_plot_overrides(reach, choices)
    hyd = engine.calculate_hydraulics(engine.hydraulic_parameters(adjusted))
    assert hyd.attrs['shape_factor'] == pytest.approx(1-reach['bfDepth']/2.)
    assert hyd.attrs['simulated_max_depth_m'] == pytest.approx(2.)
    exported = plot_export(pd.DataFrame({'q':[1.]}), adjusted)
    assert exported.plot_max_bankfull_depth_m.iloc[0] == 2.
    assert exported.plot_max_bankfull_depth_source.iloc[0] == 'Custom value'
    choices['custom_max_depth'] = .01
    with pytest.raises(ValueError, match='below mean'):
        apply_plot_overrides(reach, choices)
    choices['custom_max_depth'] = None
    assert apply_plot_overrides(reach, choices)['maxDepth'] == reach['maxDepth']
    assert reach == original


def test_inputs_are_prefilled_editable_and_restore_all_segment_defaults(small_network, counted_solves):
    network, _ = small_network
    reach = next(r for r in network['features'] if r['id'] == 23428544)
    reach = network_reach(reach, CHAMP, CASTRO_JACKSON)
    at = AppTest.from_file(ROOT/'app.py',default_timeout=30).run()
    send_map_selection(at, reach['id'])
    assert not [w for w in at.toggle if w.key.startswith('plot-')]
    for kind, field in [('width','bfWidth'),('depth','bfDepth'),('slope','slope'),('d84','d84')]:
        assert at.number_input(key=f'plot-23428544-custom_{kind}').value == reach[field]
    assert at.number_input(key='plot-23428544-custom_max_depth').value is None
    before = at.get('deck_gl_json_chart')[0].proto.json
    at.number_input(key='plot-23428544-custom_depth').set_value(.9).run()
    at.number_input(key='plot-23428544-custom_d84').set_value(90.).run()
    at.number_input(key='plot-23428544-custom_max_depth').set_value(2.).run()
    assert not at.exception and not at.error
    assert counted_solves[-1][1]['max_bankfull_depth'] == 2.
    assert at.get('deck_gl_json_chart')[0].proto.json == before
    at.number_input(key='plot-23428544-custom_max_depth').set_value(.2).run()
    assert not at.exception and any('below mean' in e.value for e in at.error)
    at.button(key='reset-plot-23428544').click().run()
    assert at.button(key='reset-plot-23428544').label == 'Restore segment defaults'
    assert not at.exception and not at.error
    assert at.number_input(key='plot-23428544-custom_max_depth').value is None
    assert at.number_input(key='plot-23428544-custom_depth').value == reach['bfDepth']
    assert at.number_input(key='plot-23428544-custom_d84').value == reach['d84']


def test_mean_marker_uses_curve_average_and_suppresses_all_monthly_traces(small_network):
    network, curves = small_network
    network, hyd = build_network_solution(network)
    reach = network['features'][0]
    physical = pd.DataFrame(hyd[reach['id']],columns=['Q','Ai','Wi','di','Ui'])
    native = engine.native_habitat(None,reach,curves[0],hydraulics=physical)
    monthly = engine.compute_monthly([reach],curves[0])
    mean = full_curve_mean(reach,hyd[reach['id']],curves[0])
    for field in ['hsi','wua']:
        fig = flow_figure(native,monthly,[7,8,9],8,field,cfs=True,mean=mean)
        names = [trace.name for trace in fig.data]
        assert 'Selected monthly flows' not in names and 'Inspected month' not in names
        point = next(trace for trace in fig.data if trace.name == 'Full-curve mean')
        assert point.marker.symbol == 'diamond'
        assert point.y[0] == mean[field]
        assert point.x[0] == pytest.approx(mean['q']*engine.CFS_PER_M3S)
        assert not fig.layout.shapes


def test_mean_mode_removes_segment_monthly_values_and_tracks_local_edits(small_network, counted_solves, monkeypatch):
    at = AppTest.from_file(ROOT/'app.py',default_timeout=30).run()
    send_map_selection(at,23428544)
    assert at.selectbox(key='inspect_month')
    actual = engine.compute_monthly
    def no_months(*args,**kwargs):
        raise AssertionError('Mean mode must not evaluate selected monthly habitat')
    monkeypatch.setattr(engine,'compute_monthly',no_months)
    at.toggle(key='mean_hsi').set_value(True).run()
    assert not at.exception and not at.error
    for key in ['start_month','end_month']:
        assert at.selectbox(key=key).disabled
    assert at.radio(key='period_mode').disabled
    assert at.checkbox(key='require_all').disabled
    assert 'inspect_month' not in [x.key for x in at.selectbox]
    assert not any('EROM flow' in m.label or 'Monthly HSI' == m.label or 'Plot bankfull flow' in m.label for m in at.metric)
    assert not any(e.label == 'Monthly results for this segment' for e in at.expander)
    def mean_point(app):
        figure = next(p.proto for p in app.get('plotly_chart') if p.proto.id.endswith('-flow-wua'))
        spec = json.loads(figure.spec)
        return next(t for t in spec['data'] if t['name']=='Full-curve mean')['y'][0]
    first = mean_point(at)
    before_map = at.get('deck_gl_json_chart')[0].proto.json
    at.number_input(key='plot-23428544-custom_d84').set_value(90.).run()
    assert not at.exception and not at.error
    assert mean_point(at) != first
    assert at.get('deck_gl_json_chart')[0].proto.json == before_map
    monkeypatch.setattr(engine,'compute_monthly',actual)
    at.toggle(key='mean_hsi').set_value(False).run()
    assert not at.exception and not at.error
    assert not at.selectbox(key='start_month').disabled
    assert at.selectbox(key='inspect_month')


def test_map_controls_are_outside_sidebar_and_icon_buttons_update_view(small_network):
    st.cache_data.clear()
    at = AppTest.from_file(ROOT/'app.py',default_timeout=30).run()
    sidebar_keys = {x.key for x in at.sidebar.selectbox} | {x.key for x in at.sidebar.toggle}
    assert {'network_width_source','network_depth_source','mean_hsi'}.isdisjoint(sidebar_keys)
    assert not any(b.label=='Reload input data' for b in at.button)
    assert at.button(key='zoom-to-segment').disabled
    assert at.button(key='fit-network').proto.icon
    assert at.button(key='zoom-to-segment').proto.icon
    send_map_selection(at,23428544)
    at.button(key='zoom-to-segment').click().run()
    assert at.session_state.view_focus == 23428544
    zoomed = json.loads(at.get('deck_gl_json_chart')[0].proto.json)['initialViewState']
    at.button(key='fit-network').click().run()
    assert at.session_state.view_focus is None
    fitted = json.loads(at.get('deck_gl_json_chart')[0].proto.json)['initialViewState']
    assert fitted != zoomed
    at.radio(key='network_layer').set_value('Provisional Priority').run()
    assert any(i.value=='Provisional Model stream segment restoration priority' for i in at.info)


def test_gift_default_selection_works_with_curve_dialog_and_mean_plots(small_network):
    st.cache_data.clear()
    at = AppTest.from_file(ROOT/'app.py',default_timeout=30).run()
    at.selectbox(key='stage').set_value('gift_shiny_default').run()
    assert not at.exception and not at.error and not at.warning
    at.button(key='view_suitability_curves').click().run()
    assert not at.exception
    assert len([p for p in at.get('plotly_chart') if '-dialog-' in p.proto.id]) == 3
    at.run()
    at.toggle(key='mean_hsi').set_value(True).run()
    send_map_selection(at,23428544)
    assert not at.exception and not at.error
    chart = next(p.proto for p in at.get('plotly_chart') if p.proto.id.endswith('-flow-wua'))
    assert any(t['name']=='Full-curve mean' for t in json.loads(chart.spec)['data'])
