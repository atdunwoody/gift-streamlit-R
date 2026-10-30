"""Edit input sources, fields, and units here, then launch or rerun the app.

The app refreshes changed inputs without building every hydraulic curve.
Changed reaches are calculated when selected. Run prepare_data.py directly in
VSCode to refresh the complete monthly overview before publishing input changes.
"""
from pathlib import Path

# Keep paths anchored to this file, not the current working directory.
# These work unchanged locally and on Streamlit Community Cloud.
ROOT = Path(__file__).resolve().parent
DATA_DIRECTORY = ROOT / "data"
NETWORK_PATH = ROOT / "inputs/UGR_channel_geometry_predictions_monthly_EROM.gpkg"
NETWORK_LAYER = "channel_geometry_predictions"
MAX_DEPTH_PATH = NETWORK_PATH  # Use the active streams file unless a separate source is needed.
MAX_DEPTH_LAYER = NETWORK_LAYER
PRIORITY_PATH = ROOT / "inputs/Provisional_Model_Layer.gpkg"  # CSV attribute tables are also supported.
PRIORITY_LAYER = "provisional_model_layer"
CURVES_DIRECTORY = ROOT / "inputs/curves"
APP_TITLE = "GIFT Habitat Explorer"
# Rscript is discovered on PATH and in standard Windows R installation folders.
# Set an absolute path here if needed, for example:
# RSCRIPT_PATH = r"C:\Program Files\R\R-4.5.2\bin\Rscript.exe"
# Environment variable GIFT_RSCRIPT takes precedence over this setting.
RSCRIPT_PATH = None
# Physiographic division for the selected-segment Bieger Regional option.
# IMP = Intermontane Plateau, covering the supplied Upper Grande Ronde network.
BIEGER_REGION = "IMP"

# Source columns. Required model fields must exist; null values remain unavailable.
# Optional display fields can be disabled with None. All join IDs must be integers.
NETWORK_FIELDS = {
    "id": "COMID",
    "name": "GNIS_NAME",
    "bankfull_width": "bf_width_pred_m",
    "bankfull_depth": "bf_depth_pred_m",  # Mean bankfull depth, not maximum depth.
    "slope": "SLOPE",
    "d84": "D84_pred",
    "drainage_area": "TotDASqKm",
    "summer_width": "late_summer_wetted_width_pred_m",
    "width_extrapolated": "bf_width_extrap",
    "depth_extrapolated": "bf_depth_extrap",
}
MAX_DEPTH_ID_FIELD = "COMID"
MAX_DEPTH_FIELD = "bf_depth_max_pred_m"
PRIORITY_ID_FIELD = "COMID"
PRIORITY_FIELDS = {"score": "High priority combined", "category": "FinalScore_cat"}
# The included streams file has no temperature column. Join scores from the UGR layer above.
# Set the source to "network" if TEMPERATURE_FIELD is in the streams GeoPackage.
TEMPERATURE_SOURCE = "priority"  # "network" or "priority"; None disables temperature.
TEMPERATURE_FIELD = "currTempScoreKSee"

# Map each calendar month (1=January) to its source discharge column.
# For columns such as erom_q01, replace the expression with f"erom_q{month:02d}".
MONTHLY_FLOW_FIELDS = {
    month: f"erom_q_{name}_m3s"
    for month, name in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)
}
# Units of the selected source fields. Converted to SI before GIFT calculations.
FIELD_UNITS = {
    "bankfull_width": "m",  # m or ft
    "bankfull_depth": "m",  # m or ft
    "max_depth": "m",       # m or ft
    "summer_width": "m",    # m or ft
    "slope": "m/m",         # m/m, ft/ft, or percent
    "d84": "mm",            # mm, m, or in
    "drainage_area": "km2", # km2 or mi2
    "monthly_flow": "m3/s", # m3/s or cfs
}
SIMPLIFY_METERS = 8.0  # Display geometry only; never used for model calculations.

HGMP = "https://www.fws.gov/sites/default/files/documents/Upper%20Grande%20Ronde%20SPCH.pdf"
SUBBASIN = "https://cfw.nwcouncil.org/Content/FWProgram/ReviewCycle/fy2002bm/workplan/011130Grande.pdf"
# Inclusive months, 1=January. Ranges with start > end cross December.
DEFAULT_PERIODS = {
    "chinook_juvenile_rearing": dict(start=7, end=9, source=SUBBASIN,
        note="Late-summer rearing assessment. Juveniles also rear outside this season; adjust for the study objective."),
    "chinook_spawning_stream_river": dict(start=8, end=9, source=HGMP,
        note="Upper Grande Ronde spring/summer Chinook spawning; does not represent fall Chinook timing."),
    "steelhead_juvenile_rearing": dict(start=7, end=9, source=SUBBASIN,
        note="Late-summer rearing assessment. Steelhead juveniles rear across multiple seasons and years."),
    "steelhead_spawning": dict(start=3, end=6, source=HGMP,
        note="Basin spawning window, March into June. Monthly means approximate partial-month endpoints."),
    "salmonid_general_sensitivity": dict(start=7, end=9, source="",
        note="Late-summer assessment period, not a calibrated species or life-stage timing window."),
}
