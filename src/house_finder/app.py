"""Streamlit GUI: filter Danish areas by distance to water, harbours, nature and stations.

Run through ``asgi.py`` (``uv run house-finder``) so the map tile route is available.
"""

from __future__ import annotations

import folium
import numpy as np
import pandas as pd
import streamlit as st
from folium.plugins import FastMarkerCluster, MarkerCluster
from streamlit_folium import st_folium

from house_finder import config, houses
from house_finder.data import data_ready, get_data
from house_finder.tiles import encode_style, tile_url

st.set_page_config(page_title="House Finder Denmark", page_icon=":material/home:",
                   layout="wide")

if not data_ready():
    st.error(
        "Map data has not been built yet or needs rebuilding for the current criteria. "
        "Run `uv run house-finder-build` in the project folder (downloads ~500 MB of "
        "OpenStreetMap data if it is not already present, then takes a few minutes), "
        "then reload this page."
    )
    st.stop()

data = get_data()

PRICE_CAP = 20.0  # million kr.; slider top = no limit
AREA_CAP = 400  # m²
DEFAULT_TYPES = {"villa", "terraced house", "villa apartment", "condo", "cooperative",
                 "holiday house", "farm", "hobby farm"}


@st.cache_data(show_spinner="Loading listings...", max_entries=1)
def houses_frame(fetched: str) -> pd.DataFrame:
    """Listings as a DataFrame plus their grid cell (``fetched`` keys the cache)."""
    df = pd.DataFrame(houses.load_houses()["houses"])
    rows, cols, valid = data.cells_of(df.lat.to_numpy(), df.lon.to_numpy())
    return df.assign(row=rows, col=cols, valid=valid)

# ---------------------------------------------------------------- sidebar --
sb = st.sidebar
sb.title("House Finder Denmark")
sb.caption("Highlights land within the chosen straight-line distances of every enabled criterion.")

criteria = []  # (label, tuple of layer names, max distance in metres)

sb.subheader("Water")
if sb.checkbox("Near water", value=True):
    water_types = sb.multiselect("Water types", ["Coast / sea", "Lakes"],
                                 default=["Coast / sea", "Lakes"])
    lake_ha = sb.select_slider("Minimum lake size (ha)", options=list(config.LAKE_MIN_AREAS_HA),
                               value=20, disabled="Lakes" not in water_types)
    water_km = sb.slider("Max distance to water (km)", 0.1, 20.0, 1.0, 0.1)
    layers = (["coast"] if "Coast / sea" in water_types else []) + \
             ([f"lake_{lake_ha}"] if "Lakes" in water_types else [])
    if layers:
        criteria.append(("Water", tuple(layers), water_km * 1000))
    else:
        sb.warning("Select at least one water type, or untick 'Near water'.")

sb.subheader("Harbour")
if sb.checkbox("Near a harbour / marina", value=True):
    harbour_km = sb.slider("Max distance to harbour (km)", 0.1, 30.0, 5.0, 0.1)
    criteria.append(("Harbour", ("harbour",), harbour_km * 1000))

sb.subheader("Nature")
if sb.checkbox("Near nature", value=True):
    nature_types = sb.multiselect("Nature types", list(config.NATURE_TYPES),
                                  default=list(config.NATURE_TYPES),
                                  format_func=config.NATURE_TYPES.get)
    nature_km = sb.slider("Max distance to nature (km)", 0.1, 20.0, 1.0, 0.1)
    if nature_types:
        criteria.append(("Nature", tuple(f"nature_{t}" for t in nature_types),
                         nature_km * 1000))
    else:
        sb.warning("Select at least one nature type, or untick 'Near nature'.")

sb.subheader("Train station")
if sb.checkbox("Near a rail station", value=True):
    station_types = sb.multiselect("Station types", list(config.STATION_TYPES),
                                   default=list(config.STATION_TYPES),
                                   format_func=config.STATION_TYPES.get)
    station_km = sb.slider("Max distance to station (km)", 0.1, 30.0, 2.0, 0.1)
    if station_types:
        criteria.append(("Station", tuple(f"station_{t}" for t in station_types),
                         station_km * 1000))
    else:
        sb.warning("Select at least one station type, or untick 'Near a rail station'.")

sb.subheader("Grocery shopping")
if sb.checkbox("Near a grocery store", value=False):
    grocery_km = sb.slider("Max distance to grocery shopping (km)", 0.1, 20.0, 2.0, 0.1)
    criteria.append(("Grocery shopping", ("grocery",), grocery_km * 1000))

sb.subheader("Institutions")
if sb.checkbox("Near an institution", value=False):
    institution_types = sb.multiselect(
        "Institution types", list(config.INSTITUTION_TYPES),
        default=list(config.INSTITUTION_TYPES),
        format_func=config.INSTITUTION_TYPES.get,
    )
    institution_km = sb.slider("Max distance to institution (km)", 0.1, 30.0, 2.0, 0.1)
    if institution_types:
        criteria.append((
            "Institution",
            tuple(f"institution_{kind}" for kind in institution_types),
            institution_km * 1000,
        ))
    else:
        sb.warning("Select at least one institution type, or untick 'Near an institution'.")

sb.subheader("Houses for sale")
house_data = houses.load_houses()
show_houses = sb.toggle("Show houses in matching areas", value=house_data is not None)
if house_data is None:
    sb.caption("No listings downloaded yet.")
else:
    hrs = houses.age(house_data).total_seconds() / 3600
    sb.caption(f"{len(house_data['houses']):,} listings, downloaded {hrs:.1f} h ago.")
fresh = houses.is_fresh(house_data)
if sb.button("Refresh listings" if house_data else "Download listings",
             icon=":material/download:", disabled=fresh,
             help=(f"Listings are re-used for {config.HOUSES_MAX_AGE_HOURS} h to keep the "
                   "load on Boligsiden low." if fresh else
                   "Downloads all current listings (~200 MB, takes ~6 minutes).")):
    with sb.status("Downloading listings from Boligsiden...", expanded=True) as status:
        bar = st.progress(0.0)
        line = st.empty()

        def report(msg, frac=None):
            line.caption(msg)
            if frac is not None:
                bar.progress(frac)

        try:
            house_data = houses.fetch_houses(report)
        except houses.HouseFetchError as e:
            status.update(label="Download failed", state="error")
            st.error(str(e))
        else:
            status.update(label=f"Downloaded {len(house_data['houses']):,} listings",
                          state="complete", expanded=False)
            st.rerun()

type_counts = houses_frame(house_data["fetched"]).type.value_counts() if house_data else {}
if house_data:
    all_types = sorted(type_counts.index, key=lambda t: -type_counts[t])
    sel_types = sb.multiselect(
        "Property types", all_types,
        default=[t for t in all_types if t in DEFAULT_TYPES],
        format_func=lambda t: f"{houses.PROPERTY_TYPES.get(t, t.capitalize())} ({type_counts[t]:,})",
    )
    price_rng = sb.slider("Price (million kr.)", 0.0, PRICE_CAP, (0.0, PRICE_CAP), 0.25,
                          help=f"{PRICE_CAP:g} at the top means no upper limit.")
    area_rng = sb.slider("Living area (m²)", 0, AREA_CAP, (0, AREA_CAP), 10,
                         help=f"{AREA_CAP} at the top means no upper limit.")
    min_rooms = sb.slider("Minimum rooms", 0, 8, 0)
    sb.caption("Listings via Boligsiden's unofficial API, for personal use only. "
               "© Boligsiden and the listing realtors.")

sb.subheader("Display")
highlight = sb.color_picker("Highlight colour", "#FF00C8")
opacity = sb.slider("Highlight opacity", 0.2, 1.0, 0.7, 0.05)
dim_other = sb.toggle("Dim non-matching land", value=True)
show_stations = sb.checkbox("Show stations", value=True)
show_harbours = sb.checkbox("Show harbours", value=False)

key = tuple((layers, float(m)) for _, layers, m in criteria)

shown = None  # filtered listings inside matching areas
if house_data and show_houses:
    df = houses_frame(house_data["fetched"])
    match_mask = data.match(key)
    keep = df.valid.to_numpy() & match_mask[df.row.to_numpy(), df.col.to_numpy()]
    keep &= df.type.isin(sel_types).to_numpy()

    def in_range(col, lo, hi, cap):
        """Missing values only pass while the slider is at its full range."""
        v = df[col]
        full = lo <= 0 and hi >= cap
        return ((v >= lo) & ((v <= hi) | (hi >= cap))).fillna(False).to_numpy() | (
            v.isna().to_numpy() & full)

    keep &= in_range("price", price_rng[0] * 1e6, price_rng[1] * 1e6, PRICE_CAP * 1e6)
    keep &= in_range("living_m2", area_rng[0], area_rng[1], AREA_CAP)
    keep &= in_range("rooms", min_rooms, AREA_CAP, AREA_CAP)
    shown = df[keep].sort_values("price")

# ----------------------------------------------------------------- header --
st.title("Areas in Denmark matching your criteria")
with st.spinner("Computing matching areas..."):
    match_km2 = data.match_area_km2(key)
c1, c2, c3 = st.columns(3)
c1.metric("Matching area", f"{match_km2:,.0f} km²", border=True)
c2.metric("Share of Danish land", f"{100 * match_km2 / data.land_area_km2:.2f} %", border=True)
if shown is not None:
    c3.metric("Houses for sale in matching areas", f"{len(shown):,}", border=True)
else:
    c3.metric("Active criteria", len(criteria), border=True)
if not criteria:
    st.info("No criteria enabled – all Danish land is highlighted.")


# -------------------------------------------------------------------- map --
def base_map(show_stations: bool, show_harbours: bool) -> folium.Map:
    m = folium.Map(location=[56.0, 10.8], zoom_start=7, tiles="OpenStreetMap",
                   control_scale=True)
    colours = {"train": "#d62728", "metro": "#1f3a93", "light_rail": "#ff7f0e"}
    if show_stations:
        for st_type, label in config.STATION_TYPES.items():
            fg = folium.FeatureGroup(name=f"Stations: {label}")
            for lat, lon, name in data.markers.get(f"station_{st_type}", []):
                folium.CircleMarker(
                    [lat, lon], radius=4, color="white", weight=1, fill=True,
                    fill_color=colours[st_type], fill_opacity=1.0,
                    tooltip=f"{name or 'Station'} ({label})",
                ).add_to(fg)
            fg.add_to(m)
    if show_harbours:
        fg = folium.FeatureGroup(name="Harbours & marinas")
        for lat, lon, name in data.markers.get("harbour", []):
            folium.CircleMarker([lat, lon], radius=4, color="white", weight=1, fill=True,
                                fill_color="#1f77b4", fill_opacity=1.0,
                                tooltip=name or "Harbour / marina").add_to(fg)
        fg.add_to(m)
    # Load the marker-cluster plugin up front: the houses layer is injected later.
    MarkerCluster(control=False).add_to(m)
    folium.LayerControl(collapsed=True).add_to(m)
    return m


# JS callback building one house marker; row = [lat, lon, address, type, price, m²,
# rooms, year, energy, image, boligsiden url, realtor url].
HOUSE_MARKER_JS = """
function (row) {
    var esc = function (s) { return String(s).replace(/[&<>"']/g, function (c) {
        return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); };
    var safe = function (u) { return /^https:\\/\\//.test(u || '') ? esc(u) : null; };
    var kr = function (v) { return v == null ? '?' : Number(v).toLocaleString('da-DK') + ' kr.'; };
    var facts = [row[3], row[5] ? row[5] + ' m²' : null, row[6] ? row[6] + ' rooms' : null,
                 row[7] ? 'built ' + row[7] : null, row[8] ? 'energy ' + row[8] : null]
                .filter(function (x) { return x; }).map(esc).join(' · ');
    var img = safe(row[9]), bs = safe(row[10]), rt = safe(row[11]);
    var html = '<div style="width:260px;font-family:sans-serif">' +
        (img ? '<img src="' + img + '" style="width:260px;height:173px;object-fit:cover;border-radius:4px">' : '') +
        '<div style="font-weight:600;font-size:15px;margin-top:4px">' + kr(row[4]) + '</div>' +
        '<div>' + esc(row[2]) + '</div><div style="color:#555;font-size:12px">' + facts + '</div>' +
        '<div style="margin-top:4px">' +
        (bs ? '<a href="' + bs + '" target="_blank" rel="noopener">Boligsiden</a>' : '') +
        (rt ? ' · <a href="' + rt + '" target="_blank" rel="noopener">Realtor</a>' : '') +
        '</div></div>';
    var m = L.circleMarker(new L.LatLng(row[0], row[1]), {radius: 7, color: '#000',
        weight: 1.5, fillColor: '#ffd400', fillOpacity: 1});
    m.bindTooltip(esc(kr(row[4]) + ' – ' + row[2]));
    m.bindPopup(html, {maxWidth: 280});
    return m;
}
"""

HOUSE_COLS = ["lat", "lon", "address", "type_label", "price", "living_m2", "rooms",
              "year_built", "energy", "image", "boligsiden_url", "realtor_url"]


def house_rows(frame: pd.DataFrame) -> list[list]:
    f = frame.assign(type_label=frame.type.map(
        lambda t: houses.PROPERTY_TYPES.get(t, str(t).capitalize())))[HOUSE_COLS]
    f = f.astype(object).where(f.notna(), None)
    return [[v.item() if hasattr(v, "item") else v for v in r] for r in f.itertuples(index=False)]


overlay = folium.FeatureGroup(name="Matching areas")
folium.TileLayer(
    tiles=tile_url(key, encode_style(highlight, opacity, dim_other)),
    attr="House Finder", name="Matching areas", overlay=True, control=False,
    max_zoom=19, z_index=5,
).add_to(overlay)
if shown is not None and len(shown):
    FastMarkerCluster(
        house_rows(shown), callback=HOUSE_MARKER_JS, name="Houses for sale",
        disable_clustering_at_zoom=15, max_cluster_radius=50, chunked_loading=True,
    ).add_to(overlay)

map_col, info_col = st.columns([3, 1])
with map_col:
    result = st_folium(
        base_map(show_stations, show_harbours),
        feature_group_to_add=overlay,
        key=f"map_{show_stations}_{show_harbours}",
        height=720,
        use_container_width=True,
        returned_objects=["last_clicked"],
    )

LAYER_LABELS = {
    "coast": "Coast / sea",
    **{f"lake_{ha}": f"Lake ≥ {ha} ha" for ha in config.LAKE_MIN_AREAS_HA},
    "harbour": "Harbour / marina",
    "grocery": "Grocery shopping",
    **{f"nature_{k}": v for k, v in config.NATURE_TYPES.items()},
    **{f"station_{k}": f"Station: {v}" for k, v in config.STATION_TYPES.items()},
    **{f"institution_{k}": f"Institution: {v}" for k, v in config.INSTITUTION_TYPES.items()},
}


def fmt_m(m: int) -> str:
    if m >= config.MAX_STORED_DISTANCE:
        return f"> {config.MAX_STORED_DISTANCE / 1000:.0f} km"
    return f"{m:,} m" if m < 1000 else f"{m / 1000:.1f} km"


with info_col:
    st.subheader("Point details")
    clicked = (result or {}).get("last_clicked")
    if not clicked:
        st.caption("Click anywhere on the map to see distances from that spot.")
    else:
        lat, lon = clicked["lat"], clicked["lng"]
        st.caption(f"{lat:.5f}, {lon:.5f}")
        cell = data.cell_of(lat, lon)
        if cell is None or not data.land[cell]:
            st.warning("Not on Danish land.")
        else:
            r, c = cell
            for label, layers, max_m in criteria:
                d = int(min(data.layers[n][r, c] for n in layers))
                ok = "✅" if d <= max_m else "❌"
                st.write(f"{ok} **{label}**: {fmt_m(d)} (limit {fmt_m(int(max_m))})")
            with st.expander("All distances", expanded=not criteria):
                for name, label in LAYER_LABELS.items():
                    st.write(f"{label}: {fmt_m(int(data.layers[name][r, c]))}")

if shown is not None:
    st.subheader(f"Houses for sale in matching areas ({len(shown):,})")
    if not len(shown):
        st.info("No listings match. Widen the distances or relax the house filters.")
    else:
        rr, cc = shown.row.to_numpy(), shown.col.to_numpy()
        table = pd.DataFrame({
            "Photo": shown.image,
            "Address": shown.address,
            "Type": shown.type.map(lambda t: houses.PROPERTY_TYPES.get(t, str(t).capitalize())),
            "Price (kr.)": shown.price,
            "m²": shown.living_m2,
            "Rooms": shown.rooms,
            "Built": shown.year_built,
            "Energy": shown.energy,
        })
        for label, layers, _ in criteria:
            table[f"{label} (m)"] = np.min([data.layers[n][rr, cc] for n in layers], axis=0)
        table["Boligsiden"] = shown.boligsiden_url
        table["Realtor"] = shown.realtor_url
        st.dataframe(
            table, hide_index=True, height=480, row_height=60,
            column_config={
                "Photo": st.column_config.ImageColumn(width="small"),
                "Price (kr.)": st.column_config.NumberColumn(format="localized"),
                "Built": st.column_config.NumberColumn(format="%d"),
                "Boligsiden": st.column_config.LinkColumn(display_text="Open"),
                "Realtor": st.column_config.LinkColumn(display_text="Open"),
            },
        )
        st.caption("Click a yellow marker on the map for a photo and links. "
                   "Listings © Boligsiden and the listing realtors.")

st.caption(
    "Data © OpenStreetMap contributors (ODbL). Distances are straight-line on a ~90 m grid, "
    "so values are accurate to roughly ±100 m."
)
