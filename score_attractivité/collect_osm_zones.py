"""
Collecte OSM pour toutes les zones de coordonnees_zones.csv
===========================================================
Stratégie : télécharger TOUS les POI du Grand Casablanca UNE seule fois
            par type, puis compter localement par zone (numpy vectorisé).
            → ~12 requêtes Overpass au total (au lieu de 62 × 12 = 744)

Accessibilité : calcul local Haversine (pas de réseau)
Équipements   : comptage local depuis les POI téléchargés

Prérequis : pip install requests pandas numpy

Utilisation :
    python collect_osm_zones.py
"""

from __future__ import annotations
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

import requests

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
HERE = Path(__file__).parent
CACHE_DIR = HERE / "osm_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Bounding box Grand Casablanca (sud, ouest, nord, est)
BBOX = (33.35, -7.90, 33.70, -7.40)

CENTRE_VILLE = (33.5928, -7.6200)   # Place Mohammed V (lat, lng)

VITESSE_MOYENNE_KMH = 25.0
FACTEUR_DETOUR      = 1.30

R_500  =  500.0
R_1KM  = 1000.0
R_2KM  = 2000.0

# Endpoints Overpass avec fallback automatique
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

USER_AGENT = "GeoScoring/1.0 (attractivite-casablanca)"

COORDS_PATH = r"C:\Users\del\Desktop\scoring\data_annonces.csv"
OUTPUT_CSV  = r"C:\Users\del\Desktop\scoring\osm_annonces_data.csv"
# ─────────────────────────────────────────────
# REQUÊTES OVERPASS (bounding box entière)
# ─────────────────────────────────────────────
QUERIES = {
    "bus_stop": """
        [out:json][timeout:180];
        node["highway"="bus_stop"]({s},{w},{n},{e});
        out;
    """,
    "tram_stop": """
        [out:json][timeout:180];
        (
          node["railway"="tram_stop"]({s},{w},{n},{e});
          node["public_transport"="stop_position"]["tram"="yes"]({s},{w},{n},{e});
        );
        out;
    """,
    "taxi": """
        [out:json][timeout:180];
        node["amenity"="taxi"]({s},{w},{n},{e});
        out;
    """,
    "school": """
        [out:json][timeout:180];
        (
          node["amenity"="school"]({s},{w},{n},{e});
          way ["amenity"="school"]({s},{w},{n},{e});
        );
        out center;
    """,
    "hospital": """
        [out:json][timeout:180];
        (
          node["amenity"="hospital"]({s},{w},{n},{e});
          way ["amenity"="hospital"]({s},{w},{n},{e});
        );
        out center;
    """,
    "clinic": """
        [out:json][timeout:180];
        (
          node["amenity"="clinic"]({s},{w},{n},{e});
          way ["amenity"="clinic"]({s},{w},{n},{e});
        );
        out center;
    """,
    "pharmacy": """
        [out:json][timeout:180];
        node["amenity"="pharmacy"]({s},{w},{n},{e});
        out;
    """,
    "supermarket": """
        [out:json][timeout:180];
        (
          node["shop"="supermarket"]({s},{w},{n},{e});
          way ["shop"="supermarket"]({s},{w},{n},{e});
        );
        out center;
    """,
    "convenience": """
        [out:json][timeout:180];
        node["shop"="convenience"]({s},{w},{n},{e});
        out;
    """,
    "mosque": """
        [out:json][timeout:180];
        (
          node["amenity"="place_of_worship"]["religion"="muslim"]({s},{w},{n},{e});
          way ["amenity"="place_of_worship"]["religion"="muslim"]({s},{w},{n},{e});
        );
        out center;
    """,
    "bank": """
        [out:json][timeout:180];
        node["amenity"="bank"]({s},{w},{n},{e});
        out;
    """,
    "restaurant": """
        [out:json][timeout:180];
        node["amenity"="restaurant"]({s},{w},{n},{e});
        out;
    """,
}


# ─────────────────────────────────────────────
# TÉLÉCHARGEMENT OVERPASS AVEC CACHE + FALLBACK
# ─────────────────────────────────────────────

def _clean(q: str) -> str:
    return "\n".join(ln.strip() for ln in q.splitlines() if ln.strip())


def overpass_fetch(name: str, query: str, bbox=BBOX) -> list:
    """
    Télécharge les éléments OSM pour le type `name` sur toute la bbox.
    Résultat mis en cache disque → pas de re-téléchargement si relancé.
    Retourne une liste d'éléments Overpass.
    """
    cache_path = CACHE_DIR / f"{name}.json"
    if cache_path.exists():
        print(f"  [cache] {name}", flush=True)
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)["elements"]

    s, w, n, e = bbox
    q = _clean(query.format(s=s, w=w, n=n, e=e))
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    print(f"  [téléchargement] {name} …", flush=True)
    last_err = None

    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(2):
            try:
                r = requests.post(
                    endpoint,
                    data={"data": q},
                    headers=headers,
                    timeout=300,
                )
                if r.status_code == 200:
                    data = r.json()
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    print(f"    → {len(data['elements'])} éléments", flush=True)
                    return data["elements"]
                if r.status_code in (429, 504):
                    print(f"    {endpoint} : HTTP {r.status_code}, attente 20s …", flush=True)
                    time.sleep(20)
                    continue
                last_err = f"HTTP {r.status_code}"
                print(f"    {endpoint} : {last_err}", flush=True)
                break
            except Exception as exc:
                last_err = str(exc)
                print(f"    {endpoint} : erreur ({last_err}), retry 10s …", flush=True)
                time.sleep(10)

    print(f"  AVERTISSEMENT : impossible de télécharger {name} ({last_err})", flush=True)
    return []


# ─────────────────────────────────────────────
# EXTRACTION DES COORDONNÉES
# ─────────────────────────────────────────────

def extract_coords(elements: list) -> np.ndarray:
    """Extrait un tableau (N, 2) [lat, lon] depuis des éléments Overpass."""
    coords = []
    for el in elements:
        if "lat" in el and "lon" in el:
            coords.append((el["lat"], el["lon"]))
        elif "center" in el:
            coords.append((el["center"]["lat"], el["center"]["lon"]))
    return np.array(coords) if coords else np.zeros((0, 2))


# ─────────────────────────────────────────────
# CALCUL VECTORISÉ
# ─────────────────────────────────────────────

def haversine_vec(lat0: float, lon0: float,
                  pts: np.ndarray) -> np.ndarray:
    """Distances Haversine (m) du point (lat0, lon0) vers un tableau (N,2) [lat, lon]."""
    if len(pts) == 0:
        return np.array([])
    R = 6_371_000.0
    p0   = math.radians(lat0)
    p1   = np.radians(pts[:, 0])
    dphi = np.radians(pts[:, 0] - lat0)
    dlmb = np.radians(pts[:, 1] - lon0)
    a = np.sin(dphi/2)**2 + math.cos(p0) * np.cos(p1) * np.sin(dlmb/2)**2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def count_within(lat: float, lon: float, pts: np.ndarray, radius_m: float) -> int:
    """Compte les points dans `pts` à moins de `radius_m` mètres de (lat, lon)."""
    if len(pts) == 0:
        return 0
    return int((haversine_vec(lat, lon, pts) <= radius_m).sum())


def temps_estime_min(dist_km: float) -> float:
    return round(dist_km * FACTEUR_DETOUR / VITESSE_MOYENNE_KMH * 60, 1)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # ── Chargement des annonces ──────────────
    annonces = pd.read_csv(COORDS_PATH).dropna(subset=["latitude", "longitude"])
    annonces.columns = annonces.columns.str.strip()

    # Points uniques (plusieurs annonces peuvent partager les mêmes coords)
    coords_uniq = annonces[["latitude", "longitude"]].drop_duplicates().copy()
    coords_uniq.columns = ["lat", "lng"]
    total = len(coords_uniq)

    print(f"=== Collecte OSM — {total} points uniques ===", flush=True)
    print(f"Annonces totales : {len(annonces)}", flush=True)
    print(f"Stratégie : 1 requête par type POI pour tout Casablanca\n", flush=True)

    # ── Téléchargement POI (une fois pour toutes) ─────────────────
    print(">> Téléchargement des POI …", flush=True)
    poi = {name: extract_coords(overpass_fetch(name, q))
           for name, q in QUERIES.items()}

    for name, arr in poi.items():
        print(f"   {name:15s} : {len(arr):>5} éléments", flush=True)

    # ── Calcul par point unique ───────────────
    print(f"\n>> Calcul par point ({total} points) …", flush=True)
    resultats = []
    clat, clng = CENTRE_VILLE

    for i, (_, row) in enumerate(coords_uniq.iterrows(), 1):
        lat, lng = row["lat"], row["lng"]
        dist_km = haversine_vec(lat, lng, np.array([[clat, clng]]))[0] / 1000
        rec = {
            "latitude":                lat,
            "longitude":               lng,
            "distance_vol_oiseau_km":  round(dist_km, 3),
            "temps_voiture_estime_min":temps_estime_min(dist_km),
            "nb_arrets_bus_500m":      count_within(lat, lng, poi["bus_stop"],    R_500),
            "nb_arrets_tramway_500m":  count_within(lat, lng, poi["tram_stop"],   R_500),
            "nb_taxis_500m":           count_within(lat, lng, poi["taxi"],        R_500),
            "nb_ecoles_1km":           count_within(lat, lng, poi["school"],      R_1KM),
            "nb_hopitaux_2km":         count_within(lat, lng, poi["hospital"],    R_2KM),
            "nb_cliniques_2km":        count_within(lat, lng, poi["clinic"],      R_2KM),
            "nb_pharmacies_500m":      count_within(lat, lng, poi["pharmacy"],    R_500),
            "nb_supermarches_500m":    count_within(lat, lng, poi["supermarket"], R_500),
            "nb_epiceries_500m":       count_within(lat, lng, poi["convenience"], R_500),
            "nb_mosquees_500m":        count_within(lat, lng, poi["mosque"],      R_500),
            "nb_banques_1km":          count_within(lat, lng, poi["bank"],        R_1KM),
            "nb_restaurants_500m":     count_within(lat, lng, poi["restaurant"],  R_500),
        }
        resultats.append(rec)
        if i % 10 == 0 or i == total:
            print(f"  {i}/{total} points traités", flush=True)

    df_osm = pd.DataFrame(resultats)

    # ── Rejoindre à toutes les annonces (y compris doublons de coords) ──
    df_final = annonces.merge(df_osm, on=["latitude", "longitude"], how="left")

    df_final.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

    print(f"\n✓ Sauvegardé : {OUTPUT_CSV}", flush=True)
    print(f"  Lignes    : {len(df_final)}", flush=True)
    print(f"  Colonnes  : {len(df_final.columns)}", flush=True)
    print("\nAperçu :")
    print(df_osm[["latitude","longitude","distance_vol_oiseau_km",
                  "nb_arrets_bus_500m","nb_ecoles_1km","nb_banques_1km"]].head(5).to_string(index=False))
