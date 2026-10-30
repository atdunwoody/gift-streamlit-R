"""R GIFT calculations and Python summaries shared by Streamlit and exports.

The original GIFT habitat() function evaluates the depth/velocity distributions.
The prepared monthly hydraulics are independent of the selected habitat curves.
"""
from __future__ import annotations

import calendar
import io
import json
from pathlib import Path
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "model"))
from r_engine import RBackendError, avg_hydraulics, habitat, source_provenance
from tools.habitat_results import month_range

MONTHS = tuple(calendar.month_name[1:])
MONTH_ABBR = tuple(calendar.month_abbr[1:])
CFS_PER_M3S = 35.31466672148859
STATUS_LABELS = {
    "ok": "Within modeled range",
    "zero_flow": "Zero flow; HSI and WUA are zero",
    "missing_flow": "Monthly EROM flow unavailable",
    "no_hydraulics": "Hydraulic predictions unavailable",
    "pending_selection": "Select this segment to calculate habitat",
    "below_range": "Flow below modeled minimum",
    "above_bankfull": "Flow exceeds modeled bankfull discharge",
    "substrate_unclassified": "D84 outside supplied substrate classes",
    "incomplete_months": "Withheld: incomplete selected period",
    "partial_months": "Partial period: available months only",
    "no_valid_months": "No supported monthly habitat results",
}


def load_dataset(directory: Path):
    network = json.loads((directory / "network.json").read_text(encoding="utf-8"))
    curves = json.loads((directory / "curves.json").read_text(encoding="utf-8"))
    ids = [r["id"] for r in network["features"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate COMIDs in prepared network")
    if any(len(r["months"]) != 12 for r in network["features"]):
        raise ValueError("Every segment must have 12 monthly records")
    for curve in curves:
        validate_curves(curve)
    return network, curves


def validate_curves(curve: dict) -> dict:
    """Reject ambiguous coordinates/classes; preserve nearest-coordinate lookup."""
    result = dict(curve)
    for kind in ("depth", "velocity", "substrate"):
        columns = ["lower", "upper", "suit"] if kind == "substrate" else [kind, "suit"]
        a = np.asarray(curve[kind], dtype=float)
        if a.ndim != 2 or a.shape[1] != len(columns) or len(a) == 0:
            raise ValueError(f"{kind}: expected a nonempty table with columns {', '.join(columns)}")
        if len(a) > 10000:
            raise ValueError(f"{kind}: maximum 10,000 rows per curve")
        if not np.isfinite(a).all() or (a[:, :-1] < 0).any():
            raise ValueError(f"{kind}: coordinates must be finite and nonnegative")
        if ((a[:, -1] < 0) | (a[:, -1] > 1)).any():
            raise ValueError(f"{kind}: suitability must be between 0 and 1")
        a = a[np.argsort(a[:, 0], kind="stable")]
        if kind == "substrate":
            if (a[:, 1] <= a[:, 0]).any():
                raise ValueError("substrate: every upper bound must exceed its lower bound")
            if (a[1:, 0] < a[:-1, 1]).any():
                raise ValueError("substrate: classes must not overlap")
        elif len(a) < 2 or (np.diff(a[:, 0]) <= 0).any():
            raise ValueError(f"{kind}: provide at least two distinct coordinates; duplicates are not allowed")
        result[kind] = a.tolist()
    return result


def curve_from_csvs(depth: bytes, velocity: bytes, substrate: bytes, label="Custom curves", defaults=None):
    curve = {"id": "custom", "label": label.strip() or "Custom curves"}
    if defaults:
        for key in ("defaultStart", "defaultEnd", "defaultMode", "defaultMonth", "periodNote", "periodSource"):
            curve[key] = defaults.get(key)
    for kind, raw in (("depth", depth), ("velocity", velocity), ("substrate", substrate)):
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError(f"{kind}: file exceeds 5 MB")
        table = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
        table.columns = [str(c).strip().lower() for c in table.columns]
        if table.columns.duplicated().any():
            raise ValueError(f"{kind}: duplicate column names")
        columns = ["lower", "upper", "suit"] if kind == "substrate" else [kind, "suit"]
        if not set(columns).issubset(table.columns):
            raise ValueError(f"{kind}: required columns are {', '.join(columns)}")
        curve[kind] = table[columns].apply(pd.to_numeric, errors="raise").values.tolist()
    return validate_curves(curve)


def substrate_score(d84, curve):
    if d84 is None or not np.isfinite(d84):
        return np.nan
    matches = [s for low, high, s in curve["substrate"] if low <= d84 < high]
    return float(matches[0]) if len(matches) == 1 else np.nan


def compute_monthly(features, curve):
    """Evaluate all exact monthly flows in one batch through original GIFT."""
    curve = validate_curves(curve)
    records = []
    for reach in features:
        s = substrate_score(reach["d84"], curve)
        for month, (q, depth, velocity, width, status) in enumerate(reach["months"], 1):
            records.append(dict(id=reach["id"], month=month, q=q, depth=depth,
                                velocity=velocity, width=width, s=s, d=np.nan, v=np.nan,
                                hsi=np.nan, wua=np.nan, normalized=np.nan,
                                summer_width=reach.get("summerWidth"), status=status))
    frame = pd.DataFrame(records)
    dry = frame.status.eq("zero_flow")
    frame.loc[dry, ["depth", "velocity", "width", "hsi", "wua", "normalized"]] = 0.0
    good = frame.status.eq("ok")
    if good.any():
        hyd = frame.loc[good, ["q", "depth", "velocity", "width"]].rename(
            columns={"q": "Q", "depth": "di", "velocity": "Ui", "width": "Wi"})
        hyd["Ai"] = hyd.di * hyd.Wi
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            result = habitat(hyd, pd.DataFrame(curve["depth"], columns=["depth", "suit"]),
                             pd.DataFrame(curve["velocity"], columns=["velocity", "suit"]))
        frame.loc[good, "d"] = result["d.suit"].to_numpy()
        frame.loc[good, "v"] = result["v.suit"].to_numpy()
        frame.loc[good, "hsi"] = frame.loc[good, "d"] * frame.loc[good, "v"] * frame.loc[good, "s"]
        frame.loc[good, "wua"] = frame.loc[good, "hsi"] * frame.loc[good, "width"]
        normalizable = good & frame.summer_width.gt(0)
        if normalizable.any():
            frame.loc[normalizable, "normalized"] = frame.loc[normalizable, "wua"] / frame.loc[normalizable, "summer_width"]
        frame.loc[good & frame.s.isna(), "status"] = "substrate_unclassified"
    return frame


def aggregate_period(monthly: pd.DataFrame, months, require_all=True):
    """Median of monthly habitat, with an explicit complete-period policy."""
    if not months or len(set(months)) != len(months) or any(type(m) is not int or not 1 <= m <= 12 for m in months):
        raise ValueError("Select unique calendar months from 1 through 12")
    rows = monthly[monthly.month.isin(months)].copy()
    groups = rows.groupby("id", sort=False)
    summary = groups.q.agg(q="median", qMin="min", qMax="max")
    valid = rows[rows.hsi.notna()]
    fields = ["depth", "velocity", "width", "d", "v", "hsi", "wua", "normalized"]
    summary = summary.join(valid.groupby("id", sort=False)[fields].median())
    summary["s"] = groups.s.first()
    summary["validCount"] = groups.hsi.count().astype(int)
    summary["selectedCount"] = len(months)
    summary["complete"] = summary.validCount.eq(len(months))
    summary["requireAll"] = bool(require_all)
    summary["status"] = "ok"
    summary.loc[~summary.complete, "status"] = "incomplete_months" if require_all else "partial_months"
    summary.loc[summary.validCount.eq(0), "status"] = "no_valid_months"
    if len(months) == 1:
        summary["status"] = groups.status.first()
    if require_all:
        summary.loc[~summary.complete, fields] = np.nan
    # Index once instead of rescanning the entire network for each reach/month.
    # Iterate the selected months so ranges across December keep their order.
    valid_pairs = set(zip(valid.id, valid.month))
    summary["validMonths"] = [";".join(str(m) for m in months if (i, m) in valid_pairs)
                              for i in summary.index]
    return summary


HYDRAULIC_FIELDS = ("slope", "bfWidth", "bfDepth", "maxDepth", "d84")


def hydraulic_parameters(reach):
    return tuple(reach.get(key) for key in HYDRAULIC_FIELDS)


def calculate_hydraulics(parameters):
    """Solve one reach only. The caller owns the bounded Streamlit cache."""
    slope, width, depth, maximum, d84 = parameters
    if any(value is None or not np.isfinite(value) or value <= 0
           for value in (slope, width, depth, d84)):
        return pd.DataFrame()
    if maximum is not None and (not np.isfinite(maximum) or maximum <= 0):
        return pd.DataFrame()
    if maximum is not None and maximum < depth:
        raise ValueError("Maximum bankfull depth is below mean bankfull depth")
    optional = {"max_bankfull_depth": maximum} if maximum is not None else {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return avg_hydraulics(slope, width, depth, d84,
                              full_curve=True, **optional)


def reach_with_hydraulics(reach, hyd):
    """Interpolate the clicked reach's twelve flows without changing its inputs."""
    result = dict(reach, months=[])
    supported = not hyd.empty
    if supported:
        result.update(modelStatus="ok", shapeRaw=hyd.attrs["shape_factor_raw"],
                      shapeUsed=hyd.attrs["shape_factor"], qMin=float(hyd.Q.min()),
                      qMax=float(hyd.Q.max()), nativeCount=len(hyd))
    for record in reach["months"]:
        q = record[0]
        status = ("missing_flow" if q is None or not np.isfinite(q) or q < 0
                  else "no_hydraulics" if not supported else "zero_flow" if q == 0
                  else "below_range" if q < result["qMin"]
                  else "above_bankfull" if q > result["qMax"] else "ok")
        values = [float(np.interp(q, hyd.Q, hyd[k])) for k in ("di", "Ui", "Wi")] if status == "ok" else [None] * 3
        result["months"].append([q, *values, status])
    return result


def native_habitat(directory: Path, reach, curve, *, hydraulics=None):
    """Calculate the selected reach's response; directory is retained for callers.

    No native hydraulic files are read. Supplying hydraulics reuses the cached
    physical solution across life stages, month ranges, and display units.
    """
    hyd = calculate_hydraulics(hydraulic_parameters(reach)) if hydraulics is None else hydraulics.copy()
    if hyd.empty:
        return pd.DataFrame()
    hyd["Ai"] = hyd.di * hyd.Wi
    evaluated = habitat(hyd, pd.DataFrame(curve["depth"], columns=["depth", "suit"]),
                        pd.DataFrame(curve["velocity"], columns=["velocity", "suit"]))
    s = substrate_score(reach["d84"], curve)
    output = pd.DataFrame(dict(q=hyd.Q, depth=hyd.di, velocity=hyd.Ui, width=hyd.Wi,
                               d=evaluated["d.suit"], v=evaluated["v.suit"], s=s))
    output["hsi"] = output.d * output.v * output.s
    output["wua"] = output.hsi * output.width
    summer_width = reach.get("summerWidth")
    output["normalized"] = output.wua / summer_width if summer_width and summer_width > 0 else np.nan
    return output


def input_source_rows(metadata):
    """Describe the actual sources and fields retained during preparation."""
    fields, units = metadata["networkFields"], metadata["sourceUnits"]
    sources = {
        "network": (metadata["basePath"], metadata["baseLayer"]),
        "max_depth": (metadata["maxDepthPath"], metadata["maxDepthLayer"]),
        "priority": (metadata["priorityPath"], metadata["priorityLayer"]),
    }
    rows = []
    def add(label, source, field, unit=""):
        path, layer = sources[source]
        rows.append({"Input": label, "Source file": path,
                     "Layer": "CSV" if Path(path).suffix.lower() == ".csv" else layer,
                     "Field": field or "Disabled", "Source units": unit})
    for key, label in (("id", "Stream ID"), ("name", "Stream name"),
                       ("bankfull_width", "Bankfull width"), ("bankfull_depth", "Mean bankfull depth"),
                       ("slope", "Channel slope"), ("d84", "Substrate D84"),
                       ("drainage_area", "Drainage area"), ("summer_width", "Summer wetted width"),
                       ("width_extrapolated", "Width extrapolation flag"),
                       ("depth_extrapolated", "Depth extrapolation flag")):
        add(label, "network", fields.get(key), units.get(key, ""))
    add("Maximum bankfull depth", "max_depth", metadata["maxDepthField"], units["max_depth"])
    add("Maximum-depth join ID", "max_depth", metadata["maxDepthIdField"])
    add("Priority score", "priority", metadata["priorityFields"].get("score"))
    add("Priority category", "priority", metadata["priorityFields"].get("category"))
    add("Priority join ID", "priority", metadata["priorityIdField"])
    if metadata.get("temperatureSource"):
        add("Temperature score", metadata["temperatureSource"], metadata["temperatureField"])
    for month, field in sorted(metadata["monthlyFlowFields"].items(), key=lambda item: int(item[0])):
        add(f"{MONTHS[int(month)-1]} flow", "network", field, units["monthly_flow"])
    return rows


def network_export(features, summary, curve_id, months, metadata=None):
    base = pd.DataFrame([dict(COMID=r["id"], GNIS_NAME=r["name"],
                             currTempScoreKSee=r["temp"], **{"High priority combined": r["priority"]},
                             bf_width_pred_m=r["bfWidth"], bf_depth_pred_m=r["bfDepth"],
                             joined_bf_depth_max_pred_m=r["maxDepth"], D84_pred_mm=r["d84"],
                             slope_m_per_m=r["slope"],
                             bankfull_width_source=r.get("networkInputs", {}).get("width_source", "CHaMP Regression"),
                             bankfull_depth_source=r.get("networkInputs", {}).get("depth_source", "CHaMP Regression"),
                             shape_factor_used=r.get("shapeUsed")) for r in features])
    merged = base.merge(summary.rename_axis("COMID").reset_index(), on="COMID", validate="one_to_one")
    merged["curve_set"] = curve_id
    merged["calculation_engine"] = "Original R GIFT"
    merged["gift_source_revision"] = source_provenance()["revision"]
    merged["selected_months"] = ";".join(map(str, months))
    metadata = metadata or {}
    merged["base_file"] = metadata.get("basePath", "")
    merged["base_layer"] = metadata.get("baseLayer", "")
    merged["max_depth_source"] = metadata.get("maxDepthPath", "")
    merged["max_depth_field"] = metadata.get("maxDepthField", "")
    for key, label in (("bankfull_width", "bankfull_width_field"), ("bankfull_depth", "bankfull_depth_field"),
                       ("slope", "slope_field"), ("d84", "d84_field")):
        merged[label] = metadata.get("networkFields", {}).get(key, "")
    full_mean = "aggregation" in summary and summary.aggregation.eq("full_curve_mean").all()
    merged["map_aggregation"] = "full_curve_mean" if full_mean else "selected_months_median"
    if full_mean:
        return merged.drop(columns=["normalized", "validMonths", "selected_months"]).rename(columns={
            "hsi": "HSI_full_curve_mean", "wua": "WUA_full_curve_mean_m2_per_m",
            "q": "modeled_Q_midpoint_m3s", "qMin": "modeled_Q_min_m3s", "qMax": "modeled_Q_max_m3s",
            "d": "depth_suitability_full_curve_mean", "v": "velocity_suitability_full_curve_mean",
            "s": "substrate_suitability", "depth": "depth_full_curve_mean_m",
            "velocity": "velocity_full_curve_mean_m_s", "width": "width_full_curve_mean_m",
            "validCount": "valid_flow_samples", "selectedCount": "flow_samples"})
    return merged.drop(columns=["normalized"]).rename(columns={"hsi": "HSI_median", "wua": "WUA_median_m2_per_m",
                                  "q": "EROM_Q_median_m3s",
                                  "qMin": "EROM_Q_min_m3s", "qMax": "EROM_Q_max_m3s",
                                  "d": "depth_suitability_median", "v": "velocity_suitability_median",
                                  "s": "substrate_suitability", "depth": "mean_depth_median_m",
                                  "velocity": "mean_velocity_median_m_s", "width": "wetted_width_median_m"})


def selected_comid(event, allowed_ids):
    """Extract a clicked PathLayer object; ignore unknown/stale object IDs."""
    selection = event.get("selection", {}) if hasattr(event, "get") else {}
    for objects in selection.get("objects", {}).values():
        for obj in objects:
            candidate = obj.get("comid", obj.get("properties", {}).get("comid"))
            try:
                candidate = int(candidate)
            except (TypeError, ValueError):
                continue
            if candidate in allowed_ids:
                return candidate
    return None


def selected_plotly_comid(event, allowed_ids):
    """Use the embedded COMID, rather than indices in a filtered scatter trace."""
    selection = event.get("selection", {}) if hasattr(event, "get") else {}
    if not hasattr(selection, "get"):
        return None
    points = selection.get("points", [])
    if not isinstance(points, (list, tuple)):
        return None
    for point in reversed(points):
        if not hasattr(point, "get"):
            continue
        custom = point.get("customdata")
        if not isinstance(custom, (list, tuple)) or len(custom) < 2:
            continue
        candidate = custom[1]
        try:
            comid = int(candidate)
            if isinstance(candidate, bool) or float(candidate) != comid:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        if comid in allowed_ids:
            return comid
    return None


def gift_diagnostics(reach, monthly, summary, months, curve, metadata=None):
    """Return the prepared model inputs and exact monthly values in SI units."""
    fields = ("bfWidth", "bfDepth", "maxDepth", "slope", "d84", "shapeRaw", "shapeUsed",
              "qMin", "qMax", "nativeCount")
    rows = monthly.set_index("month").loc[list(months)].reset_index()
    rows = rows.drop(columns=["normalized", "summer_width"], errors="ignore")
    return {
        "COMID": reach["id"], "stream_name": reach["name"],
        "calculation_engine": "Original R GIFT",
        "gift_source_revision": source_provenance()["revision"],
        "model_status": reach["modelStatus"],
        "input_parameters": {field: reach.get(field) for field in fields},
        "plot_adjustments": reach.get("plotInputs"),
        "input_sources": input_source_rows(metadata) if metadata else [],
        "units": {"bfWidth": "m", "bfDepth": "m", "maxDepth": "m", "slope": "m/m",
                  "d84": "mm", "qMin": "m3/s", "qMax": "m3/s",
                  "monthly_q": "m3/s", "monthly_depth": "m", "monthly_velocity": "m/s",
                  "monthly_width": "m", "monthly_wua": "m2/m"},
        "selected_months": list(months), "require_all_months": bool(summary.requireAll),
        "period_status": summary.status,
        # Preserve float precision and encode unavailable numbers as JSON null.
        "monthly_inputs_and_results": rows.astype(object).where(rows.notna(), None).to_dict(orient="records"),
        "suitability_curves": curve,
    }
