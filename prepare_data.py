"""Check source provenance and rebuild stale data. Run directly in VSCode."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import runpy
import shutil
import tempfile
import threading
from types import SimpleNamespace

from tools.prepare_network import GROUPS, prepare_network
from r_engine import ENGINE_FILES

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = 4
_BUILD_LOCK = threading.RLock()


def load_settings():
    """Read current settings on every full app rerun, bypassing Python's import cache."""
    return SimpleNamespace(**runpy.run_path(str(ROOT / "settings.py")))


def build_options(config):
    names = {
        "network_path": "NETWORK_PATH", "network_layer": "NETWORK_LAYER",
        "max_depth_path": "MAX_DEPTH_PATH", "max_depth_layer": "MAX_DEPTH_LAYER",
        "priority_path": "PRIORITY_PATH", "priority_layer": "PRIORITY_LAYER",
        "curves_directory": "CURVES_DIRECTORY", "output_directory": "DATA_DIRECTORY",
        "network_fields": "NETWORK_FIELDS", "max_depth_id_field": "MAX_DEPTH_ID_FIELD",
        "max_depth_field": "MAX_DEPTH_FIELD", "priority_id_field": "PRIORITY_ID_FIELD",
        "priority_fields": "PRIORITY_FIELDS", "temperature_source": "TEMPERATURE_SOURCE",
        "temperature_field": "TEMPERATURE_FIELD", "monthly_flow_fields": "MONTHLY_FLOW_FIELDS",
        "field_units": "FIELD_UNITS", "simplify_meters": "SIMPLIFY_METERS",
        "default_periods": "DEFAULT_PERIODS",
    }
    options = {key: getattr(config, name) for key, name in names.items()}
    for key in ("network_path", "max_depth_path", "priority_path", "curves_directory", "output_directory"):
        path = Path(options[key]).expanduser()
        options[key] = (Path(config.ROOT) / path).resolve() if not path.is_absolute() else path.resolve()
    for period in options["default_periods"].values():
        if any(type(period[k]) is not int or not 1 <= period[k] <= 12 for k in ("start", "end")):
            raise ValueError("Default months must be integers from 1 through 12")
    return options


@lru_cache(maxsize=4096)
def _hash_at_stat(path, signature):
    # Read again whenever path, size, modification/change time, or inode changes.
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_hash(path):
    path = Path(path).resolve()
    stat = path.stat()
    return _hash_at_stat(str(path), (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino))


def portable_path(path):
    """Identify repository assets independently of the machine's absolute root.

    Files outside this repository retain absolute paths, so two identically
    named external sources are never treated as interchangeable.
    """
    path = Path(path).expanduser().resolve()
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def source_state(config, options):
    """Hash source contents, settings and model code with portable paths."""
    files = []
    for role in ("network", "max_depth", "priority"):
        path = options[f"{role}_path"]
        files.append(dict(role=role, path=portable_path(path), sha256=file_hash(path)))
        # GeoPackage edits may be committed to SQLite's write-ahead log first.
        wal = Path(str(path) + "-wal")
        if path.suffix.lower() == ".gpkg" and wal.is_file():
            files.append(dict(role=f"{role}_wal", path=portable_path(wal), sha256=file_hash(wal)))
    for key, _, _ in GROUPS:
        for kind in ("depth", "velocity", "substrate"):
            path = options["curves_directory"] / f"{key}_{kind}.csv"
            files.append(dict(role=f"curve:{key}:{kind}", path=portable_path(path), sha256=file_hash(path)))
    code = {}
    for relative in ("prepare_data.py", "tools/prepare_network.py", *ENGINE_FILES):
        code[relative] = file_hash(ROOT / relative)
    state = dict(schemaVersion=SCHEMA_VERSION, title=config.APP_TITLE,
                 configuration={k: portable_path(v) if isinstance(v, Path) else v for k, v in options.items()},
                 inputs=files, calculationCodeSHA256=code)
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), state


def current_manifest(directory, fingerprint):
    try:
        manifest = json.loads((directory / "build-info.json").read_text(encoding="utf-8"))
        outputs = manifest.get("outputs", {})
        if (manifest.get("schemaVersion") != SCHEMA_VERSION or manifest.get("fingerprint") != fingerprint
                or not {"network.json", "curves.json"}.issubset(outputs)):
            return None
        for relative, expected in outputs.items():
            path = directory / relative
            if not path.is_file() or file_hash(path) != expected:
                return None
        return manifest
    except (OSError, ValueError, TypeError):
        return None


def _publish(staging, target):
    """Replace the complete prepared dataset; roll back if publication fails."""
    backup = None
    if target.exists():
        backup = Path(tempfile.mkdtemp(prefix=f".{target.name}-previous-", dir=target.parent))
        backup.rmdir()
        target.rename(backup)
    try:
        staging.rename(target)
    except BaseException:
        if backup is not None:
            backup.rename(target)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)


def ensure_prepared_data(config=None, *, force=False):
    """Never serve data from a different source, mapping, or incomplete build."""
    config = config or load_settings()
    with _BUILD_LOCK:
        options = build_options(config)
        fingerprint, state = source_state(config, options)
        directory = options["output_directory"]
        manifest = None if force else current_manifest(directory, fingerprint)
        if manifest is not None:
            return dict(rebuilt=False, manifest=manifest)
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{directory.name}-build-", dir=directory.parent) as temp:
            staging = Path(temp) / "data"
            prepare_network(**dict(options, output_directory=staging))
            if source_state(config, options)[0] != fingerprint:
                raise ValueError("Input files changed during preparation. Rerun the app after edits finish.")
            outputs = {p.relative_to(staging).as_posix(): file_hash(p) for p in sorted(staging.rglob("*")) if p.is_file()}
            manifest = dict(state, fingerprint=fingerprint, outputs=outputs,
                            builtAt=datetime.now(timezone.utc).isoformat(),
                            aggregation="Median of separately evaluated monthly habitat results.",
                            requireAllMonthsDefault=True)
            (staging / "build-info.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            _publish(staging, directory)
        return dict(rebuilt=True, manifest=manifest)


def main():
    result = ensure_prepared_data(force=True)
    print(f"Prepared data matches the configured sources and fields. Build: {result['manifest']['builtAt']}")
    print("Launch or rerun the app to load the updated data.")


if __name__ == "__main__":
    main()
