"""Network regression scenarios and a uniformly sampled full-flow mean.

These functions solve and cache network regressions and full-flow means.
The app defaults to CHaMP width and Castro and Jackson mean depth; choosing
CHaMP for both dimensions can use the bundled monthly overview.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bankfull_regressions import CHAMP, network_reach
from r_engine import RBackendError
from habitat_engine import (calculate_hydraulics, hydraulic_parameters, native_habitat,
                            reach_with_hydraulics)

MEAN_FLOW_SAMPLES = 201


def build_network_solution(network, width_source=CHAMP, depth_source=CHAMP, region="IMP"):
    """Solve each network reach once; retain physical curves independently of biology."""
    features, hydraulics, skipped, above = [], {}, [], []
    for original in network["features"]:
        reach = network_reach(original, width_source, depth_source, region)
        try:
            if reach.get("scenarioError"):
                raise ValueError(reach["scenarioError"])
            if width_source == depth_source == CHAMP and reach.get("maxDepth") is None:
                raise ValueError("Maximum bankfull depth is unavailable for CHaMP Regression.")
            hyd = calculate_hydraulics(hydraulic_parameters(reach))
        except RBackendError:
            raise
        except (ValueError, RuntimeError) as exc:
            reach["scenarioError"] = str(exc)
            hyd = pd.DataFrame()
        evaluated = reach_with_hydraulics(reach, hyd)
        if hyd.empty:
            evaluated.update(modelStatus=reach.get("scenarioError", "Missing or invalid hydraulic inputs"),
                             shapeRaw=None, shapeUsed=None, qMin=None, qMax=None, nativeCount=0)
            skipped.append(reach["id"])
        else:
            # Store numerical columns only, avoiding thousands of cached DataFrames.
            hydraulics[reach["id"]] = hyd[["Q", "Ai", "Wi", "di", "Ui"]].to_numpy()
            if evaluated["shapeUsed"] > .7:
                above.append(reach["id"])
        features.append(evaluated)
    metadata = dict(network["metadata"], skippedHydraulics=skipped, shapeFactorAbove07=above,
                    pendingHydraulics=[], bankfullWidthSource=width_source,
                    bankfullDepthSource=depth_source, biegerRegion=region)
    return dict(network, features=features, metadata=metadata), hydraulics


def full_curve_mean(reach, physical, curve, samples=MEAN_FLOW_SAMPLES):
    """Normalized area under the response curve, using equally spaced discharges.

    This is a discharge-range mean, not a time mean or a flow-duration-weighted
    estimate. No extrapolation to zero or above bankfull is performed.
    """
    if type(samples) is not int or samples < 3:
        raise ValueError("Use at least three flow samples.")
    result = dict(id=reach["id"], q=np.nan, qMin=np.nan, qMax=np.nan, s=np.nan,
                  selectedCount=samples, validCount=0, complete=False, requireAll=True,
                  status="no_hydraulics", validMonths="", aggregation="full_curve_mean")
    fields = ("depth", "velocity", "width", "d", "v", "hsi", "wua", "normalized")
    result.update({field: np.nan for field in fields})
    if physical is None or len(physical) < 2:
        return result
    q = np.linspace(float(physical[0, 0]), float(physical[-1, 0]), samples)
    if not np.isfinite(q).all() or q[-1] <= q[0]:
        return result
    hyd = pd.DataFrame({"Q": q})
    for index, field in enumerate(("Ai", "Wi", "di", "Ui"), 1):
        hyd[field] = np.interp(q, physical[:, 0], physical[:, index])
    response = native_habitat(None, reach, curve, hydraulics=hyd)
    result.update(q=float(np.mean(q)), qMin=float(q[0]), qMax=float(q[-1]),
                  s=float(response.s.iloc[0]), validCount=int(response.hsi.notna().sum()))
    result["complete"] = result["validCount"] == samples
    result["status"] = "ok" if result["complete"] else "substrate_unclassified"
    for field in fields:
        values = response[field].to_numpy()
        if np.isfinite(values).all():
            result[field] = float(np.trapezoid(values, q)/(q[-1]-q[0]))
    return result


def full_curve_summary(network, hydraulics, curve, samples=MEAN_FLOW_SAMPLES):
    rows = [full_curve_mean(reach, hydraulics.get(reach["id"]), curve, samples)
            for reach in network["features"]]
    return pd.DataFrame(rows).set_index("id")
