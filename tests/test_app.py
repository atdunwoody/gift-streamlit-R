"""State transitions through Streamlit's official AppTest runner."""
from pathlib import Path
import json
import sys

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from habitat_engine import load_dataset
from bankfull_regressions import CHAMP


def champ_app():
    """Use the bundled CHaMP overview for tests of its source-input behavior."""
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30)
    at.session_state.network_depth_source = CHAMP
    return at


def send_map_selection(at, comid):
    """Replay the selection widget payload, without requiring a browser/GPU."""
    chart = at.get("deck_gl_json_chart")[0].proto
    state = at._tree.get_widget_states()
    event = next((w for w in state.widgets if w.id == chart.id), None)
    if event is None:
        event = state.widgets.add(id=chart.id)
    paths = next(layer for layer in json.loads(chart.json)["layers"] if layer["id"] == "streams")["data"]
    index = next((i for i, r in enumerate(paths) if r["comid"] == comid), 0)
    event.string_value = json.dumps({"selection": {
        "indices": {"streams": [index]}, "objects": {"streams": [{"comid": comid}]}}})
    return at._run(widget_state=state)


def send_plot_selection(at, comparison, comid):
    chart = next(p.proto for p in at.get("plotly_chart") if p.proto.id.endswith(f"-comparison-{comparison}"))
    trace = json.loads(chart.spec)["data"][0]
    index = next((i for i, row in enumerate(trace["customdata"]) if int(row[1]) == comid), 0)
    state = at._tree.get_widget_states()
    event = next((w for w in state.widgets if w.id == chart.id), None)
    if event is None:
        event = state.widgets.add(id=chart.id)
    event.string_value = json.dumps({"selection": {
        "points": [{"curve_number": 0, "point_number": index, "point_index": index,
                    "customdata": ["Selected test reach", comid]}],
        "point_indices": [index], "box": [], "lasso": []}})
    return at._run(widget_state=state)


def test_default_and_life_stage_controls():
    at = AppTest.from_file(ROOT/"app.py",default_timeout=30).run()
    assert not at.exception
    assert at.session_state.selected_id is None
    send_map_selection(at, 23428378)
    assert at.radio(key="flow_units").value == "cfs"
    assert any("Potential maximum: 1" in item.value for item in at.caption)
    assert at.selectbox(key="start_month").value == 7
    assert at.selectbox(key="end_month").value == 9
    at.selectbox(key="stage").set_value("steelhead_spawning").run()
    assert not at.exception
    assert at.selectbox(key="start_month").value == 3
    assert at.selectbox(key="end_month").value == 6
    at.radio(key="period_mode").set_value("Single month").run()
    at.selectbox(key="single_month").set_value(2).run()
    assert not at.exception
    assert at.selectbox(key="inspect_month").value == 2
    at.radio(key="period_mode").set_value("Month range").run()
    assert at.selectbox(key="start_month").value == 3
    assert at.selectbox(key="end_month").value == 6
    at.selectbox(key="start_month").set_value(11).run()
    at.selectbox(key="end_month").set_value(2).run()
    assert not at.exception
    assert at.selectbox(key="inspect_month").options == ["November","December","January","February"]
    at.checkbox(key="require_all").uncheck().run()
    at.radio(key="network_layer").set_value("Temperature Suitability").run()
    at.button_group(key="map_metric").set_value("wua").run()
    at.selectbox(key="basemap").set_value("Satellite").run()
    at.radio(key="flow_units").set_value("cfs").run()
    assert not at.exception
    assert any("do not change" in item.value for item in at.info)


def test_missing_input_selection_and_custom_curves():
    at = AppTest.from_file(ROOT/"app.py",default_timeout=30).run()
    send_map_selection(at, 23429096)
    assert not at.exception
    assert at.session_state.selected_id == 23429096
    assert any("missing hydraulic" in item.value for item in at.info)
    _, curves = load_dataset(ROOT/"data")
    custom = dict(curves[0],id="custom",label="Uploaded test curves")
    at.session_state["pending_custom"] = custom
    at.run()
    assert not at.exception
    assert at.selectbox(key="stage").value == "custom"
    assert at.selectbox(key="start_month").value == 7
    assert at.selectbox(key="end_month").value == 9


def test_map_selection_reuses_network_records_and_updates_results(monkeypatch):
    import presentation

    calls = []
    original = presentation.map_records

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(presentation, "map_records", counted)
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not at.exception
    assert len(calls) == 1
    for comid in (23428378, 23428390, 23429096):
        send_map_selection(at, comid)
        assert not at.exception
        assert at.session_state.selected_id == comid
        assert any(item.value == f"COMID {comid}" for item in at.caption)
        deck = json.loads(at.get("deck_gl_json_chart")[0].proto.json)
        selected = next(layer for layer in deck["layers"] if layer["id"] == "selected")
        assert {r["comid"] for r in selected["data"]} == {comid}
        assert len(calls) == 1
    # An unknown/stale click must not replace the last valid reach.
    send_map_selection(at, -1)
    assert not at.exception
    assert at.session_state.selected_id == 23429096
    at.selectbox(key="basemap").set_value("Satellite").run()
    assert not at.exception
    assert len(calls) == 1
    at.button_group(key="map_metric").set_value("wua").run()
    assert not at.exception
    assert len(calls) == 2
    at.selectbox(key="end_month").set_value(10).run()
    assert not at.exception
    assert len(calls) == 3


def test_only_open_plot_tabs_and_result_table_are_rendered():
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not at.exception
    assert not any(p.proto.id.endswith("-flow-wua") for p in at.get("plotly_chart"))
    send_map_selection(at, 23428378)
    keys = [p.proto.id for p in at.get("plotly_chart")]
    assert any(k.endswith("-flow-wua") for k in keys)
    assert not any(k.endswith("-flow-hsi") for k in keys)
    assert any(k.endswith("-comparison-Temperature suitability") for k in keys)
    assert not any(k.endswith("-comparison-Provisional Model priority") for k in keys)
    assert not any(b.proto.label == "Download monthly results (SI units)" for b in at.get("download_button"))
    at.session_state.flow_tabs = "HSI"
    at.session_state.comparison_tabs = "Provisional Model priority"
    at.session_state.monthly_results = True
    at.run()
    assert not at.exception
    keys = [p.proto.id for p in at.get("plotly_chart")]
    assert any(k.endswith("-flow-hsi") for k in keys)
    assert not any(k.endswith("-flow-wua") for k in keys)
    assert any(k.endswith("-comparison-Provisional Model priority") for k in keys)
    assert not any(k.endswith("-comparison-Temperature suitability") for k in keys)
    assert any(b.proto.label == "Download monthly results (SI units)" for b in at.get("download_button"))
    send_map_selection(at, 23428390)
    assert not at.exception
    assert at.session_state.flow_tabs == "HSI"
    assert at.session_state.comparison_tabs == "Provisional Model priority"


def test_requested_controls_and_missing_input_diagnostics():
    at = champ_app().run()
    assert not at.exception
    assert "reach_search" not in {s.key for s in at.selectbox}
    assert at.button_group(key="map_metric").options == ["HSI", "WUA"]
    assert not any("Three perspectives" in s.value for s in at.subheader)
    send_map_selection(at, 23429096)
    assert not at.exception
    parameters = next(d.value for d in at.dataframe if "Parameter" in d.value.columns)
    missing = parameters[parameters.Status.eq("Missing required input")]
    assert set(missing.Parameter) == {"Bankfull width", "Mean bankfull depth", "Maximum bankfull depth"}
    assert any(b.proto.label == "Download GIFT inputs and diagnostics (JSON)" for b in at.get("download_button"))


def test_click_each_comparison_updates_map_and_inspector():
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not at.exception
    for comparison in ("Temperature suitability", "Provisional Model priority", "Depth suitability", "Velocity suitability", "Substrate suitability"):
        at.session_state.comparison_tabs = comparison
        at.run()
        assert not at.exception
        chart = next(p.proto for p in at.get("plotly_chart") if p.proto.id.endswith(f"-comparison-{comparison}"))
        spec = json.loads(chart.spec)
        assert spec["layout"]["yaxis"]["title"]["text"] == "HSI"
        comid = next(int(row[1]) for row in spec["data"][0]["customdata"]
                     if int(row[1]) != at.session_state.selected_id)
        send_plot_selection(at, comparison, comid)
        assert not at.exception
        assert at.session_state.selected_id == comid
        assert at.session_state.view_focus == comid
        assert at.session_state.comparison_tabs == comparison
        assert any(c.value == f"COMID {comid}" for c in at.caption)
        deck = json.loads(at.get("deck_gl_json_chart")[0].proto.json)
        selected = next(layer for layer in deck["layers"] if layer["id"] == "selected")
        assert {r["comid"] for r in selected["data"]} == {comid}
    # The comparison metric is controlled independently of the map's setting.
    at.run()
    at.button_group(key="map_metric").set_value("wua").run()
    assert not at.exception
    chart = next(p.proto for p in at.get("plotly_chart") if p.proto.id.endswith("-comparison-Substrate suitability"))
    assert json.loads(chart.spec)["layout"]["yaxis"]["title"]["text"] == "HSI"
