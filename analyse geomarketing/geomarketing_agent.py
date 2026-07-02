"""
Agent d'analyse geomarketing - Grand Casablanca
================================================
Prend en entree des coordonnees (latitude, longitude) et produit une analyse
geomarketing en langage naturel, en combinant :

  1. Contexte socio-demographique HCP (par commune/arrondissement)
     -> data_hcp.csv
  2. Points d'interet OSM en cache (banques, ecoles, sante, transport, etc.)
     -> osm_cache/*.json
  3. Limites administratives des communes (point-in-polygon)
     -> osm_cache/communes_boundaries.geojson

Prerequis :
    pip install requests pandas numpy shapely anthropic python-dotenv

Cle API requise pour la generation de l'analyse par le LLM :
    ANTHROPIC_API_KEY, definie soit dans l'environnement, soit dans un
    fichier .env place a cote de ce script (charge automatiquement).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

HERE = Path(__file__).parent
CACHE_DIR = HERE / "osm_cache"
HCP_CSV = HERE / "data_hcp.csv"
BOUNDARIES_GEOJSON = CACHE_DIR / "communes_boundaries.geojson"

CENTRE_VILLE = (33.5928, -7.6200)
VITESSE_MOYENNE_KMH = 25.0   # vitesse voiture (trajets banque/mall)
VITESSE_MARCHE_KMH = 5.0     # vitesse pieton (trajets de proximite)
FACTEUR_DETOUR = 1.30

R_500 = 500.0
R_1KM = 1000.0
R_2KM = 2000.0

POI_RADIUS = {
    "bus_stop": R_500,
    "tram_stop": R_500,
    "taxi": R_500,
    "school": R_1KM,
    "pharmacy": R_500,
    "supermarket": R_500,
    "mosque": R_500,
    "bank": R_1KM,
    "restaurant": R_500,
    "cafe": R_500,
    "mall": R_2KM,
}

POI_LABELS = {
    "bus_stop": "arrets_bus",
    "tram_stop": "arrets_tramway",
    "taxi": "stations_taxi",
    "school": "ecoles",
    "pharmacy": "pharmacies",
    "supermarket": "supermarches",
    "mosque": "mosquees",
    "bank": "banques",
    "restaurant": "restaurants",
    "cafe": "cafes",
    "mall": "malls",
}

# Temps de trajet vers le POI le plus proche par categorie : (nom_variable, vitesse_kmh)
# Vitesse pieton pour les usages de proximite, vitesse voiture pour banque/mall.
NEAREST_POI_CONFIG = {
    "tram_stop":   ("Temps_Tramway_Plus_Proche_min",     VITESSE_MARCHE_KMH),
    "school":      ("Temps_Ecole_Plus_Proche_min",       VITESSE_MARCHE_KMH),
    "supermarket": ("Temps_Supermarche_Plus_Proche_min", VITESSE_MARCHE_KMH),
    "mosque":      ("Temps_Mosquee_Plus_Proche_min",     VITESSE_MARCHE_KMH),
    "bank":        ("Temps_Banque_Plus_Proche_min",      VITESSE_MOYENNE_KMH),
    "mall":        ("Temps_Mall_Plus_Proche_min",        VITESSE_MOYENNE_KMH),
}

DEFAULT_MODEL = "claude-sonnet-5"


def haversine_vec(lat0, lon0, pts):
    if len(pts) == 0:
        return np.array([])
    R = 6_371_000.0
    p0 = math.radians(lat0)
    p1 = np.radians(pts[:, 0])
    dphi = np.radians(pts[:, 0] - lat0)
    dlmb = np.radians(pts[:, 1] - lon0)
    a = np.sin(dphi / 2) ** 2 + math.cos(p0) * np.cos(p1) * np.sin(dlmb / 2) ** 2
    return 2 * R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def count_within(lat, lon, pts, radius_m):
    if len(pts) == 0:
        return 0
    return int((haversine_vec(lat, lon, pts) <= radius_m).sum())


def temps_estime_min(dist_km, vitesse_kmh=VITESSE_MOYENNE_KMH):
    return round(dist_km * FACTEUR_DETOUR / vitesse_kmh * 60, 1)


def nearest_distance_km(lat, lon, pts):
    """Distance (km) au point le plus proche dans `pts`, ou None si `pts` est vide."""
    if len(pts) == 0:
        return None
    return float(haversine_vec(lat, lon, pts).min()) / 1000


def extract_coords(elements):
    coords = []
    for el in elements:
        if "lat" in el and "lon" in el:
            coords.append((el["lat"], el["lon"]))
        elif "center" in el:
            coords.append((el["center"]["lat"], el["center"]["lon"]))
    return np.array(coords) if coords else np.zeros((0, 2))


def load_poi_cache():
    poi = {}
    for name in POI_RADIUS:
        path = CACHE_DIR / f"{name}.json"
        if not path.exists():
            print(f"  [attention] cache manquant pour '{name}' ({path.name})", file=sys.stderr)
            poi[name] = np.zeros((0, 2))
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        poi[name] = extract_coords(data.get("elements", []))
    return poi


def load_hcp_data():
    df = pd.read_csv(HCP_CSV)
    df.columns = df.columns.str.strip()
    df["commune"] = df["commune"].str.strip()
    return df.set_index("commune")


def load_commune_boundaries():
    from shapely.geometry import shape
    if not BOUNDARIES_GEOJSON.exists():
        raise FileNotFoundError(f"Fichier introuvable : {BOUNDARIES_GEOJSON}")
    with open(BOUNDARIES_GEOJSON, encoding="utf-8") as f:
        gj = json.load(f)
    boundaries = []
    for feat in gj["features"]:
        commune = feat["properties"]["commune"]
        poly = shape(feat["geometry"])
        boundaries.append((commune, poly))
    return boundaries


def find_commune(lat, lon, boundaries):
    from shapely.geometry import Point
    pt = Point(lon, lat)
    for commune, poly in boundaries:
        if poly.contains(pt) or poly.touches(pt):
            return commune, True
    if not boundaries:
        return None, False
    best_commune, best_dist = None, float("inf")
    for commune, poly in boundaries:
        c = poly.centroid
        d = haversine_vec(lat, lon, np.array([[c.y, c.x]]))[0]
        if d < best_dist:
            best_dist, best_commune = d, commune
    return best_commune, False


def get_context(lat, lon):
    poi = load_poi_cache()
    hcp = load_hcp_data()
    boundaries = load_commune_boundaries()
    commune, match_exact = find_commune(lat, lon, boundaries)
    socio_demo = None
    if commune is not None and commune in hcp.index:
        socio_demo = hcp.loc[commune].to_dict()
    clat, clon = CENTRE_VILLE
    dist_km = haversine_vec(lat, lon, np.array([[clat, clon]]))[0] / 1000
    points_interet = {}
    for name, radius in POI_RADIUS.items():
        label = POI_LABELS[name]
        rayon_txt = f"{int(radius)}m" if radius < 1000 else f"{radius/1000:.0f}km"
        points_interet[f"nb_{label}_{rayon_txt}"] = count_within(lat, lon, poi[name], radius)
    accessibilite = {
        "distance_centre_ville_km": round(dist_km, 3),
        "temps_voiture_estime_min": temps_estime_min(dist_km),
    }
    for poi_name, (var_name, vitesse) in NEAREST_POI_CONFIG.items():
        d_km = nearest_distance_km(lat, lon, poi[poi_name])
        accessibilite[var_name] = temps_estime_min(d_km, vitesse) if d_km is not None else None

    return {
        "coordonnees": {"latitude": lat, "longitude": lon},
        "commune": commune,
        "commune_match_exact": match_exact,
        "socio_demographique": socio_demo,
        "accessibilite": accessibilite,
        "points_interet": points_interet,
    }


SYSTEM_PROMPT = """Tu es un analyste geomarketing specialise dans le Grand Casablanca.
On te fournit un contexte structure (JSON) pour un point precis : donnees
socio-demographiques de la commune (source HCP) et points d'interet recenses
autour du point (source OpenStreetMap), avec distances/temps d'acces au
centre-ville.

Redige une analyse factuelle de ce point pour une decision d'implantation
commerciale ou immobiliere, en Markdown, structuree en exactement 3 parties
(rien avant, rien apres) :
## 1. Profil socio-demographique de la zone
## 2. Accessibilite et connectivite (transport, distance centre-ville)
## 3. Densite et nature des services de proximite

Utilise ces 3 titres de niveau 2 (##) exactement, dans cet ordre. N'ajoute
aucun titre general avant la partie 1 (pas de titre "# Analyse du point..."
ni de phrase d'introduction) : commence directement par "## 1. ...". N'ajoute
aucune quatrieme partie de type synthese, conclusion, forces et faiblesses,
ou recommandation : arrete-toi juste apres la partie 3.

Dans la partie 2, tiens compte des temps de trajet estimes vers le POI le
plus proche de chaque categorie (champs Temps_..._Plus_Proche_min), en plus
de la distance au centre-ville.

Sois factuel, base-toi uniquement sur les donnees fournies, et signale
explicitement si une donnee manque (ex: commune sans correspondance HCP)."""


def generate_analysis(context, model=DEFAULT_MODEL):
    import anthropic
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False, indent=2),
        }],
    )
    # message.content peut contenir des blocs autres que du texte (ex: ThinkingBlock
    # si le raisonnement etendu est actif) : on ne garde que les blocs de type "text".
    texts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
    if not texts:
        raise RuntimeError("Aucun bloc de texte dans la reponse du modele (contenu : "
                            f"{[getattr(b, 'type', type(b).__name__) for b in message.content]})")
    return "\n".join(texts)


def main():
    parser = argparse.ArgumentParser(description="Agent d'analyse geomarketing (Grand Casablanca)")
    parser.add_argument("--lat", type=float, required=True, help="Latitude du point")
    parser.add_argument("--lon", type=float, required=True, help="Longitude du point")
    parser.add_argument("--context-only", action="store_true", help="N'affiche que le contexte structure (JSON)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    print(f">> Calcul du contexte pour ({args.lat}, {args.lon}) ...", file=sys.stderr)
    context = get_context(args.lat, args.lon)

    if not context["commune_match_exact"]:
        print(f"  [attention] point hors des polygones connus, commune la plus proche retenue : {context['commune']}", file=sys.stderr)
    if context["socio_demographique"] is None:
        print(f"  [attention] aucune donnee HCP trouvee pour la commune '{context['commune']}'", file=sys.stderr)

    if args.context_only:
        output_text = json.dumps(context, ensure_ascii=False, indent=2)
        print(output_text)
    else:
        print(">> Generation de l'analyse (appel API Claude) ...", file=sys.stderr)
        output_text = generate_analysis(context, model=args.model)
        print(output_text)

    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
        print(f"\n>> Sauvegarde : {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
