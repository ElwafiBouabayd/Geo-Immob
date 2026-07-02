from __future__ import annotations
import json
import time
from pathlib import Path

import requests

# -----------------------------------------------
# CONFIG
# -----------------------------------------------
HERE = Path(__file__).parent
CACHE_DIR = HERE / "osm_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Bounding box Grand Casablanca (sud, ouest, nord, est)
BBOX = (33.35, -7.90, 33.70, -7.40)

# Endpoints Overpass avec fallback automatique
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

USER_AGENT = "GeoScoring/1.0 (attractivite-casablanca)"

# -----------------------------------------------
# REQUETES OVERPASS (bounding box entiere)
# -----------------------------------------------
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
    "cafe": """
        [out:json][timeout:180];
        node["amenity"="cafe"]({s},{w},{n},{e});
        out;
    """,
    "mall": """
        [out:json][timeout:180];
        (
          node["shop"="mall"]({s},{w},{n},{e});
          way ["shop"="mall"]({s},{w},{n},{e});
        );
        out center;
    """,
}


# -----------------------------------------------
# TELECHARGEMENT OVERPASS AVEC CACHE + FALLBACK
# -----------------------------------------------

def _clean(q: str) -> str:
    return "\n".join(ln.strip() for ln in q.splitlines() if ln.strip())


def overpass_fetch(name: str, query: str, bbox=BBOX) -> list:
    """
    Telecharge les elements OSM pour le type `name` sur toute la bbox.
    Resultat mis en cache disque -> pas de re-telechargement si relance.
    Retourne une liste d'elements Overpass.
    """
    cache_path = CACHE_DIR / f"{name}.json"
    if cache_path.exists():
        print(f"  [cache] {name}", flush=True)
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)["elements"]

    s, w, n, e = bbox
    q = _clean(query.format(s=s, w=w, n=n, e=e))
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    print(f"  [telechargement] {name} ...", flush=True)
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
                    print(f"    -> {len(data['elements'])} elements", flush=True)
                    return data["elements"]
                if r.status_code in (429, 504):
                    print(f"    {endpoint} : HTTP {r.status_code}, attente 20s ...", flush=True)
                    time.sleep(20)
                    continue
                last_err = f"HTTP {r.status_code}"
                print(f"    {endpoint} : {last_err}", flush=True)
                break
            except Exception as exc:
                last_err = str(exc)
                print(f"    {endpoint} : erreur ({last_err}), retry 10s ...", flush=True)
                time.sleep(10)

    print(f"  AVERTISSEMENT : impossible de telecharger {name} ({last_err})", flush=True)
    return []


# -----------------------------------------------
# MAIN
# -----------------------------------------------

if __name__ == "__main__":
    print(f"=== Collecte OSM cache - {len(QUERIES)} types de POI ===", flush=True)
    print(f"Zone : Grand Casablanca (bbox {BBOX})\n", flush=True)

    for name, q in QUERIES.items():
        elements = overpass_fetch(name, q)
        print(f"   {name:15s} : {len(elements):>5} elements", flush=True)

    print(f"\n✓ Termine. Fichiers a jour dans : {CACHE_DIR}", flush=True)
