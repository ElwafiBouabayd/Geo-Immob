# -*- coding: utf-8 -*-
"""
Calcul des variables geomarketing par parcelle/batiment de Casablanca.

Etape 1 : telecharge les empreintes de batiments OSM (proxy parcellaire)
Etape 2 : pour chaque batiment, calcule les 32 variables cibles :

  Distances minimales aux POIs (en metres) :
    dist_education_min_m, dist_banque_min_m, dist_sante_min_m,
    dist_commerce_min_m, dist_transport_min_m, dist_environnement_min_m,
    dist_nuisance_min_m

  Distances minimales aux axes routiers (en metres) :
    dist_autoroute_min_m, dist_voie_primaire_min_m,
    dist_voie_secondaire_min_m, dist_boulevard_principal

  Distances ciblees (en metres) :
    dist_tram, dist_ecole, dist_clinique, dist_mer, dist_parc

  Comptes en buffer :
    nb_nuisance_500m, nb_axes_500m, nb_stations_1km, nb_ecoles_1km,
    nb_sante_1km, nb_restaurants_500m, nb_commerces_1km,
    nb_banques_1km, nb_nuisance (1km)

  Densite et surface :
    densite_education (POIs/km2 dans buffer 1km)
    surface_verte_1km (m2 d'espaces verts dans buffer 1km)

  Temps de trajet (minutes, vol d'oiseau / 30 km/h) :
    temps_transport_centre, temps_CFC, temps_Maarif,
    temps_SidiMaarouf, temps_port

Sortie : parcelles_features.csv

Prerequis : avoir d'abord lance extract_pois_casablanca.py
Dependances : pip install requests pandas numpy
"""

import math
import time
import requests
import pandas as pd
import numpy as np
from pathlib import Path

BBOX = (33.45, -7.75, 33.70, -7.45)

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

HEADERS = {
    "User-Agent": "Geomarketing-Casablanca/1.0 (contact: elwafibouabayd@gmail.com)",
    "Accept": "application/json",
}

POI_DIR = Path("pois_casablanca")
OUT_FILE = Path("parcelles_features.csv")

# Vitesse moyenne urbaine pour les temps de trajet a vol d'oiseau (km/h)
VITESSE_URBAINE_KMH = 30.0

# Reperes geographiques pour les temps de trajet (lat, lon)
LANDMARKS = {
    "transport_centre": (33.5945, -7.6200),  # Place Mohammed V / centre-ville
    "CFC":              (33.5394, -7.6630),  # Casablanca Finance City (Anfa)
    "Maarif":           (33.5728, -7.6306),  # Maarif (Twin Center)
    "SidiMaarouf":      (33.5260, -7.6360),  # Sidi Maarouf
    "port":             (33.6075, -7.6160),  # Port de Casablanca
}

# Categories de POIs (cles attendues dans pois_casablanca/poi_<cat>.csv)
CAT_EDUCATION = "education"
CAT_BANQUE    = "banque_finance"
CAT_SANTE     = "sante"
CAT_COMMERCE  = "commerce_retail"
CAT_TRANSPORT = "transports_lourds"
CAT_ENVIRO    = "espaces_verts_loisirs"
CAT_NUISANCE  = "industrie_nuisances"
CAT_RESTO     = "restauration_loisirs"

ROAD_TYPES = {
    "motorway":  ["motorway", "motorway_link"],
    "primary":   ["trunk", "trunk_link", "primary", "primary_link"],
    "secondary": ["secondary", "secondary_link", "tertiary", "tertiary_link"],
}


# ---------------------------------------------------------------------------
# Overpass helpers
# ---------------------------------------------------------------------------
def overpass_query(query, retries=3):
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        host = endpoint.split('//')[1].split('/')[0]
        for attempt in range(retries):
            try:
                r = requests.post(endpoint, data=query.encode("utf-8"),
                                  headers=HEADERS, timeout=300)
                r.raise_for_status()
                return r.json().get("elements", [])
            except Exception as e:
                last_err = e
                print("  [{} try {}] {}".format(host, attempt + 1, e))
                time.sleep(3 * (attempt + 1))
    print("  ECHEC TOTAL: {}".format(last_err))
    return []


def fetch_buildings(bbox):
    s, w, n, e = bbox
    query = ('[out:json][timeout:240];\n(\n'
             '  way["building"]({},{},{},{});\n'
             '  relation["building"]({},{},{},{});\n'
             ');\nout geom tags;\n').format(s, w, n, e, s, w, n, e)
    print("-> Telechargement des batiments OSM (1-3 min)...")
    return overpass_query(query)


def fetch_roads(road_values, bbox):
    s, w, n, e = bbox
    regex = "|".join(road_values)
    query = ('[out:json][timeout:180];\n(\n'
             '  way["highway"~"^({})$"]({},{},{},{});\n);\nout geom;\n'
             ).format(regex, s, w, n, e)
    return overpass_query(query)


# ---------------------------------------------------------------------------
# Geometrie
# ---------------------------------------------------------------------------
def polygon_centroid_and_area(geom_pts):
    if len(geom_pts) < 3:
        return None, None, 0.0
    lats = np.array([p["lat"] for p in geom_pts])
    lons = np.array([p["lon"] for p in geom_pts])
    lat0 = lats.mean()
    R = 6371000.0
    x = np.radians(lons - lons.mean()) * R * np.cos(np.radians(lat0))
    y = np.radians(lats - lat0) * R
    area = 0.5 * np.abs(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    cx = ((x + np.roll(x, -1)) * 0.5).mean()
    cy = ((y + np.roll(y, -1)) * 0.5).mean()
    centroid_lat = lat0 + np.degrees(cy / R)
    centroid_lon = lons.mean() + np.degrees(cx / (R * np.cos(np.radians(lat0))))
    return centroid_lat, centroid_lon, float(area)


def buildings_to_dataframe(elements):
    rows = []
    for el in elements:
        geom = el.get("geometry") or []
        if not geom:
            continue
        lat, lon, area = polygon_centroid_and_area(geom)
        if lat is None:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_id": el["id"],
            "osm_type": el["type"],
            "lat": lat,
            "lon": lon,
            "surface_m2": round(area, 1),
            "building_type": tags.get("building"),
            "levels": tags.get("building:levels"),
            "name": tags.get("name"),
            "addr_street": tags.get("addr:street"),
            "addr_housenumber": tags.get("addr:housenumber"),
        })
    return pd.DataFrame(rows)


def haversine_matrix(coords_a, coords_b):
    R = 6371000.0
    a = np.radians(coords_a)
    b = np.radians(coords_b)
    dlat = a[:, 0:1] - b[:, 0]
    dlon = a[:, 1:2] - b[:, 1]
    h = np.sin(dlat/2)**2 + np.cos(a[:, 0:1]) * np.cos(b[:, 0]) * np.sin(dlon/2)**2
    return 2 * R * np.arcsin(np.sqrt(h))


def haversine_to_point(coords, lat, lon):
    """Distance haversine d'un tableau (N,2) de points lat/lon vers un point unique."""
    R = 6371000.0
    a = np.radians(coords)
    b = np.radians(np.array([lat, lon]))
    dlat = a[:, 0] - b[0]
    dlon = a[:, 1] - b[1]
    h = np.sin(dlat/2)**2 + np.cos(a[:, 0]) * math.cos(math.radians(lat)) * np.sin(dlon/2)**2
    return 2 * R * np.arcsin(np.sqrt(h))


def min_distance_batched(parcels, targets, batch=300):
    if len(targets) == 0:
        return np.full(len(parcels), np.nan)
    out = np.empty(len(parcels))
    for i in range(0, len(parcels), batch):
        d = haversine_matrix(parcels[i:i+batch], targets)
        out[i:i+batch] = d.min(axis=1)
    return out


def count_in_radius_batched(parcels, targets, radius_m, batch=300):
    if len(targets) == 0:
        return np.zeros(len(parcels), dtype=int)
    counts = np.zeros(len(parcels), dtype=int)
    for i in range(0, len(parcels), batch):
        d = haversine_matrix(parcels[i:i+batch], targets)
        counts[i:i+batch] = (d <= radius_m).sum(axis=1)
    return counts


def sum_weight_in_radius_batched(parcels, targets, weights, radius_m, batch=300):
    """Somme ponderee (ex: surface m2) des cibles dans un rayon."""
    if len(targets) == 0:
        return np.zeros(len(parcels))
    weights = np.asarray(weights, dtype=float)
    out = np.zeros(len(parcels))
    for i in range(0, len(parcels), batch):
        d = haversine_matrix(parcels[i:i+batch], targets)
        mask = d <= radius_m
        out[i:i+batch] = (mask * weights).sum(axis=1)
    return out


def roads_to_node_array(elements):
    coords = []
    for el in elements:
        for pt in el.get("geometry", []):
            coords.append((pt["lat"], pt["lon"]))
    return np.array(coords) if coords else np.empty((0, 2))


# ---------------------------------------------------------------------------
# Chargement des CSV produits par extract_pois_casablanca.py
# ---------------------------------------------------------------------------
def load_poi_csv(name):
    f = POI_DIR / "poi_{}.csv".format(name)
    if not f.exists():
        print("  [WARN] fichier manquant : {}".format(f))
        return pd.DataFrame(columns=["lat", "lon", "subtype"])
    df = pd.read_csv(f)
    return df.dropna(subset=["lat", "lon"]) if not df.empty else df


def load_simple_csv(filename, required=("lat", "lon")):
    f = POI_DIR / filename
    if not f.exists():
        print("  [WARN] fichier manquant : {}".format(f))
        return pd.DataFrame(columns=list(required))
    df = pd.read_csv(f)
    if df.empty:
        return df
    return df.dropna(subset=list(required))


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------
def main():
    if not POI_DIR.exists():
        raise SystemExit("Dossier '{}' introuvable. Lance d'abord extract_pois_casablanca.py".format(POI_DIR))

    # 1) Batiments
    elements = fetch_buildings(BBOX)
    print("   {} batiments recuperes".format(len(elements)))
    parcels = buildings_to_dataframe(elements)
    parcels = parcels[parcels["surface_m2"] >= 20].reset_index(drop=True)
    print("   {} parcelles apres filtre (>= 20 m2)".format(len(parcels)))
    if parcels.empty:
        print("Aucune parcelle. Arret.")
        return

    pc = parcels[["lat", "lon"]].to_numpy(dtype=float)

    # 2) Chargement de tous les POIs
    print("\n-> Chargement des POIs")
    poi = {
        CAT_EDUCATION: load_poi_csv(CAT_EDUCATION),
        CAT_BANQUE:    load_poi_csv(CAT_BANQUE),
        CAT_SANTE:     load_poi_csv(CAT_SANTE),
        CAT_COMMERCE:  load_poi_csv(CAT_COMMERCE),
        CAT_TRANSPORT: load_poi_csv(CAT_TRANSPORT),
        CAT_ENVIRO:    load_poi_csv(CAT_ENVIRO),
        CAT_NUISANCE:  load_poi_csv(CAT_NUISANCE),
        CAT_RESTO:     load_poi_csv(CAT_RESTO),
    }
    for k, v in poi.items():
        print("   {:25s} {:>5} POIs".format(k, len(v)))

    def coords_of(df):
        return df[["lat", "lon"]].to_numpy(dtype=float) if not df.empty else np.empty((0, 2))

    # 3) Distances minimales par categorie
    print("\n-> Distances minimales par categorie")
    parcels["dist_education_min_m"]     = min_distance_batched(pc, coords_of(poi[CAT_EDUCATION])).round(0)
    parcels["dist_banque_min_m"]        = min_distance_batched(pc, coords_of(poi[CAT_BANQUE])).round(0)
    parcels["dist_sante_min_m"]         = min_distance_batched(pc, coords_of(poi[CAT_SANTE])).round(0)
    parcels["dist_commerce_min_m"]      = min_distance_batched(pc, coords_of(poi[CAT_COMMERCE])).round(0)
    parcels["dist_transport_min_m"]     = min_distance_batched(pc, coords_of(poi[CAT_TRANSPORT])).round(0)
    parcels["dist_environnement_min_m"] = min_distance_batched(pc, coords_of(poi[CAT_ENVIRO])).round(0)
    parcels["dist_nuisance_min_m"]      = min_distance_batched(pc, coords_of(poi[CAT_NUISANCE])).round(0)

    # 4) Distances aux axes routiers
    print("\n-> Reseau routier")
    road_nodes = {}
    for cls, vals in ROAD_TYPES.items():
        elems = fetch_roads(vals, BBOX)
        nodes = roads_to_node_array(elems)
        road_nodes[cls] = nodes
        print("   {:10s} {} noeuds".format(cls, len(nodes)))
        time.sleep(2)

    parcels["dist_autoroute_min_m"]        = min_distance_batched(pc, road_nodes["motorway"]).round(0)
    parcels["dist_voie_primaire_min_m"]    = min_distance_batched(pc, road_nodes["primary"]).round(0)
    parcels["dist_voie_secondaire_min_m"]  = min_distance_batched(pc, road_nodes["secondary"]).round(0)

    # nb_axes_500m : on agrege motorway+primary+secondary (noeuds)
    all_axes = np.vstack([n for n in road_nodes.values() if len(n) > 0]) \
               if any(len(n) > 0 for n in road_nodes.values()) else np.empty((0, 2))
    parcels["nb_axes_500m"] = count_in_radius_batched(pc, all_axes, 500)

    # dist_boulevard_principal : depuis boulevards_points.csv
    bd = load_simple_csv("boulevards_points.csv")
    parcels["dist_boulevard_principal"] = min_distance_batched(pc, coords_of(bd)).round(0)

    # 5) Distances ciblees (ecole, clinique, tram, parc, mer)
    print("\n-> Distances ciblees")

    # ecoles (subtype == 'school')
    edu = poi[CAT_EDUCATION]
    ecoles = edu[edu["subtype"] == "school"] if "subtype" in edu.columns and not edu.empty else edu
    parcels["dist_ecole"] = min_distance_batched(pc, coords_of(ecoles)).round(0)

    # cliniques / hopitaux (subtype in 'clinic','hospital')
    sante = poi[CAT_SANTE]
    if "subtype" in sante.columns and not sante.empty:
        cliniques = sante[sante["subtype"].isin(["clinic", "hospital"])]
    else:
        cliniques = sante
    parcels["dist_clinique"] = min_distance_batched(pc, coords_of(cliniques)).round(0)

    # tram (depuis poi_tram.csv si dispo, sinon filtre transports_lourds)
    tram = load_simple_csv("poi_tram.csv")
    if tram.empty:
        tr = poi[CAT_TRANSPORT]
        if "subtype" in tr.columns and not tr.empty:
            tram = tr[tr["subtype"] == "tram_stop"]
        else:
            tram = pd.DataFrame(columns=["lat", "lon"])
    parcels["dist_tram"] = min_distance_batched(pc, coords_of(tram)).round(0)

    # parcs (subtype == 'park')
    env = poi[CAT_ENVIRO]
    parcs = env[env["subtype"] == "park"] if "subtype" in env.columns and not env.empty else env
    parcels["dist_parc"] = min_distance_batched(pc, coords_of(parcs)).round(0)

    # mer (trait de cote)
    coast = load_simple_csv("coastline_points.csv")
    parcels["dist_mer"] = min_distance_batched(pc, coords_of(coast)).round(0)

    # 6) Comptes en buffer
    print("\n-> Comptes en buffer")
    parcels["nb_ecoles_1km"]      = count_in_radius_batched(pc, coords_of(ecoles), 1000)
    parcels["nb_sante_1km"]       = count_in_radius_batched(pc, coords_of(poi[CAT_SANTE]), 1000)
    parcels["nb_commerces_1km"]   = count_in_radius_batched(pc, coords_of(poi[CAT_COMMERCE]), 1000)
    parcels["nb_banques_1km"]     = count_in_radius_batched(pc, coords_of(poi[CAT_BANQUE]), 1000)
    parcels["nb_stations_1km"]    = count_in_radius_batched(pc, coords_of(poi[CAT_TRANSPORT]), 1000)
    parcels["nb_nuisance"]        = count_in_radius_batched(pc, coords_of(poi[CAT_NUISANCE]), 1000)
    parcels["nb_nuisance_500m"]   = count_in_radius_batched(pc, coords_of(poi[CAT_NUISANCE]), 500)

    # restaurants : sous-categorie de restauration_loisirs
    resto = poi[CAT_RESTO]
    if "subtype" in resto.columns and not resto.empty:
        restos_only = resto[resto["subtype"].isin(["restaurant", "cafe", "fast_food"])]
    else:
        restos_only = resto
    parcels["nb_restaurants_500m"] = count_in_radius_batched(pc, coords_of(restos_only), 500)

    # 7) Densite et surface verte
    print("\n-> Densite education et surface verte")
    AREA_KM2_1KM_BUFFER = math.pi * 1.0 * 1.0  # ~3.1416 km2
    parcels["densite_education"] = (parcels["nb_ecoles_1km"] / AREA_KM2_1KM_BUFFER).round(3)

    green = load_simple_csv("green_polygons.csv")
    if not green.empty and "surface_m2" in green.columns:
        gcoords = green[["lat", "lon"]].to_numpy(dtype=float)
        gareas  = green["surface_m2"].to_numpy(dtype=float)
        parcels["surface_verte_1km"] = sum_weight_in_radius_batched(pc, gcoords, gareas, 1000).round(0)
    else:
        parcels["surface_verte_1km"] = 0.0

    # 8) Temps de trajet (vol d'oiseau / vitesse moyenne)
    print("\n-> Temps de trajet (haversine / {} km/h)".format(VITESSE_URBAINE_KMH))
    vitesse_m_par_min = VITESSE_URBAINE_KMH * 1000.0 / 60.0
    for label, (lat, lon) in LANDMARKS.items():
        d = haversine_to_point(pc, lat, lon)  # metres
        parcels["temps_{}".format(label)] = (d / vitesse_m_par_min).round(2)

    # 9) Selection finale : metadonnees + 32 colonnes cibles, dans l'ordre demande
    target_columns = [
        "dist_education_min_m", "dist_banque_min_m", "dist_sante_min_m",
        "dist_commerce_min_m", "dist_transport_min_m", "dist_environnement_min_m",
        "nb_nuisance_500m", "dist_nuisance_min_m",
        "dist_autoroute_min_m", "dist_voie_primaire_min_m",
        "dist_voie_secondaire_min_m", "dist_boulevard_principal",
        "nb_axes_500m", "dist_tram", "nb_stations_1km",
        "temps_transport_centre", "temps_CFC", "temps_Maarif",
        "temps_SidiMaarouf", "temps_port",
        "nb_ecoles_1km", "dist_ecole", "densite_education",
        "nb_sante_1km", "dist_clinique",
        "nb_restaurants_500m", "nb_commerces_1km", "nb_banques_1km",
        "dist_mer", "dist_parc", "surface_verte_1km", "nb_nuisance",
    ]

    # Verification : toutes les colonnes attendues doivent exister
    missing = [c for c in target_columns if c not in parcels.columns]
    if missing:
        raise SystemExit("Colonnes manquantes : {}".format(missing))

    meta_cols = ["osm_id", "osm_type", "lat", "lon", "surface_m2",
                 "building_type", "levels", "name", "addr_street", "addr_housenumber"]
    final_cols = [c for c in meta_cols if c in parcels.columns] + target_columns
    out = parcels[final_cols]

    out.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
    print("\n=== Resultat ===")
    print("Parcelles : {}".format(len(out)))
    print("Variables cibles : {}".format(len(target_columns)))
    print("Total colonnes   : {}".format(out.shape[1]))
    print("Fichier   : {}".format(OUT_FILE.resolve()))


if __name__ == "__main__":
    main()
