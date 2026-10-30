"""Prepare geometry and hydraulics using the explicit configuration in settings.py."""

from pathlib import Path
import json
import math
import shutil
import sqlite3
import sys
import time
import warnings

import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely import from_wkb, transform

ROOT = Path(__file__).resolve().parents[1]
MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
GROUPS = [
    ("chinook_juvenile_rearing", "Chinook · Juvenile rearing", 7),
    ("chinook_spawning_stream_river", "Chinook - Spawning", 8),
    ("steelhead_juvenile_rearing", "Steelhead · Juvenile rearing", 7),
    ("steelhead_spawning", "Steelhead · Spawning", 3),
    ("salmonid_general_sensitivity", "General salmonid", 7),
]

sys.path.insert(0, str(ROOT / "model"))
from r_engine import avg_hydraulics, habitat, source_provenance


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def read_layer(path, layer, id_field, required_fields):
    """Read only the requested source; never create a missing SQLite database."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {path}")
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
        columns = set(frame.columns)
        rows = frame.astype(object).where(pd.notna(frame), None).to_dict("records")
        spatial = None
    else:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
            con.row_factory = sqlite3.Row
            columns = {r[1] for r in con.execute(f"PRAGMA table_info({quote(layer)})")}
            if not columns:
                raise ValueError(f"Layer {layer!r} was not found in {path}")
            rows = [dict(r) for r in con.execute(f"SELECT * FROM {quote(layer)}")]
            spatial = con.execute(
                "SELECT column_name, srs_id FROM gpkg_geometry_columns WHERE table_name=?", (layer,)
            ).fetchone()
    missing = sorted(set([id_field, *required_fields]) - columns)
    if missing:
        raise ValueError(f"Missing configured fields in {path} / {layer}: {', '.join(missing)}. "
                         "Check the field mappings in settings.py.")
    for row in rows:
        value = clean_number(row[id_field])
        if value is None or not value.is_integer():
            raise ValueError(f"Invalid integer ID {row[id_field]!r} in {path} / {id_field}")
        row[id_field] = int(value)
    ids = [r[id_field] for r in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {id_field} in {path}/{layer}; join would be ambiguous")
    return rows, tuple(spatial) if spatial else None


def clean_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def unit_factors(units):
    conversions = {
        "bankfull_width": {"m": 1.0, "ft": 0.3048},
        "bankfull_depth": {"m": 1.0, "ft": 0.3048},
        "max_depth": {"m": 1.0, "ft": 0.3048},
        "summer_width": {"m": 1.0, "ft": 0.3048},
        "slope": {"m/m": 1.0, "ft/ft": 1.0, "percent": 0.01},
        "d84": {"mm": 1.0, "m": 1000.0, "in": 25.4},
        "drainage_area": {"km2": 1.0, "mi2": 2.589988110336},
        "monthly_flow": {"m3/s": 1.0, "cfs": 1 / 35.31466672148859},
    }
    for key, options in conversions.items():
        if units.get(key) not in options:
            raise ValueError(f"FIELD_UNITS[{key!r}] must be one of {list(options)}")
    return {key: options[units[key]] for key, options in conversions.items()}


def decode_geometry(blob):
    if blob[:2] != b"GP":
        raise ValueError("Expected GeoPackage binary geometry")
    envelope = (blob[3] >> 1) & 7
    offset = 8 + {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[envelope]
    return from_wkb(blob[offset:])


def json_write(path, value):
    path.write_text(json.dumps(value, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def prepare_network(*, network_path, network_layer, max_depth_path, max_depth_layer,
                    priority_path, priority_layer, curves_directory, output_directory,
                    network_fields, max_depth_id_field, max_depth_field, priority_id_field,
                    priority_fields, temperature_source, temperature_field,
                    monthly_flow_fields, field_units, simplify_meters,
                    default_periods=None, reference_path=None,
                    calculate_hydraulics=True, baseline_network=None):
    """Build an offline snapshot, or read fresh inputs without hydraulic solves.

    With calculate_hydraulics=False and output_directory=None, return an
    in-memory dataset without creating files. Reuse monthly hydraulics only
    where the verified baseline has identical model inputs and monthly flow.
    """
    NETWORK_PATH, NETWORK_LAYER = Path(network_path), network_layer
    MAX_DEPTH_PATH, MAX_DEPTH_LAYER = Path(max_depth_path), max_depth_layer
    PRIORITY_PATH, PRIORITY_LAYER = Path(priority_path), priority_layer
    CURVES_DIRECTORY = Path(curves_directory)
    OUTPUT_DIRECTORY = Path(output_directory) if output_directory is not None else None
    start = time.perf_counter()
    native_dir = curve_dir = None
    if OUTPUT_DIRECTORY is not None:
        OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)
        native_dir = OUTPUT_DIRECTORY / "hydraulics"
        if calculate_hydraulics:
            native_dir.mkdir(exist_ok=True)
        curve_dir = OUTPUT_DIRECTORY / "curves"
        curve_dir.mkdir(exist_ok=True)
    required = ("id", "bankfull_width", "bankfull_depth", "slope", "d84")
    if any(not network_fields.get(key) for key in required) or not max_depth_field:
        raise ValueError("ID, bankfull width, mean/maximum depth, slope, and D84 fields are required")
    if set(monthly_flow_fields) != set(range(1, 13)) or any(not v for v in monthly_flow_fields.values()):
        raise ValueError("MONTHLY_FLOW_FIELDS must map every month from 1 through 12 to a column")
    if temperature_source not in (None, "network", "priority"):
        raise ValueError("TEMPERATURE_SOURCE must be 'network', 'priority', or None")
    if temperature_source is not None and not temperature_field:
        raise ValueError("Set TEMPERATURE_FIELD, or set TEMPERATURE_SOURCE to None")
    factors = unit_factors(field_units)
    network_columns = [v for v in network_fields.values() if v] + list(monthly_flow_fields.values())
    priority_columns = [v for v in priority_fields.values() if v]
    if temperature_source == "network":
        network_columns.append(temperature_field)
    elif temperature_source == "priority":
        priority_columns.append(temperature_field)
    rows, spatial = read_layer(NETWORK_PATH, NETWORK_LAYER, network_fields["id"], network_columns)
    if not rows or spatial is None:
        raise ValueError(f"The streams layer must contain spatial features: {NETWORK_PATH} / {NETWORK_LAYER}")
    priorities, _ = read_layer(PRIORITY_PATH, PRIORITY_LAYER, priority_id_field, priority_columns)
    priorities = {r[priority_id_field]: r for r in priorities}
    max_depth_rows, _ = read_layer(MAX_DEPTH_PATH, MAX_DEPTH_LAYER, max_depth_id_field, [max_depth_field])
    max_depths = {r[max_depth_id_field]: r.get(max_depth_field) for r in max_depth_rows}
    geom_field, crs = spatial
    project = Transformer.from_crs(crs, 4326, always_xy=True).transform
    curves = []
    for key, label, month in GROUPS:
        period = (default_periods or {}).get(key, {"start": month + 1, "end": month + 1, "note": "Editable study assumption."})
        obj = {"id": key, "label": label, "defaultMonth": month,
               "defaultStart": period["start"] - 1, "defaultEnd": period["end"] - 1,
               "defaultMode": "single" if period["start"] == period["end"] else "range",
               "periodNote": period.get("note", ""), "periodSource": period.get("source", "")}
        for kind in ("depth", "velocity", "substrate"):
            path = CURVES_DIRECTORY / f"{key}_{kind}.csv"
            table = pd.read_csv(path)
            columns = ["lower", "upper", "suit"] if kind == "substrate" else [kind, "suit"]
            obj[kind] = table[columns].values.tolist()
            if curve_dir is not None and path.resolve() != (curve_dir / path.name).resolve():
                shutil.copy2(path, curve_dir / path.name)
        curves.append(obj)
    if OUTPUT_DIRECTORY is not None:
        json_write(OUTPUT_DIRECTORY / "curves.json", curves)
    features, fixtures, skipped, above_07, unmatched = [], [], [], [], []
    baseline = {r["id"]: r for r in (baseline_network or {}).get("features", [])}
    driver_fields = ("slope", "bfWidth", "bfDepth", "maxDepth", "d84")
    pending = []
    for i, row in enumerate(rows):
        comid = row[network_fields["id"]]
        def value(key):
            number = clean_number(row.get(network_fields.get(key)))
            return number * factors.get(key, 1.0) if number is not None else None
        maximum = clean_number(max_depths.get(comid))
        maximum = maximum * factors["max_depth"] if maximum is not None else None
        geometry = decode_geometry(row[geom_field]).simplify(simplify_meters, preserve_topology=True)
        geometry = transform(geometry, project, interleaved=False)
        lines = [geometry] if geometry.geom_type == "LineString" else list(geometry.geoms)
        coords = [[[round(x, 6), round(y, 6)] for x, y, *_ in line.coords] for line in lines]
        priority_row = priorities.get(comid)
        if priority_row is None:
            unmatched.append(comid)
        temperature_row = row if temperature_source == "network" else priority_row if temperature_source == "priority" else None
        feature = {
            "id": comid, "name": row.get(network_fields.get("name")) or "Unnamed stream", "coords": coords,
            "da": value("drainage_area"), "slope": value("slope"),
            "bfWidth": value("bankfull_width"), "bfDepth": value("bankfull_depth"),
            "maxDepth": maximum, "summerWidth": value("summer_width"), "d84": value("d84"),
            "temp": clean_number(temperature_row.get(temperature_field)) if temperature_row else None,
            "priority": clean_number(priority_row.get(priority_fields.get("score"))) if priority_row else None,
            "category": priority_row.get(priority_fields.get("category")) if priority_row else None,
            "extrapolated": any(row.get(network_fields.get(k)) for k in ("width_extrapolated", "depth_extrapolated")),
            "months": [], "modelStatus": "ok",
        }
        fields = [(network_fields[key], feature[target]) for key, target in
                  (("slope", "slope"), ("bankfull_width", "bfWidth"), ("bankfull_depth", "bfDepth"), ("d84", "d84"))]
        fields.append((max_depth_field, maximum))
        invalid = [field for field, number in fields if number is None or number <= 0]
        if not invalid and maximum < feature["bfDepth"]:
            raise ValueError(f"COMID {comid}: maximum bankfull depth is below mean bankfull depth")
        if invalid:
            feature["modelStatus"] = "Missing or invalid: " + ", ".join(invalid)
            skipped.append(comid)
            hyd = None
        elif calculate_hydraulics:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                hyd = avg_hydraulics(feature["slope"], feature["bfWidth"], feature["bfDepth"], feature["d84"],
                                     max_bankfull_depth=maximum,
                                     full_curve=True)
            feature.update({"shapeRaw": hyd.attrs["shape_factor_raw"], "shapeUsed": hyd.attrs["shape_factor"],
                            "qMin": float(hyd.Q.min()), "qMax": float(hyd.Q.max()), "nativeCount": len(hyd)})
            if hyd.attrs["shape_factor_above_0_7"]:
                above_07.append(comid)
            # Little-endian float64, columns Q, mean depth, mean velocity, width.
            if native_dir is not None:
                np.asarray(hyd[["Q", "di", "Ui", "Wi"]].values.T, dtype="<f8").tofile(native_dir / f"{comid}.bin")
        else:
            hyd = None
            shape = 1.0 - feature["bfDepth"] / maximum
            feature.update(shapeRaw=shape, shapeUsed=shape, qMin=None, qMax=None, nativeCount=None)
            if shape > .7:
                above_07.append(comid)
        previous = baseline.get(comid)
        reusable = (not calculate_hydraulics and not invalid and previous is not None
                    and previous.get("modelStatus") == "ok"
                    and all(previous.get(k) == feature[k] for k in driver_fields)
                    and len(previous.get("months", [])) == 12)
        if reusable:
            for key in ("shapeRaw", "shapeUsed", "qMin", "qMax", "nativeCount"):
                feature[key] = previous.get(key)
        for month in range(1, 13):
            q = clean_number(row[monthly_flow_fields[month]])
            q = q * factors["monthly_flow"] if q is not None else None
            status = ("missing_flow" if q is None or q < 0 else "no_hydraulics" if invalid
                      else "pending_selection" if hyd is None and q != 0
                      else "zero_flow" if q == 0 else "below_range" if q < feature["qMin"]
                      else "above_bankfull" if q > feature["qMax"] else "ok")
            values = [float(np.interp(q, hyd.Q, hyd[k])) for k in ("di", "Ui", "Wi")] if status == "ok" else [None] * 3
            if reusable and previous["months"][month - 1][0] == q:
                feature["months"].append(list(previous["months"][month - 1]))
            else:
                feature["months"].append([q, *values, status])
        if any(row[4] == "pending_selection" for row in feature["months"]):
            feature["modelStatus"] = "Awaiting segment selection"
            pending.append(comid)
        features.append(feature)
        if hyd is not None and len(fixtures) < 9:
            tests = []
            for curve in curves:
                dp = pd.DataFrame(curve["depth"], columns=["depth", "suit"])
                vp = pd.DataFrame(curve["velocity"], columns=["velocity", "suit"])
                sp = pd.DataFrame(curve["substrate"], columns=["lower", "upper", "suit"])
                monthly = []
                valid_months = [j for j, a in enumerate(feature["months"]) if a[4] == "ok"]
                if valid_months:
                    exact = pd.DataFrame([{"Q": feature["months"][j][0], "Ai": feature["months"][j][1]*feature["months"][j][3],
                                           "di": feature["months"][j][1], "Ui": feature["months"][j][2], "Wi": feature["months"][j][3]}
                                          for j in valid_months])
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        result = habitat(exact, dp, vp, substrate_curve=sp, substrate_size_mm=feature["d84"])
                    monthly = [{"month": j, "d": float(r["d.suit"]), "v": float(r["v.suit"]),
                                "s": float(r["s.suit"]), "wua": float(r.WUA)} for j, (_, r) in zip(valid_months, result.iterrows())]
                tests.append({"curve": curve["id"], "monthly": monthly})
            fixtures.append({"id": comid, "tests": tests})
        if calculate_hydraulics and (i+1) % 150 == 0:
            print(f"Prepared {i+1}/{len(rows)} reaches", flush=True)
    # Store relative source references for files shipped with the application.
    # External inputs remain absolute for unambiguous provenance.
    def source_display_path(path):
        path = Path(path).resolve()
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError:
            return str(path)

    metadata = {
        "calculationEngine": "Original R GIFT",
        "giftSourceRevision": source_provenance()["revision"],
        "base": NETWORK_PATH.name, "basePath": source_display_path(NETWORK_PATH), "baseLayer": NETWORK_LAYER,
        "prioritySource": PRIORITY_PATH.name, "priorityPath": source_display_path(PRIORITY_PATH),
        "priorityLayer": PRIORITY_LAYER, "join": network_fields["id"], "featureCount": len(features),
        "priorityMatches": len(features)-len(unmatched), "unmatchedPriority": unmatched,
        "skippedHydraulics": skipped, "shapeFactorCapped": [], "shapeFactorAbove07": above_07,
        "pendingHydraulics": pending,
        "shapeMethod": "1 - mean bankfull depth / maximum bankfull depth; no cap",
        "maxDepthSource": MAX_DEPTH_PATH.name, "maxDepthPath": source_display_path(MAX_DEPTH_PATH),
        "maxDepthLayer": MAX_DEPTH_LAYER, "maxDepthField": max_depth_field,
        "maxDepthMatches": sum(r[network_fields["id"]] in max_depths for r in rows),
        "networkFields": network_fields, "priorityFields": priority_fields,
        "maxDepthIdField": max_depth_id_field, "priorityIdField": priority_id_field,
        "temperatureSource": temperature_source, "temperatureField": temperature_field,
        "monthlyFlowFields": monthly_flow_fields, "sourceUnits": field_units,
        "flowReference": "1971–2000", "flowType": "NHDPlusV2 gage-adjusted long-term monthly mean, Q0001E",
        "flowInterpretation": "Includes represented alterations and gage corrections; diversions are incompletely accounted for.",
        "displaySimplificationMeters": simplify_meters,
        "defaultPeriods": default_periods or {},
        "periodSummary": "Median of habitat results calculated separately at each selected monthly EROM flow; not habitat at median flow.",
    }
    network = {"metadata": metadata, "features": features}
    if OUTPUT_DIRECTORY is None:
        return network, curves
    json_write(OUTPUT_DIRECTORY / "network.json", network)
    if reference_path is not None:
        json_write(Path(reference_path), fixtures)
    license_source = ROOT / "LICENSE.txt"
    if not license_source.exists():
        license_source = ROOT / "LICENSE"
    license_target = OUTPUT_DIRECTORY.parent / "LICENSE.txt"
    if license_source.resolve() != license_target.resolve():
        shutil.copy2(license_source, license_target)
    print(json.dumps({"segments": len(features), "invalid": skipped, "shapeFactorsAbove07": len(above_07), "capped": 0, "priorityMatches": metadata["priorityMatches"],
                      "initialDataMB": round((OUTPUT_DIRECTORY/'network.json').stat().st_size/1e6, 3),
                      "seconds": round(time.perf_counter()-start, 2)}), flush=True)
    return network, curves


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    from prepare_data import main
    main()
