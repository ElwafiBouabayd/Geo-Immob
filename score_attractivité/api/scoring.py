"""
Module de calcul du score d'attractivite residentielle — Casablanca v2.

4 dimensions :
  Dynamisme      (35%) : prix_m2_moyen, nb_annonces
  Socioeco       (25%) : 5 variables HCP
  Accessibilite  (20%) : distance centre-ville, temps, transports
  Equipements    (20%) : POI OSM (ecoles, sante, commerces, …)

Normalisation : MinMax variable par variable (inversion si signe negatif),
puis combinaison ponderee → remise a [0, 100] sur l'echelle des zones existantes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from math import atan2, cos, radians, sin, sqrt
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Dimensions :  variable → (signe, poids_intra_dimension)
# ─────────────────────────────────────────────────────────────────────────────
DIMENSIONS: Dict[str, Dict[str, Tuple[str, float]]] = {
    "Dynamisme": {
        "prix_m2_moyen": ("pos", 0.60),
        "nb_annonces":   ("pos", 0.40),
    },
    "Socioeco": {
        "Densite_pop_km2":                          ("pos", 0.15),
        "Taille_de_menage":                         ("neg", 0.20),
        "Taux_activite":                            ("pos", 0.25),
        "Part_salaries_parmi_actifs":               ("pos", 0.20),
        "Part_population_niveau_etudes_superieur":  ("pos", 0.20),
    },
    "Accessibilite": {
        "distance_vol_oiseau_km":   ("neg", 0.30),
        "temps_voiture_estime_min": ("neg", 0.30),
        "nb_arrets_bus_500m":       ("pos", 0.20),
        "nb_arrets_tramway_500m":   ("pos", 0.15),
        "nb_taxis_500m":            ("pos", 0.05),
    },
    "Equipements": {
        "nb_ecoles_1km":       ("pos", 0.20),
        "nb_sante_2km":        ("pos", 0.20),
        "nb_pharmacies_500m":  ("pos", 0.15),
        "nb_commerce_500m":    ("pos", 0.15),
        "nb_banques_1km":      ("pos", 0.15),
        "nb_mosquees_500m":    ("pos", 0.10),
        "nb_restaurants_500m": ("pos", 0.05),
    },
}

WEIGHTS: Dict[str, float] = {
    "Dynamisme":    0.35,
    "Socioeco":     0.25,
    "Accessibilite":0.20,
    "Equipements":  0.20,
}

ALL_VARS: List[str] = [v for d in DIMENSIONS.values() for v in d]

DIMENSIONS_VAR_SIGN: Dict[str, str] = {
    v: sign for d in DIMENSIONS.values() for v, (sign, _) in d.items()
}

# ─────────────────────────────────────────────────────────────────────────────
# Chemins
# ─────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # dossier scoring/

_UPLOADS_FALLBACK = os.path.join(
    os.path.expandvars(r"%APPDATA%"),
    r"Claude\local-agent-mode-sessions"
    r"\4103bc82-d774-43a4-9fc0-c62fad4e5874"
    r"\c6ecfecd-958e-45bc-a0b7-0120f68de213"
    r"\local_411d6805-f5e3-4df0-a751-593f0b2572e3\uploads",
)

OSM_PATH      = os.path.join(BASE_DIR, "osm_zones_data.csv")
ANNONCES_PATH = os.path.join(BASE_DIR, "data_annonces.csv")
HCP_PATH      = os.path.join(BASE_DIR, "data_hcp.csv")

# Mapping arrondissement Zones.csv → commune data_hcp.csv
ARROND_MAPPING: Dict[str, str] = {
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

# Colonnes HCP utilisees (noms normalises sans accents pour l'API)
HCP_RAW_TO_CLEAN = {
    "Densite_pop_km2":                          "Densite_pop_km2",
    "Taille_de_ménage":                         "Taille_de_menage",
    "Taux_activité":                            "Taux_activite",
    "Part_salariés_parmi_actifs":               "Part_salaries_parmi_actifs",
    "Part_population_niveau_études_supérieur":  "Part_population_niveau_etudes_superieur",
}

# ─────────────────────────────────────────────────────────────────────────────
# Chargements internes
# ─────────────────────────────────────────────────────────────────────────────

def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * R * atan2(sqrt(a), sqrt(1 - a))


def _find_zones_csv() -> pd.DataFrame:
    """Trouve Zones.csv avec Code Zone, Zone, Arrondissement."""
    candidates = [
        os.path.join(BASE_DIR, "Zones.csv"),
        os.path.join(_UPLOADS_FALLBACK, "Zones.csv"),
    ]
    for path in candidates:
        if os.path.exists(path):
            df = pd.read_csv(path)
            df.columns = df.columns.str.strip()
            if "Arrondissement" in df.columns:
                return df
    raise FileNotFoundError(
        "Zones.csv introuvable ou sans colonne 'Arrondissement'. "
        "Placez le fichier dans le dossier scoring/."
    )


@lru_cache(maxsize=1)
def load_zone_data() -> pd.DataFrame:
    """
    Assemble un DataFrame zone-level avec toutes les variables brutes.
    Index : Code Zone.
    """
    # 1. OSM data
    osm = pd.read_csv(OSM_PATH)
    osm.columns = osm.columns.str.strip()

    # Variables derivees
    osm["nb_sante_2km"]    = osm["nb_hopitaux_2km"].fillna(0) + osm["nb_cliniques_2km"].fillna(0)
    osm["nb_commerce_500m"] = osm["nb_supermarches_500m"].fillna(0) + osm["nb_epiceries_500m"].fillna(0)

    # 2. Zones meta (nom + arrondissement)
    zones_meta = _find_zones_csv()
    osm = osm.merge(
        zones_meta[["Code Zone", "Zone", "Arrondissement"]],
        on="Code Zone", how="left",
    )

    # 3. HCP (normalise noms colonnes)
    hcp_raw = pd.read_csv(HCP_PATH)
    hcp_raw.columns = hcp_raw.columns.str.strip()
    hcp = hcp_raw[["commune"] + list(HCP_RAW_TO_CLEAN.keys())].copy()
    hcp = hcp.rename(columns=HCP_RAW_TO_CLEAN)

    osm["commune_hcp"] = osm["Arrondissement"].map(ARROND_MAPPING)
    osm = osm.merge(hcp, left_on="commune_hcp", right_on="commune", how="left")
    osm = osm.drop(columns=["commune"], errors="ignore")

    # 4. Dynamisme : agreger annonces par quartier, puis joindre par Haversine
    ann = pd.read_csv(ANNONCES_PATH)
    ann.columns = ann.columns.str.strip()
    agg = ann.groupby("quartier").agg(
        lat_q         = ("latitude",  "mean"),
        lng_q         = ("longitude", "mean"),
        prix_m2_moyen = ("prix_m2",   "mean"),
        nb_annonces   = ("prix_m2",   "count"),
    ).reset_index()

    def nearest_quartier(lat: float, lng: float) -> str:
        dists = agg.apply(
            lambda r: _haversine(lat, lng, r["lat_q"], r["lng_q"]), axis=1
        )
        return agg.loc[dists.idxmin(), "quartier"]

    osm["quartier_proche"] = osm.apply(
        lambda r: nearest_quartier(r["lat"], r["lng"]), axis=1
    )
    osm = osm.merge(
        agg[["quartier", "prix_m2_moyen", "nb_annonces"]],
        left_on="quartier_proche", right_on="quartier", how="left",
    ).drop(columns=["quartier"], errors="ignore")

    return osm.reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# Normalisation
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _minmax_bounds() -> Dict[str, Tuple[float, float]]:
    df = load_zone_data()
    bounds: Dict[str, Tuple[float, float]] = {}
    for v in ALL_VARS:
        if v not in df.columns:
            bounds[v] = (0.0, 1.0)
            continue
        col = df[v].dropna().astype(float)
        bounds[v] = (float(col.min()), float(col.max()))
    return bounds


def _normalize(var: str, value: float, clip: bool = True) -> float:
    lo, hi = _minmax_bounds()[var]
    rng = hi - lo if hi > lo else 1.0
    x = (float(value) - lo) / rng
    if clip:
        x = max(0.0, min(1.0, x))
    sign, _ = DIMENSIONS_VAR_SIGN[var], None
    if DIMENSIONS_VAR_SIGN[var] == "neg":
        x = 1.0 - x
    return x


@lru_cache(maxsize=1)
def _raw_score_bounds() -> Tuple[float, float]:
    sub = compute_subscores_table()
    w = np.array([WEIGHTS[d] for d in DIMENSIONS])
    raw = sub[[f"raw_{d}" for d in DIMENSIONS]].values @ w
    return float(raw.min()), float(raw.max())


# ─────────────────────────────────────────────────────────────────────────────
# Calcul des sous-scores
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def compute_subscores_table() -> pd.DataFrame:
    df = load_zone_data()
    out = pd.DataFrame({"Code Zone": df["Code Zone"]})

    for dim, var_dict in DIMENSIONS.items():
        dim_score = np.zeros(len(df))
        for var, (sign, w_intra) in var_dict.items():
            if var not in df.columns:
                continue
            col = df[var].fillna(0).astype(float).values
            lo, hi = _minmax_bounds()[var]
            rng = hi - lo if hi > lo else 1.0
            x = (col - lo) / rng
            x = np.clip(x, 0, 1)
            if sign == "neg":
                x = 1.0 - x
            dim_score += x * w_intra
        out[f"raw_{dim}"] = dim_score

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Fonctions publiques
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def all_zone_scores() -> pd.DataFrame:
    """Table complete des scores par zone, normalisee [0-100], triee par rang."""
    df   = load_zone_data()
    sub  = compute_subscores_table()
    w    = np.array([WEIGHTS[d] for d in DIMENSIONS])
    raw  = sub[[f"raw_{d}" for d in DIMENSIONS]].values @ w
    lo, hi = float(raw.min()), float(raw.max())
    rng  = hi - lo if hi > lo else 1.0
    score_100 = (raw - lo) / rng * 100.0

    # Sous-scores normalises [0-100]
    sub_norm = {}
    for d in DIMENSIONS:
        col = sub[f"raw_{d}"].values
        lo_d, hi_d = float(col.min()), float(col.max())
        rng_d = hi_d - lo_d if hi_d > lo_d else 1.0
        sub_norm[f"Score_{d}"] = ((col - lo_d) / rng_d * 100).round(1)

    result = pd.DataFrame({
        "Code Zone":       df["Code Zone"],
        "Zone":            df.get("Zone",            pd.Series([""] * len(df))),
        "Arrondissement":  df.get("Arrondissement",  pd.Series([""] * len(df))),
        "lat":             df.get("lat",             pd.Series([None] * len(df))),
        "lng":             df.get("lng",             pd.Series([None] * len(df))),
        **sub_norm,
        "Score_Attractivite": score_100.round(1),
        "prix_m2_moyen":  df.get("prix_m2_moyen", pd.Series([None] * len(df))).round(0),
    }).sort_values("Score_Attractivite", ascending=False).reset_index(drop=True)

    result.insert(0, "Rang", np.arange(1, len(result) + 1))
    return result


def score_from_variables(values: Dict[str, float]) -> Dict[str, float]:
    """
    Calcule le score pour un jeu de variables custom.
    Normalisation basee sur les bornes des zones existantes.
    """
    missing = [v for v in ALL_VARS if v not in values]
    if missing:
        raise ValueError(f"Variables manquantes : {missing}")

    sub_scores: Dict[str, float] = {}
    for dim, var_dict in DIMENSIONS.items():
        dim_val = sum(
            _normalize(var, values[var]) * w_intra
            for var, (_, w_intra) in var_dict.items()
        )
        sub_scores[dim] = float(dim_val)

    raw = sum(WEIGHTS[d] * sub_scores[d] for d in DIMENSIONS)
    lo, hi = _raw_score_bounds()
    rng = hi - lo if hi > lo else 1.0
    score_100 = max(0.0, min(100.0, (raw - lo) / rng * 100.0))

    result = {f"Score_{d}": round(sub_scores[d] * 100, 1) for d in DIMENSIONS}
    result["Score_Attractivite"] = round(score_100, 1)
    return result


def get_zone_features(code_zone: str) -> Dict[str, float]:
    """Variables brutes d'une zone (pour pre-remplir le formulaire)."""
    df = load_zone_data()
    row = df[df["Code Zone"] == code_zone]
    if row.empty:
        raise KeyError(f"Zone introuvable : {code_zone}")
    return {
        v: round(float(row.iloc[0][v]), 4) if v in row.columns and not pd.isna(row.iloc[0][v]) else 0.0
        for v in ALL_VARS
    }


def load_coordinates() -> pd.DataFrame:
    """Coordonnees lat/lng par zone (depuis osm_zones_data.csv)."""
    try:
        df = pd.read_csv(OSM_PATH)
        df.columns = df.columns.str.strip()
        if "lat" in df.columns and "lng" in df.columns:
            return df[["Code Zone", "lat", "lng"]]
    except Exception:
        pass
    return pd.DataFrame(columns=["Code Zone", "lat", "lng"])
