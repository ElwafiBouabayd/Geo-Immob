# -*- coding: utf-8 -*-
"""
Geomarketing Casablanca - Application Streamlit
Carte interactive avec evaluation par parcelle.
Adaptee au schema parcelles_features.csv (32 variables cibles).
"""

import streamlit as st
import pandas as pd
import numpy as np
import folium
from streamlit_folium import st_folium
from pathlib import Path

CSV_PATH = "parcelles_features.csv"

PALETTE = ['#d73027', '#f46d43', '#fdae61', '#fee08b',
           '#d9ef8b', '#a6d96a', '#66bd63', '#1a9850']

PRIMARY = "#1f4e78"
ACCENT  = "#2e75b6"
DANGER  = "#c0392b"
SUCCESS = "#27ae60"
WARN    = "#e67e22"

DIST_VARS = {
    "dist_education_min_m":      ("Distance education",        "low_good"),
    "dist_ecole":                ("Distance ecole",            "low_good"),
    "dist_banque_min_m":         ("Distance banque",           "low_good"),
    "dist_sante_min_m":          ("Distance sante",            "low_good"),
    "dist_clinique":             ("Distance clinique/hopital", "low_good"),
    "dist_commerce_min_m":       ("Distance commerce",         "low_good"),
    "dist_transport_min_m":      ("Distance transport",        "low_good"),
    "dist_environnement_min_m":  ("Distance espace vert",      "low_good"),
    "dist_parc":                 ("Distance parc",             "low_good"),
    "dist_tram":                 ("Distance tram",             "low_good"),
    "dist_mer":                  ("Distance mer",              "low_good"),
    "dist_nuisance_min_m":       ("Distance nuisance",         "high_good"),
    "dist_autoroute_min_m":      ("Distance autoroute",        "low_good"),
    "dist_voie_primaire_min_m":  ("Distance voie primaire",    "low_good"),
    "dist_voie_secondaire_min_m": ("Distance voie secondaire", "low_good"),
    "dist_boulevard_principal":  ("Distance boulevard",        "low_good"),
}
COUNT_VARS = {
    "nb_ecoles_1km":       ("Ecoles 1 km",         "high_good"),
    "nb_sante_1km":        ("Sante 1 km",          "high_good"),
    "nb_commerces_1km":    ("Commerces 1 km",      "high_good"),
    "nb_banques_1km":      ("Banques 1 km",        "high_good"),
    "nb_stations_1km":     ("Stations 1 km",       "high_good"),
    "nb_restaurants_500m": ("Restaurants 500 m",   "high_good"),
    "nb_axes_500m":        ("Axes routiers 500 m", "high_good"),
    "nb_nuisance_500m":    ("Nuisances 500 m",     "low_good"),
    "nb_nuisance":         ("Nuisances 1 km",      "low_good"),
}
OTHER_VARS = {
    "densite_education":  ("Densite education /km2",   "high_good"),
    "surface_verte_1km":  ("Surface verte 1 km (m2)",  "high_good"),
}
TIME_VARS = {
    "temps_transport_centre": ("Temps centre-ville",  "low_good"),
    "temps_CFC":              ("Temps CFC",           "low_good"),
    "temps_Maarif":           ("Temps Maarif",        "low_good"),
    "temps_SidiMaarouf":      ("Temps Sidi Maarouf",  "low_good"),
    "temps_port":             ("Temps port",          "low_good"),
}
ALL_VARS = {**DIST_VARS, **COUNT_VARS, **OTHER_VARS, **TIME_VARS}


st.set_page_config(
    page_title="Geomarketing Casablanca",
    layout="wide",
    initial_sidebar_state="expanded",
)

CSS = """
<style>
.block-container { padding-top: 1.2rem; padding-bottom: 1rem; }
.app-title { color: __PRIMARY__; font-size: 1.75rem; font-weight: 700; margin: 0; letter-spacing: -0.5px; }
.app-subtitle { color: #6b7280; font-size: 0.85rem; margin-top: 2px; margin-bottom: 1rem; font-weight: 400; }
.header-band { background: linear-gradient(135deg, __PRIMARY__, __ACCENT__); margin: -1.2rem -1rem 1.2rem -1rem; padding: 1.2rem 2rem 1rem 2rem; color: white; }
.header-band .app-title { color: white; }
.header-band .app-subtitle { color: rgba(255,255,255,0.85); margin-bottom: 0; }
[data-testid="stSidebar"] { background: #f9fafb; }
[data-testid="stSidebar"] h1 { color: __PRIMARY__; font-size: 1.1rem; font-weight: 700; border-bottom: 2px solid __ACCENT__; padding-bottom: 0.4rem; }
[data-testid="stSidebar"] h3 { color: #374151; font-size: 0.8rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 1.2rem; margin-bottom: 0.4rem; }
.detail-card { background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 1rem 1.2rem; margin-bottom: 0.7rem; }
.detail-card-title { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.7px; color: #6b7280; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.4rem; margin-bottom: 0.6rem; }
.detail-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.85rem; }
.detail-row .key { color: #6b7280; }
.detail-row .val { color: #111827; font-weight: 600; }
.score-block { margin: 0.5rem 0; }
.score-header { display: flex; justify-content: space-between; font-size: 0.85rem; color: #4b5563; margin-bottom: 0.3rem; }
.score-value { color: __PRIMARY__; font-weight: 700; }
.score-bar { background: #e5e7eb; height: 6px; border-radius: 3px; overflow: hidden; }
.score-bar-fill { height: 100%; transition: width 0.3s; }
[data-testid="stMetricValue"] { color: __PRIMARY__; font-size: 1.3rem; font-weight: 700; }
[data-testid="stMetricLabel"] { font-size: 0.7rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.4px; }
.empty-state { text-align: center; color: #9ca3af; padding: 3rem 1rem; background: #f9fafb; border: 2px dashed #e5e7eb; border-radius: 10px; }
.pill { display: inline-block; padding: 2px 10px; border-radius: 12px; background: #eff6ff; color: __PRIMARY__; font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; }
#MainMenu, footer { visibility: hidden; }
</style>
"""
st.markdown(CSS.replace("__PRIMARY__", PRIMARY).replace("__ACCENT__", ACCENT),
            unsafe_allow_html=True)


def color_for_value(value, vmin, vmax, invert=False):
    if pd.isna(value) or vmax == vmin:
        return '#9ca3af'
    t = (value - vmin) / (vmax - vmin)
    if invert:
        t = 1 - t
    t = max(0, min(1, t))
    return PALETTE[int(t * (len(PALETTE) - 1))]


def fmt_distance(v):
    if pd.isna(v):
        return "-"
    return f"{int(v)} m" if v < 1000 else f"{v/1000:.2f} km"


def fmt_time_min(v):
    if pd.isna(v):
        return "-"
    if v < 1:
        return f"{int(v*60)} s"
    if v < 60:
        return f"{v:.1f} min"
    h = int(v // 60)
    m = int(v % 60)
    return f"{h} h {m:02d}"


def fmt_int(v):
    if pd.isna(v):
        return "-"
    return f"{int(v):,}".replace(",", " ")


def fmt_surface(v):
    if pd.isna(v):
        return "-"
    if v < 10000:
        return f"{int(v):,} m2".replace(",", " ")
    return f"{v/10000:.2f} ha"


@st.cache_data(show_spinner="Chargement des donnees...")
def load_data(path):
    df = pd.read_csv(path)
    df = df.dropna(subset=['lat', 'lon'])
    return df


def render_card(title, body_html):
    html = ('<div class="detail-card">'
            f'<div class="detail-card-title">{title}</div>'
            f'{body_html}'
            '</div>')
    st.markdown(html, unsafe_allow_html=True)


def score_block(label, value_str, pct, color):
    return ('<div class="score-block">'
            '<div class="score-header">'
            f'<span>{label}</span>'
            f'<span class="score-value">{value_str}</span>'
            '</div>'
            '<div class="score-bar">'
            f'<div class="score-bar-fill" style="width:{pct}%; background:{color};"></div>'
            '</div>'
            '</div>')


def detail_row(key, val, color=None):
    style = f' style="color:{color}"' if color else ''
    return ('<div class="detail-row">'
            f'<span class="key">{key}</span>'
            f'<span class="val"{style}>{val}</span>'
            '</div>')


# Header
st.markdown(
    '<div class="header-band">'
    '<div class="app-title">Geomarketing Casablanca</div>'
    '<div class="app-subtitle">Evaluation par parcelle - Distances, accessibilite, temps de trajet</div>'
    '</div>',
    unsafe_allow_html=True,
)

csv_path = Path(CSV_PATH)
if not csv_path.exists():
    st.markdown(
        '<div class="empty-state">'
        f'<h3 style="color:{DANGER}; margin-bottom:0.5rem;">Fichier introuvable</h3>'
        f'<p>Le fichier <code>{CSV_PATH}</code> n\'existe pas dans le dossier courant.</p>'
        '<p>Lance d\'abord <code>python calcul_parcelle_features.py</code>.</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.stop()

df = load_data(CSV_PATH)
present_vars = {k: v for k, v in ALL_VARS.items() if k in df.columns}


with st.sidebar:
    st.markdown("# Parametres")
    st.markdown("### Visualisation")

    default_color = "dist_education_min_m" if "dist_education_min_m" in present_vars \
                    else next(iter(present_vars))

    color_by = st.selectbox(
        "Variable de coloration",
        options=list(present_vars.keys()),
        format_func=lambda k: present_vars[k][0],
        index=list(present_vars.keys()).index(default_color),
        label_visibility="collapsed",
    )
    st.caption("Variable utilisee pour colorer les points")

    st.markdown("### Performance")
    sample_pct = st.select_slider(
        "Echantillon affiche",
        options=[10, 25, 50, 100],
        value=25,
        format_func=lambda x: f"{x} %",
    )
    point_radius = st.slider("Taille des points", 1, 10, 3)

    st.markdown("### Legende")
    orient_lbl = "Bas -> Haut" if present_vars[color_by][1] == "high_good" else "Haut -> Bas"
    legend_parts = [
        f'<div style="font-size:0.75rem;color:#6b7280;margin-bottom:6px">'
        f'Rouge = mauvais, Vert = bon &nbsp;|&nbsp; {orient_lbl}</div>',
        '<div style="line-height:1.6;font-size:0.8rem;">',
    ]
    for col, lbl in zip(['#d73027', '#fdae61', '#a6d96a', '#1a9850'],
                        ['Tres bas', 'Bas', 'Haut', 'Tres haut']):
        legend_parts.append(
            f'<div style="display:flex;align-items:center;gap:8px;">'
            f'<span style="width:12px;height:12px;border-radius:50%;background:{col};"></span>'
            f'<span>{lbl}</span></div>'
        )
    legend_parts.append('</div>')
    st.markdown("".join(legend_parts), unsafe_allow_html=True)

    st.markdown("### Statistiques")
    st.metric("Parcelles totales", f"{len(df):,}".replace(",", " "))
    if "dist_education_min_m" in df.columns:
        st.metric("Dist. ecole moy.", fmt_distance(df["dist_education_min_m"].mean()))
    if "nb_commerces_1km" in df.columns:
        st.metric("Commerces 1 km moy.", f"{df['nb_commerces_1km'].mean():.1f}")
    if "temps_transport_centre" in df.columns:
        st.metric("Temps centre moy.", fmt_time_min(df["temps_transport_centre"].mean()))


sampled = df.sample(frac=sample_pct / 100, random_state=42) if sample_pct < 100 else df
values = sampled[color_by].dropna()
if len(values) == 0:
    vmin, vmax = 0.0, 1.0
else:
    vmin, vmax = float(values.min()), float(values.max())
invert = present_vars[color_by][1] == "low_good"


def tooltip_value(val, var):
    if pd.isna(val):
        return "-"
    if var.startswith("dist_"):
        return fmt_distance(val)
    if var.startswith("temps_"):
        return fmt_time_min(val)
    if var == "surface_verte_1km":
        return fmt_surface(val)
    if var.startswith("nb_") or var == "densite_education":
        return f"{val:.2f}" if not float(val).is_integer() else str(int(val))
    return f"{val:.2f}"


@st.cache_resource(show_spinner="Construction de la carte...")
def build_map(_data, color_col, vmin, vmax, invert, radius, label):
    m = folium.Map(location=[33.575, -7.6], zoom_start=12, tiles='CartoDB positron')
    for _, row in _data.iterrows():
        c = color_for_value(row[color_col], vmin, vmax, invert)
        tip_val = tooltip_value(row[color_col], color_col)
        folium.CircleMarker(
            location=[row['lat'], row['lon']],
            radius=radius,
            color=c, fill=True, fill_color=c,
            fill_opacity=0.75, weight=0,
            tooltip=f"OSM {row['osm_id']} - {label}: {tip_val}",
        ).add_to(m)
    return m


fmap = build_map(sampled, color_by, vmin, vmax, invert,
                 point_radius, present_vars[color_by][0])


col_map, col_info = st.columns([2.2, 1])

with col_map:
    pill = f'{len(sampled):,} parcelles affichees'.replace(",", " ")
    st.markdown(f'<span class="pill">{pill}</span>', unsafe_allow_html=True)
    map_data = st_folium(
        fmap,
        height=620,
        use_container_width=True,
        returned_objects=["last_object_clicked"],
    )

with col_info:
    clicked = map_data.get("last_object_clicked") if map_data else None

    if clicked is None:
        st.markdown(
            '<div class="empty-state">'
            '<h4 style="color:#374151; margin-bottom:0.5rem;">Aucune parcelle selectionnee</h4>'
            '<p style="font-size:0.85rem;">Cliquez sur un point de la carte pour afficher ses caracteristiques geomarketing.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        click_lat, click_lon = clicked['lat'], clicked['lng']
        idx = ((sampled['lat'] - click_lat) ** 2
               + (sampled['lon'] - click_lon) ** 2).idxmin()
        parcel = sampled.loc[idx]

        ident_html = "".join([
            detail_row("OSM ID", parcel['osm_id']),
            detail_row("Type", parcel.get('building_type', '') or '-'),
            detail_row("Surface", f"{parcel.get('surface_m2', 0):,.0f} m2".replace(",", " ")),
            detail_row("Niveaux", parcel.get('levels', '') or '-'),
            detail_row("Adresse", parcel.get('addr_street', '') or '-'),
            detail_row("Coordonnees", f"{parcel['lat']:.5f}, {parcel['lon']:.5f}"),
        ])
        render_card("Identification", ident_html)

        amenity_dists = [
            ("dist_education_min_m",      "Education"),
            ("dist_ecole",                "Ecole"),
            ("dist_banque_min_m",         "Banque"),
            ("dist_sante_min_m",          "Sante"),
            ("dist_clinique",             "Clinique/Hopital"),
            ("dist_commerce_min_m",       "Commerce"),
            ("dist_transport_min_m",      "Transport"),
            ("dist_environnement_min_m",  "Espace vert"),
            ("dist_parc",                 "Parc"),
            ("dist_tram",                 "Tram"),
            ("dist_mer",                  "Mer"),
        ]
        items = []
        for col, lbl in amenity_dists:
            if col in parcel.index and pd.notna(parcel[col]):
                items.append({"lbl": lbl, "v": float(parcel[col])})
        if items:
            items.sort(key=lambda x: x["v"])
            body = "".join(detail_row(it["lbl"], fmt_distance(it["v"]), color=ACCENT)
                           for it in items[:8])
            render_card("Amenites les plus proches", body)

        road_specs = [
            ("dist_autoroute_min_m",       "Autoroute"),
            ("dist_voie_primaire_min_m",   "Voie primaire"),
            ("dist_voie_secondaire_min_m", "Voie secondaire"),
            ("dist_boulevard_principal",   "Boulevard"),
        ]
        road_html_parts = []
        for col, lbl in road_specs:
            if col in parcel.index:
                road_html_parts.append(detail_row(lbl, fmt_distance(parcel[col])))
        if "nb_axes_500m" in parcel.index:
            road_html_parts.append(detail_row("Axes 500 m (nb noeuds)", fmt_int(parcel["nb_axes_500m"])))
        if road_html_parts:
            render_card("Accessibilite routiere", "".join(road_html_parts))

        count_specs = [
            ("nb_ecoles_1km",       "Ecoles 1 km"),
            ("nb_sante_1km",        "Sante 1 km"),
            ("nb_commerces_1km",    "Commerces 1 km"),
            ("nb_banques_1km",      "Banques 1 km"),
            ("nb_stations_1km",     "Stations transport 1 km"),
            ("nb_restaurants_500m", "Restaurants 500 m"),
        ]
        density_items = [(c, l) for c, l in count_specs
                         if c in parcel.index and pd.notna(parcel[c])]
        if density_items:
            max_n = max(int(parcel[c]) for c, _ in density_items) or 1
            blocks = []
            for c, l in density_items:
                n = int(parcel[c])
                pct = (n / max_n) * 100
                blocks.append(score_block(l, str(n), pct, SUCCESS))
            render_card("Densite en buffer", "".join(blocks))

        env_parts = []
        if "surface_verte_1km" in parcel.index:
            env_parts.append(detail_row("Surface verte 1 km",
                                        fmt_surface(parcel["surface_verte_1km"]),
                                        color=SUCCESS))
        if "densite_education" in parcel.index:
            env_parts.append(detail_row("Densite education /km2",
                                        f'{parcel["densite_education"]:.2f}'))
        if env_parts:
            render_card("Environnement", "".join(env_parts))

        nuis_parts = []
        if "dist_nuisance_min_m" in parcel.index:
            nuis_parts.append(detail_row("Nuisance la plus proche",
                                         fmt_distance(parcel["dist_nuisance_min_m"])))
        if "nb_nuisance_500m" in parcel.index:
            nuis_parts.append(detail_row("Nuisances 500 m",
                                         fmt_int(parcel["nb_nuisance_500m"]),
                                         color=DANGER))
        if "nb_nuisance" in parcel.index:
            nuis_parts.append(detail_row("Nuisances 1 km",
                                         fmt_int(parcel["nb_nuisance"]),
                                         color=DANGER))
        if nuis_parts:
            render_card("Nuisances", "".join(nuis_parts))

        time_specs = [
            ("temps_transport_centre", "Centre-ville"),
            ("temps_CFC",              "CFC"),
            ("temps_Maarif",           "Maarif"),
            ("temps_SidiMaarouf",      "Sidi Maarouf"),
            ("temps_port",             "Port"),
        ]
        time_items = [(c, l) for c, l in time_specs
                      if c in parcel.index and pd.notna(parcel[c])]
        if time_items:
            max_t = max(parcel[c] for c, _ in time_items) or 1
            blocks = []
            for c, l in time_items:
                v = float(parcel[c])
                pct = (v / max_t) * 100
                blocks.append(score_block(l, fmt_time_min(v), pct, WARN))
            render_card("Temps de trajet (a 30 km/h)", "".join(blocks))
