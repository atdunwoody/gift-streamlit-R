"""Source selection, configurable fields, unit conversion, and stale-build regressions."""
import copy
from pathlib import Path
import shutil
import sqlite3
import struct
import sys

import numpy as np
import pandas as pd
import pytest
from shapely import to_wkb
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from habitat_engine import aggregate_period, compute_monthly, input_source_rows, load_dataset, network_export
from prepare_data import ensure_prepared_data, load_settings
from tools.prepare_network import read_layer


@pytest.fixture
def configured_inputs(tmp_path):
    config = copy.deepcopy(load_settings())
    config.DATA_DIRECTORY = tmp_path / "prepared"
    config.NETWORK_PATH = tmp_path / "streams" / "network.gpkg"
    config.NETWORK_PATH.parent.mkdir()
    config.NETWORK_LAYER = "reaches"
    config.NETWORK_FIELDS = dict(id="reach_no", name="stream_label", bankfull_width="width_ft",
        bankfull_depth="depth_ft", slope="slope_pct", d84="grain_m", drainage_area=None,
        summer_width=None, width_extrapolated=None, depth_extrapolated=None)
    config.MONTHLY_FLOW_FIELDS = {m: f"flow_{m:02d}" for m in range(1, 13)}
    config.FIELD_UNITS.update(bankfull_width="ft", bankfull_depth="ft", max_depth="ft",
                              slope="percent", d84="m", monthly_flow="cfs")
    geometry = b"GP\x00\x01" + struct.pack("<i", 4326) + to_wkb(LineString([(-118.1, 45.3), (-118.0, 45.4)]))
    with sqlite3.connect(config.NETWORK_PATH) as con:
        con.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, srs_id INTEGER)")
        con.execute("INSERT INTO gpkg_geometry_columns VALUES ('reaches', 'geom', 4326)")
        columns = ', '.join(f'"{field}" REAL' for field in config.MONTHLY_FLOW_FIELDS.values())
        con.execute('CREATE TABLE reaches (reach_no INTEGER, stream_label TEXT, geom BLOB, '
                    'width_ft REAL, alternative_width_ft REAL, depth_ft REAL, slope_pct REAL, grain_m REAL, ' + columns + ')')
        for comid in (10, 20):
            con.execute('INSERT INTO reaches VALUES (' + ','.join('?' for _ in range(20)) + ')',
                [comid, f"Test reach {comid}", geometry, 8 / .3048, 12 / .3048, .4 / .3048, 1., .05, *([1.] * 12)])
        con.execute("CREATE TABLE alternate_reaches AS SELECT * FROM reaches")
        con.execute("UPDATE alternate_reaches SET width_ft = ?", (16 / .3048,))
        con.execute("INSERT INTO gpkg_geometry_columns VALUES ('alternate_reaches', 'geom', 4326)")
    # Same basename as the streams source, but a different file, layer, and join key.
    config.MAX_DEPTH_PATH = tmp_path / "depths" / "network.gpkg"
    config.MAX_DEPTH_PATH.parent.mkdir()
    config.MAX_DEPTH_LAYER = "depths"
    config.MAX_DEPTH_ID_FIELD, config.MAX_DEPTH_FIELD = "depth_id", "maximum_ft"
    with sqlite3.connect(config.MAX_DEPTH_PATH) as con:
        con.execute("CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT, srs_id INTEGER)")
        con.execute("CREATE TABLE depths (depth_id INTEGER, maximum_ft REAL)")
        con.executemany("INSERT INTO depths VALUES (?, ?)", [(10, 1.3 / .3048), (20, 1.3 / .3048)])
    config.PRIORITY_PATH = tmp_path / "scores.csv"
    config.PRIORITY_ID_FIELD = "score_id"
    config.PRIORITY_FIELDS = dict(score="rank_score", category="rank_class")
    config.TEMPERATURE_SOURCE, config.TEMPERATURE_FIELD = "priority", "temperature"
    pd.DataFrame(dict(score_id=[10, 20], rank_score=[.6, .8], rank_class=["A", "B"],
                      temperature=[.75, .9])).to_csv(config.PRIORITY_PATH, index=False)
    config.CURVES_DIRECTORY = tmp_path / "curves"
    shutil.copytree(ROOT / "inputs/curves", config.CURVES_DIRECTORY)
    return config


def test_custom_fields_joins_units_and_build_reuse(configured_inputs):
    config = configured_inputs
    first = ensure_prepared_data(config)
    assert first["rebuilt"]
    second = ensure_prepared_data(config)
    assert not second["rebuilt"]
    assert first["manifest"]["fingerprint"] == second["manifest"]["fingerprint"]
    net, curves = load_dataset(config.DATA_DIRECTORY)
    r = net["features"][0]
    assert r["id"] == 10
    assert r["bfWidth"] == pytest.approx(8.)
    assert r["bfDepth"] == pytest.approx(.4)
    assert r["maxDepth"] == pytest.approx(1.3)
    assert r["slope"] == pytest.approx(.01)
    assert r["d84"] == pytest.approx(50.)
    assert r["months"][0][0] == pytest.approx(1 / 35.31466672148859)
    assert r["temp"] == .75 and r["priority"] == .6
    assert r["summerWidth"] is None and r["da"] is None
    inputs = {s["role"]: s for s in first["manifest"]["inputs"]}
    assert inputs["network"]["path"] != inputs["max_depth"]["path"]
    assert inputs["network"]["sha256"] != inputs["max_depth"]["sha256"]
    rows = input_source_rows(net["metadata"])
    assert next(row for row in rows if row["Input"] == "Bankfull width")["Field"] == "width_ft"
    monthly = compute_monthly(net["features"], curves[0])
    export = network_export(net["features"], aggregate_period(monthly, [7]), curves[0]["id"], [7], net["metadata"])
    assert set(export.base_file) == {str(config.NETWORK_PATH)}
    assert set(export.bankfull_width_field) == {"width_ft"}
    assert set(export.max_depth_source) == {str(config.MAX_DEPTH_PATH)}


@pytest.mark.parametrize("change", ["path", "contents", "field", "layer", "units", "curves", "periods"])
def test_input_changes_rebuild_without_manual_preparation(configured_inputs, tmp_path, change):
    config = configured_inputs
    first = ensure_prepared_data(config)
    if change == "path":
        alternate = tmp_path / "alternate" / config.NETWORK_PATH.name
        alternate.parent.mkdir()
        shutil.copy2(config.NETWORK_PATH, alternate)
        config.NETWORK_PATH = alternate
    elif change == "contents":
        with sqlite3.connect(config.NETWORK_PATH) as con:
            con.execute("UPDATE reaches SET width_ft=? WHERE reach_no=10", (14 / .3048,))
    elif change == "field":
        config.NETWORK_FIELDS["bankfull_width"] = "alternative_width_ft"
    elif change == "layer":
        config.NETWORK_LAYER = "alternate_reaches"
    elif change == "units":
        config.FIELD_UNITS["bankfull_width"] = "m"
    elif change == "curves":
        path = config.CURVES_DIRECTORY / "chinook_juvenile_rearing_depth.csv"
        frame = pd.read_csv(path)
        frame.loc[0, "suit"] = .123
        frame.to_csv(path, index=False)
    else:
        config.DEFAULT_PERIODS["chinook_juvenile_rearing"]["start"] = 6
    second = ensure_prepared_data(config)
    assert second["rebuilt"]
    assert first["manifest"]["fingerprint"] != second["manifest"]["fingerprint"]
    assert not ensure_prepared_data(config)["rebuilt"]
    net, curves = load_dataset(config.DATA_DIRECTORY)
    if change in ("contents", "field", "layer", "units"):
        expected = {"contents": 14., "field": 12., "layer": 16., "units": 8 / .3048}[change]
        assert net["features"][0]["bfWidth"] == pytest.approx(expected)
        native = np.fromfile(config.DATA_DIRECTORY / "hydraulics/10.bin", dtype="<f8").reshape(4, -1)
        assert native[3].max() == pytest.approx(expected, rel=.002)
    elif change == "path":
        assert net["metadata"]["basePath"] == str(config.NETWORK_PATH)
    elif change == "curves":
        assert curves[0]["depth"][0][1] == .123
    else:
        assert curves[0]["defaultStart"] == 5


@pytest.mark.parametrize("problem", ["missing", "corrupt"])
def test_missing_or_corrupt_prepared_hydraulics_rebuild(configured_inputs, problem):
    config = configured_inputs
    ensure_prepared_data(config)
    path = config.DATA_DIRECTORY / "hydraulics/10.bin"
    expected = path.read_bytes()
    if problem == "missing":
        path.unlink()
    else:
        path.write_bytes(b"old data")
    assert ensure_prepared_data(config)["rebuilt"]
    assert path.read_bytes() == expected


def test_invalid_configuration_keeps_previous_build_but_raises(configured_inputs):
    config = configured_inputs
    ensure_prepared_data(config)
    previous = (config.DATA_DIRECTORY / "network.json").read_bytes()
    config.NETWORK_FIELDS["bankfull_width"] = "misspelled_width"
    with pytest.raises(ValueError, match="Missing configured fields.*misspelled_width"):
        ensure_prepared_data(config)
    assert (config.DATA_DIRECTORY / "network.json").read_bytes() == previous
    config.NETWORK_PATH = config.NETWORK_PATH.with_name("missing.gpkg")
    with pytest.raises(FileNotFoundError):
        ensure_prepared_data(config)
    assert not config.NETWORK_PATH.exists()


def test_rebuild_removes_native_files_for_removed_reaches(configured_inputs):
    config = configured_inputs
    ensure_prepared_data(config)
    assert (config.DATA_DIRECTORY / "hydraulics/20.bin").is_file()
    with sqlite3.connect(config.NETWORK_PATH) as con:
        con.execute("DELETE FROM reaches WHERE reach_no=20")
    assert ensure_prepared_data(config)["rebuilt"]
    assert not (config.DATA_DIRECTORY / "hydraulics/20.bin").exists()


def test_duplicate_join_ids_are_rejected(configured_inputs):
    path = configured_inputs.MAX_DEPTH_PATH
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO depths VALUES (10, 9.)")
    with pytest.raises(ValueError, match="Duplicate depth_id"):
        read_layer(path, "depths", "depth_id", ["maximum_ft"])


def test_app_reload_updates_displayed_width_and_sources(configured_inputs, monkeypatch):
    from test_app import champ_app
    import prepare_data

    config = configured_inputs
    monkeypatch.setattr(prepare_data, "load_settings", lambda: copy.deepcopy(config))
    at = champ_app().run()
    assert not at.exception and not at.error
    at.session_state.selected_id = 10
    at.run()
    def model_width():
        frame = next(d.value for d in at.dataframe
                     if set(("Input", "Value", "Units")).issubset(d.value.columns))
        return frame.set_index("Input").loc["Bankfull width", "Value"]
    assert model_width() == pytest.approx(8.)
    config.NETWORK_FIELDS["bankfull_width"] = "alternative_width_ft"
    at.run()  # Full reruns automatically check settings and source files.
    assert not at.exception and not at.error
    assert model_width() == pytest.approx(12.)
    sources = next(d.value for d in at.dataframe if "Source file" in d.value.columns)
    assert sources.set_index("Input").loc["Bankfull width", "Field"] == "alternative_width_ft"
    assert sources.set_index("Input").loc["Temperature score", "Source file"] == str(config.PRIORITY_PATH)
    # A bad path must stop the app instead of rendering the previous map/inputs.
    config.NETWORK_PATH = config.NETWORK_PATH.with_name("does_not_exist.gpkg")
    at.run()  # Full reruns automatically check settings and source files.
    assert not at.exception
    assert any("does_not_exist.gpkg" in e.value for e in at.error)
    assert not at.get("deck_gl_json_chart")
