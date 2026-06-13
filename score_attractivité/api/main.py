"""
API FastAPI — Score d'attractivite residentielle — Casablanca v2

Lancement :
    cd C:\\Users\\del\\Desktop\\scoring\\api
    uvicorn main:app --reload --port 8001

Endpoints :
    GET  /api/health
    GET  /api/dimensions         → dimensions, variables, signes, poids
    GET  /api/weights            → poids des 4 dimensions
    GET  /api/zones              → liste des zones (code + libelle + arrondissement)
    GET  /api/zones/scores       → tableau complet des scores (avec rang)
    GET  /api/zones/map          → zones + lat/lng + scores (pour la carte)
    GET  /api/zones/{code}       → detail d'une zone (features + scores)
    POST /api/score              → calcul d'un score pour des valeurs custom
"""

from __future__ import annotations

import os
from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scoring import (
    ALL_VARS,
    DIMENSIONS,
    DIMENSIONS_VAR_SIGN,
    WEIGHTS,
    all_zone_scores,
    get_zone_features,
    load_coordinates,
    load_zone_data,
    score_from_variables,
)

app = FastAPI(
    title="Score d'attractivite Residentielle — Casablanca v2",
    description=(
        "API de calcul du score d'attractivite par zone (4 dimensions : "
        "Dynamisme, Socioeco, Accessibilite, Equipements)."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# Modeles Pydantic
# ─────────────────────────────────────────────

class ScoreRequest(BaseModel):
    values: Dict[str, float] = Field(
        ...,
        description="Dict variable → valeur. Toutes les variables sont requises.",
    )


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/dimensions")
def get_dimensions():
    """Structure des 4 dimensions avec leurs variables, signes et poids."""
    return [
        {
            "dimension": dim,
            "poids_dimension": round(WEIGHTS[dim] * 100, 0),
            "variables": [
                {
                    "name": v,
                    "sign": sign,
                    "direction": "negatif" if sign == "neg" else "positif",
                    "poids_intra": round(w_intra * 100, 0),
                }
                for v, (sign, w_intra) in var_dict.items()
            ],
        }
        for dim, var_dict in DIMENSIONS.items()
    ]


@app.get("/api/weights")
def get_weights():
    return [
        {"dimension": d, "poids": round(WEIGHTS[d] * 100, 1)}
        for d in DIMENSIONS
    ]


@app.get("/api/zones")
def list_zones():
    """Liste des zones pour les selecteurs."""
    df = load_zone_data()
    cols = [c for c in ["Code Zone", "Zone", "Arrondissement"] if c in df.columns]
    return df[cols].fillna("").to_dict(orient="records")


@app.get("/api/zones/scores")
def all_scores():
    """Tableau complet des scores, trie par rang."""
    return all_zone_scores().fillna(0).to_dict(orient="records")


@app.get("/api/zones/map")
def zones_for_map():
    """Zones avec coordonnees lat/lng et scores (pour la carte Leaflet)."""
    coords = load_coordinates()
    if coords.empty:
        return {
            "available": False,
            "message": "Coordonnees indisponibles dans osm_zones_data.csv.",
            "zones": [],
        }

    scores = all_zone_scores()
    merged = scores.merge(coords, on="Code Zone", how="left")

    # Preferer lat/lng de scores (deja presents) sinon depuis coords
    if "lat_x" in merged.columns:
        merged["lat"] = merged["lat_x"].combine_first(merged["lat_y"])
        merged["lng"] = merged["lng_x"].combine_first(merged["lng_y"])
        merged = merged.drop(columns=["lat_x", "lat_y", "lng_x", "lng_y"], errors="ignore")

    merged = merged.dropna(subset=["lat", "lng"])

    return {
        "available": True,
        "count": int(len(merged)),
        "zones": merged.fillna(0).to_dict(orient="records"),
    }


@app.get("/api/zones/{code_zone}")
def zone_detail(code_zone: str):
    """Detail complet d'une zone : features brutes + sous-scores + score global."""
    try:
        features = get_zone_features(code_zone)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    scores_df = all_zone_scores()
    row = scores_df[scores_df["Code Zone"] == code_zone]
    if row.empty:
        raise HTTPException(status_code=404, detail="Zone non scoree")
    info = row.iloc[0].to_dict()

    return {
        "code_zone":      code_zone,
        "zone":           info.get("Zone", ""),
        "arrondissement": info.get("Arrondissement", ""),
        "rang":           int(info.get("Rang", 0)),
        "total_zones":    int(len(scores_df)),
        "prix_m2_moyen":  info.get("prix_m2_moyen"),
        "scores": {
            **{f"Score_{dim}": info.get(f"Score_{dim}", 0) for dim in DIMENSIONS},
            "Score_Attractivite": info.get("Score_Attractivite", 0),
        },
        "features": features,
    }


@app.post("/api/score")
def compute_score(req: ScoreRequest):
    """Calcule le score pour un jeu de valeurs custom."""
    try:
        result = score_from_variables(req.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


# ─────────────────────────────────────────────
# Frontend statique
# ─────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
