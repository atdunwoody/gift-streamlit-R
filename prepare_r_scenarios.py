"""Build reusable R results offline. Run directly in VSCode after input changes.

The source CHaMP geometry and the default CHaMP-width/Castro-and-Jackson-depth
scenario are calculated with the original R functions. No R work is done in
the browser. Interrupted builds reuse completed reach calculations.
"""
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd

from bankfull_regressions import CASTRO_JACKSON, CHAMP, network_reach
from curve_presets import gift_shiny_default
from habitat_engine import (calculate_hydraulics, hydraulic_parameters, load_dataset,
                            reach_with_hydraulics)
from network_scenarios import full_curve_mean
from prepare_data import build_options, file_hash, load_settings, source_state
from r_engine import ENGINE_FILES, runtime_info
from r_scenarios import curve_key, model_hashes
from runtime_data import load_runtime_dataset

WORKERS = min(4, os.cpu_count() or 1)
PREPARE_MEAN_SUMMARIES = True


def clean(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return value


def json_file(path, value):
    path.write_text(json.dumps(clean(value), separators=(",", ":"), allow_nan=False), encoding="utf-8")


def solve_task(task):
    original, width, depth, region, curves, cache_directory, signature = task
    reach = network_reach(original, width, depth, region)
    key = hashlib.sha256(json.dumps([reach, width, depth, region, curves, signature],
                                   sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path = Path(cache_directory) / f"{key}.npz"
    if path.is_file():
        try:
            with np.load(path, allow_pickle=False) as cache:
                if str(cache["key"]) == key:
                    return str(path)
        except (OSError, ValueError, KeyError):
            pass
    try:
        if reach.get("scenarioError"):
            raise ValueError(reach["scenarioError"])
        if width == depth == CHAMP and reach.get("maxDepth") is None:
            raise ValueError("Maximum bankfull depth is unavailable for CHaMP Regression.")
        hyd = calculate_hydraulics(hydraulic_parameters(reach))
    except ValueError as exc:
        reach["scenarioError"] = str(exc)
        hyd = pd.DataFrame()
    evaluated = reach_with_hydraulics(reach, hyd)
    physical = np.empty((0, 5)) if hyd.empty else hyd[["Q", "Ai", "Wi", "di", "Ui"]].to_numpy()
    if hyd.empty:
        evaluated.update(modelStatus=reach.get("scenarioError", "Missing or invalid hydraulic inputs"),
                         shapeRaw=None, shapeUsed=None, qMin=None, qMax=None, nativeCount=0)
    means = {curve_key(curve): full_curve_mean(evaluated, physical if len(physical) else None, curve)
             for curve in curves}
    encoded = json.dumps(clean(dict(reach=evaluated, means=means)), separators=(",", ":"), allow_nan=False)
    with tempfile.NamedTemporaryFile(dir=cache_directory, suffix=".npz", delete=False) as temp:
        temporary = Path(temp.name)
        np.savez_compressed(temp, key=key, physical=physical, payload=encoded)
    temporary.replace(path)
    return str(path)


def main():
    config = load_settings()
    options = build_options(config)
    config.DATA_DIRECTORY = options["output_directory"]
    config.CURVES_DIRECTORY = options["curves_directory"]
    info = runtime_info()
    baseline, curves = load_runtime_dataset(config)
    active_curves = curves
    shiny = gift_shiny_default()
    if shiny.get("available"):
        curves = [*curves, shiny]
    if not PREPARE_MEAN_SUMMARIES:
        curves = []
    output = Path(config.DATA_DIRECTORY) / "r_scenarios"
    output.mkdir(parents=True, exist_ok=True)
    cache = Path(config.DATA_DIRECTORY) / "r_build_cache"
    cache.mkdir(exist_ok=True)
    signature = model_hashes()
    scenarios = [(CHAMP, CHAMP), (CHAMP, CASTRO_JACKSON)]
    jobs = [(reach, width, depth, config.BIEGER_REGION, curves, str(cache), signature)
            for width, depth in scenarios for reach in baseline["features"]]
    grouped = {pair: [] for pair in scenarios}
    workers = max(1, int(os.environ.get("GIFT_PREPARE_WORKERS", str(WORKERS))))
    print(f"Calculating {len(jobs)} reach/scenario combinations with {workers} R workers.", flush=True)
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as executor:
        futures = {executor.submit(solve_task, job): (job[1], job[2]) for job in jobs}
        for count, future in enumerate(as_completed(futures), 1):
            grouped[futures[future]].append(future.result())
            if count % 50 == 0 or count == len(jobs):
                print(f"Completed {count}/{len(jobs)} reach/scenario combinations", flush=True)
    entries = []
    for (width, depth), paths in grouped.items():
        features, physical, means = {}, {}, {curve_key(curve): [] for curve in curves}
        for path in paths:
            with np.load(path, allow_pickle=False) as item:
                payload = json.loads(str(item["payload"]))
                reach = payload["reach"]
                features[reach["id"]] = reach
                if len(item["physical"]):
                    physical[str(reach["id"])] = item["physical"]
                for key, row in payload["means"].items():
                    means[key].append(row)
        ordered = [features[reach["id"]] for reach in baseline["features"]]
        metadata = dict(baseline["metadata"], calculationEngine="Original R GIFT",
                        giftSourceRevision=info["revision"], pendingHydraulics=[],
                        skippedHydraulics=[r["id"] for r in ordered if str(r["id"]) not in physical],
                        shapeFactorAbove07=[r["id"] for r in ordered if (r.get("shapeUsed") or 0) > .7],
                        bankfullWidthSource=width, bankfullDepthSource=depth, biegerRegion=config.BIEGER_REGION)
        network = dict(metadata=metadata, features=ordered)
        stem = "champ_champ" if depth == CHAMP else "champ_castro_jackson"
        network_path, physical_path = output / f"{stem}.json", output / f"{stem}.npz"
        json_file(network_path, network)
        np.savez_compressed(physical_path, **physical)
        entry = dict(width=width, depth=depth, region=config.BIEGER_REGION,
                     network=network_path.name, network_sha256=file_hash(network_path),
                     physical=physical_path.name, physical_sha256=file_hash(physical_path), means={})
        for key, rows in means.items():
            path = output / f"{stem}_mean_{key[:16]}.json"
            json_file(path, sorted(rows, key=lambda r: r["id"]))
            entry["means"][key] = dict(file=path.name, sha256=file_hash(path))
        entries.append(entry)
        if width == depth == CHAMP:
            # The source overview now comes from the original R calculations.
            for reach in network["features"]:
                reach.pop("networkInputs", None)
            json_file(Path(config.DATA_DIRECTORY) / "network.json", network)
    json_file(Path(config.DATA_DIRECTORY) / "curves.json", active_curves)
    curve_directory = Path(config.DATA_DIRECTORY) / "curves"
    curve_directory.mkdir(exist_ok=True)
    for curve in active_curves:
        for kind in ("depth", "velocity", "substrate"):
            name = f"{curve['id']}_{kind}.csv"
            shutil.copy2(Path(config.CURVES_DIRECTORY) / name, curve_directory / name)
    fingerprint, state = source_state(config, build_options(config))
    json_file(output / "index.json", dict(source_fingerprint=fingerprint, model_hashes=model_hashes(),
        reach_ids=sorted(features), scenarios=entries, runtime=info,
        built_at=datetime.now(timezone.utc).isoformat()))
    manifest_path = Path(config.DATA_DIRECTORY) / "build-info.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    manifest.update(state, fingerprint=fingerprint, builtAt=datetime.now(timezone.utc).isoformat(), calculationRuntime=info)
    assets = [Path(config.DATA_DIRECTORY) / name for name in ("network.json", "curves.json")]
    assets.extend(curve_directory.glob("*.csv"))
    assets.extend(output.glob("*"))
    manifest["outputs"] = {path.relative_to(config.DATA_DIRECTORY).as_posix(): file_hash(path)
                           for path in assets if path.is_file()}
    json_file(manifest_path, manifest)
    print("Prepared R network scenarios, native curves, means, and source overview.", flush=True)


if __name__ == "__main__":
    main()
