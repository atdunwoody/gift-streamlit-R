"""Verify prepared R results and rejection of stale or unrelated assets."""
import copy
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bankfull_regressions import CASTRO_JACKSON, CHAMP
from habitat_engine import calculate_hydraulics, hydraulic_parameters, load_dataset
from network_scenarios import full_curve_mean
from prepare_data import load_settings
import r_scenarios
from runtime_data import runtime_signature
from test_data_preparation import configured_inputs


@pytest.mark.parametrize("depth", [CHAMP, CASTRO_JACKSON])
def test_prepared_native_curves_and_means_match_live_r(depth):
    config = load_settings()
    network, curves = load_dataset(config.DATA_DIRECTORY)
    prepared = r_scenarios.prepared_scenario(network, config.DATA_DIRECTORY,
        runtime_signature(config)[0], CHAMP, depth, config.BIEGER_REGION, physical=True)
    assert prepared is not None
    modeled, physical, entry = prepared
    assert len(modeled["features"]) == 728
    means = r_scenarios.prepared_mean(entry, config.DATA_DIRECTORY, curves[0])
    assert means is not None
    for comid in (23428544, 23428378):
        reach = next(r for r in modeled["features"] if r["id"] == comid)
        actual = calculate_hydraulics(hydraulic_parameters(reach))
        np.testing.assert_allclose(physical[comid], actual[["Q", "Ai", "Wi", "di", "Ui"]], rtol=2e-12, atol=2e-12)
        expected = full_curve_mean(reach, physical[comid], curves[0])
        for field in ("hsi", "wua", "d", "v", "s"):
            assert means.at[comid, field] == pytest.approx(expected[field], abs=2e-12)


def test_prepared_scenarios_reject_changed_inputs_reaches_and_model(monkeypatch):
    config = load_settings()
    network, _ = load_dataset(config.DATA_DIRECTORY)
    fingerprint = runtime_signature(config)[0]
    parameters = (config.DATA_DIRECTORY, fingerprint, CHAMP, CASTRO_JACKSON, config.BIEGER_REGION)
    assert r_scenarios.prepared_scenario(network, *parameters) is not None
    assert r_scenarios.prepared_scenario(network, config.DATA_DIRECTORY, "changed-inputs",
        CHAMP, CASTRO_JACKSON, config.BIEGER_REGION) is None
    subset = dict(network, features=network["features"][:-1])
    assert r_scenarios.prepared_scenario(subset, *parameters) is None
    monkeypatch.setattr(r_scenarios, "model_hashes", lambda: {"changed-model": "different"})
    assert r_scenarios.prepared_scenario(network, *parameters) is None


def test_changed_curve_does_not_reuse_a_prepared_mean():
    config = load_settings()
    network, curves = load_dataset(config.DATA_DIRECTORY)
    prepared = r_scenarios.prepared_scenario(network, config.DATA_DIRECTORY,
        runtime_signature(config)[0], CHAMP, CASTRO_JACKSON, config.BIEGER_REGION)
    assert prepared is not None
    changed = copy.deepcopy(curves[0])
    changed["depth"][0][1] = .12345
    assert r_scenarios.prepared_mean(prepared[2], config.DATA_DIRECTORY, changed) is None


def test_offline_rebuild_reads_current_sources_and_publishes_verified_assets(configured_inputs, monkeypatch):
    import prepare_r_scenarios
    from prepare_data import build_options, current_manifest, source_state
    config = configured_inputs
    config.NETWORK_FIELDS["bankfull_width"] = "alternative_width_ft"
    monkeypatch.setattr(prepare_r_scenarios, "load_settings", lambda: config)
    monkeypatch.setattr(prepare_r_scenarios, "WORKERS", 1)
    monkeypatch.setattr(prepare_r_scenarios, "PREPARE_MEAN_SUMMARIES", False)
    prepare_r_scenarios.main()
    network, curves = load_dataset(config.DATA_DIRECTORY)
    assert len(network["features"]) == 2
    assert all(reach["bfWidth"] == pytest.approx(12.) for reach in network["features"])
    fingerprint, _ = source_state(config, build_options(config))
    assert current_manifest(config.DATA_DIRECTORY, fingerprint) is not None
    prepared = r_scenarios.prepared_scenario(network, config.DATA_DIRECTORY,
        fingerprint, CHAMP, CHAMP, config.BIEGER_REGION, physical=True)
    assert prepared is not None and len(prepared[1]) == 2
