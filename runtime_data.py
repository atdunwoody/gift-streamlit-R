"""Load the overview without rebuilding hydraulics or scanning native files."""
from __future__ import annotations

import json

from habitat_engine import load_dataset, validate_curves
from prepare_data import ROOT, build_options, file_hash, source_state
from tools.prepare_network import prepare_network
from r_engine import ENGINE_FILES


def hydraulic_model_signature():
    return tuple(file_hash(ROOT / relative) for relative in (
        *ENGINE_FILES, "habitat_engine.py", "bankfull_regressions.py", "network_scenarios.py"))


def runtime_signature(config):
    options = build_options(config)
    fingerprint, _ = source_state(config, options)
    directory = options["output_directory"]
    assets = tuple(file_hash(directory / name) if (directory / name).is_file() else None
                   for name in ("network.json", "curves.json", "build-info.json"))
    implementation = tuple(file_hash(ROOT / relative) for relative in (
        "runtime_data.py", "habitat_engine.py", "presentation.py"))
    return fingerprint, assets, implementation


def _verified_snapshot(directory):
    """Validate only the small overview assets. Native .bin files are unused."""
    try:
        manifest = json.loads((directory / "build-info.json").read_text(encoding="utf-8"))
        outputs = manifest["outputs"]
        if any(file_hash(directory / name) != outputs[name]
               for name in ("network.json", "curves.json")):
            return None, None
        code = manifest["calculationCodeSHA256"]
        if any(code[relative] != file_hash(ROOT / relative) for relative in ENGINE_FILES):
            return None, None
        return load_dataset(directory), manifest
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def load_runtime_dataset(config):
    """Use a matching overview; refresh changed inputs without hydraulic solves."""
    options = build_options(config)
    fingerprint, _ = source_state(config, options)
    snapshot, manifest = _verified_snapshot(options["output_directory"])
    if snapshot is not None and manifest.get("fingerprint") == fingerprint:
        return snapshot
    network, curves = prepare_network(
        **dict(options, output_directory=None), calculate_hydraulics=False,
        baseline_network=snapshot[0] if snapshot is not None else None)
    for curve in curves:
        validate_curves(curve)
    return network, curves
