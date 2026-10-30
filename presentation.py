"""Python map and chart construction; independent of Streamlit session state."""
import html
import json
import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pydeck as pdk
from pydeck.bindings.json_tools import default_serialize

from habitat_engine import CFS_PER_M3S, MONTHS, MONTH_ABBR, STATUS_LABELS

GREEN = "#186354"
INK = "#183e36"
PALETTE = [[233, 241, 219], [157, 206, 170], [68, 151, 121], [20, 94, 78], [8, 52, 45]]
TEMP_PALETTE = [[151, 102, 58], [216, 182, 137], [242, 225, 190],
                [157, 205, 197], [52, 142, 169], [23, 80, 126]]
# Put tan at 80% of the observed maximum, separating the numerous high scores.
TEMP_STOPS = [0., .60, .80, .90, .96, 1.]
PRIORITY_PALETTE = [[241, 229, 211], [222, 176, 125], [197, 117, 69], [142, 64, 37]]
COMPARISON_FIELDS = {
    "Temperature suitability": ("feature", "temp"),
    "Provisional Model priority": ("feature", "priority"),
    "Depth suitability": ("summary", "d"),
    "Velocity suitability": ("summary", "v"),
    "Substrate suitability": ("summary", "s"),
}


class CompactDeck(pdk.Deck):
    """Use PyDeck's serializer without whitespace in the map sent to Streamlit."""

    def to_json(self):
        return json.dumps(self, default=default_serialize, separators=(",", ":"))


def number(value, decimals=3):
    return f"{value:,.{decimals}f}" if value is not None and np.isfinite(value) else "Unavailable"


def period_label(months):
    return MONTHS[months[0] - 1] if len(months) == 1 else f"{MONTH_ABBR[months[0]-1]}–{MONTH_ABBR[months[-1]-1]} median"


def metric_scale(features, summary, layer, metric):
    if layer in ("Temperature", "Temperature Suitability"):
        values = [r["temp"] for r in features]
        title, palette = "Temperature suitability", TEMP_PALETTE
    elif layer in ("Priority", "Provisional Priority"):
        values = [r["priority"] for r in features]
        title, palette = "Provisional Model priority", PRIORITY_PALETTE
    else:
        values = summary[metric].tolist()
        title, palette = {"hsi": "HSI", "wua": "WUA (m²/m)"}[metric], PALETTE
    finite = [v for v in values if v is not None and np.isfinite(v)]
    high = float(max(finite, default=0.0))
    return title, high, palette


def potential_maximum_note(layer, metric):
    """Keep observed color limits separate from theoretical score limits."""
    prefix = "Colors span 0 to the maximum observed value for this map. "
    if layer in ("Priority", "Provisional Priority"):
        return prefix + "A potential maximum is not specified for the supplied priority score."
    if layer in ("Temperature", "Temperature Suitability") or metric == "hsi":
        return prefix + "Potential maximum: 1."
    return prefix + "Potential maximum WUA equals wetted width when HSI = 1; it varies by reach and flow."


def rgb(value, high, palette):
    if value is None or not np.isfinite(value):
        return [157, 162, 159, 165]
    point = np.clip(value / high if high > 0 else 0.0, 0, 1)
    stops = TEMP_STOPS if palette == TEMP_PALETTE else np.linspace(0, 1, len(palette))
    colors = np.asarray(palette)
    return [*[int(round(np.interp(point, stops, colors[:, i]))) for i in range(3)], 245]


def legend_gradient(palette):
    stops = TEMP_STOPS if palette == TEMP_PALETTE else np.linspace(0, 1, len(palette))
    return ",".join("rgb("+",".join(map(str, color))+f") {stop*100:g}%"
                    for color, stop in zip(palette, stops))


def map_records(features, summary, layer, metric, high, palette):
    records = []
    rows = summary.to_dict(orient="index")
    for r in features:
        row = rows[r["id"]]
        value = (r["temp"] if layer in ("Temperature", "Temperature Suitability") else
                 r["priority"] if layer in ("Priority", "Provisional Priority") else row[metric])
        color = rgb(value, high, palette)
        for path in r["coords"]:
            records.append(dict(comid=r["id"], name=html.escape(r["name"]), path=path,
                                color=color, value=number(value),
                                coverage=f"{int(row['validCount'])}/{int(row['selectedCount'])} " +
                                         ("modeled flows" if row.get("aggregation") == "full_curve_mean" else "months"),
                                status=html.escape(STATUS_LABELS.get(row["status"], row["status"]))))
    return records


def map_view(features, selected=None):
    coords = [xy for r in features if selected is None or r["id"] == selected for path in r["coords"] for xy in path]
    a = np.asarray(coords)
    low, high = a.min(axis=0), a.max(axis=0)
    lon, lat = (low + high) / 2
    x_span = max((high[0]-low[0])/360, 1e-8)
    def mercator_y(latitude):
        radians = math.radians(float(np.clip(latitude,-85,85)))
        return (1-math.log(math.tan(radians)+1/math.cos(radians))/math.pi)/2
    y_span = max(abs(mercator_y(high[1])-mercator_y(low[1])), 1e-8)
    zoom = float(np.clip(min(math.log2(700/(512*x_span)), math.log2(530/(512*y_span)))-.35,5,15))
    return pdk.ViewState(longitude=float(lon), latitude=float(lat), zoom=zoom, pitch=0, bearing=0)


def network_map(records, view, selected_id, basemap="Light"):
    layers = [pdk.Layer("PathLayer", id="streams", data=records, get_path="path", get_color="color",
                        get_width=5, width_min_pixels=5, width_max_pixels=5,
                        cap_rounded=True, joint_rounded=True,
                        pickable=True, auto_highlight=True, highlight_color=[250, 197, 67, 230])]
    selected = [r for r in records if r["comid"] == selected_id]
    if selected:
        layers.insert(0, pdk.Layer("PathLayer", id="selected-halo", data=selected,
                                   get_path="path", get_color=[255, 255, 255], get_width=10,
                                   width_min_pixels=10, width_max_pixels=10, pickable=False))
        layers.append(pdk.Layer("PathLayer", id="selected", data=selected, get_path="path",
                                get_color=[235, 172, 43], get_width=6,
                                width_min_pixels=6, width_max_pixels=6, pickable=False))
    if basemap == "Satellite":
        style = "app/static/satellite.json"
    else:
        style = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json" if basemap == "Light" else "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
    return CompactDeck(layers=layers, initial_view_state=view, map_style=style, map_provider="carto",
                    tooltip={"html":
                    "<b>{name}</b><br>COMID {comid}<br>Map value: {value}<br>Habitat: {coverage}<br>{status}",
                    "style": {"backgroundColor": "#183e36", "color": "white"}})


def flow_figure(native, monthly, months, detail_month, metric, cfs=True, mean=None):
    factor, unit = (CFS_PER_M3S, "cfs") if cfs else (1.0, "m³/s")
    title = "WUA (m² per meter of reach)" if metric == "wua" else "HSI"
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for key, label, color, dash in (("d", "Depth suitability", "rgba(99,130,162,.60)", "dot"),
                                     ("v", "Velocity suitability", "rgba(152,130,101,.60)", "dash")):
        if not native.empty:
            fig.add_trace(go.Scatter(x=native.q*factor, y=native[key], name=label,
                                     mode="lines", line=dict(color=color, width=1.5, dash=dash)), secondary_y=True)
    if not native.empty:
        fig.add_trace(go.Scatter(x=native.q*factor, y=native[metric], name="WUA" if metric == "wua" else "HSI",
                                 mode="lines", line=dict(color=GREEN, width=3)), secondary_y=False)
    if mean is None:
        points = monthly[monthly.month.isin(months) & monthly[metric].notna()]
        if not points.empty:
            labels = [MONTHS[int(m)-1] for m in points.month]
            fig.add_trace(go.Scatter(x=points.q*factor, y=points[metric], text=labels, mode="markers",
                                     name="Selected monthly flows", marker=dict(size=9, color="#d79d32",
                                     line=dict(color="white", width=1)),
                                     hovertemplate=f"%{{text}}<br>Flow: %{{x:.3f}} {unit}<br>{title}: %{{y:.4f}}<extra></extra>"), secondary_y=False)
        active = monthly[monthly.month.eq(detail_month)]
        if not active.empty and pd.notna(active.iloc[0].q) and pd.notna(active.iloc[0][metric]):
            row = active.iloc[0]
            fig.add_trace(go.Scatter(x=[row.q*factor], y=[row[metric]], mode="markers", name="Inspected month",
                                     marker=dict(size=14, symbol="circle-open", color=INK, line=dict(width=2))), secondary_y=False)
        # Use all known selected-month flows, including unsupported flows, so the
        # band still explains why a requested period exceeds the response curve.
        selected_flows = monthly.loc[monthly.month.isin(months), "q"].dropna()
        selected_flows = selected_flows[np.isfinite(selected_flows) & selected_flows.ge(0)] * factor
        if not selected_flows.empty:
            low, high = float(selected_flows.min()), float(selected_flows.max())
            if low < high:
                fig.add_vrect(x0=low, x1=high, fillcolor="rgba(215,157,50,0.10)",
                              line_width=0, layer="below")
            else:
                fig.add_vline(x=low, line_color="rgba(180,133,44,0.45)",
                              line_width=1.5, line_dash="dot", layer="below")
    elif np.isfinite(mean.get("q", np.nan)) and np.isfinite(mean.get(metric, np.nan)):
        fig.add_trace(go.Scatter(x=[mean["q"]*factor], y=[mean[metric]], mode="markers",
                                 name="Full-curve mean", marker=dict(size=12, symbol="diamond", color="#d79d32",
                                 line=dict(color="white", width=1)),
                                 hovertemplate=f"Full-curve mean {title}: %{{y:.4f}}<br>"
                                               f"Modeled flow midpoint: %{{x:.3f}} {unit}<extra></extra>"), secondary_y=False)
    fig.update_yaxes(title_text=title, rangemode="tozero", secondary_y=False)
    if metric == "hsi":
        fig.update_yaxes(range=[0, 1], secondary_y=False)
    fig.update_yaxes(title_text="Component suitability", range=[0, 1], showgrid=False, secondary_y=True)
    fig.update_xaxes(title_text=f"Flow ({unit})", rangemode="tozero")
    fig.update_layout(height=370, template="plotly_white", margin=dict(l=15,r=15,t=20,b=15),
                      legend=dict(orientation="h", y=1.18, x=0), hovermode="x unified", font=dict(color=INK))
    return fig


def channel_geometry_figure(reach):
    """Plot the exact cross-section grid used by the GIFT hydraulic simulator."""
    from r_engine import channel_geometry

    width = reach["bfWidth"]
    x, y, maximum = channel_geometry(reach)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines", name="GIFT channel bed",
                             line=dict(color="#85633f", width=3),
                             hovertemplate="Offset: %{x:.3f} m<br>Elevation: %{y:.3f} m<extra></extra>"))
    fig.add_trace(go.Scatter(x=[0, width], y=[0, 0], mode="lines", name="Bankfull water surface",
                             line=dict(color="#347ea9", width=2, dash="dash")))
    fig.update_layout(template="plotly_white", height=390,
                      margin=dict(l=20, r=15, t=20, b=20),
                      xaxis_title="Cross-channel offset (m)",
                      yaxis_title="Elevation relative to bankfull (m)",
                      legend=dict(orientation="h", y=1.16), font=dict(color=INK))
    fig.update_xaxes(range=[-.02*width, 1.02*width])
    fig.update_yaxes(range=[-1.05*maximum, .10*maximum])
    return fig


def suitability_figure(curve, kind):
    """Display continuous coordinates or exact substrate class intervals."""
    values = np.asarray(curve[kind], dtype=float)
    if kind == "substrate":
        x, y = [], []
        for low, high, score in values:
            x.extend([low, high, None])
            y.extend([score, score, None])
        title = "Substrate size (mm)"
    else:
        x, y = values[:, 0], values[:, 1]
        title = "Depth (m)" if kind == "depth" else "Velocity (m/s)"
    fig = go.Figure(go.Scatter(x=x, y=y, mode="lines", line=dict(color=GREEN, width=2),
                              name=kind.capitalize()))
    fig.update_layout(template="plotly_white", height=260, title=kind.capitalize()+" suitability",
                      margin=dict(l=10, r=10, t=40, b=10), xaxis_title=title,
                      yaxis_title="Suitability", yaxis_range=[0, 1.05])
    return fig


def comparison_figure(features, summary, metric, comparison, selected_id, base=None):
    source, field = COMPARISON_FIELDS[comparison]
    x_values = ({r["id"]: r[field] for r in features} if source == "feature"
                else summary[field].to_dict())
    if base is not None:
        spec, count = base
        fig = go.Figure(spec)
    else:
        values = summary[metric].to_dict()
        records = [dict(COMID=r["id"], Stream=r["name"], comparison=x_values[r["id"]], habitat=values[r["id"]]) for r in features]
        frame = pd.DataFrame(records).dropna(subset=["comparison", "habitat"])
        count = len(frame)
        fig = go.Figure(go.Scatter(x=frame.comparison, y=frame.habitat, customdata=frame[["Stream", "COMID"]].values,
                             mode="markers", marker=dict(size=6,color="rgba(24,99,84,.35)"),
                             hovertemplate="%{customdata[0]}<br>COMID %{customdata[1]}<br>Comparison: %{x:.3f}<br>Habitat: %{y:.4f}<extra></extra>",
                             name="Segments"))
        maximum = float(frame.habitat.max()) if not frame.empty else 1.0
        fig.update_yaxes(range=[0, maximum * 1.05 if maximum > 0 else .01])
    active = next((r for r in features if r["id"] == selected_id), None)
    if active is not None and pd.notna(x_values[selected_id]) and pd.notna(summary.at[selected_id, metric]):
        fig.add_trace(go.Scatter(x=[x_values[selected_id]], y=[summary.at[selected_id, metric]],
                                 customdata=[[active["name"], selected_id]],
                                 hovertemplate="%{customdata[0]}<br>COMID %{customdata[1]}<br>Comparison: %{x:.3f}<br>Habitat: %{y:.4f}<extra></extra>",
                                 mode="markers", name="Selected segment",
                                 marker=dict(size=12,color="#d79d32",line=dict(color=INK,width=1))))
    fig.update_layout(template="plotly_white",height=320,margin=dict(l=15,r=15,t=15,b=15),
                      xaxis_title=comparison,yaxis_title={"hsi":"HSI","wua":"WUA (m²/m)"}[metric],
                      clickmode="event+select", dragmode="pan",
                      legend=dict(orientation="h",y=1.15),font=dict(color=INK))
    return fig, count
