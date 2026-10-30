"""Python equivalents of the web app's monthly and seasonal habitat results.

These functions use the supplied GIFT implementation, so they can also be used
for scientific exports without running the web app or JavaScript.
"""

from pathlib import Path
import math
from statistics import median
import sys
import warnings

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
from gift_habitat import habitat


def month_range(start, end):
    """Inclusive 1-based calendar months, including ranges across December."""
    if not all(isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 12 for m in (start, end)):
        raise ValueError("Calendar months must be integers from 1 to 12")
    selected = [start]
    while selected[-1] != end:
        selected.append(selected[-1] % 12 + 1)
    return selected


def finite_median(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    return float(median(values)) if values else None


def evaluate_period(reach, curves, selected_months, require_all=True):
    """Calculate every exact monthly habitat result before taking medians.

    selected_months uses calendar months 1–12. JSON network arrays use 0-based
    positions internally. Unsupported months remain null and carry a reason.
    """
    if (not selected_months or len(set(selected_months)) != len(selected_months)
            or any(not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= 12 for m in selected_months)):
        raise ValueError("Select one or more unique calendar months")
    classes = [c for c in curves["substrate"] if reach["d84"] is not None and c[0] <= reach["d84"] < c[1]]
    substrate = float(classes[0][2]) if len(classes) == 1 else None
    rows = []
    for month in selected_months:
        q, depth, velocity, width, status = reach["months"][month - 1]
        row = dict(month=month, q=q, depth=depth, velocity=velocity, width=width,
                   d=None, v=None, s=substrate, hsi=None, wua=None, normalized=None, status=status)
        if status == "zero_flow":
            row.update(depth=0.0, velocity=0.0, width=0.0, hsi=0.0, wua=0.0, normalized=0.0)
        rows.append(row)
    valid_hydraulics = [r for r in rows if r["status"] == "ok"]
    if valid_hydraulics:
        hyd = pd.DataFrame([dict(Q=r["q"], di=r["depth"], Ui=r["velocity"], Wi=r["width"],
                                 Ai=r["depth"] * r["width"]) for r in valid_hydraulics])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Component scores do not depend on substrate. Compute components
            # even when custom substrate classes leave this reach unclassified.
            result = habitat(hyd, pd.DataFrame(curves["depth"], columns=["depth", "suit"]),
                             pd.DataFrame(curves["velocity"], columns=["velocity", "suit"]))
        for row, (_, evaluated) in zip(valid_hydraulics, result.iterrows()):
            row.update(d=float(evaluated["d.suit"]), v=float(evaluated["v.suit"]))
            if substrate is None:
                row["status"] = "substrate_unclassified"
            else:
                hsi = row["d"] * row["v"] * substrate
                wua = hsi * row["width"]
                width = reach.get("summerWidth")
                row.update(hsi=hsi, wua=wua, normalized=wua / width if width and width > 0 else None)
    valid = [r for r in rows if r["hsi"] is not None and math.isfinite(r["hsi"])]
    complete = len(valid) == len(rows)
    usable = bool(valid) and (complete or not require_all)
    status = (rows[0]["status"] if len(rows) == 1 else "ok" if complete else "no_valid_months" if not valid
              else "incomplete_months" if require_all else "partial_months")
    q_values = [r["q"] for r in rows if r["q"] is not None and math.isfinite(r["q"])]
    summary = dict(id=reach["id"], selectedCount=len(rows), validCount=len(valid), complete=complete,
                   requireAll=require_all, status=status, q=finite_median(q_values),
                   qMin=min(q_values) if q_values else None, qMax=max(q_values) if q_values else None, s=substrate)
    for field in ("depth", "velocity", "width", "d", "v", "hsi", "wua", "normalized"):
        summary[field] = finite_median([r[field] for r in valid]) if usable else None
    return {**summary, "rows": rows}
