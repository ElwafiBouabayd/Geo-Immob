"""
====================================================================
 Enrichissement géomarketing du fichier `avito_casablanca_terrains_clean.xlsx`
====================================================================

Ce script enrichit chaque annonce (ligne) avec des variables décrivant son
environnement immédiat, à partir de sa Latitude / Longitude.

Variables produites (toutes en mètres, minutes, ou comptages) :

A. ACCESSIBILITÉ
   - dist_tram_m          : distance à la station de tram la plus proche (m)
   - tram_500m            : tram dans 500 m (0/1)
   - intersect_500m       : nb d'intersections de voies (nodes ≥ 2 ways) dans 500 m
   - tt_centre_min        : temps trajet estimé (min) vers Place Mohammed V
   - tt_cfc_min           : … vers Casablanca Finance City
   - tt_maarif_min        : … vers Maârif
   - tt_sidi_maarouf_min  : … vers Sidi Maârouf
   - tt_port_min          : … vers Port de Casablanca

B. AMÉNITÉS DE PROXIMITÉ
   - n_ecoles_500m / _1km, dist_ecole_m
   - n_banques_500m / _1km, dist_banque_m
   - n_malls_500m / _1km, dist_mall_m

C. QUALITÉ ENVIRONNEMENTALE
   - dist_mer_m
   - dist_parc_m, n_parcs_1km
   - surface_verte_m2_1km : somme des surfaces (m²) de leisure=park|garden dans 1 km
   - nuisance_route_500m  : longueur (m) de motorway/trunk/primary dans 500 m  (−)
   - n_industries_500m                                                          (−)
   - n_fuel_500m                                                                (−)

Sources :
   - Annonces  : fichier Excel d'entrée (Yakeey)
   - POI       : OpenStreetMap via l'API Overpass (gratuite, sans clé)
   - Temps     : Haversine × 1.3 (facteur de détour urbain) / 25 km/h

Dépendances :
   pip install pandas openpyxl requests numpy shapely pyproj

Utilisation :
   python enrich.py
   (le fichier d'entrée doit être dans le même dossier ; sinon ajuster INPUT_XLSX)
"""

from __future__ import annotations
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

# ----------------------------------------------------------------------
# Paramètres
# ----------------------------------------------------------------------
HERE = Path(__file__).parent
INPUT_XLSX  = HERE / "avito_casablanca_apparts.xlsx"
OUTPUT_XLSX = HERE / "avito_casablanca_apparts_enrichi.xlsx"
CACHE_DIR   = HERE / "osm_cache"
CACHE_DIR.mkdir(exist_ok=True)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
USER_AGENT = "yakeey-enrich/1.0 (contact: elwafibouabayd@gmail.com)"

# Bounding box englobant le Grand Casablanca (sud, ouest, nord, est)
BBOX = (33.20, -8.10, 33.90, -7.00)

# Pôles économiques (lat, lon)
POLES = {
    "centre":       (33.5895, -7.6193),  # Place Mohammed V
    "cfc":          (33.5414, -7.6651),  # Casablanca Finance City
    "maarif":       (33.5808, -7.6332),  # Maârif (centre)
    "sidi_maarouf": (33.5394, -7.6433),  # Sidi Maârouf
    "port":         (33.6066, -7.6005),  # Port de Casablanca
}

# Hypothèses temps de trajet
DETOUR_FACTOR = 1.30      # ratio distance route / distance vol d'oiseau
URBAN_SPEED_KMH = 25.0    # vitesse moyenne urbaine

# Rayons (m)
R_500 = 500.0
R_1KM = 1000.0


# ----------------------------------------------------------------------
# Requêtes Overpass
# ----------------------------------------------------------------------
QUERIES = {
    "tram": """
        [out:json][timeout:180];
        (
          node["railway"="tram_stop"]({s},{w},{n},{e});
          node["public_transport"="stop_position"]["tram"="yes"]({s},{w},{n},{e});
        );
        out;
    """,
    "school": """
        [out:json][timeout:180];
        (
          node["amenity"~"school|university|college"]({s},{w},{n},{e});
          way ["amenity"~"school|university|college"]({s},{w},{n},{e});
        );
        out center;
    """,
    "bank": """
        [out:json][timeout:180];
        node["amenity"~"^(bank|atm)$"]({s},{w},{n},{e});
        out;
    """,
    "mall": """
        [out:json][timeout:180];
        (
          node["shop"~"^(mall)$"]({s},{w},{n},{e});
          way ["shop"~"^(mall)$"]({s},{w},{n},{e});
        );
        out center;
    """,
    "park": """
        [out:json][timeout:180];
        (
          way     ["leisure"~"^(park)$"]({s},{w},{n},{e});
          relation["leisure"~"^(park)$"]({s},{w},{n},{e});
        );
        out geom;
    """,
    "coast": """
        [out:json][timeout:180];
        way["natural"="coastline"]({s},{w},{n},{e});
        out geom;
    """,
    "highway_major": """
        [out:json][timeout:180];
        way["highway"~"^(motorway|trunk|primary)$"]({s},{w},{n},{e});
        out geom;
    """,
    "highway_all": """
        [out:json][timeout:240];
        way["highway"]({s},{w},{n},{e});
        out;
        >;
        out skel qt;
    """,
    "industrial": """
        [out:json][timeout:180];
        (
          way     ["landuse"="industrial"]({s},{w},{n},{e});
          relation["landuse"="industrial"]({s},{w},{n},{e});
          way     ["man_made"="works"]({s},{w},{n},{e});
        );
        out center;
    """,
    "fuel": """
        [out:json][timeout:180];
        node["amenity"="fuel"]({s},{w},{n},{e});
        out;
    """,
}


def _clean_query(q: str) -> str:
    """Nettoie la requête Overpass : strip + suppression de l'indentation."""
    # Supprime les espaces/tabs en début de chaque ligne et lignes vides
    lines = [ln.strip() for ln in q.splitlines() if ln.strip()]
    return "\n".join(lines)


def overpass(name: str, query: str, bbox=BBOX) -> dict:
    """Exécute une requête Overpass avec cache disque et bascule de miroirs."""
    cache_path = CACHE_DIR / f"{name}.json"
    if cache_path.exists():
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    s, w, n, e = bbox
    q = _clean_query(query.format(s=s, w=w, n=n, e=e))
    print(f"  → requête Overpass : {name} …", flush=True)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }

    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(2):
            try:
                # POST en x-www-form-urlencoded : Overpass attend "data=<query>"
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
                    return data
                # 429 / 504 : serveur saturé -> on retente puis on change de miroir
                if r.status_code in (429, 504):
                    print(f"     {endpoint} : HTTP {r.status_code} (saturé), retry 15 s")
                    time.sleep(15)
                    continue
                # Autres erreurs : on bascule sur le miroir suivant
                last_err = f"HTTP {r.status_code} sur {endpoint} : {r.text[:200]}"
                print(f"     {endpoint} : {last_err[:140]}")
                break
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                print(f"     échec ({last_err}); retry 10 s …")
                time.sleep(10)
        # essaye le miroir suivant
    raise RuntimeError(f"Overpass a échoué pour {name} — dernière erreur : {last_err}")


# ----------------------------------------------------------------------
# Outils géo
# ----------------------------------------------------------------------
def haversine_vec(lat0: float, lon0: float,
                  lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    """Distances Haversine (m) du point (lat0,lon0) vers un tableau de points."""
    R = 6_371_000.0
    p0 = math.radians(lat0)
    p1 = np.radians(lats)
    dphi = np.radians(lats - lat0)
    dlmb = np.radians(lons - lon0)
    a = np.sin(dphi/2)**2 + math.cos(p0)*np.cos(p1)*np.sin(dlmb/2)**2
    return 2 * R * np.arcsin(np.sqrt(a))


def extract_point_coords(elements: list) -> np.ndarray:
    """Renvoie un tableau (N,2) de [lat, lon] depuis une liste d'éléments Overpass."""
    coords = []
    for el in elements:
        if "lat" in el and "lon" in el:
            coords.append((el["lat"], el["lon"]))
        elif "center" in el:
            coords.append((el["center"]["lat"], el["center"]["lon"]))
    return np.array(coords) if coords else np.zeros((0, 2))


def extract_polygons(elements: list) -> list:
    """Construit la liste des polygones shapely depuis ways Overpass `out geom`."""
    polys = []
    for el in elements:
        if el.get("type") == "way" and "geometry" in el:
            ring = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(ring) >= 4 and ring[0] == ring[-1]:
                try:
                    polys.append(Polygon(ring))
                except Exception:
                    pass
        elif el.get("type") == "relation" and "members" in el:
            outer = []
            for m in el["members"]:
                if m.get("role") == "outer" and "geometry" in m:
                    pts = [(p["lon"], p["lat"]) for p in m["geometry"]]
                    if len(pts) >= 4 and pts[0] == pts[-1]:
                        try:
                            outer.append(Polygon(pts))
                        except Exception:
                            pass
            polys.extend(outer)
    return polys


def extract_lines(elements: list) -> list:
    lines = []
    for el in elements:
        if el.get("type") == "way" and "geometry" in el and len(el["geometry"]) >= 2:
            lines.append(LineString([(p["lon"], p["lat"]) for p in el["geometry"]]))
    return lines


# Conversion approximative degrés → mètres autour de Casablanca (~33.6°N)
LAT_M_PER_DEG = 111_000.0
LON_M_PER_DEG = 111_000.0 * math.cos(math.radians(33.6))


def deg_buffer(lat: float, lon: float, radius_m: float):
    """Renvoie une bounding box (minlon, minlat, maxlon, maxlat) autour de (lat,lon)."""
    dlat = radius_m / LAT_M_PER_DEG
    dlon = radius_m / LON_M_PER_DEG
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def point_to_lines_dist_m(lat: float, lon: float, lines: list) -> float:
    """Distance minimale (m) d'un point à une liste de LineString (lon,lat)."""
    if not lines:
        return np.nan
    pt = Point(lon, lat)
    # distance en degrés -> conversion via le plus court élément
    d_min_deg = min(line.distance(pt) for line in lines)
    # approximation : on convertit le delta en mètres en utilisant la moyenne
    # ici on reprojette correctement via dx/dy
    # Approche plus robuste : trouver le point projeté le plus proche pour chaque ligne
    best = float("inf")
    for line in lines:
        np_pt = line.interpolate(line.project(pt))
        dlat = (np_pt.y - lat) * LAT_M_PER_DEG
        dlon = (np_pt.x - lon) * LON_M_PER_DEG
        d = math.hypot(dlat, dlon)
        if d < best:
            best = d
    return best


def lines_length_in_buffer_m(lat: float, lon: float,
                             lines: list, radius_m: float) -> float:
    """Longueur (m) des portions de lignes incluses dans le buffer (lat,lon,radius)."""
    if not lines:
        return 0.0
    minlon, minlat, maxlon, maxlat = deg_buffer(lat, lon, radius_m * 1.4)
    total = 0.0
    for line in lines:
        # filtre rapide via bbox
        x0, y0, x1, y1 = line.bounds
        if x1 < minlon or x0 > maxlon or y1 < minlat or y0 > maxlat:
            continue
        # discrétisation de la ligne et mesure par segments
        coords = list(line.coords)
        for (lon1, lat1), (lon2, lat2) in zip(coords[:-1], coords[1:]):
            # milieu du segment
            mlat = (lat1 + lat2) / 2
            mlon = (lon1 + lon2) / 2
            d_to_pt = haversine_vec(lat, lon,
                                    np.array([mlat]), np.array([mlon]))[0]
            if d_to_pt <= radius_m:
                # longueur du segment
                dy = (lat2 - lat1) * LAT_M_PER_DEG
                dx = (lon2 - lon1) * LON_M_PER_DEG
                total += math.hypot(dx, dy)
    return total


def polygons_area_in_buffer_m2(lat: float, lon: float,
                               polys_union, radius_m: float) -> float:
    """Surface (m²) de polygones dans le buffer circulaire (lat,lon,radius)."""
    if polys_union is None or polys_union.is_empty:
        return 0.0
    # Construire un cercle approximatif en lon/lat
    n = 36
    coords = []
    for i in range(n + 1):
        ang = 2 * math.pi * i / n
        dlat = (radius_m * math.cos(ang)) / LAT_M_PER_DEG
        dlon = (radius_m * math.sin(ang)) / LON_M_PER_DEG
        coords.append((lon + dlon, lat + dlat))
    buf = Polygon(coords)
    inter = polys_union.intersection(buf)
    if inter.is_empty:
        return 0.0
    # surface en degrés² → m²
    return inter.area * LAT_M_PER_DEG * LON_M_PER_DEG


# ----------------------------------------------------------------------
# Calcul des intersections de voies
# ----------------------------------------------------------------------
def build_intersection_nodes(highway_data: dict) -> np.ndarray:
    """Trouve les coordonnées des nodes qui appartiennent à ≥ 2 ways highway."""
    ways = [el for el in highway_data["elements"] if el["type"] == "way"]
    nodes = {el["id"]: (el["lat"], el["lon"])
             for el in highway_data["elements"] if el["type"] == "node"}
    node_use = {}
    for w in ways:
        for nid in w.get("nodes", []):
            node_use[nid] = node_use.get(nid, 0) + 1
    inter_coords = [nodes[nid] for nid, cnt in node_use.items()
                    if cnt >= 2 and nid in nodes]
    return np.array(inter_coords) if inter_coords else np.zeros((0, 2))


# ----------------------------------------------------------------------
# Pipeline principal
# ----------------------------------------------------------------------
def main():
    print("== Enrichissement géomarketing Yakeey Casablanca ==")
    df = pd.read_excel(INPUT_XLSX, sheet_name="Annonces nettoyées")
    print(f"Annonces chargées : {len(df)}")

    # -------- POI ponctuels --------
    print("Téléchargement des POI OpenStreetMap …")
    pts = {}
    pts["tram"]       = extract_point_coords(overpass("tram",       QUERIES["tram"])["elements"])
    pts["school"]     = extract_point_coords(overpass("school",     QUERIES["school"])["elements"])
    pts["bank"]       = extract_point_coords(overpass("bank",       QUERIES["bank"])["elements"])
    pts["mall"]       = extract_point_coords(overpass("mall",       QUERIES["mall"])["elements"])
    pts["industrial"] = extract_point_coords(overpass("industrial", QUERIES["industrial"])["elements"])
    pts["fuel"]       = extract_point_coords(overpass("fuel",       QUERIES["fuel"])["elements"])
    for k, v in pts.items():
        print(f"  POI {k:12s}: {len(v):>5}")

    # -------- Polygones (parcs) --------
    park_polys  = extract_polygons(overpass("park", QUERIES["park"])["elements"])
    park_union  = unary_union(park_polys) if park_polys else None
    park_centroids = np.array([[p.centroid.y, p.centroid.x] for p in park_polys]) \
                     if park_polys else np.zeros((0, 2))
    print(f"  Parcs/jardins : {len(park_polys)}")

    # -------- Lignes (côte, routes majeures) --------
    coast_lines     = extract_lines(overpass("coast",          QUERIES["coast"])["elements"])
    highway_major   = extract_lines(overpass("highway_major",  QUERIES["highway_major"])["elements"])
    print(f"  Tronçons côtiers : {len(coast_lines)}")
    print(f"  Routes majeures  : {len(highway_major)}")

    # -------- Intersections de voies --------
    hw_all = overpass("highway_all", QUERIES["highway_all"])
    inter_nodes = build_intersection_nodes(hw_all)
    print(f"  Intersections    : {len(inter_nodes)}")

    # -------- Boucle d'enrichissement --------
    rows = []
    n = len(df)
    for idx, row in df.iterrows():
        lat, lon = row["Latitude"], row["Longitude"]
        rec = {}

        # A. ACCESSIBILITÉ
        # Tram
        if len(pts["tram"]):
            d_tram = haversine_vec(lat, lon, pts["tram"][:, 0], pts["tram"][:, 1])
            rec["dist_tram_m"] = float(d_tram.min())
            rec["tram_500m"]   = int(rec["dist_tram_m"] <= R_500)
        else:
            rec["dist_tram_m"] = np.nan
            rec["tram_500m"]   = 0

        # Intersections 500 m
        if len(inter_nodes):
            d = haversine_vec(lat, lon, inter_nodes[:, 0], inter_nodes[:, 1])
            rec["intersect_500m"] = int((d <= R_500).sum())
        else:
            rec["intersect_500m"] = 0

        # Temps de trajet vers les pôles
        for name, (plat, plon) in POLES.items():
            d_m = haversine_vec(lat, lon, np.array([plat]), np.array([plon]))[0]
            rec[f"tt_{name}_min"] = round(
                (d_m * DETOUR_FACTOR / 1000.0) / URBAN_SPEED_KMH * 60.0, 1
            )

        # B. AMÉNITÉS
        for cat, label in [("school", "ecole"),
                           ("bank",   "banque"),
                           ("mall",   "mall")]:
            arr = pts[cat]
            if len(arr):
                d = haversine_vec(lat, lon, arr[:, 0], arr[:, 1])
                rec[f"n_{label}s_500m"] = int((d <= R_500).sum())
                rec[f"n_{label}s_1km"]  = int((d <= R_1KM).sum())
                rec[f"dist_{label}_m"]  = float(d.min())
            else:
                rec[f"n_{label}s_500m"] = 0
                rec[f"n_{label}s_1km"]  = 0
                rec[f"dist_{label}_m"]  = np.nan

        # C. QUALITÉ ENVIRONNEMENTALE
        rec["dist_mer_m"] = point_to_lines_dist_m(lat, lon, coast_lines)

        if len(park_centroids):
            d_park = haversine_vec(lat, lon,
                                   park_centroids[:, 0], park_centroids[:, 1])
            rec["dist_parc_m"]  = float(d_park.min())
            rec["n_parcs_1km"]  = int((d_park <= R_1KM).sum())
        else:
            rec["dist_parc_m"]  = np.nan
            rec["n_parcs_1km"]  = 0

        rec["surface_verte_m2_1km"] = round(
            polygons_area_in_buffer_m2(lat, lon, park_union, R_1KM), 1
        )
        rec["nuisance_route_500m"]  = round(
            lines_length_in_buffer_m(lat, lon, highway_major, R_500), 1
        )

        # Industries / stations-service
        for cat, label in [("industrial", "industries"), ("fuel", "fuel")]:
            arr = pts[cat]
            if len(arr):
                d = haversine_vec(lat, lon, arr[:, 0], arr[:, 1])
                rec[f"n_{label}_500m"] = int((d <= R_500).sum())
            else:
                rec[f"n_{label}_500m"] = 0

        rows.append(rec)
        if (idx + 1) % 50 == 0 or idx + 1 == n:
            print(f"  Traitées : {idx+1}/{n}", flush=True)

    enr = pd.DataFrame(rows)
    out = pd.concat([df.reset_index(drop=True), enr], axis=1)

    # ----- Sortie -----
    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as w:
        out.to_excel(w, sheet_name="Annonces enrichies", index=False)
        # petite synthèse
        synth = pd.DataFrame({
            "Variable": enr.columns,
            "Moyenne":  enr.mean(numeric_only=True).round(2).reindex(enr.columns),
            "Médiane":  enr.median(numeric_only=True).round(2).reindex(enr.columns),
            "Min":      enr.min(numeric_only=True).reindex(enr.columns),
            "Max":      enr.max(numeric_only=True).reindex(enr.columns),
            "% NaN":    (enr.isna().mean() * 100).round(1).reindex(enr.columns),
        })
        synth.to_excel(w, sheet_name="Stats enrichissement", index=False)

    print(f"✓ Fichier enrichi écrit : {OUTPUT_XLSX}")


if __name__ == "__main__":
    main()
