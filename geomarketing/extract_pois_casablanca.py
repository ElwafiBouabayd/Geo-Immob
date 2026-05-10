# -*- coding: utf-8 -*-
"""
Extraction des POIs de Casablanca depuis OpenStreetMap (API Overpass).

Cette version produit, en plus des POIs classiques :
  - le trait de cote (natural=coastline)         -> coastline_points.csv
  - les polygones d'espaces verts avec surface   -> green_polygons.csv
  - les boulevards principaux nommes             -> boulevards_points.csv
  - les arrets de tram                           -> poi_tram.csv

Sortie : dossier ./pois_casablanca/

Dependances :
    pip install requests pandas numpy

Usage :
    python extract_pois_casablanca.py
"""

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

OUT_DIR = Path("pois_casablanca")
OUT_DIR.mkdir(exist_ok=True)

CATEGORIES = {
    "education": [
        ('amenity', 'school'),
        ('amenity', 'kindergarten'),
        ('amenity', 'college'),
        ('amenity', 'university'),
        ('amenity', 'language_school'),
    ],
    "sante": [
        ('amenity', 'hospital'),
        ('amenity', 'clinic'),
        ('amenity', 'doctors'),
        ('amenity', 'pharmacy'),
        ('amenity', 'dentist'),
    ],
    "commerce_retail": [
        ('shop', 'mall'),
        ('shop', 'supermarket'),
        ('shop', 'convenience'),
        ('shop', 'department_store'),
        ('amenity', 'marketplace'),
    ],
    "restauration_loisirs": [
        ('amenity', 'restaurant'),
        ('amenity', 'cafe'),
        ('amenity', 'fast_food'),
        ('tourism', 'hotel'),
        ('leisure', 'fitness_centre'),
        ('amenity', 'cinema'),
        ('amenity', 'theatre'),
    ],
    "services_publics": [
        ('amenity', 'post_office'),
        ('amenity', 'townhall'),
        ('amenity', 'courthouse'),
        ('amenity', 'police'),
        ('amenity', 'fire_station'),
    ],
    "transports_lourds": [
        ('railway', 'station'),
        ('railway', 'tram_stop'),
        ('aeroway', 'aerodrome'),
        ('amenity', 'bus_station'),
    ],
    "transports_diffus": [
        ('highway', 'bus_stop'),
        ('amenity', 'taxi'),
        ('amenity', 'parking'),
        ('amenity', 'fuel'),
        ('amenity', 'charging_station'),
    ],
    "espaces_verts_loisirs": [
        ('leisure', 'park'),
        ('leisure', 'garden'),
        ('leisure', 'golf_course'),
        ('natural', 'beach'),
        ('leisure', 'pitch'),
    ],
    "culte_culture": [
        ('amenity', 'place_of_worship'),
        ('tourism', 'museum'),
        ('historic', 'monument'),
    ],
    "banque_finance": [
        ('amenity', 'bank'),
        ('amenity', 'atm'),
        ('amenity', 'bureau_de_change'),
    ],
    "industrie_nuisances": [
        ('landuse', 'industrial'),
        ('amenity', 'waste_disposal'),
        ('amenity', 'recycling'),
        ('landuse', 'cemetery'),
        ('man_made', 'wastewater_plant'),
    ],
    "economie_b2b": [
        ('office', 'company'),
        ('office', 'coworking'),
        ('amenity', 'conference_centre'),
    ],
    "accessibilite_routiere": [
        ('highway', 'motorway_junction'),
        ('barrier', 'toll_booth'),
        ('highway', 'mini_roundabout'),
        ('highway', 'traffic_signals'),
        ('highway', 'rest_area'),
        ('highway', 'services'),
    ],
}

ROAD_TYPES = {
    "motorway":  ["motorway", "motorway_link"],
    "primary":   ["trunk", "trunk_link", "primary", "primary_link"],
    "secondary": ["secondary", "secondary_link", "tertiary", "tertiary_link"],
}

# Polygones d'espaces verts pour le calcul de la surface verte
GREEN_POLYGONS = [
    ('leisure', 'park'),
    ('leisure', 'garden'),
    ('leisure', 'pitch'),
    ('leisure', 'playground'),
    ('leisure', 'golf_course'),
    ('landuse', 'grass'),
    ('landuse', 'recreation_ground'),
    ('landuse', 'forest'),
    ('natural', 'wood'),
    ('natural', 'grassland'),
    ('natural', 'scrub'),
]


def build_query(filters, bbox):
    s, w, n, e = bbox
    parts = []
    for key, value in filters:
        for t in ("node", "way", "relation"):
            parts.append('{}["{}"="{}"]({},{},{},{});'.format(t, key, value, s, w, n, e))
    body = "\n  ".join(parts)
    return "[out:json][timeout:120];\n(\n  " + body + "\n);\nout center tags;\n"


def overpass_request(query, retries=3):
    last_err = None
    for endpoint in OVERPASS_ENDPOINTS:
        host = endpoint.split('//')[1].split('/')[0]
        for attempt in range(retries):
            try:
                r = requests.post(endpoint, data=query.encode("utf-8"),
                                  headers=HEADERS, timeout=240)
                r.raise_for_status()
                return r.json().get("elements", [])
            except Exception as e:
                last_err = e
                print("  [{} try {}] {}".format(host, attempt + 1, e))
                time.sleep(3 * (attempt + 1))
    raise RuntimeError("Tous les endpoints Overpass ont echoue: {}".format(last_err))


def fetch_category(name, filters, bbox):
    try:
        return overpass_request(build_query(filters, bbox))
    except Exception as e:
        print("  ERREUR {}: {}".format(name, e))
        return []


def fetch_roads(road_class, road_values, bbox):
    s, w, n, e = bbox
    regex = "|".join(road_values)
    query = ('[out:json][timeout:180];\n(\n'
             '  way["highway"~"^({})$"]({},{},{},{});\n);\nout geom;\n'
             ).format(regex, s, w, n, e)
    try:
        return overpass_request(query)
    except Exception as e:
        print("  ERREUR roads {}: {}".format(road_class, e))
        return []


def fetch_coastline(bbox):
    """Trait de cote (natural=coastline) pour la distance a la mer."""
    s, w, n, e = bbox
    query = ('[out:json][timeout:180];\n(\n'
             '  way["natural"="coastline"]({},{},{},{});\n);\nout geom;\n'
             ).format(s, w, n, e)
    try:
        return overpass_request(query)
    except Exception as e:
        print("  ERREUR coastline: {}".format(e))
        return []


def fetch_named_boulevards(bbox):
    """Boulevards principaux nommes (highway primary/secondary avec 'boulevard'/'Bd' dans le nom)."""
    s, w, n, e = bbox
    query = ('[out:json][timeout:180];\n(\n'
             '  way["highway"~"^(primary|primary_link|trunk|trunk_link|secondary|secondary_link)$"]'
             '["name"~"[Bb]oulevard|^[Bb]d |[Bb]d\\\\."]({},{},{},{});\n'
             ');\nout geom tags;\n').format(s, w, n, e)
    try:
        return overpass_request(query)
    except Exception as e:
        print("  ERREUR boulevards: {}".format(e))
        return []


def fetch_green_polygons(bbox):
    """Polygones d'espaces verts avec geometrie complete (pour calcul de surface)."""
    s, w, n, e = bbox
    parts = []
    for key, value in GREEN_POLYGONS:
        parts.append('way["{}"="{}"]({},{},{},{});'.format(key, value, s, w, n, e))
        parts.append('relation["{}"="{}"]({},{},{},{});'.format(key, value, s, w, n, e))
    body = "\n  ".join(parts)
    query = "[out:json][timeout:180];\n(\n  " + body + "\n);\nout geom tags;\n"
    try:
        return overpass_request(query)
    except Exception as e:
        print("  ERREUR green polygons: {}".format(e))
        return []


def polygon_centroid_area(geom_pts):
    """Centroide et surface (m2) d'un polygone defini par une liste de points lat/lon."""
    if not geom_pts or len(geom_pts) < 3:
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
    return float(centroid_lat), float(centroid_lon), float(area)


def elements_to_rows(elements, category):
    rows = []
    for el in elements:
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center", {})
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        rows.append({
            "category": category,
            "osm_id": el["id"],
            "osm_type": el["type"],
            "name": tags.get("name") or tags.get("name:fr") or tags.get("name:ar"),
            "subtype": (tags.get("amenity") or tags.get("shop") or tags.get("leisure")
                        or tags.get("tourism") or tags.get("railway") or tags.get("highway")
                        or tags.get("office") or tags.get("landuse") or tags.get("natural")
                        or tags.get("historic") or tags.get("aeroway") or tags.get("man_made")),
            "lat": lat, "lon": lon,
            "address": tags.get("addr:street"),
            "city": tags.get("addr:city"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "website": tags.get("website") or tags.get("contact:website"),
        })
    return rows


def coastline_to_points(elements):
    """Echantillonne le trait de cote en points lat/lon."""
    rows = []
    for el in elements:
        for pt in el.get("geometry", []):
            rows.append({"osm_id": el["id"], "lat": pt["lat"], "lon": pt["lon"]})
    return rows


def boulevards_to_points(elements):
    """Echantillonne les boulevards en points lat/lon (avec nom)."""
    rows = []
    for el in elements:
        nm = (el.get("tags", {}) or {}).get("name", "")
        for pt in el.get("geometry", []):
            rows.append({"osm_id": el["id"], "name": nm, "lat": pt["lat"], "lon": pt["lon"]})
    return rows


def green_polygons_to_rows(elements):
    """Reduit les polygones verts a (centroide, surface_m2, type)."""
    rows = []
    for el in elements:
        geom = el.get("geometry") or []
        if not geom:
            continue
        lat, lon, area = polygon_centroid_area(geom)
        if lat is None or area <= 0:
            continue
        tags = el.get("tags", {})
        rows.append({
            "osm_id": el["id"],
            "osm_type": el["type"],
            "type_vert": (tags.get("leisure") or tags.get("landuse") or tags.get("natural")),
            "name": tags.get("name"),
            "lat": lat,
            "lon": lon,
            "surface_m2": round(area, 1),
        })
    return rows


def roads_to_points(elements):
    coords = []
    for el in elements:
        for pt in el.get("geometry", []):
            coords.append((pt["lat"], pt["lon"]))
    return np.array(coords) if coords else np.empty((0, 2))


def haversine_min(poi, road):
    if len(road) == 0 or len(poi) == 0:
        return np.full(len(poi), np.nan)
    R = 6371000.0
    a = np.radians(poi)
    b = np.radians(road)
    out = np.empty(len(poi))
    bs = 200
    for i in range(0, len(poi), bs):
        c = a[i:i+bs]
        dlat = c[:, 0:1] - b[:, 0]
        dlon = c[:, 1:2] - b[:, 1]
        h = np.sin(dlat/2)**2 + np.cos(c[:, 0:1]) * np.cos(b[:, 0]) * np.sin(dlon/2)**2
        out[i:i+bs] = (2 * R * np.arcsin(np.sqrt(h))).min(axis=1)
    return out


def add_road_accessibility(df, bbox):
    if df.empty:
        return df
    coords = df[["lat", "lon"]].to_numpy(dtype=float)
    for rc, rv in ROAD_TYPES.items():
        print("-> Reseau routier '{}'".format(rc))
        elems = fetch_roads(rc, rv, bbox)
        rcoords = roads_to_points(elems)
        print("   {} segments / {} noeuds".format(len(elems), len(rcoords)))
        df["dist_{}_m".format(rc)] = haversine_min(coords, rcoords).round(0)
        time.sleep(2)
    s = np.zeros(len(df))
    s = np.maximum(s, np.where(df["dist_motorway_m"]  < 1000, 1 - df["dist_motorway_m"]/1000,  0))
    s = np.maximum(s, np.where(df["dist_primary_m"]   < 500,  0.8 * (1 - df["dist_primary_m"]/500),  0))
    s = np.maximum(s, np.where(df["dist_secondary_m"] < 300,  0.5 * (1 - df["dist_secondary_m"]/300), 0))
    df["road_accessibility_score"] = s.round(3)
    return df


def main():
    all_rows = []
    summary = []

    # ---- 1) POIs classiques ----
    for cat, filters in CATEGORIES.items():
        print("-> {} ({} filtres)".format(cat, len(filters)))
        elements = fetch_category(cat, filters, BBOX)
        rows = elements_to_rows(elements, cat)
        pd.DataFrame(rows).to_csv(OUT_DIR / "poi_{}.csv".format(cat),
                                  index=False, encoding="utf-8-sig")
        print("   {:>5} POIs".format(len(rows)))
        summary.append({"category": cat, "count": len(rows)})
        all_rows.extend(rows)
        time.sleep(2)

    # ---- 2) Sous-categorie : tram (filtree depuis transports_lourds) ----
    print("\n-> Extraction des arrets de tram (filtre subtype=tram_stop)")
    df_lourds = pd.DataFrame([r for r in all_rows if r.get("category") == "transports_lourds"])
    if not df_lourds.empty:
        df_tram = df_lourds[df_lourds["subtype"] == "tram_stop"].copy()
        df_tram.to_csv(OUT_DIR / "poi_tram.csv", index=False, encoding="utf-8-sig")
        summary.append({"category": "tram", "count": len(df_tram)})
        print("   {} arrets de tram".format(len(df_tram)))
    else:
        pd.DataFrame().to_csv(OUT_DIR / "poi_tram.csv", index=False, encoding="utf-8-sig")
        summary.append({"category": "tram", "count": 0})

    # ---- 3) Trait de cote (dist_mer) ----
    print("\n-> Trait de cote (natural=coastline)")
    coast_elems = fetch_coastline(BBOX)
    coast_rows = coastline_to_points(coast_elems)
    pd.DataFrame(coast_rows).to_csv(OUT_DIR / "coastline_points.csv",
                                    index=False, encoding="utf-8-sig")
    summary.append({"category": "coastline_points", "count": len(coast_rows)})
    print("   {} points de cote".format(len(coast_rows)))

    # ---- 4) Polygones d'espaces verts (surface_verte_1km) ----
    print("\n-> Polygones d'espaces verts (avec surface)")
    time.sleep(2)
    green_elems = fetch_green_polygons(BBOX)
    green_rows = green_polygons_to_rows(green_elems)
    pd.DataFrame(green_rows).to_csv(OUT_DIR / "green_polygons.csv",
                                    index=False, encoding="utf-8-sig")
    summary.append({"category": "green_polygons", "count": len(green_rows)})
    print("   {} polygones verts".format(len(green_rows)))

    # ---- 5) Boulevards principaux nommes ----
    print("\n-> Boulevards principaux nommes")
    time.sleep(2)
    bd_elems = fetch_named_boulevards(BBOX)
    bd_rows = boulevards_to_points(bd_elems)
    pd.DataFrame(bd_rows).to_csv(OUT_DIR / "boulevards_points.csv",
                                 index=False, encoding="utf-8-sig")
    summary.append({"category": "boulevards_points", "count": len(bd_rows)})
    print("   {} points (sur {} boulevards)".format(len(bd_rows), len(bd_elems)))

    # ---- 6) Consolidation + accessibilite routiere par POI ----
    consolidated = pd.DataFrame(all_rows)
    print("\n=== Accessibilite routiere ===")
    consolidated = add_road_accessibility(consolidated, BBOX)
    consolidated.to_csv(OUT_DIR / "poi_all.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(summary).to_csv(OUT_DIR / "summary.csv", index=False, encoding="utf-8-sig")

    print("\n=== Resume ===")
    print(pd.DataFrame(summary).to_string(index=False))
    print("\nTotal POIs: {} - dossier: {}".format(len(consolidated), OUT_DIR.resolve()))


if __name__ == "__main__":
    main()
