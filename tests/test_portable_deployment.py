"""A portable overview must load without native files or hydraulic solves."""
from pathlib import Path
import json
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prepare_data
import runtime_data


def test_bundled_data_is_current(monkeypatch):
    def disallow_build(*args, **kwargs):
        raise AssertionError("The matching overview must load without preparation")

    monkeypatch.setattr(runtime_data, "prepare_network", disallow_build)
    config = prepare_data.load_settings()
    manifest = json.loads((config.DATA_DIRECTORY / "build-info.json").read_text())
    assert runtime_data.runtime_signature(config)[0] == manifest["fingerprint"]
    network, curves = runtime_data.load_runtime_dataset(config)
    assert len(network["features"]) == 728
    assert len(curves) == 5
    assert not network["metadata"].get("pendingHydraulics")


def test_manifest_fingerprint_survives_relocation(tmp_path, monkeypatch):
    config = prepare_data.load_settings()
    expected = runtime_data.runtime_signature(config)[0]
    relocated = tmp_path / "hosted-checkout"
    relocated.mkdir()
    for folder in ("inputs", "data", "model", "tools", "r_gift"):
        shutil.copytree(ROOT / folder, relocated / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "hydraulics"))
    for filename in ("settings.py", "prepare_data.py", "runtime_data.py", "habitat_engine.py", "presentation.py", "r_engine.py"):
        shutil.copy2(ROOT / filename, relocated / filename)
    monkeypatch.setattr(prepare_data, "ROOT", relocated)
    monkeypatch.setattr(runtime_data, "ROOT", relocated)
    def disallow_build(*args, **kwargs):
        raise AssertionError("Moving the overview must not rebuild hydraulics")
    monkeypatch.setattr(runtime_data, "prepare_network", disallow_build)
    config = prepare_data.load_settings()
    assert runtime_data.runtime_signature(config)[0] == expected
    assert not (config.DATA_DIRECTORY / "hydraulics").exists()
    network, _ = runtime_data.load_runtime_dataset(config)
    assert len(network["features"]) == 728
