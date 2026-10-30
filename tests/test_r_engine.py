"""Execute the original R backend, including process and Streamlit integration."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import r_engine
import habitat_engine
from test_r_reference import CASES, HYDRAULIC_COLUMNS, arguments


def test_original_sources_and_actual_r_runtime_are_used():
    info = r_engine.runtime_info()
    assert info["engine"] == "Original R GIFT"
    assert info["revision"] == "40c4a74f82d842cd2ab8ab4c53e2b985ac4e0030"
    assert info["R_version"].startswith("R version")
    for name, digest in info["files_sha256"].items():
        path = ROOT / "r_gift/upstream" / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert path.read_bytes() == (ROOT / "tests/r_reference/source" / name).read_bytes()
    assert habitat_engine.avg_hydraulics is r_engine.avg_hydraulics
    assert habitat_engine.habitat is r_engine.habitat


@pytest.mark.parametrize("case", CASES, ids=[case["case"] for case in CASES])
def test_live_r_hydraulics_match_frozen_r_values(case):
    grid = pd.read_csv(ROOT / "tests/r_reference/hydraulics.csv")
    expected = grid[grid.case.eq(case["case"])][HYDRAULIC_COLUMNS]
    actual = r_engine.avg_hydraulics(case["S"], case["wb"], case["db"], case["D84"], **arguments(case))
    np.testing.assert_allclose(actual[HYDRAULIC_COLUMNS], expected, rtol=2e-9, atol=2e-11)
    native = r_engine.avg_hydraulics(case["S"], case["wb"], case["db"], case["D84"],
                                     full_curve=True, **arguments(case))
    expected_native = pd.read_csv(ROOT / "tests/r_reference/native_levels.csv")
    expected_native = expected_native[expected_native.case.eq(case["case"])]
    np.testing.assert_allclose(native.iloc[expected_native["rank"]][HYDRAULIC_COLUMNS],
                               expected_native[HYDRAULIC_COLUMNS], rtol=2e-9, atol=2e-11)
    assert native.attrs["calculation_engine"] == "Original R GIFT"


@pytest.mark.parametrize("mode", ["none", "distribution", "D84_proxy"])
def test_live_r_habitat_matches_frozen_r_values(mode):
    inputs = pd.read_csv(ROOT / "tests/r_reference/habitat_inputs.csv")
    expected = pd.read_csv(ROOT / "tests/r_reference/habitat.csv")
    for key, rows in expected[expected.substrate_mode.eq(mode)].groupby("curve", sort=False):
        depth = pd.read_csv(ROOT / f"inputs/curves/{key}_depth.csv")
        velocity = pd.read_csv(ROOT / f"inputs/curves/{key}_velocity.csv")
        options = {}
        if mode != "none":
            options["substrate_curve"] = pd.read_csv(ROOT / f"inputs/curves/{key}_substrate.csv")
            options.update(gsd=[1, 2, 5, 10, 20, 40, 60, 100, 200, 400]) if mode == "distribution" else options.update(substrate_size_mm=100)
        actual = r_engine.habitat(inputs[HYDRAULIC_COLUMNS], depth, velocity, **options)
        columns = ["Q", "d.suit", "v.suit", "s.suit", "w", "WUA"]
        np.testing.assert_allclose(actual[columns], rows.sort_values("input_row")[columns],
                                   rtol=0, atol=2e-12)


def test_streamlit_reports_missing_r_instead_of_falling_back(monkeypatch):
    monkeypatch.setenv("GIFT_RSCRIPT", str(ROOT / "missing-r-installation/Rscript"))
    at = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not at.exception
    assert any("Rscript was not found" in error.value for error in at.error)
    assert not at.get("deck_gl_json_chart")
    with pytest.raises(r_engine.RSetupError, match="Rscript was not found"):
        r_engine.avg_hydraulics(.002, 10., .5, 60., full_curve=True)


def test_shared_r_worker_serializes_concurrent_requests():
    def calculate(width):
        return width, r_engine.avg_hydraulics(.002, width, .5, 60., full_curve=True)
    widths = [8.31, 9.41, 10.51, 11.61]
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(calculate, widths))
    for width, frame in values:
        assert len(frame) == 981
        assert frame.attrs["shape_factor"] == pytest.approx(width/.5/100)
        assert frame.Q.is_monotonic_increasing


def test_dead_r_worker_restarts_with_same_source_revision():
    old = r_engine._get_worker()
    old.process.kill()
    old.process.wait(timeout=10)
    info = r_engine.runtime_info()
    assert r_engine._get_worker().process.pid != old.process.pid
    assert info["revision"] == r_engine.source_provenance()["revision"]
