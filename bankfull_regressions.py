"""Alternative bankfull geometry for selected-segment plotting, in SI units.

Bieger et al. (2015), Table 3, uses drainage area in km2 and geometry in m.
Castro and Jackson (2001) uses the Western Cordillera drainage-area
relationship: width/depth in ft, drainage area in mi2, converted here to m.
The coefficients and units were verified against USGS NSS regression region
847 (GC1857), including its returned equations and estimates at 100 mi2.
"""
from __future__ import annotations

import math

CHAMP = "CHaMP Regression"
BIEGER_NATIONAL = "Bieger 2015 National"
BIEGER_REGIONAL = "Bieger 2015 Regional"
CASTRO_JACKSON = "Castro and Jackson 2001"
CUSTOM = "Custom value"
BANKFULL_OPTIONS = (CHAMP, BIEGER_NATIONAL, BIEGER_REGIONAL, CASTRO_JACKSON, CUSTOM)
NETWORK_BANKFULL_OPTIONS = BANKFULL_OPTIONS[:-1]

BIEGER_REGIONS = {
    "LUP": ("Laurentian Upland", (4.15, .308), (.31, .202), (43., 948.)),
    "APL": ("Atlantic Plain", (2.22, .363), (.24, .323), (.8, 2815.)),
    "AHI": ("Appalachian Highlands", (3.12, .415), (.26, .287), (.2, 2435.)),
    "IPL": ("Interior Plains", (2.56, .351), (.38, .191), (.5, 155213.)),
    "IHI": ("Interior Highlands", (23.23, .121), (.27, .267), (78., 2484.)),
    "RMS": ("Rocky Mountain System", (1.24, .435), (.23, .225), (.4, 25201.)),
    "IMP": ("Intermontane Plateau", (1.11, .415), (.07, .329), (9.4, 19632.)),
    "PMS": ("Pacific Mountain System", (2.76, .399), (.23, .294), (16., 20927.)),
}
NATIONAL_COEFFICIENTS = {"width": (2.70, .352), "depth": (.30, .213)}
# Western Cordillera coefficients as implemented by USGS StreamStats.
CASTRO_COEFFICIENTS = {"width": (9.40, .42), "depth": (.61, .33)}
CASTRO_REGION = "Western Cordillera"
CASTRO_RANGE_MI2 = (19.6, 5090.)
KM2_PER_MI2 = 2.589988110336
METRES_PER_FOOT = .3048


def positive_value(value, label):
    if value is None:
        raise ValueError(f"{label} is unavailable. Select another regression or enter a custom value.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a positive finite number.") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} must be a positive finite number.")
    return number


def predict_bankfull(drainage_area_km2, dimension, source, region="IMP"):
    """Predict one dimension without coupling the width and depth selections."""
    area = positive_value(drainage_area_km2, "Drainage area")
    if dimension not in ("width", "depth"):
        raise ValueError("dimension must be width or depth")
    if source == BIEGER_NATIONAL:
        a, exponent = NATIONAL_COEFFICIENTS[dimension]
        return a * area**exponent
    if source == BIEGER_REGIONAL:
        if region not in BIEGER_REGIONS:
            raise ValueError(f"Unknown Bieger region: {region}")
        entry = BIEGER_REGIONS[region]
        a, exponent = entry[1 if dimension == "width" else 2]
        return a * area**exponent
    if source == CASTRO_JACKSON:
        a, exponent = CASTRO_COEFFICIENTS[dimension]
        return METRES_PER_FOOT * a * (area / KM2_PER_MI2)**exponent
    raise ValueError(f"Unknown bankfull regression: {source}")


def default_plot_settings(reach):
    return dict(width_source=CHAMP, depth_source=CHAMP, custom_width=reach.get("bfWidth"),
                custom_depth=reach.get("bfDepth"), slope=reach.get("slope"))


def plot_reach(reach, choices, region="IMP"):
    """Resolve plot inputs without mutating source geometry or network scores.

    Only an entirely CHaMP geometry uses the supplied maximum depth. Any
    alternative or custom width/depth omits maximum depth so GIFT estimates
    its shape factor from width / mean depth / 100. Slope alone preserves the
    original geometry and maximum-depth input.
    """
    width_source, depth_source = choices["width_source"], choices["depth_source"]
    if any(s not in BANKFULL_OPTIONS for s in (width_source, depth_source)):
        raise ValueError("Choose a supported bankfull regression or custom value.")
    def dimension_value(kind, source):
        original = "bfWidth" if kind == "width" else "bfDepth"
        value = (reach.get(original) if source == CHAMP else choices.get("custom_"+kind)
                 if source == CUSTOM else predict_bankfull(reach.get("da"), kind, source, region))
        return positive_value(value, "Bankfull "+kind)
    width = dimension_value("width", width_source)
    depth = dimension_value("depth", depth_source)
    slope = positive_value(choices.get("slope"), "Channel slope")
    original_geometry = width_source == depth_source == CHAMP
    maximum = reach.get("maxDepth") if original_geometry else None
    # Preserve missing-input diagnostics for original CHaMP geometry.
    if original_geometry:
        maximum = positive_value(maximum, "Maximum bankfull depth")
        if maximum < depth:
            raise ValueError("CHaMP maximum bankfull depth is below its mean bankfull depth.")
    shape = 1-depth/maximum if maximum is not None else width/depth/100.
    if not 0 <= shape < 1:
        raise ValueError("GIFT's width/depth shape factor must be below 1. "
                         "For these plot inputs, increase mean depth or reduce bankfull width.")
    return dict(reach, bfWidth=width, bfDepth=depth, maxDepth=maximum, slope=slope,
                shapeRaw=None, shapeUsed=None, qMin=None, qMax=None, nativeCount=0,
                modelStatus="Plot inputs selected", plotInputs=dict(
                    width_source=width_source, depth_source=depth_source, bieger_region=region,
                    castro_jackson_region=CASTRO_REGION, maximum_depth_supplied=maximum is not None,
                    original_slope=reach.get("slope")))


def regression_notes(reach, choices, region="IMP"):
    """Describe the equations actually selected, including their units."""
    notes = []
    for kind in ("width", "depth"):
        source = choices[kind+"_source"]
        if source == BIEGER_NATIONAL:
            a, b = NATIONAL_COEFFICIENTS[kind]
            notes.append(f"{kind.capitalize()}: Bieger 2015 National = {a:g} × DA^{b:g}; m and km².")
        elif source == BIEGER_REGIONAL:
            name, width, depth, limits = BIEGER_REGIONS[region]
            a, b = width if kind == "width" else depth
            notes.append(f"{kind.capitalize()}: Bieger 2015 Regional ({name}) = {a:g} × DA^{b:g}; m and km².")
            area = reach.get("da")
            if area is not None and math.isfinite(area) and not limits[0] <= area <= limits[1]:
                notes.append(f"The regional estimate is outside the published drainage-area range "
                             f"of {limits[0]:g}–{limits[1]:g} km².")
        elif source == CASTRO_JACKSON:
            a, b = CASTRO_COEFFICIENTS[kind]
            notes.append(f"{kind.capitalize()}: Castro and Jackson (2001), {CASTRO_REGION} = "
                         f"{a:g} × DA^{b:g}; ft and mi², converted to m.")
            area = reach.get("da")
            if area is not None and math.isfinite(area) and not CASTRO_RANGE_MI2[0] <= area/KM2_PER_MI2 <= CASTRO_RANGE_MI2[1]:
                notes.append("The Castro and Jackson estimate is outside the StreamStats drainage-area "
                             "range of 19.6–5,090 mi².")
    return list(dict.fromkeys(notes))


def plot_export(frame, reach):
    """Record the active scenario in selected-segment CSV downloads."""
    result = frame.copy()
    from r_engine import source_provenance
    result["calculation_engine"] = "Original R GIFT"
    result["gift_source_revision"] = source_provenance()["revision"]
    provenance = reach.get("plotInputs", {})
    for column, value in {
        "plot_bankfull_width_source": provenance.get("width_source", CHAMP),
        "plot_bankfull_depth_source": provenance.get("depth_source", CHAMP),
        "plot_bankfull_width_m": reach.get("bfWidth"),
        "plot_mean_bankfull_depth_m": reach.get("bfDepth"),
        "plot_max_bankfull_depth_m": reach.get("maxDepth"),
        "plot_max_bankfull_depth_source": provenance.get("maximum_depth_source", ""),
        "plot_slope_m_per_m": reach.get("slope"),
        "plot_D84_mm": reach.get("d84"),
        "plot_shape_factor": reach.get("shapeUsed"),
        "plot_bieger_region": provenance.get("bieger_region", ""),
        "plot_castro_jackson_region": provenance.get("castro_jackson_region", ""),
    }.items():
        result[column] = value
    return result


def network_reach(reach, width_source, depth_source, region="IMP"):
    """Resolve network geometry, keeping invalid reaches explicitly unavailable."""
    if width_source not in NETWORK_BANKFULL_OPTIONS or depth_source not in NETWORK_BANKFULL_OPTIONS:
        raise ValueError("Network geometry requires a bankfull regression.")
    resolved = dict(reach, networkInputs=dict(width_source=width_source, depth_source=depth_source,
                                             bieger_region=region))
    errors = []
    for kind, field, source in (("width", "bfWidth", width_source), ("depth", "bfDepth", depth_source)):
        try:
            resolved[field] = (reach.get(field) if source == CHAMP else
                               predict_bankfull(reach.get("da"), kind, source, region))
            positive_value(resolved[field], "Bankfull "+kind)
        except ValueError as exc:
            resolved[field] = None
            errors.append(str(exc))
    if width_source != CHAMP or depth_source != CHAMP:
        resolved["maxDepth"] = None
    if errors:
        resolved["scenarioError"] = " ".join(errors)
    return resolved


def default_overrides(reach):
    """Track edits internally; unedited text boxes follow active network inputs."""
    return {**{f"override_{kind}": False for kind in ("width", "depth", "slope", "d84")},
            **{f"custom_{kind}": reach.get(field) for kind, field in (
                ("width", "bfWidth"), ("depth", "bfDepth"), ("slope", "slope"), ("d84", "d84"))},
            "custom_max_depth": None}


def apply_plot_overrides(reach, choices):
    """Copy the network scenario and apply only this segment's explicit overrides."""
    adjusted = dict(reach)
    sources = reach.get("networkInputs", dict(width_source=CHAMP, depth_source=CHAMP,
                                              bieger_region="IMP"))
    active = []
    for kind, field in (("width", "bfWidth"), ("depth", "bfDepth"), ("slope", "slope"), ("d84", "d84")):
        if choices.get("override_"+kind, False):
            adjusted[field] = positive_value(choices.get("custom_"+kind), "Plot "+kind)
            active.append(kind)
        else:
            positive_value(adjusted.get(field), "Network "+kind)
    if "width" in active or "depth" in active:
        adjusted["maxDepth"] = None
    if choices.get("custom_max_depth") is not None:
        adjusted["maxDepth"] = positive_value(choices["custom_max_depth"], "Plot maximum bankfull depth")
        active.append("maximum_depth")
    maximum = adjusted.get("maxDepth")
    original_geometry = sources["width_source"] == sources["depth_source"] == CHAMP and not (
        "width" in active or "depth" in active)
    if original_geometry:
        maximum = positive_value(maximum, "Maximum bankfull depth")
    if maximum is not None and maximum < adjusted["bfDepth"]:
        raise ValueError("Maximum bankfull depth is below mean bankfull depth.")
    shape = (1-adjusted["bfDepth"]/maximum if maximum is not None else
             adjusted["bfWidth"]/adjusted["bfDepth"]/100.)
    if not 0 <= shape < 1:
        raise ValueError("GIFT's width/depth shape factor must be below 1. "
                         "Increase mean depth or reduce bankfull width for this plot.")
    adjusted.update(plotInputs=dict(
        width_source=CUSTOM if "width" in active else sources["width_source"],
        depth_source=CUSTOM if "depth" in active else sources["depth_source"],
        bieger_region=sources.get("bieger_region", "IMP"), castro_jackson_region=CASTRO_REGION,
        maximum_depth_supplied=maximum is not None, custom_fields=active,
        maximum_depth_source=(CUSTOM if "maximum_depth" in active else
                              "CHaMP Regression" if maximum is not None else "GIFT inferred"),
        original_slope=reach.get("slope"), original_d84=reach.get("d84")))
    return adjusted
