"""Run the original GIFT R functions in one serialized Rscript worker.

No Python model is used as a fallback. The Python implementation under model/
remains available for independent numerical regression checks.
"""
from __future__ import annotations

import atexit
from collections import deque
from functools import lru_cache
import hashlib
import json
from numbers import Real
import os
from pathlib import Path
import queue
import runpy
import shutil
import subprocess
import threading

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
R_DIRECTORY = ROOT / "r_gift"
HYDRAULIC_COLUMNS = ("Q", "Ai", "Wi", "di", "Ui")
ENGINE_FILES = ("r_engine.py", "r_gift/worker.R", "r_gift/provenance.json",
                "r_gift/upstream/AvgHydraulics.R", "r_gift/upstream/Habitat.R")


class RBackendError(RuntimeError):
    """R could not be started or a worker request could not be completed."""


class RSetupError(RBackendError):
    """R or its required packages are missing or incorrectly configured."""


@lru_cache(maxsize=64)
def _hash_file(path, stat_signature):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def engine_signature():
    signatures = []
    for name in ENGINE_FILES:
        path = ROOT / name
        stat = path.stat()
        signatures.append(_hash_file(str(path), (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)))
    return tuple(signatures)


def source_provenance():
    provenance = json.loads((R_DIRECTORY / "provenance.json").read_text(encoding="utf-8"))
    for name, expected in provenance["files_sha256"].items():
        if hashlib.sha256((R_DIRECTORY / "upstream" / name).read_bytes()).hexdigest() != expected:
            raise RSetupError(f"Original R source checksum mismatch: {name}. Restore the pinned source or update its provenance.")
    dependency = provenance.get("bundled_dependency")
    if dependency:
        archive = R_DIRECTORY / "dependencies" / f"birk_{dependency['version']}.tar.gz"
        if hashlib.sha256(archive.read_bytes()).hexdigest() != dependency["sha256"]:
            raise RSetupError("Bundled birk source checksum mismatch. Restore r_gift/dependencies.")
    return provenance


def rscript_executable():
    config = runpy.run_path(str(ROOT / "settings.py"))
    specified = os.environ.get("GIFT_RSCRIPT") or config.get("RSCRIPT_PATH")
    if specified:
        candidate = Path(specified).expanduser()
        found = str(candidate) if candidate.is_file() else shutil.which(str(specified))
        if not found:
            raise RSetupError(f"Rscript was not found at {specified}. Correct RSCRIPT_PATH in settings.py.")
        return str(Path(found).resolve())
    found = shutil.which("Rscript")
    if not found and os.name == "nt":
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "R"
        candidates = sorted([*base.glob("R-*/bin/Rscript.exe"), *base.glob("R-*/bin/x64/Rscript.exe")], reverse=True)
        found = str(candidates[0]) if candidates else None
    if not found:
        raise RSetupError("Rscript was not found. Install R, run setup_r.R in R, and set RSCRIPT_PATH in settings.py if Rscript is not on PATH.")
    return str(Path(found).resolve())


class _RWorker:
    """Own the subprocess, pipe readers, and lock shared by Streamlit sessions."""

    def __init__(self, executable, signature):
        self.key = (executable, signature)
        self.lock = threading.RLock()
        self.lines = queue.Queue()
        self.messages = deque(maxlen=40)
        self.sequence = 0
        self.timeout = float(os.environ.get("GIFT_R_TIMEOUT", "300"))
        source_provenance()
        try:
            self.process = subprocess.Popen(
                [executable, "--vanilla", str(R_DIRECTORY / "worker.R"), str(R_DIRECTORY)],
                cwd=R_DIRECTORY, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        except OSError as exc:
            raise RSetupError(f"Could not start Rscript: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        try:
            ready = self._receive()
            if not ready.get("ok"):
                raise RSetupError(ready.get("error", "R worker initialization failed"))
            self.info = ready["result"]
        except BaseException:
            self.close()
            raise

    def _read_stdout(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line)
        finally:
            self.lines.put(None)

    def _read_stderr(self):
        for line in self.process.stderr:
            self.messages.append(line.rstrip())

    def _receive(self):
        try:
            line = self.lines.get(timeout=self.timeout)
        except queue.Empty as exc:
            self.close()
            raise RBackendError(f"R GIFT exceeded the {self.timeout:g}-second request timeout. Increase GIFT_R_TIMEOUT for large batches.") from exc
        if line is None:
            detail = " ".join(self.messages)[-1800:]
            raise RBackendError(f"The R GIFT worker stopped unexpectedly. {detail}")
        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            self.close()
            raise RBackendError("R GIFT returned an invalid response. Check the R runtime and worker source.") from exc

    def request(self, value):
        with self.lock:
            self.sequence += 1
            request = dict(value, id=self.sequence)
            try:
                self.process.stdin.write(json.dumps(request, allow_nan=False, separators=(",", ":")) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                self.close()
                raise RBackendError("Could not send a calculation to R GIFT. Rerun the app to restart the worker.") from exc
            response = self._receive()
            if response.get("id") != self.sequence:
                self.close()
                raise RBackendError("R GIFT response did not match its request.")
            if not response.get("ok"):
                raise ValueError(f"Original R GIFT: {response.get('error', 'calculation failed')}")
            return response["result"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


_worker = None
_worker_lock = threading.RLock()


def _get_worker():
    global _worker
    key = (rscript_executable(), engine_signature())
    with _worker_lock:
        if _worker is None or _worker.key != key or _worker.process.poll() is not None:
            if _worker is not None:
                _worker.close()
            _worker = _RWorker(*key)
        return _worker


def close_worker():
    global _worker
    with _worker_lock:
        if _worker is not None:
            _worker.close()
            _worker = None


atexit.register(close_worker)


def runtime_info():
    worker = _get_worker()
    return dict(worker.info, engine="Original R GIFT", **source_provenance())


def _frame(result):
    frame = pd.DataFrame(result["data"], columns=result["columns"], dtype=float)
    frame.attrs.update(result.get("attrs") or {})
    frame.attrs.update(calculation_engine="Original R GIFT", source_revision=source_provenance()["revision"])
    return frame


def _scalar(value, name, positive=True):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a numeric scalar")
    value = float(value)
    if not np.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be finite" + (" and greater than zero" if positive else ""))
    return value


@lru_cache(maxsize=1024)
def _hydraulics(signature, executable, parameters, full_curve, discharges):
    names = ("slope", "width", "depth", "d84", "maximum", "shape")
    value = _get_worker().request(dict(operation="hydraulics", parameters=dict(zip(names, parameters)),
                                     full_curve=full_curve, discharges=discharges))
    return _frame(value)


def avg_hydraulics(slope, bankfull_width, bankfull_depth, d84_mm, *,
                   max_bankfull_depth=None, shape_factor=None, discharges=None,
                   full_curve=False):
    """Return the original R hydraulic output or all native simulated stages."""
    values = tuple(_scalar(value, name) for name, value in zip(
        ("slope", "bankfull_width", "bankfull_depth", "d84_mm"),
        (slope, bankfull_width, bankfull_depth, d84_mm)))
    maximum = None if max_bankfull_depth is None else _scalar(max_bankfull_depth, "max_bankfull_depth")
    shape = None if shape_factor is None else _scalar(shape_factor, "shape_factor", positive=False)
    if maximum is not None and maximum < values[2]:
        raise ValueError("max_bankfull_depth must not be below mean bankfull depth")
    resolved = shape if shape is not None else 1-values[2]/maximum if maximum is not None else values[1]/values[2]/100
    if not 0 <= resolved < 1:
        raise ValueError("shape_factor must be in [0, 1)")
    if full_curve and discharges is not None:
        raise ValueError("full_curve cannot be combined with discharges")
    q = None if discharges is None else tuple(_scalar(v, "discharges") for v in np.atleast_1d(discharges))
    return _hydraulics(engine_signature(), rscript_executable(), (*values, maximum, shape), bool(full_curve), q).copy(deep=True)


def channel_geometry(reach):
    parameters = dict(slope=reach["slope"], width=reach["bfWidth"], depth=reach["bfDepth"],
                      d84=reach["d84"], maximum=reach.get("maxDepth"), shape=None)
    result = _get_worker().request(dict(operation="geometry", parameters=parameters))
    return np.asarray(result["x"]), np.asarray(result["y"]), result["maximum"]


def _table(frame, columns):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("R GIFT expects a nonempty pandas DataFrame")
    values = frame.loc[:, columns].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("R GIFT input tables must contain finite numeric values")
    return {column: values[:, i].tolist() for i, column in enumerate(columns)}


@lru_cache(maxsize=96)
def _habitat(signature, executable, payload):
    return _frame(_get_worker().request(json.loads(payload)))


def habitat(hydraulics, depth_curve, velocity_curve, *, substrate_curve=None,
            gsd=None, substrate_size_mm=None):
    """Run original Habitat; a single-size GSD preserves the app's D84 proxy."""
    if gsd is not None and substrate_size_mm is not None:
        raise ValueError("Use either gsd or substrate_size_mm, not both")
    grains = (None if gsd is None else [_scalar(v, "gsd", positive=False) for v in gsd])
    if substrate_size_mm is not None:
        if substrate_curve is None:
            raise ValueError("substrate_size_mm requires substrate_curve")
        grains = [_scalar(substrate_size_mm, "substrate_size_mm", positive=False)]
    value = dict(operation="habitat", hydraulics=_table(hydraulics, list(HYDRAULIC_COLUMNS)),
                 depth_curve=_table(depth_curve, ["depth", "suit"]),
                 velocity_curve=_table(velocity_curve, ["velocity", "suit"]),
                 substrate_curve=None if substrate_curve is None else _table(substrate_curve, ["lower", "upper", "suit"]),
                 gsd=grains)
    payload = json.dumps(value, allow_nan=False, separators=(",", ":"))
    return _habitat(engine_signature(), rscript_executable(), payload).copy(deep=True)
