"""
Score d'attractivité résidentiel — Casablanca  (v2)
====================================================
Dimensions et poids :
  - Dynamisme du marché immobilier  : 35%  ← data_annonces (prix_m2, volume)
  - Profil socio-économique         : 25%  ← data_hcp (5 variables)
  - Accessibilité et mobilité       : 20%  ← OSM (collect_osm_zones.py)
  - Équipements et services         : 20%  ← Overpass API (collect_osm_zones.py)

Prérequis : pip install pandas openpyxl
"""

import pandas as pd
import numpy as np
from math import radians, sin, cos, sqrt, atan2

# ─────────────────────────────────────────────
# POIDS
# ─────────────────────────────────────────────
POIDS = {
    "dynamisme":     0.35,
    "socioeco":      0.25,
    "accessibilite": 0.20,
    "equipements":   0.20,
}

# ─────────────────────────────────────────────
# CHARGEMENT
# ─────────────────────────────────────────────
BASE    = r"C:\Users\del\Desktop\scoring"

annonces = pd.read_csv(f"{BASE}\\data_annonces.csv")
hcp      = pd.read_csv(f"{BASE}\\data_hcp.csv")
zones    = pd.read_csv(f"{BASE}\\Zones.csv")
osm      = pd.read_csv(f"{BASE}\\osm_zones_data.csv")

annonces.columns = annonces.columns.str.strip()
hcp.columns      = hcp.columns.str.strip()
zones.columns    = zones.columns.str.strip()
osm.columns      = osm.columns.str.strip()

# ─────────────────────────────────────────────
# NORMALISATION
# ─────────────────────────────────────────────

def minmax(s):
    lo, hi = s.min(), s.max()
    if hi == lo:
        return pd.Series(50.0, index=s.index)
    return (s - lo) / (hi - lo) * 100

def minmax_inv(s):
    return 100 - minmax(s)

# ─────────────────────────────────────────────
# DIMENSION 1 — DYNAMISME DU MARCHÉ (35%)
# Variables : prix_m2_moyen, nb_annonces (volume)
# Source    : data_annonces agrégé par quartier → joint par distance Haversine
# ─────────────────────────────────────────────

agg_ann = annonces.groupby("quartier").agg(
    lat_q         = ("latitude",  "mean"),
    lng_q         = ("longitude", "mean"),
    prix_m2_moyen = ("prix_m2",   "mean"),
    nb_annonces   = ("prix_m2",   "count"),
).reset_index()

agg_ann["s_prix"]   = minmax(agg_ann["prix_m2_moyen"])
agg_ann["s_volume"] = minmax(agg_ann["nb_annonces"])

agg_ann["score_dynamisme"] = (
    agg_ann["s_prix"]   * 0.60 +
    agg_ann["s_volume"] * 0.40
).round(2)

# Joindre dynamisme aux zones par quartier le plus proche (Haversine)
def haversine(lat1, lng1, lat2, lng2):
    R = 6371
    dlat, dlng = radians(lat2 - lat1), radians(lng2 - lng1)
    a = sin(dlat/2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng/2)**2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))

osm = osm.copy()
osm["quartier_proche"] = osm.apply(
    lambda r: agg_ann.loc[
        agg_ann.apply(lambda q: haversine(r["lat"], r["lng"], q["lat_q"], q["lng_q"]), axis=1).idxmin(),
        "quartier"
    ], axis=1
)

osm = osm.merge(
    agg_ann[["quartier", "prix_m2_moyen", "nb_annonces", "score_dynamisme"]],
    left_on="quartier_proche", right_on="quartier", how="left"
).drop(columns="quartier")

# ─────────────────────────────────────────────
# DIMENSION 2 — PROFIL SOCIO-ÉCONOMIQUE (25%)
# Variables : Densite_pop_km2, Taille_de_ménage, Taux_activité,
#             Part_salariés_parmi_actifs, Part_population_niveau_études_supérieur
# ─────────────────────────────────────────────

VARS_HCP = [
    "Densite_pop_km2",
    "Taille_de_ménage",
    "Taux_activité",
    "Part_salariés_parmi_actifs",
    "Part_population_niveau_études_supérieur",
]

hcp = hcp[["commune"] + VARS_HCP].copy()

# Normalisation (Taille_de_ménage : plus petit = meilleur niveau de vie)
hcp["s_densite"]  = minmax(hcp["Densite_pop_km2"])
hcp["s_menage"]   = minmax_inv(hcp["Taille_de_ménage"])   # inversé
hcp["s_activite"] = minmax(hcp["Taux_activité"])
hcp["s_salaries"] = minmax(hcp["Part_salariés_parmi_actifs"])
hcp["s_etudes"]   = minmax(hcp["Part_population_niveau_études_supérieur"])

hcp["score_socioeco"] = (
    hcp["s_densite"]  * 0.15 +
    hcp["s_menage"]   * 0.20 +
    hcp["s_activite"] * 0.25 +
    hcp["s_salaries"] * 0.20 +
    hcp["s_etudes"]   * 0.20
).round(2)

# Mapping arrondissement (Zones.csv) → commune (data_hcp.csv)
mapping = {
    "Anfa":           "Anfa",
    "Maârif":         "Maârif",
    "Sidi Belyout":   "Sidi Belyout",
    "Ben M'Sick":     "Ben M'Sick",
    "Sbata":          "Sbata",
    "Sidi Bernoussi": "Sidi Bernoussi",
    "Sidi Moumen":    "Sidi Moumen",
    "Mly Rachid":     "Mly Rachid",
    "Sidi Othman":    "Sidi Othman",
    "Roches Noires":  "Roches Noires",
    "Hay Mohammadi":  "Hay Mohammadi",
    "Ain Sebaâ":      "Ain Sebaâ",
    "Al Fida":        "Al Fida",
    "Mers Sultan":    "Mers Sultan",
    "Ain Chock":      "Ain Chock",
    "Hay Hassani":    "Hay Hassani",
}

# ─────────────────────────────────────────────
# DIMENSION 3 — ACCESSIBILITÉ ET MOBILITÉ (20%)
# ─────────────────────────────────────────────

osm["s_dist_centre"] = minmax_inv(osm["distance_vol_oiseau_km"])
osm["s_tps_voiture"] = minmax_inv(osm["temps_voiture_estime_min"])
osm["s_bus"]         = minmax(osm["nb_arrets_bus_500m"])
osm["s_tram"]        = minmax(osm["nb_arrets_tramway_500m"])
osm["s_taxi"]        = minmax(osm["nb_taxis_500m"])

osm["score_accessibilite"] = (
    osm["s_dist_centre"] * 0.30 +
    osm["s_tps_voiture"] * 0.30 +
    osm["s_bus"]         * 0.20 +
    osm["s_tram"]        * 0.15 +
    osm["s_taxi"]        * 0.05
).round(2)

# ─────────────────────────────────────────────
# DIMENSION 4 — ÉQUIPEMENTS ET SERVICES (20%)
# ─────────────────────────────────────────────

osm["nb_sante_2km"] = osm["nb_hopitaux_2km"].fillna(0) + osm["nb_cliniques_2km"].fillna(0)

osm["s_ecoles"]      = minmax(osm["nb_ecoles_1km"])
osm["s_sante"]       = minmax(osm["nb_sante_2km"])
osm["s_pharmacies"]  = minmax(osm["nb_pharmacies_500m"])
osm["s_commerce"]    = minmax(
    osm["nb_supermarches_500m"].fillna(0) + osm["nb_epiceries_500m"].fillna(0)
)
osm["s_mosquees"]    = minmax(osm["nb_mosquees_500m"])
osm["s_banques"]     = minmax(osm["nb_banques_1km"])
osm["s_restaurants"] = minmax(osm["nb_restaurants_500m"])

osm["score_equipements"] = (
    osm["s_ecoles"]      * 0.20 +
    osm["s_sante"]       * 0.20 +
    osm["s_pharmacies"]  * 0.15 +
    osm["s_commerce"]    * 0.15 +
    osm["s_banques"]     * 0.15 +
    osm["s_mosquees"]    * 0.10 +
    osm["s_restaurants"] * 0.05
).round(2)

# ─────────────────────────────────────────────
# ASSEMBLAGE FINAL
# ─────────────────────────────────────────────

# Joindre zones (nom + arrondissement)
df = osm.merge(zones[["Code Zone", "Zone", "Arrondissement"]], on="Code Zone", how="left")

# Joindre HCP via arrondissement
df["commune_hcp"] = df["Arrondissement"].map(mapping)
df = df.merge(
    hcp[["commune", "score_socioeco"] + VARS_HCP],
    left_on="commune_hcp", right_on="commune", how="left"
).drop(columns="commune")

# Score final pondéré (brut)
df["score_final"] = (
    df["score_dynamisme"]     * POIDS["dynamisme"]     +
    df["score_socioeco"]      * POIDS["socioeco"]      +
    df["score_accessibilite"] * POIDS["accessibilite"] +
    df["score_equipements"]   * POIDS["equipements"]
)

# ─────────────────────────────────────────────
# NORMALISATION FINALE (X - Xmin) / (Xmax - Xmin) × 100
# Appliquée à tous les scores → max = 100, min = 0
# ─────────────────────────────────────────────
SCORES_A_NORMALISER = [
    "score_final",
    "score_dynamisme",
    "score_accessibilite",
    "score_equipements",
    "score_socioeco",
]
for col in SCORES_A_NORMALISER:
    lo, hi = df[col].min(), df[col].max()
    if hi > lo:
        df[col] = ((df[col] - lo) / (hi - lo) * 100).round(1)
    else:
        df[col] = 50.0

df["rang"] = df["score_final"].rank(ascending=False, method="min").astype("Int64")

# ─────────────────────────────────────────────
# EXPORT EXCEL
# ─────────────────────────────────────────────

COLS_EXPORT = [
    "Code Zone", "Zone", "Arrondissement", "lat", "lng",
    "score_final", "rang",
    "score_dynamisme", "score_accessibilite", "score_equipements", "score_socioeco",
    "prix_m2_moyen", "nb_annonces",
    "distance_vol_oiseau_km", "temps_voiture_estime_min",
    "nb_arrets_bus_500m", "nb_arrets_tramway_500m", "nb_taxis_500m",
    "nb_ecoles_1km", "nb_sante_2km", "nb_pharmacies_500m",
    "nb_supermarches_500m", "nb_banques_1km", "nb_mosquees_500m", "nb_restaurants_500m",
    "Densite_pop_km2", "Taille_de_ménage", "Taux_activité",
    "Part_salariés_parmi_actifs", "Part_population_niveau_études_supérieur",
]
COLS_EXPORT = [c for c in COLS_EXPORT if c in df.columns]

resultat = df[COLS_EXPORT].sort_values("rang").round(2)

arr_score = df.groupby("Arrondissement").agg(
    score_final         = ("score_final",         "mean"),
    score_dynamisme     = ("score_dynamisme",      "mean"),
    score_accessibilite = ("score_accessibilite",  "mean"),
    score_equipements   = ("score_equipements",    "mean"),
    score_socioeco      = ("score_socioeco",       "mean"),
    prix_m2_moyen       = ("prix_m2_moyen",        "mean"),
    nb_zones            = ("Code Zone",            "count"),
).round(2).sort_values("score_final", ascending=False).reset_index()

out = r"C:\Users\del\Desktop\scoring\score_attractivite_v2.xlsx"
with pd.ExcelWriter(out, engine="openpyxl") as writer:
    resultat.to_excel(writer, sheet_name="Score par zone",           index=False)
    arr_score.to_excel(writer, sheet_name="Score par arrondissement", index=False)
    resultat.head(20).to_excel(writer, sheet_name="Top 20 zones",    index=False)

print(f"✓ Exporté : {out}")
print(f"\n📊 Résumé")
print(f"   Zones analysées : {len(resultat)}")
print(f"   Score moyen     : {resultat['score_final'].mean():.1f}/100")
print(f"   Score max       : {resultat['score_final'].max():.1f}  — {resultat.iloc[0]['Zone'][:50]}")
print(f"   Score min       : {resultat['score_final'].min():.1f}  — {resultat.iloc[-1]['Zone'][:50]}")

print(f"\n🏆 Top 10 zones")
print(resultat[["Code Zone", "Arrondissement", "score_final",
                "score_dynamisme", "score_accessibilite",
                "score_equipements", "score_socioeco"]].head(10).to_string(index=False))

print(f"\n📋 Score par arrondissement")
print(arr_score.to_string(index=False))
