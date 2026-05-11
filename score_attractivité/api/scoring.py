"""
Module de calcul du score d'attractivite.

Reprend la methodologie du notebook score_attractivite.ipynb :
  - 4 dimensions : Accessibilite, Amenites, Environnement, SocioDemo
  - Normalisation MinMax variable par variable (inversion si signe negatif)
  - Sous-score = moyenne des variables normalisees de la dimension
  - Score global = combinaison ponderee (poids issus de la regression
    hedonique Ridge -- fichier poids_dimensions.csv) puis remise a [0, 100]
    sur l'echelle des zones existantes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


# -----------------------------------------------------------------------------
# Definition des dimensions (identique au notebook)
# -----------------------------------------------------------------------------
DIMENSIONS: Dict[str, Dict[str, str]] = {
    "Accessibilite": {
        "dist_tram":                  "neg",
        "dist_voie_primaire_min_m":   "neg",
        "dist_voie_secondaire_min_m": "neg",
        "temps_transport_centre":     "neg",
        "temps_CFC":                  "neg",
        "temps_Maarif":               "neg",
        "temps_SidiMaarouf":          "neg",
        "temps_port":                 "neg",
    },
    "Amenites": {
        "nb_ecoles_1km":      "pos",
        "nb_sante_1km":       "pos",
        "nb_commerces_1km":   "pos",
        "nb_restaurants_500m":"pos",
        "nb_banques_1km":     "pos",
    },
    "Environnement": {
        "dist_mer":          "neg",
        "dist_parc":         "neg",
        "surface_verte_1km": "pos",
        "nb_nuisance_500m":  "neg",
    },
    "SocioDemo": {
        "Taux_activité":                             "pos",
        "Part_population_niveau_études_supérieur":   "pos",
        "Taux_chômage":                              "neg",
        "Taux_croissance":                           "pos",
    },
}

ALL_VARS: List[str] = [v for d in DIMENSIONS.values() for v in d]


# -----------------------------------------------------------------------------
# Chemins
# -----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH    = os.path.join(BASE_DIR, "data_finale.csv")
POIDS_PATH   = os.path.join(BASE_DIR, "poids_dimensions.csv")
COORDS_PATH  = os.path.join(BASE_DIR, "coordonnees_zones.csv")


# -----------------------------------------------------------------------------
# Chargements (mis en cache)
# -----------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_zones() -> pd.DataFrame:
    """Agrege data_finale.csv au niveau zone (moyenne des numeriques)."""
    df = pd.read_csv(DATA_PATH)
    df.columns = [c.strip() for c in df.columns]
    df = df[df["Prix Terrain au m² (DH)"] >= 100]

    num_cols = df.select_dtypes(include=[np.number]).columns.tolist()
    zones = df.groupby("Code Zone", as_index=False)[num_cols].mean()

    zone_label = (
        df.groupby("Code Zone")["Zone déchiffrée"]
          .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
          .reset_index()
    )
    arr_label = (
        df.groupby("Code Zone")["Arrondissement"]
          .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
          .reset_index()
    )
    pref_label = (
        df.groupby("Code Zone")["Préfecture"]
          .agg(lambda s: s.mode().iat[0] if not s.mode().empty else s.iloc[0])
          .reset_index()
    )
    zones = (
        zones
        .merge(zone_label, on="Code Zone")
        .merge(arr_label,  on="Code Zone")
        .merge(pref_label, on="Code Zone")
    )

    # Verification des colonnes
    missing = [v for v in ALL_VARS if v not in zones.columns]
    if missing:
        raise RuntimeError(f"Colonnes manquantes dans data_finale.csv : {missing}")
    return zones


@lru_cache(maxsize=1)
def load_weights() -> Dict[str, float]:
    """Charge les poids depuis poids_dimensions.csv (Poids en %)."""
    p = pd.read_csv(POIDS_PATH)
    p.columns = [c.strip().lstrip("﻿") for c in p.columns]
    poids = dict(zip(p["Dimension"], p["Poids (%)"].astype(float) / 100.0))
    # Garantir la presence des 4 dimensions
    for d in DIMENSIONS:
        if d not in poids:
            raise RuntimeError(f"Poids manquant pour la dimension : {d}")
    return poids


@lru_cache(maxsize=1)
def _minmax_bounds() -> Dict[str, Tuple[float, float]]:
    """Pour chaque variable : (min, max) calcules sur l'ensemble des zones."""
    zones = load_zones()
    bounds: Dict[str, Tuple[float, float]] = {}
    for v in ALL_VARS:
        col = zones[v].astype(float)
        bounds[v] = (float(col.min()), float(col.max()))
    return bounds


@lru_cache(maxsize=1)
def _raw_score_bounds() -> Tuple[float, float]:
    """Min et max du score brut (avant remise a [0, 100])."""
    sub = compute_subscores_table()  # DataFrame zones x 4 dimensions
    weights = load_weights()
    w = np.array([weights[d] for d in DIMENSIONS])
    raw = sub[list(DIMENSIONS.keys())].values @ w
    return float(raw.min()), float(raw.max())


@lru_cache(maxsize=1)
def compute_subscores_table() -> pd.DataFrame:
    """Calcule la table des sous-scores [0,1] pour chaque zone (cache)."""
    zones = load_zones()
    bounds = _minmax_bounds()

    sub = pd.DataFrame(index=zones.index)
    for dim, var_dict in DIMENSIONS.items():
        cols = []
        for var, sign in var_dict.items():
            lo, hi = bounds[var]
            rng = hi - lo if hi > lo else 1.0
            x = (zones[var].astype(float).values - lo) / rng
            if sign == "neg":
                x = 1.0 - x
            cols.append(x)
        sub[dim] = np.mean(np.column_stack(cols), axis=1)
    sub["Code Zone"] = zones["Code Zone"].values
    return sub


# -----------------------------------------------------------------------------
# Fonctions de calcul exposees
# -----------------------------------------------------------------------------
def normalize_one(var: str, value: float) -> float:
    """Normalise une variable selon les bornes des zones existantes."""
    lo, hi = _minmax_bounds()[var]
    rng = hi - lo if hi > lo else 1.0
    x = (float(value) - lo) / rng
    x = max(0.0, min(1.0, x))  # clip dans [0,1] pour points hors-borne
    if DIMENSIONS_VAR_SIGN[var] == "neg":
        x = 1.0 - x
    return x


def score_from_variables(values: Dict[str, float]) -> Dict[str, float]:
    """
    Calcule le score complet pour un point custom defini par ses variables.

    Args:
        values: dict variable -> valeur (toutes les variables de ALL_VARS).

    Returns:
        dict avec sous-scores (0-100) + score global (0-100).
    """
    missing = [v for v in ALL_VARS if v not in values]
    if missing:
        raise ValueError(f"Variables manquantes : {missing}")

    weights = load_weights()
    sub_scores: Dict[str, float] = {}
    for dim, var_dict in DIMENSIONS.items():
        normed = [normalize_one(var, values[var]) for var in var_dict]
        sub_scores[dim] = float(np.mean(normed))

    raw = sum(weights[d] * sub_scores[d] for d in DIMENSIONS)
    lo, hi = _raw_score_bounds()
    rng = hi - lo if hi > lo else 1.0
    score_100 = (raw - lo) / rng * 100.0
    score_100 = max(0.0, min(100.0, score_100))

    return {
        "Score_Accessibilite":  round(sub_scores["Accessibilite"]  * 100, 1),
        "Score_Amenites":       round(sub_scores["Amenites"]       * 100, 1),
        "Score_Environnement":  round(sub_scores["Environnement"]  * 100, 1),
        "Score_SocioDemo":      round(sub_scores["SocioDemo"]      * 100, 1),
        "Score_Attractivite":   round(score_100, 1),
    }


@lru_cache(maxsize=1)
def all_zone_scores() -> pd.DataFrame:
    """Table complete des scores par zone, triee, avec rang."""
    zones = load_zones()
    sub = compute_subscores_table()
    weights = load_weights()
    w = np.array([weights[d] for d in DIMENSIONS])
    raw = sub[list(DIMENSIONS.keys())].values @ w
    lo, hi = float(raw.min()), float(raw.max())
    rng = hi - lo if hi > lo else 1.0
    score_100 = (raw - lo) / rng * 100.0

    out = pd.DataFrame({
        "Code Zone":           zones["Code Zone"],
        "Zone":                zones["Zone déchiffrée"],
        "Arrondissement":      zones["Arrondissement"],
        "Prefecture":          zones["Préfecture"],
        "Prix m2 (DH)":        zones["Prix Terrain au m² (DH)"].round(0),
        "Score_Accessibilite": (sub["Accessibilite"] * 100).round(1),
        "Score_Amenites":      (sub["Amenites"]      * 100).round(1),
        "Score_Environnement": (sub["Environnement"] * 100).round(1),
        "Score_SocioDemo":     (sub["SocioDemo"]     * 100).round(1),
        "Score_Attractivite":  score_100.round(1),
    }).sort_values("Score_Attractivite", ascending=False).reset_index(drop=True)
    out.insert(0, "Rang", np.arange(1, len(out) + 1))
    return out


def get_zone_features(code_zone: str) -> Dict[str, float]:
    """Renvoie le dict variable -> valeur pour une zone (utile pour pre-remplir)."""
    zones = load_zones()
    row = zones[zones["Code Zone"] == code_zone]
    if row.empty:
        raise KeyError(f"Code Zone introuvable : {code_zone}")
    return {v: float(row.iloc[0][v]) for v in ALL_VARS}


def load_coordinates() -> pd.DataFrame:
    """
    Charge coordonnees_zones.csv si disponible.
    Format attendu : colonnes 'Code Zone', 'lat', 'lng' (et optionnellement
    'geometry' pour un polygone GeoJSON).
    Renvoie un DataFrame vide si le fichier n'existe pas.
    """
    if not os.path.exists(COORDS_PATH):
        return pd.DataFrame(columns=["Code Zone", "lat", "lng"])
    df = pd.read_csv(COORDS_PATH)
    df.columns = [c.strip() for c in df.columns]
    return df


# Index inverse variable -> signe (pour normalize_one)
DIMENSIONS_VAR_SIGN: Dict[str, str] = {
    v: sign for d in DIMENSIONS.values() for v, sign in d.items()
}
