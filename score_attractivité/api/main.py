"""
API FastAPI - Score d'attractivite des zones de Casablanca.

Lancement :
    cd score_attractivite/api
    uvicorn main:app --reload --port 8000

Endpoints :
    GET  /api/health
    GET  /api/dimensions      -> liste des dimensions et variables
    GET  /api/weights         -> poids par dimension
    GET  /api/zones           -> liste des zones (code + libelle + arrondissement)
    GET  /api/zones/scores    -> tableau complet des scores (rang inclus)
    GET  /api/zones/map       -> zones + lat/lng + score (pour la carte)
    GET  /api/zones/{code}    -> detail d'une zone (features + score)
    POST /api/score           -> calcul d'un score pour des valeurs custom
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from scoring import (
    ALL_VARS,
    DIMENSIONS,
    DIMENSIONS_VAR_SIGN,
    all_zone_scores,
    get_zone_features,
    load_coordinates,
    load_weights,
    load_zones,
    score_from_variables,
)


app = FastAPI(
    title="Score d'attractivite - Casablanca",
    description=(
        "API de calcul du score d'attractivite par zone, base sur une analyse "
        "hedonique (4 dimensions : Accessibilite, Amenites, Environnement, "
        "SocioDemographie)."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -----------------------------------------------------------------------------
# Modeles
# -----------------------------------------------------------------------------
class ScoreRequest(BaseModel):
    """Valeurs des variables pour un calcul de score custom."""
    values: Dict[str, float] = Field(
        ...,
        description="Dict variable -> valeur. Toutes les variables sont requises.",
    )


class ScoreResponse(BaseModel):
    Score_Accessibilite: float
    Score_Amenites:      float
    Score_Environnement: float
    Score_SocioDemo:     float
    Score_Attractivite:  float


# -----------------------------------------------------------------------------
# Endpoints API
# -----------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/dimensions")
def get_dimensions():
    """Retourne la structure des 4 dimensions avec leurs variables et signes."""
    out = []
    for dim, var_dict in DIMENSIONS.items():
        out.append({
            "dimension": dim,
            "variables": [
                {"name": v, "sign": s, "direction": "negatif" if s == "neg" else "positif"}
                for v, s in var_dict.items()
            ],
        })
    return out


@app.get("/api/weights")
def get_weights():
    """Poids de chaque dimension (somme = 1)."""
    w = load_weights()
    return [{"dimension": d, "poids": round(w[d] * 100, 2)} for d in DIMENSIONS]


@app.get("/api/zones")
def list_zones():
    """Liste minimale des zones pour les selecteurs."""
    z = load_zones()[["Code Zone", "Zone déchiffrée", "Arrondissement", "Préfecture"]]
    z = z.rename(columns={"Zone déchiffrée": "Zone", "Préfecture": "Prefecture"})
    return z.to_dict(orient="records")


@app.get("/api/zones/scores")
def all_scores():
    """Tableau complet des scores, trie par rang d'attractivite."""
    return all_zone_scores().to_dict(orient="records")


@app.get("/api/zones/map")
def zones_for_map():
    """
    Zones avec leurs coordonnees lat/lng et leurs scores.

    Lit coordonnees_zones.csv s'il existe. Si le fichier est absent, renvoie
    une reponse avec available=false.
    """
    coords = load_coordinates()
    if coords.empty:
        return {
            "available": False,
            "message": (
                "Fichier coordonnees_zones.csv introuvable. "
                "Format attendu : colonnes 'Code Zone', 'lat', 'lng'."
            ),
            "zones": [],
        }

    scores = all_zone_scores()
    merged = scores.merge(coords, on="Code Zone", how="left")
    merged = merged.dropna(subset=["lat", "lng"])

    return {
        "available": True,
        "count": int(len(merged)),
        "zones": merged.to_dict(orient="records"),
    }


@app.get("/api/zones/{code_zone}")
def zone_detail(code_zone: str):
    """Detail complet d'une zone : features + sous-scores + score global."""
    try:
        features = get_zone_features(code_zone)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    scores = all_zone_scores()
    row = scores[scores["Code Zone"] == code_zone]
    if row.empty:
        raise HTTPException(status_code=404, detail="Zone non scoree")
    info = row.iloc[0].to_dict()

    return {
        "code_zone":      code_zone,
        "zone":           info.get("Zone"),
        "arrondissement": info.get("Arrondissement"),
        "prefecture":     info.get("Prefecture"),
        "prix_m2":        info.get("Prix m2 (DH)"),
        "rang":           int(info.get("Rang", 0)),
        "scores": {
            "Score_Accessibilite": info["Score_Accessibilite"],
            "Score_Amenites":      info["Score_Amenites"],
            "Score_Environnement": info["Score_Environnement"],
            "Score_SocioDemo":     info["Score_SocioDemo"],
            "Score_Attractivite":  info["Score_Attractivite"],
        },
        "features": features,
    }


@app.post("/api/score", response_model=ScoreResponse)
def compute_score(req: ScoreRequest):
    """Calcule le score pour un jeu de valeurs custom."""
    try:
        result = score_from_variables(req.values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


# -----------------------------------------------------------------------------
# Frontend statique
# -----------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
