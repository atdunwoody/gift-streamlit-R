"""Scientific parity, joins, availability, uploads, and click-event checks."""
import copy
import csv
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest
import pydeck as pdk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from habitat_engine import (aggregate_period, compute_monthly, curve_from_csvs,
                            gift_diagnostics, load_dataset, month_range, native_habitat,
                            network_export, selected_comid, selected_plotly_comid, validate_curves)
from presentation import (COMPARISON_FIELDS, comparison_figure, flow_figure, map_records,
                          map_view, metric_scale, network_map, potential_maximum_note, rgb)
from prepare_data import load_settings
from tools.prepare_network import quote


@pytest.fixture(scope="module")
def data():
    return load_dataset(ROOT / "data")


@pytest.fixture(scope="module")
def monthly(data):
    net, curves = data
    return {c["id"]: compute_monthly(net["features"], c) for c in curves}


def assert_value(actual, expected):
    if expected is None:
        assert pd.isna(actual)
    elif isinstance(expected, (bool, str)):
        assert actual == expected
    else:
        assert actual == pytest.approx(expected, abs=2e-12, rel=0)


def test_authoritative_input_joins(data):
    net, _ = data
    config = load_settings()
    with sqlite3.connect(config.NETWORK_PATH) as con:
        base = pd.read_sql_query(f'SELECT * FROM {quote(config.NETWORK_LAYER)}', con).set_index(config.NETWORK_FIELDS["id"])
    with sqlite3.connect(config.MAX_DEPTH_PATH) as con:
        depth = pd.read_sql_query(f'SELECT * FROM {quote(config.MAX_DEPTH_LAYER)}', con).set_index(config.MAX_DEPTH_ID_FIELD)
    with sqlite3.connect(config.PRIORITY_PATH) as con:
        priority = pd.read_sql_query(f'SELECT * FROM {quote(config.PRIORITY_LAYER)}', con).set_index(config.PRIORITY_ID_FIELD)
    assert len(net["features"]) == 728
    assert net["metadata"]["priorityMatches"] == 728
    assert net["metadata"]["unmatchedPriority"] == []
    for r in net["features"]:
        for field, expected in (("bfWidth",base.loc[r["id"],config.NETWORK_FIELDS["bankfull_width"]]),
                                ("bfDepth",base.loc[r["id"],config.NETWORK_FIELDS["bankfull_depth"]]),
                                ("temp",priority.loc[r["id"],config.TEMPERATURE_FIELD]),
                                ("maxDepth",depth.loc[r["id"],config.MAX_DEPTH_FIELD]),
                                ("priority",priority.loc[r["id"],config.PRIORITY_FIELDS["score"]])):
            assert_value(r[field], None if pd.isna(expected) else expected)


def test_ranges_and_coverage(monthly):
    assert month_range(11,2) == [11,12,1,2]
    assert month_range(8,8) == [8]
    with pytest.raises(ValueError):
        month_range(0,2)
    assert aggregate_period(monthly["chinook_juvenile_rearing"],[7,8,9]).hsi.notna().sum() == 696
    assert aggregate_period(monthly["steelhead_spawning"],[3,4,5,6]).hsi.notna().sum() == 407
    frame = monthly["steelhead_spawning"]
    complete = aggregate_period(frame,[3,4,5,6],True)
    partial = aggregate_period(frame,[3,4,5,6],False)
    excluded = complete.validCount.between(1,3)
    assert complete.loc[excluded,"hsi"].isna().all()
    assert partial.loc[excluded,"hsi"].notna().all()
    assert partial.loc[excluded,"status"].eq("partial_months").all()


def test_upload_validation_and_session_independent_calculation(data):
    net, curves = data
    curve = copy.deepcopy(curves[0])
    def raw(kind):
        columns = ["lower","upper","suit"] if kind == "substrate" else [kind,"suit"]
        return pd.DataFrame(curve[kind],columns=columns).to_csv(index=False).encode()
    custom = curve_from_csvs(raw("depth"),raw("velocity"),raw("substrate"),defaults=curve)
    np.testing.assert_allclose(compute_monthly(net["features"][:4],custom).hsi,
                               compute_monthly(net["features"][:4],curve).hsi,equal_nan=True)
    bad = copy.deepcopy(curve); bad["depth"].append(bad["depth"][0])
    with pytest.raises(ValueError,match="duplicates"):
        validate_curves(bad)
    bad = copy.deepcopy(curve); bad["substrate"] = [[0,20,1],[10,30,.5]]
    with pytest.raises(ValueError,match="overlap"):
        validate_curves(bad)
    bad = copy.deepcopy(curve); bad["velocity"][0][1] = 1.1
    with pytest.raises(ValueError,match="between"):
        validate_curves(bad)
    custom["substrate"] = [[0,1,1]]
    frame = compute_monthly(net["features"][:1],custom)
    hyd_ok = frame.depth.gt(0)
    assert frame.loc[hyd_ok,"d"].notna().all()
    assert frame.loc[hyd_ok,"hsi"].isna().all()
    assert frame.loc[hyd_ok,"status"].eq("substrate_unclassified").all()


def test_map_click_and_serialization(data, monthly):
    net,_ = data
    allowed = {r["id"] for r in net["features"]}
    comid = net["features"][4]["id"]
    event = {"selection":{"objects":{"streams":[{"comid":comid}]}}}
    assert selected_comid(event,allowed) == comid
    assert selected_comid({"selection":{"objects":{"streams":[{"comid":-1}]}}},allowed) is None
    summary = aggregate_period(monthly["chinook_juvenile_rearing"],[7,8,9])
    title,high,palette = metric_scale(net["features"],summary,"GIFT habitat","hsi")
    records = map_records(net["features"],summary,"GIFT habitat","hsi",high,palette)
    assert {r["comid"] for r in records} == allowed
    for basemap in ("Light","Dark","Satellite"):
        chart = network_map(records,map_view(net["features"]),comid,basemap)
        compact = chart.to_json()
        padded = pdk.Deck.to_json(chart)
        # Transport formatting must not change coordinates, colors, or layer IDs.
        assert json.loads(compact) == json.loads(padded)
        assert len(compact) < len(padded) * .45
        deck = json.loads(compact)
        assert all(layer["id"] for layer in deck["layers"])
        streams = next(layer for layer in deck["layers"] if layer["id"] == "streams")
        assert streams["pickable"] is True
        assert streams["data"][0]["comid"] in allowed


def test_display_units_do_not_change_calculation(data, monthly):
    net,curves = data
    reach = net["features"][0]
    native = native_habitat(ROOT/"data",reach,curves[0])
    frame = monthly[curves[0]["id"]]
    frame = frame[frame.id.eq(reach["id"])]
    before = frame.copy(deep=True)
    a = flow_figure(native,frame,[7,8,9],7,"wua",False)
    b = flow_figure(native,frame,[7,8,9],7,"wua",True)
    np.testing.assert_allclose(np.asarray(a.data[2].x)*35.31466672148859,b.data[2].x)
    pd.testing.assert_frame_equal(before,frame)


@pytest.mark.parametrize("layer,metric", [("GIFT habitat", "hsi"), ("GIFT habitat", "wua"),
                                         ("Temperature", "hsi"), ("Priority", "hsi")])
def test_map_scale_uses_observed_maximum(data, monthly, layer, metric):
    net, _ = data
    summary = aggregate_period(monthly["chinook_juvenile_rearing"], [7, 8, 9])
    _, maximum, palette = metric_scale(net["features"], summary, layer, metric)
    values = ([r["temp"] for r in net["features"]] if layer == "Temperature" else
              [r["priority"] for r in net["features"]] if layer == "Priority" else summary[metric])
    expected = max(v for v in values if v is not None and np.isfinite(v))
    assert maximum == pytest.approx(expected)
    assert rgb(maximum, maximum, palette)[:3] == palette[-1]
    assert "maximum observed" in potential_maximum_note(layer, metric)
    if layer == "GIFT habitat" and metric == "hsi":
        assert maximum < 1
        assert "Potential maximum: 1" in potential_maximum_note(layer, metric)
    zero = summary.copy()
    zero[metric] = 0.0
    _, zero_max, zero_palette = metric_scale(net["features"], zero, "GIFT habitat", metric)
    assert zero_max == 0
    assert rgb(0, zero_max, zero_palette)[:3] == zero_palette[0]


def test_selected_flow_highlight_and_default_units(data, monthly):
    net, curves = data
    reach = net["features"][0]
    native = native_habitat(ROOT / "data", reach, curves[0])
    frame = monthly[curves[0]["id"]]
    frame = frame[frame.id.eq(reach["id"])]
    months = [7, 8, 9]
    figure = flow_figure(native, frame, months, 7, "wua")
    assert figure.layout.xaxis.title.text == "Flow (cfs)"
    band = figure.layout.shapes[0]
    chosen = frame[frame.month.isin(months)].q * 35.31466672148859
    assert band.type == "rect" and band.layer == "below"
    assert band.x0 == pytest.approx(chosen.min())
    assert band.x1 == pytest.approx(chosen.max())
    single = flow_figure(native, frame, [8], 8, "hsi", False)
    line = single.layout.shapes[0]
    assert line.type == "line"
    assert line.x0 == line.x1 == pytest.approx(frame.loc[frame.month.eq(8), "q"].iloc[0])
    unsupported = frame.copy()
    unsupported.loc[unsupported.month.eq(8), ["hsi", "wua"]] = np.nan
    unsupported.loc[unsupported.month.eq(8), "q"] = native.q.max() * 1.2
    figure = flow_figure(native, unsupported, [7, 8], 7, "hsi", False)
    assert figure.layout.shapes[0].x1 == pytest.approx(native.q.max() * 1.2)


def test_plot_selection_uses_embedded_comid():
    allowed = {100, 200}
    event = {"selection": {"points": [{"point_index": 999, "curve_number": 1,
                                       "customdata": ["Selected reach", 200]}]}}
    assert selected_plotly_comid(event, allowed) == 200
    for candidate in (-1, 200.5, None, "invalid", True):
        event["selection"]["points"][0]["customdata"][1] = candidate
        assert selected_plotly_comid(event, allowed) is None
    assert selected_plotly_comid({"selection": {"points": []}}, allowed) is None
    assert selected_plotly_comid({"selection": None}, allowed) is None


@pytest.mark.parametrize("comparison", list(COMPARISON_FIELDS))
def test_comparison_coordinates_and_observed_hsi_axis(data, monthly, comparison):
    net, _ = data
    summary = aggregate_period(monthly["chinook_juvenile_rearing"], [7, 8, 9])
    figure, count = comparison_figure(net["features"], summary, "hsi", comparison, None)
    trace = figure.data[0]
    assert count == len(trace.customdata) > 0
    source, field = COMPARISON_FIELDS[comparison]
    reaches = {r["id"]: r for r in net["features"]}
    for x, y, (_, comid) in zip(trace.x, trace.y, trace.customdata):
        expected = reaches[comid][field] if source == "feature" else summary.at[comid, field]
        assert x == pytest.approx(expected)
        assert y == pytest.approx(summary.at[comid, "hsi"])
    assert figure.layout.yaxis.range[0] == 0
    assert figure.layout.yaxis.range[1] == pytest.approx(max(trace.y) * 1.05)
    assert figure.layout.yaxis.range[1] < 1
    selected_id = int(trace.customdata[2][1])
    selected, selected_count = comparison_figure(net["features"], summary, "hsi", comparison,
                                                selected_id, base=(figure.to_dict(), count))
    assert selected_count == count
    assert selected.data[-1].customdata[0][1] == selected_id
    assert selected.layout.yaxis.range == figure.layout.yaxis.range
    summary["hsi"] = .0001
    small, _ = comparison_figure(net["features"], summary, "hsi", comparison, None)
    assert small.layout.yaxis.range[1] == pytest.approx(.000105)
    summary["hsi"] = 0.0
    zeros, _ = comparison_figure(net["features"], summary, "hsi", comparison, None)
    assert zeros.layout.yaxis.range[1] > 0
    summary["hsi"] = np.nan
    empty, count = comparison_figure(net["features"], summary, "hsi", comparison, None)
    assert count == 0
    assert empty.layout.yaxis.range[1] > 0


def test_diagnostics_preserve_missing_and_out_of_range_inputs(data, monthly):
    net, curves = data
    reaches = {r["id"]: r for r in net["features"]}
    frame = monthly["chinook_juvenile_rearing"]
    months = [7, 8, 9]
    summary = aggregate_period(frame, months)
    missing = reaches[23429096]
    payload = gift_diagnostics(missing, frame[frame.id.eq(missing["id"])], summary.loc[missing["id"]], months, curves[0])
    decoded = json.loads(json.dumps(payload, allow_nan=False))
    assert decoded["input_parameters"]["bfWidth"] is None
    assert decoded["input_parameters"]["bfDepth"] is None
    assert decoded["input_parameters"]["maxDepth"] is None
    assert decoded["input_parameters"]["d84"] == missing["d84"]
    assert decoded["monthly_inputs_and_results"][0]["q"] == missing["months"][6][0]
    assert decoded["monthly_inputs_and_results"][0]["depth"] is None
    assert decoded["monthly_inputs_and_results"][0]["status"] == "no_hydraulics"
    unsupported = frame[frame.status.eq("above_bankfull")].iloc[0]
    reach = reaches[int(unsupported.id)]
    month = int(unsupported.month)
    period = aggregate_period(frame, [month]).loc[reach["id"]]
    payload = gift_diagnostics(reach, frame[frame.id.eq(reach["id"])], period, [month], curves[0])
    assert payload["input_parameters"]["qMax"] == reach["qMax"]
    assert payload["monthly_inputs_and_results"][0]["q"] > reach["qMax"]
    assert payload["monthly_inputs_and_results"][0]["status"] == "above_bankfull"
    assert payload["monthly_inputs_and_results"][0]["hsi"] is None
    exported = network_export(net["features"], summary, curves[0]["id"], months)
    assert "HSI_median" in exported
    assert "WUA_median_m2_per_m" in exported
    assert not any("normalized" in name or "summer_width" in name for name in exported.columns)


def test_diagnostics_for_substrate_curve_gap(data):
    net, curves = data
    reach = net["features"][0]
    curve = dict(curves[0], substrate=[[0, 1, 1]])
    frame = compute_monthly([reach], curve)
    summary = aggregate_period(frame, [7, 8, 9]).loc[reach["id"]]
    payload = gift_diagnostics(reach, frame, summary, [7, 8, 9], curve)
    row = payload["monthly_inputs_and_results"][0]
    assert row["status"] == "substrate_unclassified"
    assert row["depth"] is not None and row["d"] is not None
    assert row["s"] is None and row["hsi"] is None
    assert payload["suitability_curves"]["substrate"] == [[0, 1, 1]]
