"""Read verified, offline R calculations for the two bundled network scenarios."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r_engine import ENGINE_FILES

ROOT = Path(__file__).resolve().parent


def model_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in (*ENGINE_FILES, "bankfull_regressions.py", "habitat_engine.py", "network_scenarios.py")}


def curve_key(curve):
    # Period labels and month defaults do not change the full-curve average.
    values = {name: curve[name] for name in ("depth", "velocity", "substrate")}
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prepared_scenario(network, directory, fingerprint, width, depth, region, *, physical=False):
    """Return None for missing/stale assets, then the caller calculates with R."""
    folder = Path(directory) / "r_scenarios"
    try:
        index = json.loads((folder / "index.json").read_text(encoding="utf-8"))
        if index["source_fingerprint"] != fingerprint or index["model_hashes"] != model_hashes():
            return None
        if sorted(r["id"] for r in network["features"]) != index["reach_ids"]:
            return None
        entry = next(e for e in index["scenarios"]
                     if (e["width"], e["depth"], e["region"]) == (width, depth, region))
        path = folder / entry["network"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["network_sha256"]:
            return None
        prepared = json.loads(path.read_text(encoding="utf-8"))
        curves = None
        if physical:
            path = folder / entry["physical"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry["physical_sha256"]:
                return None
            with np.load(path, allow_pickle=False) as arrays:
                curves = {int(key): arrays[key] for key in arrays.files}
        return prepared, curves, entry
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None


def prepared_mean(entry, directory, curve):
    item = entry.get("means", {}).get(curve_key(curve))
    if item is None:
        return None
    try:
        path = Path(directory) / "r_scenarios" / item["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            return None
        frame = pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
        return frame.set_index("id")
    except (OSError, ValueError, KeyError, TypeError):
        return None
