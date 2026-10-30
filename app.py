"""GIFT Habitat Explorer. In VSCode run run_streamlit.py to launch this app."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import zipfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bankfull_regressions import (CASTRO_JACKSON, CHAMP, NETWORK_BANKFULL_OPTIONS, apply_plot_overrides,
                                 default_overrides, plot_export, regression_notes)
from curve_presets import gift_shiny_default
from habitat_engine import (CFS_PER_M3S, MONTHS, STATUS_LABELS, aggregate_period,
                            calculate_hydraulics, compute_monthly, curve_from_csvs, gift_diagnostics,
                            hydraulic_parameters, input_source_rows, reach_with_hydraulics,
                            month_range, native_habitat, network_export, selected_comid,
                            selected_plotly_comid)
from presentation import (COMPARISON_FIELDS, channel_geometry_figure, comparison_figure,
                          flow_figure, legend_gradient, map_records, map_view, metric_scale,
                          network_map, number, period_label, potential_maximum_note, suitability_figure)
from network_scenarios import build_network_solution, full_curve_mean, full_curve_summary
from prepare_data import load_settings
from runtime_data import hydraulic_model_signature, load_runtime_dataset, runtime_signature
from r_engine import RBackendError, engine_signature, rscript_executable, runtime_info
from r_scenarios import prepared_mean, prepared_scenario

settings = load_settings()
APP_TITLE, ROOT = settings.APP_TITLE, settings.ROOT
DATA_DIRECTORY = Path(settings.DATA_DIRECTORY).expanduser()
DATA_DIRECTORY = (ROOT / DATA_DIRECTORY).resolve() if not DATA_DIRECTORY.is_absolute() else DATA_DIRECTORY.resolve()


@st.cache_resource(show_spinner=False)
def cached_r_environment(signature, executable):
    return runtime_info()


def data_signature():
    """Check sources and overview assets without touching native hydraulic files."""
    return runtime_signature(settings)


@st.cache_data(show_spinner=False, max_entries=2)
def cached_data(signature):
    return load_runtime_dataset(settings)


@st.cache_data(show_spinner=False, max_entries=12)
def cached_monthly(curve_json, signature, full_network=False):
    network = (cached_network_solution(signature)[0] if full_network else cached_geometry(signature))
    return compute_monthly(network["features"], json.loads(curve_json))


@st.cache_data(show_spinner=False, max_entries=2)
def cached_network_solution(signature):
    sources, width_source, depth_source, region, model_signature = signature
    network, _ = cached_data(sources)
    prepared = prepared_scenario(network, DATA_DIRECTORY, sources[0], width_source, depth_source,
                                 region, physical=True)
    if prepared is not None:
        return prepared[0], prepared[1]
    return build_network_solution(network, width_source, depth_source, region)


@st.cache_data(show_spinner=False, max_entries=4)
def cached_geometry(signature):
    sources, width_source, depth_source, region, model_signature = signature
    if width_source == depth_source == CHAMP:
        network, _ = cached_data(sources)
        return network
    network, _ = cached_data(sources)
    prepared = prepared_scenario(network, DATA_DIRECTORY, sources[0], width_source, depth_source, region)
    if prepared is not None:
        return prepared[0]
    return cached_network_solution(signature)[0]


@st.cache_data(show_spinner=False, max_entries=12)
def cached_full_curve_summary(curve_json, signature):
    sources, width_source, depth_source, region, _ = signature
    original, _ = cached_data(sources)
    prepared = prepared_scenario(original, DATA_DIRECTORY, sources[0], width_source, depth_source, region)
    if prepared is not None:
        summary = prepared_mean(prepared[2], DATA_DIRECTORY, json.loads(curve_json))
        if summary is not None:
            return summary
    network, physical = cached_network_solution(signature)
    return full_curve_summary(network, physical, json.loads(curve_json))


@st.cache_data(show_spinner=False, max_entries=256)
def cached_hydraulics(comid, parameters, model_signature):
    """One physical solution per segment/input set, independent of life stage."""
    return calculate_hydraulics(parameters)


@st.cache_data(show_spinner=False, max_entries=96)
def cached_native(comid, curve_json, parameters, model_signature, summer_width, _reach):
    hyd = cached_hydraulics(comid, parameters, model_signature)
    return native_habitat(DATA_DIRECTORY, _reach, json.loads(curve_json), hydraulics=hyd)


@st.cache_data(show_spinner=False, max_entries=96)
def cached_plot_mean(comid, curve_json, parameters, model_signature, summer_width, _reach):
    hyd = cached_hydraulics(comid, parameters, model_signature)
    physical = None if hyd.empty else hyd[["Q", "Ai", "Wi", "di", "Ui"]].to_numpy()
    return full_curve_mean(_reach, physical, json.loads(curve_json))


@st.cache_data(show_spinner=False, max_entries=256)
def cached_reach_monthly(comid, curve_json, parameters, model_signature,
                         flows, summer_width, _reach):
    hyd = cached_hydraulics(comid, parameters, model_signature)
    return compute_monthly([reach_with_hydraulics(_reach, hyd)], json.loads(curve_json))


@st.cache_data(show_spinner=False, max_entries=24)
def cached_comparison(curve_json, signature, months, require_all, metric,
                      comparison, _features, _summary, overview_revision=0):
    """Cache the network points independently of the selected-reach marker."""
    figure, count = comparison_figure(_features, _summary, metric, comparison, None)
    return figure.to_dict(), count


@st.cache_data(show_spinner=False, max_entries=12)
def cached_network_csv(curve_json, signature, months, require_all, _features, _summary, _metadata,
                       overview_revision=0):
    return csv_bytes(network_export(_features, _summary, json.loads(curve_json)["id"], list(months), _metadata))


def map_content(features, summary, layer, metric, view_key):
    """Keep one map's immutable reach records in this user's session."""
    content = st.session_state.get("_map_content")
    if content is None or content["key"] != view_key:
        title, high, palette = metric_scale(features, summary, layer, metric)
        content = dict(key=view_key, title=title, high=high, palette=palette,
                       records=map_records(features, summary, layer, metric, high, palette),
                       allowed=frozenset(r["id"] for r in features),
                       views={None: map_view(features)},
                       valid_count=int(summary[metric].notna().sum()))
        st.session_state._map_content = content
    return content


def csv_bytes(frame):
    return frame.to_csv(index=False).encode("utf-8-sig")


def curve_zip(curve):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for kind in ("depth", "velocity", "substrate"):
            columns = ["lower", "upper", "suit"] if kind == "substrate" else [kind, "suit"]
            z.writestr(f"{kind}.csv", pd.DataFrame(curve[kind], columns=columns).to_csv(index=False))
    return buffer.getvalue()


def apply_defaults(curve):
    start, end = curve.get("defaultStart", 6) + 1, curve.get("defaultEnd", 8) + 1
    st.session_state.period_mode = "Single month" if start == end else "Month range"
    st.session_state.start_month = start
    st.session_state.end_month = end
    st.session_state.single_month = curve.get("defaultMonth", start - 1) + 1
    st.session_state.inspect_month = start
    st.session_state.saved_months = {k: st.session_state[k] for k in ("start_month", "end_month", "single_month")}


def remember_month(key):
    st.session_state.saved_months[key] = st.session_state[key]


def period_mode_changed():
    # Hidden widgets are cleaned up by Streamlit. Restore their saved choices.
    for key, value in st.session_state.saved_months.items():
        st.session_state[key] = value


def active_curve(curves):
    if st.session_state.get("stage") == "custom":
        return st.session_state.custom_curve
    return next(c for c in curves if c["id"] == st.session_state.stage)


def stage_changed(curves):
    apply_defaults(active_curve(curves))


@st.dialog("Suitability Curves", width="large")
def show_suitability_curves(curve):
    st.markdown(f"**{curve['label']}**")
    if curve.get("curveNote"):
        st.caption(curve["curveNote"])
    for kind in ("depth", "velocity", "substrate"):
        st.plotly_chart(suitability_figure(curve, kind), width="stretch", key=f"dialog-{kind}")
    st.caption("Depth and velocity use GIFT's nearest-coordinate lookup. Substrate classes include "
               "their lower bound and exclude their upper bound.")


def map_selected(key, allowed):
    comid = selected_comid(st.session_state.get(key, {}), allowed)
    if comid is not None and comid != st.session_state.get("selected_id"):
        st.session_state.selected_id = comid
        st.session_state.selection_revision = st.session_state.get("selection_revision", 0) + 1


def comparison_selected(key, allowed):
    comid = selected_plotly_comid(st.session_state.get(key, {}), allowed)
    if comid is not None and comid != st.session_state.get("selected_id"):
        st.session_state.selected_id = comid
        st.session_state.selection_revision = st.session_state.get("selection_revision", 0) + 1
        st.session_state.view_focus = comid
        # A new map widget clears the previous read-only PyDeck selection and
        # centers the view on the reach selected in the scatterplot.
        st.session_state.view_revision += 1
        st.rerun("workspace")


def sidebar(curves):
    with st.sidebar:
        st.markdown("### GIFT\nHabitat Explorer")
        st.caption("UPPER GRANDE RONDE · EROM NETWORK")
        if st.session_state.get("stage") == "chinook_spawning_large_river":
            st.session_state.stage = "chinook_spawning_stream_river"
            stage_changed(curves)
        if "stage" not in st.session_state:
            st.session_state.stage = curves[0]["id"]
            apply_defaults(curves[0])
        if "pending_custom" in st.session_state:
            custom = st.session_state.pop("pending_custom")
            st.session_state.custom_curve = custom
            st.session_state.stage = "custom"
            apply_defaults(custom)
        options = {c["id"]: c["label"] for c in curves}
        if "custom_curve" in st.session_state:
            options["custom"] = st.session_state.custom_curve["label"]
        st.selectbox("Suitability curves / species / life stage", list(options), key="stage", format_func=options.get,
                     on_change=stage_changed, args=(curves,))
        curve = active_curve(curves)
        if curve.get("available") is False:
            st.warning(curve["curveNote"])
            st.info("To enable this preset, add the original Shiny depth, velocity, and substrate CSVs "
                    "as gift_shiny_default_depth.csv, gift_shiny_default_velocity.csv, and "
                    "gift_shiny_default_substrate.csv in inputs/curves. Select another curve set to continue.")
            st.stop()
        if st.button("View Suitability Curves", width="stretch", key="view_suitability_curves"):
            show_suitability_curves(curve)
        if curve.get("curveNote"):
            st.caption(curve["curveNote"])
        with st.expander("Use your own suitability curves"):
            st.caption("Upload one set of depth, velocity, and substrate CSVs. Coordinates use m, m/s, and mm; suitability ranges from 0 to 1.")
            with st.form("custom_curves"):
                label = st.text_input("Curve set name", value="Custom curves")
                depth = st.file_uploader("Depth CSV: depth, suit", type="csv", key="depth_upload")
                velocity = st.file_uploader("Velocity CSV: velocity, suit", type="csv", key="velocity_upload")
                substrate = st.file_uploader("Substrate CSV: lower, upper, suit", type="csv", key="substrate_upload")
                submitted = st.form_submit_button("Apply uploaded curves", width="stretch")
            if submitted:
                if any(f is None for f in (depth, velocity, substrate)):
                    st.error("Choose all three CSVs before applying.")
                else:
                    try:
                        custom = curve_from_csvs(depth.getvalue(), velocity.getvalue(), substrate.getvalue(), label, defaults=curve)
                        custom["periodNote"] = "Custom curves start with the previous life stage's assessment period. Set the months appropriate to your curves."
                        custom["periodSource"] = ""
                        st.session_state.pending_custom = custom
                        st.rerun()
                    except (ValueError, UnicodeError, pd.errors.ParserError) as exc:
                        st.error(str(exc))
            st.download_button("Download current CSV examples", curve_zip(curve), "gift_curve_examples.zip", mime="application/zip", on_click="ignore")
        st.divider()
        mean_hsi = st.session_state.get("mean_hsi", False)
        st.radio("EROM flow period", ["Single month", "Month range"], horizontal=True, key="period_mode",
                 on_change=period_mode_changed, disabled=mean_hsi)
        if st.session_state.period_mode == "Single month":
            st.selectbox("Month", range(1, 13), format_func=lambda m: MONTHS[m-1], key="single_month", on_change=remember_month, args=("single_month",), disabled=mean_hsi)
            months = [int(st.session_state.single_month)]
            require_all = True
        else:
            a, b = st.columns(2)
            a.selectbox("From", range(1, 13), format_func=lambda m: MONTHS[m-1], key="start_month", on_change=remember_month, args=("start_month",), disabled=mean_hsi)
            b.selectbox("Through", range(1, 13), format_func=lambda m: MONTHS[m-1], key="end_month", on_change=remember_month, args=("end_month",), disabled=mean_hsi)
            months = month_range(int(st.session_state.start_month), int(st.session_state.end_month))
            require_all = st.checkbox("Require every selected month", value=True, key="require_all", disabled=mean_hsi,
                help="When checked, habitat is withheld if any selected month is unsupported. Otherwise, the median uses available months and marks partial coverage.")
            st.caption("Ranges include both endpoints and can cross December.")
        st.button("Restore life-stage default", on_click=stage_changed, args=(curves,), width="stretch", disabled=mean_hsi)
        if mean_hsi:
            metric_label = st.session_state.get("map_metric", "hsi").upper()
            st.caption(f"Monthly flow selections are inactive while mean {metric_label} is mapped.")
        st.caption(curve.get("periodNote", "Editable assessment period."))
        if curve.get("periodSource"):
            st.markdown(f"[Timing reference]({curve['periodSource']})")
        cfs = st.radio("Flow units", ["cfs", "m³/s"], horizontal=True, key="flow_units") == "cfs"
        st.divider()
        st.caption("Monthly flows are 1971–2000 climatological means, not current observations.")
        st.caption("GIFT Python model · GPL-3.0")
    return curve, months, require_all, cfs


def map_layer_controls():
    layer, basemap = st.columns([4, 1])
    layer.radio("Network Layer", ["GIFT habitat", "Temperature Suitability", "Provisional Priority"],
                horizontal=True, key="network_layer")
    basemap.selectbox("Basemap", ["Light", "Dark", "Satellite"], key="basemap")


def network_controls():
    """Full-app widgets above the map also update disabled sidebar controls."""
    with st.container(key="network-controls", border=True):
        width, depth, metric, mean = st.columns([1, 1, .7, .9], vertical_alignment="bottom")
        width.selectbox("Bankfull width regression", NETWORK_BANKFULL_OPTIONS, key="network_width_source")
        depth.selectbox("Bankfull depth regression", NETWORK_BANKFULL_OPTIONS,
                        index=NETWORK_BANKFULL_OPTIONS.index(CASTRO_JACKSON), key="network_depth_source")
        metric.segmented_control("Habitat metric", ["hsi", "wua"], default="hsi", required=True,
                                 format_func=str.upper, key="map_metric")
        label = st.session_state.map_metric.upper()
        mean.toggle(f"Map mean {label}", value=False, key="mean_hsi",
                    help=f"Off: median habitat at selected monthly flows. On: each segment's mean {label} "
                         "over its full modeled flow curve, without flow-duration weighting.")


def fit_network():
    st.session_state.view_focus = None
    st.session_state.view_revision += 1


def zoom_to_segment():
    st.session_state.view_focus = st.session_state.selected_id
    st.session_state.view_revision += 1


def monthly_display(frame, cfs):
    factor, unit = (CFS_PER_M3S, "cfs") if cfs else (1, "m³/s")
    out = frame[["month", "q", "depth", "velocity", "width", "d", "v", "s", "hsi", "wua", "status"]].copy()
    out["month"] = out.month.map(lambda m: MONTHS[int(m)-1])
    out["q"] *= factor
    out["status"] = out.status.map(lambda s: STATUS_LABELS.get(s,s))
    return out.rename(columns={"month":"Month", "q":f"Flow ({unit})", "depth":"Mean depth (m)",
        "velocity":"Mean velocity (m/s)", "width":"Wetted width (m)", "d":"Depth suitability",
        "v":"Velocity suitability", "s":"Substrate suitability", "hsi":"HSI", "wua":"WUA (m²/m)", "status":"Availability"})


def inspector(reach, monthly, summary, months, cfs, mean_hsi=False):
    st.caption("SELECTED SEGMENT")
    st.subheader(reach["name"])
    st.caption(f"COMID {reach['id']}")
    detail = None
    if mean_hsi:
        st.caption("Full-curve means for this segment; monthly flow selections are inactive.")
        table = pd.DataFrame({"Component": ["Mean depth", "Mean velocity", "Substrate D84"],
                              "Raw value": [f"{number(summary.depth)} m", f"{number(summary.velocity)} m/s",
                                            f"{number(reach['d84'], 1)} mm"],
                              "Mean suitability": [number(summary.d), number(summary.v), number(summary.s)]})
        st.dataframe(table, hide_index=True, width="stretch", height=145)
        a, b = st.columns(2)
        a.metric("Mean HSI", number(summary.hsi))
        b.metric("Mean WUA (m²/m)", number(summary.wua))
        if summary.status != "ok":
            st.info(STATUS_LABELS.get(summary.status, summary.status))
        if reach["modelStatus"] != "ok":
            st.caption(reach["modelStatus"])
    else:
        if st.session_state.get("inspect_month") not in months:
            st.session_state.inspect_month = months[0]
        detail = st.selectbox("Inspect month", months, format_func=lambda m: MONTHS[m-1], key="inspect_month")
        row = monthly[monthly.month.eq(detail)].iloc[0]
        factor, unit = (CFS_PER_M3S, "cfs") if cfs else (1, "m³/s")
        st.metric(f"{MONTHS[detail-1]} EROM flow ({unit})", number(row.q * factor if pd.notna(row.q) else None))
        table = pd.DataFrame({"Component": ["Mean depth", "Mean velocity", "Substrate D84"],
                              "Raw value": [f"{number(row.depth)} m", f"{number(row.velocity)} m/s", f"{number(reach['d84'],1)} mm"],
                              "Suitability": [number(row.d), number(row.v), number(row.s)]})
        st.dataframe(table, hide_index=True, width="stretch", height=145)
        a, b = st.columns(2)
        a.metric("Monthly HSI", number(row.hsi))
        b.metric("Monthly WUA (m²/m)", number(row.wua))
        if row.status not in ("ok", "zero_flow"):
            st.warning(STATUS_LABELS.get(row.status, row.status))
        else:
            st.caption(STATUS_LABELS[row.status])
        if reach["modelStatus"] != "ok":
            st.caption(reach["modelStatus"])
        if len(months) > 1:
            st.markdown(f"**{period_label(months)}** · {int(summary.validCount)}/{len(months)} supported months")
            st.caption(f"HSI {number(summary.hsi)} · WUA {number(summary.wua)} m²/m")
            if summary.status != "ok":
                st.caption(STATUS_LABELS.get(summary.status, summary.status))
    st.caption(f"Temperature score: {number(reach['temp'])} · Priority score: {number(reach['priority'])}")
    with st.expander("Network hydraulic input values"):
        st.dataframe(pd.DataFrame([
            {"Input": label, "Value": reach.get(field), "Units": unit}
            for label, field, unit in (("Bankfull width", "bfWidth", "m"),
                ("Mean bankfull depth", "bfDepth", "m"), ("Maximum bankfull depth", "maxDepth", "m"),
                ("Channel slope", "slope", "m/m"), ("Substrate D84", "d84", "mm"))
        ]), hide_index=True, width="stretch")
    return detail


@st.fragment(key="workspace")
def workspace(network, curve, monthly, summary, months, cfs, signature, mean_hsi=False):
    features = network["features"]
    if st.session_state.get("_overview_signature") != signature:
        st.session_state._overview_signature = signature
        st.session_state._evaluated_reaches = {}
    if st.session_state.get("_plot_source_signature") != signature[0]:
        st.session_state._plot_source_signature = signature[0]
        st.session_state._plot_choices = {}
        for key in list(st.session_state):
            if key.startswith("plot-"):
                st.session_state.pop(key, None)
    allowed_ids = {r["id"] for r in features}
    if st.session_state.get("selected_id") not in allowed_ids:
        st.session_state.selected_id = None
    selected_id = st.session_state.selected_id
    curve_json = json.dumps(curve, sort_keys=True)
    model_signature = hydraulic_model_signature()
    reach = next((r for r in features if r["id"] == selected_id), None)
    selected_monthly = selected_summary = None
    if reach is not None:
        parameters = hydraulic_parameters(reach)
        with st.spinner("Calculating the selected segment…"):
            try:
                hyd = cached_hydraulics(selected_id, parameters, model_signature)
            except RBackendError:
                raise
            except (ValueError, RuntimeError):
                hyd = pd.DataFrame()
            evaluated = reach_with_hydraulics(reach, hyd)
            if not mean_hsi:
                selected_monthly = compute_monthly([evaluated], curve) if hyd.empty else cached_reach_monthly(
                    selected_id, curve_json, parameters, model_signature,
                    tuple(row[0] for row in reach["months"]), reach.get("summerWidth"), reach)
        selected_summary = (summary.loc[selected_id] if mean_hsi else
                            aggregate_period(selected_monthly, months, bool(summary.requireAll.iloc[0])).loc[selected_id])
        if any(row[4] == "pending_selection" for row in reach["months"]):
            if selected_id not in st.session_state._evaluated_reaches:
                st.session_state._evaluated_reaches[selected_id] = evaluated
        reach = evaluated
    overrides = st.session_state._evaluated_reaches
    # Shared comparison/export caches must distinguish which reaches were
    # evaluated, including two users who have evaluated the same number of IDs.
    overview_revision = (tuple(sorted(overrides)), mean_hsi)
    if overrides and not mean_hsi:
        features = [overrides.get(r["id"], r) for r in features]
        updated = [cached_reach_monthly(
            r["id"], curve_json, hydraulic_parameters(r), model_signature,
            tuple(row[0] for row in r["months"]), r.get("summerWidth"), r)
            for r in overrides.values()]
        monthly = monthly.set_index(["id", "month"]).copy()
        for frame in updated:
            replacement = frame.set_index(["id", "month"])
            monthly.loc[replacement.index, replacement.columns] = replacement
        monthly = monthly.reset_index()
        summary = aggregate_period(monthly, months, bool(summary.requireAll.iloc[0]))
    pending = [r["id"] for r in features if any(row[4] == "pending_selection" for row in r["months"])]
    network = dict(network, features=features,
                   metadata=dict(network["metadata"], pendingHydraulics=pending))
    mapping_summary = summary
    layer = st.session_state.network_layer
    metric = st.session_state.map_metric
    basemap = st.session_state.basemap
    metric_label = metric.upper()
    if st.session_state.get("view_focus") not in allowed_ids:
        st.session_state.view_focus = None
    content = map_content(features, mapping_summary, layer, metric,
                          (signature, curve_json, tuple(months), bool(summary.requireAll.iloc[0]),
                           layer, metric, overview_revision))
    left, right = st.columns([2.6, 1], gap="large")
    with left:
        focus = st.session_state.get("view_focus")
        if focus not in content["views"]:
            content["views"][focus] = map_view(features, focus)
        deck = network_map(content["records"], content["views"][focus], selected_id, basemap)
        key = f"network-map-{st.session_state.view_revision}"
        with st.container(key="map-shell"):
            st.pydeck_chart(
                deck, key=key, height=530, on_select=lambda: map_selected(key, content["allowed"]),
                selection_mode="single-object")
            with st.container(key="map-actions", horizontal=True, gap="small"):
                st.button("Fit network", key="fit-network", icon=":material/fit_screen:",
                          help="Fit network", on_click=fit_network)
                st.button("Zoom to segment", key="zoom-to-segment", icon=":material/my_location:",
                          help="Zoom to segment", on_click=zoom_to_segment, disabled=selected_id is None)
        colors = legend_gradient(content["palette"])
        st.markdown(f'<div class="legend"><span>{content["title"]} &nbsp; 0</span><div style="background:linear-gradient(90deg,{colors})"></div><span>{number(content["high"])}</span></div>', unsafe_allow_html=True)
        st.caption(potential_maximum_note(layer, metric))
        map_basis = f"Mean {metric_label} across the full modeled flow curve" if mean_hsi else period_label(months)
        st.caption(f"{curve['label']} · {map_basis} · {content['valid_count']}/{len(features)} segments with habitat results")
        st.caption(f"Network width: {signature[1]} · Network depth: {signature[2]}. "
                   "Regressions apply to every segment. Individual plot edits do not change map values.")
        if mean_hsi:
            st.caption(f"Mean {metric_label} is the area under each segment's {metric_label}–discharge curve divided by its modeled "
                       "discharge range, estimated from 201 equally spaced flows. Selected months do not "
                       "affect the map or plots. Gold diamonds show the full-curve means.")
        if pending:
            st.info(f"{len(pending)} segments await habitat calculation. Select a segment to calculate it; gray values are unavailable until evaluated.")
        if layer == "Temperature Suitability":
            st.info("Temperature scores describe juvenile-salmon suitability under the supplied interpretation of See et al. (2021) using NoRWeST mean August stream temperature. They do not change with the selected life stage or month.")
            st.caption("Temperature colors place tan at 80% of the observed maximum to distinguish higher suitability scores.")
        elif layer == "Provisional Priority":
            st.info("Provisional Model stream segment restoration priority")
        st.caption("Higher values → greater suitability or priority. Gray = unavailable map value. Gold = selected segment. Select a reach on the map or in a comparison plot.")
    with right:
        if reach is None:
            st.info("Select a stream segment or comparison point to calculate its hydraulic and habitat curves.")
        else:
            inspector(reach, selected_monthly, selected_summary, months, cfs, mean_hsi)
    if reach is not None:
        if not mean_hsi:
            diagnostics_panel(reach, selected_monthly, selected_summary, months, cfs, curve, network["metadata"])
        flow_panel(reach, selected_monthly, months, cfs, curve_json, signature, mean_hsi)
    comparison_panel(features, mapping_summary, selected_id, curve_json, months, signature, overview_revision, mean_hsi)
    network_downloads(network, mapping_summary, curve, months, bool(summary.requireAll.iloc[0]), signature,
                      overview_revision)


def diagnostics_panel(reach, monthly, summary, months, cfs, curve, metadata):
    rows = monthly.set_index("month").loc[months].reset_index()
    if not rows[["hsi", "wua"]].isna().any().any() and pd.notna(summary.hsi) and pd.notna(summary.wua):
        return
    payload = gift_diagnostics(reach, monthly, summary, months, curve, metadata)
    outside_bankfull = rows.status.eq("above_bankfull").any()
    with st.expander("GIFT inputs and unavailable-result diagnostics", expanded=not outside_bankfull):
        st.markdown(f"**{reach['name']} (COMID: {reach['id']})**")
        source_label = "Selected plot" if reach.get("plotInputs") else "Active network regressions"
        st.caption(f"{source_label} · Hydraulic model: {reach['modelStatus']} · Period: {STATUS_LABELS.get(summary.status, summary.status)}")
        factor, flow_unit = (CFS_PER_M3S, "cfs") if cfs else (1.0, "m³/s")
        definitions = [
            ("Bankfull width", "bfWidth", "m", True),
            ("Mean bankfull depth", "bfDepth", "m", True),
            ("Maximum bankfull depth", "maxDepth", "m", True),
            ("Channel slope", "slope", "m/m", True),
            ("Substrate D84", "d84", "mm", True),
            ("Raw shape factor", "shapeRaw", "dimensionless", False),
            ("Shape factor used", "shapeUsed", "dimensionless", False),
            ("Minimum modeled flow", "qMin", flow_unit, False),
            ("Maximum modeled flow", "qMax", flow_unit, False),
            ("Native hydraulic levels", "nativeCount", "count", False),
        ]
        parameters = []
        for label, field, unit, required in definitions:
            value = payload["input_parameters"][field]
            missing = value is None or not np.isfinite(value)
            if field == "maxDepth" and reach.get("maxDepth") is None and (
                    reach.get("plotInputs", {}).get("maximum_depth_supplied") is False or
                    any(s != CHAMP for s in (reach.get("networkInputs", {}).get("width_source", CHAMP),
                                             reach.get("networkInputs", {}).get("depth_source", CHAMP)))):
                required = False
                status = "Not supplied for this plot"
            else:
                status = ("Missing required input" if required else "Unavailable") if missing else (
                "Invalid: must be positive" if required and value <= 0 else "Available")
            shown = value * factor if not missing and field in ("qMin", "qMax") else value
            parameters.append(dict(Parameter=label, Value="Unavailable" if missing else f"{shown:.6g}",
                                   Units=unit, Status=status))
        st.dataframe(pd.DataFrame(parameters), hide_index=True, width="stretch")
        shape_method = ("1 - mean bankfull depth / maximum bankfull depth" if reach.get("maxDepth") is not None
                        else "(bankfull width / mean bankfull depth) / 100")
        st.caption(f"Active curves: {curve['label']}. Shape factor = {shape_method}; no cap is applied.")
        curve_ranges = [
            {"Curve": "Depth", "Input range": f"{min(r[0] for r in curve['depth']):.6g} to {max(r[0] for r in curve['depth']):.6g}", "Units": "m"},
            {"Curve": "Velocity", "Input range": f"{min(r[0] for r in curve['velocity']):.6g} to {max(r[0] for r in curve['velocity']):.6g}", "Units": "m/s"},
            {"Curve": "Substrate classes", "Input range": "; ".join(f"[{lo:.6g}, {hi:.6g})" for lo, hi, _ in curve["substrate"]), "Units": "mm"},
        ]
        st.dataframe(pd.DataFrame(curve_ranges), hide_index=True, width="stretch")
        st.markdown("**Inputs and availability at each selected monthly flow**")
        st.dataframe(monthly_display(rows, cfs), hide_index=True, width="stretch")
        st.caption("Unavailable values are retained. Positive flows outside the modeled range are not extrapolated. The JSON download includes full input values and the active suitability curves in SI units.")
        st.download_button("Download GIFT inputs and diagnostics (JSON)",
                           json.dumps(payload, allow_nan=False, ensure_ascii=False, indent=2),
                           f"gift_{reach['id']}_diagnostics.json", mime="application/json", on_click="ignore")


def remember_plot_input(comid, kind, key, network_default=None):
    choices = st.session_state._plot_choices.setdefault(comid, {})
    value = st.session_state[key]
    choices["custom_"+kind] = value
    if kind == "max_depth":
        return
    same = (value is None and network_default is None) if value is None or network_default is None else bool(
        np.isclose(value, network_default, rtol=1e-12, atol=1e-12))
    choices["override_"+kind] = not same


def reset_plot_inputs(reach):
    comid = reach["id"]
    st.session_state._plot_choices[comid] = default_overrides(reach)
    for field in default_overrides(reach):
        st.session_state.pop(f"plot-{comid}-{field}", None)


def plot_controls(reach):
    """Editable defaults with internal change tracking and no override switches."""
    comid = reach["id"]
    st.session_state.setdefault("_plot_choices", {})
    choices = st.session_state._plot_choices.setdefault(comid, default_overrides(reach))
    with st.expander("Adjust selected-segment hydraulics", expanded=True):
        st.caption("Edit these values for this segment's plots and downloads. The map and comparisons "
                   "retain the network regressions selected above the map.")
        columns = st.columns(2)
        definitions = (("width", "Bankfull width", "bfWidth", "m", .01, "%.4f"),
                       ("depth", "Mean bankfull depth", "bfDepth", "m", .01, "%.4f"),
                       ("slope", "Slope", "slope", "m/m", .0001, "%.7f"),
                       ("d84", "D84", "d84", "mm", 1., "%.3f"))
        for index, (kind, label, field, units, step, fmt) in enumerate(definitions):
            name, key = "custom_"+kind, f"plot-{comid}-custom_{kind}"
            # Unedited fields follow any new network regression. Explicit edits
            # persist by COMID, including a deliberately cleared required value.
            if not choices.get("override_"+kind, False):
                choices[name] = reach.get(field)
                st.session_state[key] = reach.get(field)
            else:
                st.session_state.setdefault(key, choices.get(name))
            columns[index % 2].number_input(f"{label} for plotting ({units})", min_value=0., value=None,
                step=step, format=fmt, key=key, placeholder="Enter a value",
                on_change=remember_plot_input, args=(comid, kind, key, reach.get(field)))
        maximum_key = f"plot-{comid}-custom_max_depth"
        st.session_state.setdefault(maximum_key, choices.get("custom_max_depth"))
        st.number_input("Maximum bankfull depth for plotting (m)", min_value=0., value=None,
                        step=.01, format="%.4f", key=maximum_key, placeholder="Automatic (leave blank)",
                        on_change=remember_plot_input, args=(comid, "max_depth", maximum_key))
        st.caption("Leave maximum depth blank for automatic behavior: unchanged CHaMP dimensions use "
                   "the segment's source maximum; alternative or edited width/depth uses GIFT's "
                   "width/depth shape estimate. An entered maximum overrides this behavior for the plot.")
        st.button("Restore segment defaults", key=f"reset-plot-{comid}", width="stretch",
                  on_click=reset_plot_inputs, args=(reach,))
        sources = reach.get("networkInputs", {"width_source": CHAMP, "depth_source": CHAMP})
        region = getattr(settings, "BIEGER_REGION", "IMP")
        for note in regression_notes(reach, sources, region):
            st.caption(note)
    return apply_plot_overrides(reach, choices)


@st.fragment
def flow_panel(reach, selected_monthly, months, cfs, curve_json, signature, mean_hsi=False):
    """Render monthly or full-flow mean plots without redrawing the map."""
    selected_id = reach["id"]
    detail = None if mean_hsi else st.session_state.inspect_month
    st.subheader("Habitat response to flow")
    st.caption(f"{reach['name']} (COMID: {selected_id}) · {json.loads(curve_json)['label']}")
    try:
        adjusted = plot_controls(reach)
    except (ValueError, KeyError) as exc:
        st.error(f"Plot inputs: {exc}")
        st.info("A flow-response curve is unavailable because this segment has missing hydraulic inputs "
                "or invalid plot values. Choose another regression or enter positive values.")
        return
    parameters = hydraulic_parameters(adjusted)
    model_signature = hydraulic_model_signature()
    try:
        hyd = cached_hydraulics(selected_id, parameters, model_signature)
    except RBackendError:
        raise
    except (ValueError, RuntimeError) as exc:
        st.error(f"Plot inputs: {exc}")
        return
    reach = reach_with_hydraulics(adjusted, hyd)
    mean = None
    if mean_hsi:
        mean = cached_plot_mean(selected_id, curve_json, parameters, model_signature, reach.get("summerWidth"), reach)
    else:
        selected_monthly = cached_reach_monthly(selected_id, curve_json, parameters, model_signature,
            tuple(row[0] for row in reach["months"]), reach.get("summerWidth"), reach)
    a, b, c, d = st.columns(4)
    a.metric("Plot bankfull width (m)", number(reach["bfWidth"]))
    b.metric("Plot mean bankfull depth (m)", number(reach["bfDepth"]))
    c.metric("Plot slope (m/m)", f"{reach['slope']:.6g}")
    if mean_hsi:
        d.metric("Plot mean HSI", number(mean["hsi"]))
    else:
        factor, unit = (CFS_PER_M3S, "cfs") if cfs else (1., "m³/s")
        d.metric(f"Plot bankfull flow ({unit})", number(reach.get("qMax")*factor if reach.get("qMax") is not None else None))
    wua_tab, hsi_tab, geometry_tab = st.tabs(["WUA", "HSI", "Channel Geometry"], key="flow_tabs", on_change="rerun")
    results_key = "full_curve_results" if mean_hsi else "monthly_results"
    native = pd.DataFrame()
    if wua_tab.open or hsi_tab.open or st.session_state.get(results_key, False):
        native = cached_native(selected_id, curve_json, parameters, model_signature, reach.get("summerWidth"), reach)
        if native.empty:
            st.info("A flow-response curve is unavailable because this segment has missing hydraulic inputs.")
    for tab, field in ((wua_tab, "wua"), (hsi_tab, "hsi")):
        if tab.open:
            with tab:
                st.plotly_chart(flow_figure(native, selected_monthly, months, detail, field, cfs, mean=mean),
                                width="stretch", key=f"flow-{field}")
    if geometry_tab.open:
        with geometry_tab:
            st.plotly_chart(channel_geometry_figure(reach), width="stretch", key="channel-geometry")
            maximum = hyd.attrs.get("simulated_max_depth_m")
            st.caption(f"Active plot geometry: width {number(reach['bfWidth'])} m; mean bankfull depth "
                       f"{number(reach['bfDepth'])} m; GIFT shape factor {number(reach.get('shapeUsed'), 5)}; "
                       f"modeled maximum depth {number(maximum)} m. The vertical axis is exaggerated. "
                       "This is GIFT's assumed cross section, not a surveyed section.")
    substrate = mean["s"] if mean_hsi else selected_monthly.iloc[0].s
    st.caption(f"Substrate suitability = {number(substrate)} at plot D84 = {number(reach['d84'],1)} mm. "
               "Dashed component curves use the right axis. No extrapolation beyond the modeled flow range.")
    if mean_hsi:
        st.caption("Gold diamond: mean HSI or WUA across the full modeled discharge range, shown at "
                   "the range midpoint. Its height is the curve average, rather than the curve value "
                   "at that discharge. Monthly markers and flow-range shading are inactive.")
    else:
        st.caption("Gold points are separately evaluated monthly results; pale shading spans selected "
                   "flows, with a dotted line for a single flow.")
    label = "Full-curve summary and downloads" if mean_hsi else "Monthly results for this segment"
    details = st.expander(label, key=results_key, on_change="rerun")
    if details.open:
        with details:
            if mean_hsi:
                st.dataframe(pd.DataFrame({"Measure": ["Mean HSI", "Mean WUA (m²/m)",
                    "Mean depth suitability", "Mean velocity suitability", "Substrate suitability"],
                    "Value": [mean[k] for k in ("hsi", "wua", "d", "v", "s")]}),
                    hide_index=True, width="stretch")
            else:
                selected_rows = selected_monthly.set_index("month").loc[months].reset_index()
                selected_rows["D84_mm"] = reach["d84"]
                st.dataframe(monthly_display(selected_rows, cfs), hide_index=True, width="stretch")
                st.caption("Raw values refer to each listed month's exact flow; component suitability incorporates GIFT's within-reach distributions.")
                export = selected_rows.drop(columns=["summer_width", "normalized"]).rename(columns={"id":"COMID", "q":"EROM_Q_m3s", "depth":"mean_depth_m", "velocity":"mean_velocity_m_s", "width":"wetted_width_m", "wua":"WUA_m2_per_m"})
                st.download_button("Download monthly results (SI units)", csv_bytes(plot_export(export, reach)), f"gift_{selected_id}_monthly.csv", mime="text/csv", on_click="ignore")
            if not native.empty:
                export = native.drop(columns=["normalized"]).rename(columns={"q":"Q_m3s", "depth":"mean_depth_m", "velocity":"mean_velocity_m_s", "width":"wetted_width_m", "wua":"WUA_m2_per_m"})
                st.download_button("Download full flow-response curve (SI units)", csv_bytes(plot_export(export, reach)),
                                   f"gift_{selected_id}_flow_curve.csv", mime="text/csv", on_click="ignore")


@st.fragment
def comparison_panel(features, summary, selected_id, curve_json, months, signature, overview_revision=0, mean_hsi=False):
    st.divider()
    use_wua = st.toggle("Plot WUA", value=False, key="comparison_wua",
                        help="Off: plot HSI. On: plot WUA (m²/m). This controls all comparison graphs.")
    metric = "wua" if use_wua else "hsi"
    label = metric.upper()
    st.subheader(f"{label} comparisons")
    basis = f"mean {label} across each segment's full modeled flow curve" if mean_hsi else "the selected months"
    st.caption(f"Click a dot to select and locate its reach on the map. Values summarize {basis}; the {label} axis follows the observed maximum.")
    comparisons = list(COMPARISON_FIELDS)
    tabs = st.tabs(comparisons, key="comparison_tabs", on_change="rerun")
    for tab, comparison in zip(tabs, comparisons):
        if tab.open:
            with tab:
                base = cached_comparison(curve_json, signature, tuple(months), bool(summary.requireAll.iloc[0]),
                                         metric, comparison, features, summary, overview_revision)
                figure, count = comparison_figure(features, summary, metric, comparison, selected_id, base=base)
                key = f"selection-{st.session_state.selection_revision}-{metric}-comparison-{comparison}"
                allowed = frozenset(r["id"] for r in features)
                st.plotly_chart(figure, width="stretch", key=key, selection_mode="points",
                                on_select=lambda key=key, allowed=allowed: comparison_selected(key, allowed))
                st.caption(f"{count} segments with both values. Each point represents one segment; this comparison does not establish causation.")


def network_downloads(network, summary, curve, months, require_all, signature, overview_revision=0):
    """Reuse the export until the overview or assessment settings change."""
    features = network["features"]
    export = cached_network_csv(json.dumps(curve, sort_keys=True), signature, tuple(months),
                                require_all, features, summary, network["metadata"], overview_revision)
    full_mean = "aggregation" in summary and summary.aggregation.eq("full_curve_mean").all()
    metric = st.session_state.map_metric
    download_label = f"Download network mean-{metric.upper()} summary (CSV)" if full_mean else "Download network period summary (CSV)"
    filename = f"gift_network_mean_{metric}_summary.csv" if full_mean else "gift_network_period_summary.csv"
    st.download_button(download_label, export, filename, mime="text/csv", on_click="ignore")
    with st.expander("Coverage and input notes"):
        status_counts = summary.status.value_counts().rename_axis("Status").reset_index(name="Segments")
        status_counts["Status"] = status_counts.Status.map(lambda s: STATUS_LABELS.get(s,s))
        st.dataframe(status_counts, hide_index=True, width="stretch")
        if full_mean:
            st.caption(f"{int(summary.complete.sum())}/{len(features)} segments have a complete full-flow mean. "
                       "Averages use 201 equally spaced discharges within each segment's modeled range.")
        else:
            st.caption(f"{int(summary.complete.sum())}/{len(features)} segments have every selected month. A positive flow outside the modeled range stays unavailable. A valid zero-flow month contributes zero HSI and WUA.")
        st.caption(f"Maximum bankfull depth joined for {network['metadata']['maxDepthMatches']} stream IDs; priority joined for {network['metadata']['priorityMatches']}. {len(network['metadata']['skippedHydraulics'])} segments have missing or invalid hydraulic inputs. Shape factors are used without capping; {len(network['metadata']['shapeFactorAbove07'])} exceed 0.7.")


def methods(network):
    metadata = network["metadata"]
    with st.expander("Input sources and field mappings"):
        info = cached_r_environment(engine_signature(), rscript_executable())
        st.caption(f"Calculation engine: Original R GIFT · source revision {info['revision']} · {info['R_version']}")
        st.dataframe(pd.DataFrame([{"R package": name, "Version": version}
                                  for name, version in info["packages"].items()]), hide_index=True)
        st.caption("These are the files and columns used for the loaded model inputs. Source units are converted to SI for calculation. Edit settings.py, then rerun or restart the app. Input sources are checked on every full rerun.")
        st.dataframe(pd.DataFrame(input_source_rows(metadata)), hide_index=True, width="stretch")
    with st.expander("Data and methods"):
        st.markdown("""
**Network and joins.** The active files, layers, join keys, fields, and source units
are listed in **Input sources and field mappings**. Geometry, bankfull width,
mean depth, slope, D84, and monthly flows come from the configured streams layer.
Maximum depth and priority use their configured ID joins. Temperature uses the
explicitly configured streams or priority source. Missing values remain unavailable.

**Hydraulics and habitat.** The controls above the map select independent bankfull width and
depth regressions for every segment: CHaMP Regression, Bieger 2015 National,
Bieger 2015 Regional, or Castro and Jackson 2001. The defaults are CHaMP width
and Castro and Jackson mean depth. Using CHaMP for both dimensions uses
`1 - mean bankfull depth / maximum bankfull depth` for its shape factor.
Selected-segment plots can override width, mean depth, slope, and D84.
Plot overrides are saved separately for each segment and do not replace network scores.
With a blank plot maximum-depth field, alternative or custom width/depth omits maximum bankfull depth; GIFT then uses
`(bankfull width / mean bankfull depth) / 100` for its shape factor. An entered maximum depth overrides this behavior for that plot.
The Bieger 2015 Regional division is configured by `BIEGER_REGION` in settings.py
(Intermontane Plateau for this network). Castro and Jackson uses the Western
Cordillera drainage-area relationships verified against USGS StreamStats,
converting feet and mi² to SI.
The equations are shown with the plot controls, and selected-segment downloads
record the active width/depth sources, geometry, and slope.
Selecting a segment runs the original R GIFT functions in a separate Rscript process and
caches its hydraulic solution. The map overview uses the bundled monthly
hydraulics where the source inputs still match. Mean depth,
velocity, and wetted width are interpolated to each exact monthly flow before
GIFT evaluates its 121-bin distributions and nearest-coordinate suitability lookup.
Substrate suitability uses the class containing predicted D84, with inclusive lower
and exclusive upper bounds. D84 represents one grain size, not a full distribution.

**HSI and WUA.** HSI is the product of depth, velocity, and substrate suitability.
WUA is HSI × wetted width, expressed in m² per meter of reach. Period summaries
take the median of separately calculated monthly HSI and WUA. They do not evaluate
habitat at median discharge or multiply separately aggregated suitability scores.
Raw values in the inspector always refer to its single selected month.

**Flows and availability.** EROM values are gage-adjusted 1971–2000 monthly climatology,
not current observations. Represented alterations and gage corrections are included;
diversions are incompletely accounted for. The default requires every selected month.
Turning this off permits available-month medians, with coverage retained in the export.
Unsupported positive flows are never extrapolated or replaced by zero. A modeled
zero-flow month has zero HSI and WUA; depth/velocity suitability is undefined.

**Comparison scores.** The configured temperature score is displayed unchanged.
For the supplied `currTempScoreKSee` field, higher values indicate better juvenile-salmon temperature suitability
from [See et al. (2021)](https://doi.org/10.1002/ecs2.3404). Its score transformation
was not independently reconstructed, and it is not a spawning temperature model.
Priority is also displayed unchanged. Neither score is a GIFT component.

**Display and source.** Display geometry uses the configured simplification; hydraulic calculations
use the original reach attributes. The Python source and original GIFT model are
included in the project under GPL-3.0. Custom uploads are held in the user's session;
derived computations use a bounded server cache. Full app reruns check the source
files, field mappings, units, curves, and calculation code before using prepared data.
Changed inputs refresh the stream attributes. Segments needing new hydraulics
are evaluated when selected, and their results are added to the map and export.
Verified R calculations are bundled for CHaMP width/depth and for the default
CHaMP-width/Castro-and-Jackson-depth scenario, including full-curve means for the
supplied curve sets. Changed inputs and other scenarios run the original R functions
and are cached. Source revisions and R package versions appear in the input notes.
Mean-map mode averages each segment's full HSI or WUA response curve over discharge,
using 201 equally spaced flows and trapezoidal integration divided by the
modeled discharge range. It is not weighted by flow duration. The default map
continues to show the median result at the selected monthly flows.
""")


def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🌊", layout="wide")
    st.markdown("""<style>
    .stApp {background:#f7f8f4;color:#183e36}
    [data-testid="stSidebar"] {background:#edf2e9}
    h1,h2,h3 {color:#183e36;letter-spacing:-.025em}
    .block-container {padding-top:2rem;max-width:1650px}
    [data-testid="stMetricValue"] {font-size:1.65rem;color:#186354}
    .legend {display:flex;align-items:center;gap:12px;font-size:13px;color:#486058;margin-top:8px}
    .legend div {height:9px;flex:1;border-radius:4px;max-width:280px}
    .st-key-map-shell {position:relative}
    .st-key-map-actions {
        position:absolute !important;top:12px;left:12px;z-index:10;
        width:96px !important;gap:8px !important;
    }
    .st-key-map-actions button {
        width:44px;min-width:44px;height:42px;min-height:42px;padding:0;gap:0;
        background:white;color:#183e36;border:1px solid #ccd7d0;
        box-shadow:0 1px 5px #183e3626;
    }
    .st-key-map-actions button [data-testid="stMarkdownContainer"] p {
        font-size:0;line-height:0;margin:0;
    }
    .st-key-map-actions button:disabled {opacity:.5}
    /* Tooltips appear only when a pickable stream or comparison dot is hit.
       Keep the map's normal grab cursor and the graph's pan cursor elsewhere. */
    [data-testid="stDeckGlJsonChart"]:has(.deck-tooltip[style*="display: block"]) canvas,
    [data-testid="stDeckGlJsonChart"]:has(.deck-tooltip[style*="display: block"])
    [style*="cursor: grab;"] {
        cursor:pointer !important;
    }
    [class*="st-key-selection-"][class*="-comparison-"]
    [data-testid="stPlotlyChart"]:has(.hoverlayer .hovertext) .nsewdrag {
        cursor:pointer !important;
    }
    </style>""", unsafe_allow_html=True)
    st.title(APP_TITLE)
    st.caption("Geomorphic Instream Flow Tool results across the Upper Grande Ronde basin.")
    with st.spinner("Checking original R GIFT…"):
        cached_r_environment(engine_signature(), rscript_executable())
    try:
        with st.spinner("Loading stream network…"):
            sources = data_signature()
            network, curves = cached_data(sources)
    except (OSError, ValueError, sqlite3.Error, KeyError) as exc:
        st.error(f"Input data could not be loaded: {exc}")
        st.info("Check the paths, layers, fields, and units in settings.py, then reload the app.")
        st.stop()
    st.session_state.setdefault("view_revision", 0)
    st.session_state.setdefault("selection_revision", 0)
    curve, months, require_all, cfs = sidebar([*curves, gift_shiny_default()])
    st.session_state.current_curve = curve
    map_layer_controls()
    network_controls()
    signature = (sources, st.session_state.network_width_source, st.session_state.network_depth_source,
                 getattr(settings, "BIEGER_REGION", "IMP"), hydraulic_model_signature())
    mean_hsi = st.session_state.mean_hsi
    if signature[1] != CHAMP or signature[2] != CHAMP or mean_hsi:
        with st.spinner("Calculating network geometry for the selected regressions…"):
            network = cached_network_solution(signature)[0] if mean_hsi else cached_geometry(signature)
    if mean_hsi:
        with st.spinner("Averaging habitat over each segment's modeled flow curve…"):
            summary = cached_full_curve_summary(json.dumps(curve, sort_keys=True), signature)
        monthly = None
    else:
        with st.spinner("Evaluating monthly habitat with GIFT…"):
            monthly = cached_monthly(json.dumps(curve, sort_keys=True), signature)
            summary = aggregate_period(monthly, months, require_all)
    workspace(network, curve, monthly, summary, months, cfs, signature, mean_hsi)
    methods(network)


if __name__ == "__main__":
    try:
        main()
    except RBackendError as exc:
        st.error(f"Original R GIFT could not run: {exc}")
        st.info("Install R and run setup_r.R in R, then check_r.py in VSCode. Set RSCRIPT_PATH in settings.py if needed.")
        st.stop()
