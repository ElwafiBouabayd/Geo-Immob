"""
API FastAPI — Estimation du prix au m² des appartements à Casablanca.

Modèle   : XGBoost régressant directement Prix/m² (DH).
Artefacts: estimer_apparts/model_artifacts/
             best_xgboost_pipeline.joblib   (preprocessor + XGBRegressor)
             label_encoders.joblib          (LabelEncoder par colonne catégorielle)

Endpoints
---------
GET  /                       Redirection vers /ui
GET  /ui                     Interface web (estimation temps réel)
GET  /docs                   Documentation Swagger
GET  /health                 Healthcheck (modèle chargé, métriques, version)
GET  /metadata               Features, classes catégorielles, hyperparamètres
GET  /quartiers              Liste des 57 quartiers disponibles
POST /predict                Estimation prix/m², fourchette ±20 %, prix total
POST /predict/explain        Estimation + contributions TreeSHAP par feature

Auteur : généré pour ebbe — mai 2026
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd
import xgboost as xgb
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────────────────────────────────────
# Chemins
# ─────────────────────────────────────────────────────────────────────────────
_API_DIR      = Path(__file__).resolve().parent
_STATIC_DIR   = _API_DIR / "static"
# Les artefacts sont dans estimer_apparts/ au même niveau que estimation_prix_m2/
_ROOT_DIR     = _API_DIR.parent.parent          # prix-immobilier-evaluation/
_ARTIFACTS    = _ROOT_DIR / "estimer_apparts" / "model_artifacts"

# ─────────────────────────────────────────────────────────────────────────────
# Chargement des artefacts
# ─────────────────────────────────────────────────────────────────────────────
PIPELINE: Any     = joblib.load(_ARTIFACTS / "best_xgboost_pipeline.joblib")
ENCODERS: Dict    = joblib.load(_ARTIFACTS / "label_encoders.joblib")

PREPROCESSOR      = PIPELINE.named_steps["preprocessor"]
XGB_MODEL         = PIPELINE.named_steps["model"]
BOOSTER           = XGB_MODEL.get_booster()

# Noms des features dans l'ordre attendu par le pipeline
NUM_COLS: List[str] = [
    "Surface (m2)", "Etage", "Chambres", "Salles de bain",
    "Latitude", "Longitude",
    "dist_tram_m", "tram_500m", "intersect_500m",
    "tt_centre_min", "tt_cfc_min", "tt_maarif_min",
    "tt_sidi_maarouf_min", "tt_port_min",
    "n_ecoles_500m", "n_ecoles_1km", "dist_ecole_m",
    "n_banques_500m", "n_banques_1km", "dist_banque_m",
    "n_malls_500m", "n_malls_1km", "dist_mall_m",
    "dist_mer_m", "dist_parc_m", "n_parcs_1km",
    "surface_verte_m2_1km", "nuisance_route_500m",
    "n_industries_500m", "n_fuel_500m",
]
CAT_COLS: List[str] = ["Secteur/Quartier", "Type de bien", "Condition", "Standing"]
ALL_COLS            = NUM_COLS + CAT_COLS

ETAGE_MAP = {"RDC": 0, **{f"Etage {i}": i for i in range(1, 12)}}
ETAGE_MAP_INV = {v: k for k, v in ETAGE_MAP.items()}

# Noms des features après preprocessing (pour SHAP et /metadata)
FEAT_NAMES_PROCESSED: List[str] = [
    f.replace("num__", "").replace("remainder__", "")
    for f in PREPROCESSOR.get_feature_names_out()
]

# Valeurs par défaut globales (médianes estimées sur l'ensemble Casablanca)
DEFAULTS: Dict[str, float] = {
    "Latitude": 33.5731, "Longitude": -7.5898,
    "dist_tram_m": 850.0, "tram_500m": 0, "intersect_500m": 1,
    "tt_centre_min": 12.0, "tt_cfc_min": 22.0, "tt_maarif_min": 16.0,
    "tt_sidi_maarouf_min": 26.0, "tt_port_min": 20.0,
    "n_ecoles_500m": 2, "n_ecoles_1km": 5, "dist_ecole_m": 250.0,
    "n_banques_500m": 1, "n_banques_1km": 4, "dist_banque_m": 380.0,
    "n_malls_500m": 0, "n_malls_1km": 1, "dist_mall_m": 900.0,
    "dist_mer_m": 4500.0, "dist_parc_m": 450.0, "n_parcs_1km": 2,
    "surface_verte_m2_1km": 28000.0, "nuisance_route_500m": 1500.0,
    "n_industries_500m": 0, "n_fuel_500m": 0,
}

# Métriques du modèle (issus du notebook)
MODEL_METRICS = {
    "test_r2":    0.8835,
    "cv_r2_mean": 0.8586,
    "test_mape":  0.2512,
}

BEST_PARAMS = {
    "n_estimators":    200,
    "max_depth":       5,
    "learning_rate":   0.1,
    "subsample":       0.8,
    "colsample_bytree": 0.8,
    "reg_alpha":       0,
    "reg_lambda":      4,
}

# Index des quartiers disponibles
QUARTIERS = sorted(ENCODERS["Secteur/Quartier"].classes_.tolist())


# ─────────────────────────────────────────────────────────────────────────────
# Schémas Pydantic
# ─────────────────────────────────────────────────────────────────────────────
class PredictRequest(BaseModel):
    surface_m2:           float  = Field(...,  ge=10, le=1000, description="Surface en m²")
    quartier:             str    = Field(...,  description="Secteur/Quartier (ex: 'Maarif')")
    etage:                str    = Field("Etage 2", description="RDC, Etage 1 … Etage 11")
    chambres:             int    = Field(2,    ge=1, le=10)
    salles_de_bain:       int    = Field(1,    ge=1, le=5)
    type_de_bien:         str    = Field("Appartement", description="Appartement ou Studio")
    condition:            str    = Field("Bon etat",    description="Neuf, Bon etat, A renover")
    standing:             str    = Field("Moyen standing", description="Economique, Moyen standing, Haut standing")
    # Géospatiales — valeurs par défaut = médianes Casablanca
    dist_tram_m:          Optional[float] = Field(None, description="Distance au tram (m)")
    tram_500m:            Optional[int]   = Field(None, ge=0, le=1)
    intersect_500m:       Optional[int]   = Field(None, ge=0)
    tt_centre_min:        Optional[float] = Field(None, description="Temps trajet Centre-Ville (min)")
    tt_cfc_min:           Optional[float] = Field(None)
    tt_maarif_min:        Optional[float] = Field(None)
    tt_sidi_maarouf_min:  Optional[float] = Field(None)
    tt_port_min:          Optional[float] = Field(None)
    n_ecoles_500m:        Optional[int]   = Field(None, ge=0)
    n_ecoles_1km:         Optional[int]   = Field(None, ge=0)
    dist_ecole_m:         Optional[float] = Field(None, ge=0)
    n_banques_500m:       Optional[int]   = Field(None, ge=0)
    n_banques_1km:        Optional[int]   = Field(None, ge=0)
    dist_banque_m:        Optional[float] = Field(None, ge=0)
    n_malls_500m:         Optional[int]   = Field(None, ge=0)
    n_malls_1km:          Optional[int]   = Field(None, ge=0)
    dist_mall_m:          Optional[float] = Field(None, ge=0)
    dist_mer_m:           Optional[float] = Field(None, ge=0)
    dist_parc_m:          Optional[float] = Field(None, ge=0)
    n_parcs_1km:          Optional[int]   = Field(None, ge=0)
    surface_verte_m2_1km: Optional[float] = Field(None, ge=0)
    nuisance_route_500m:  Optional[float] = Field(None, ge=0)
    n_industries_500m:    Optional[int]   = Field(None, ge=0)
    n_fuel_500m:          Optional[int]   = Field(None, ge=0)

    model_config = {
        "json_schema_extra": {
            "example": {
                "surface_m2": 80, "quartier": "Maarif",
                "etage": "Etage 3", "chambres": 2, "salles_de_bain": 1,
                "type_de_bien": "Appartement", "condition": "Bon etat",
                "standing": "Moyen standing",
                "dist_mer_m": 3500, "tt_maarif_min": 2,
            }
        }
    }


class PredictResponse(BaseModel):
    # ── Résultat principal ──────────────────────────────────────────────────
    prix_m2:          float = Field(description="Prix estimé au m² (DH/m²)")
    fourchette_basse: float = Field(description="Estimation basse (−20 %, DH/m²)")
    fourchette_haute: float = Field(description="Estimation haute (+20 %, DH/m²)")
    prix_total:       float = Field(description="Prix total estimé (DH)")
    prix_total_bas:   float = Field(description="Prix total estimation basse (DH)")
    prix_total_haut:  float = Field(description="Prix total estimation haute (DH)")
    # ── Contexte ────────────────────────────────────────────────────────────
    surface_m2:       float
    quartier:         str
    etage:            str
    type_de_bien:     str
    condition:        str
    standing:         str
    features_used:    Dict[str, float]


class ShapContribution(BaseModel):
    feature:   str
    value:     float
    shap_m2:   float = Field(description="Contribution SHAP en DH/m²")
    impact_dh: float = Field(description="Impact sur le prix total (DH)")


class ExplainResponse(PredictResponse):
    baseline_m2:   float = Field(description="Prédiction moyenne (DH/m²)")
    baseline_total: float
    contributions: List[ShapContribution]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _build_row(req: PredictRequest) -> pd.DataFrame:
    """Construit le DataFrame encodé prêt pour le pipeline."""
    # Validation quartier
    if req.quartier not in ENCODERS["Secteur/Quartier"].classes_:
        raise HTTPException(
            status_code=422,
            detail=f"Quartier inconnu : '{req.quartier}'. "
                   f"Utilisez GET /quartiers pour la liste complète.",
        )
    # Validation étage
    if req.etage not in ETAGE_MAP:
        raise HTTPException(
            status_code=422,
            detail=f"Etage inconnu : '{req.etage}'. "
                   f"Valeurs : {list(ETAGE_MAP)}",
        )
    # Validation catégorielles
    for col, val in [
        ("Type de bien", req.type_de_bien),
        ("Condition",    req.condition),
        ("Standing",     req.standing),
    ]:
        if val not in ENCODERS[col].classes_:
            raise HTTPException(
                status_code=422,
                detail=f"'{col}' invalide : '{val}'. "
                       f"Valeurs autorisées : {list(ENCODERS[col].classes_)}",
            )

    # Valeurs géospatiales : requête > défauts globaux
    geo = {}
    for k, default in DEFAULTS.items():
        v = getattr(req, k, None)
        geo[k] = v if v is not None else default

    row = {
        "Surface (m2)":   req.surface_m2,
        "Etage":          ETAGE_MAP[req.etage],
        "Chambres":       req.chambres,
        "Salles de bain": req.salles_de_bain,
        **geo,
        # catégorielles (raw strings → LabelEncoder)
        "Secteur/Quartier": req.quartier,
        "Type de bien":     req.type_de_bien,
        "Condition":        req.condition,
        "Standing":         req.standing,
    }

    df = pd.DataFrame([row])
    for col in CAT_COLS:
        df[col] = ENCODERS[col].transform(df[col].astype(str))
    return df[ALL_COLS]


def _predict(df: pd.DataFrame) -> float:
    """Prédiction directe via le pipeline complet."""
    return float(PIPELINE.predict(df)[0])


def _shap_contribs(df: pd.DataFrame) -> tuple[np.ndarray, float]:
    """
    Contributions TreeSHAP en DH/m² via le booster natif XGBoost.
    Retourne (contribs_array, baseline_value).
    Contribs[-1] = valeur de base (expected_value).
    """
    X_transformed = PREPROCESSOR.transform(df)
    dmat = xgb.DMatrix(X_transformed, feature_names=FEAT_NAMES_PROCESSED)
    raw = BOOSTER.predict(dmat, pred_contribs=True)[0]   # shape: (n_features + 1,)
    return raw[:-1], float(raw[-1])                       # contributions, baseline


# ─────────────────────────────────────────────────────────────────────────────
# Application FastAPI
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Estimation Prix m² Appartements — Casablanca",
    description=(
        "API d'estimation du prix au m² des appartements à Casablanca "
        "(XGBoost, R²≈0.88 test, MAPE≈25 %).\n\n"
        "**Workflow recommandé** : `GET /quartiers` → `POST /predict`\n\n"
        "**Réponse** : prix/m², fourchette ±20 %, prix total."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if _STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui")


@app.get("/ui", include_in_schema=False)
def ui():
    index = _STATIC_DIR / "index_apparts.html"
    if not index.exists():
        raise HTTPException(404, "UI non installée")
    return FileResponse(str(index))


@app.get("/health", tags=["meta"])
def health():
    """Statut de l'API et métriques du modèle."""
    return {
        "status":        "ok",
        "model":         "XGBoost (Pipeline sklearn)",
        "n_features":    len(ALL_COLS),
        "n_quartiers":   len(QUARTIERS),
        "test_r2":       MODEL_METRICS["test_r2"],
        "cv_r2_mean":    MODEL_METRICS["cv_r2_mean"],
        "test_mape":     MODEL_METRICS["test_mape"],
        "mape_note":     "La fourchette ±20% reflète l'incertitude du modèle (MAPE ~25%)",
    }


@app.get("/metadata", tags=["meta"])
def metadata():
    """Liste des features, valeurs catégorielles autorisées, hyperparamètres."""
    return {
        "numeric_features":      NUM_COLS,
        "categorical_features":  {col: list(enc.classes_) for col, enc in ENCODERS.items()},
        "etage_values":          list(ETAGE_MAP.keys()),
        "default_geo_values":    DEFAULTS,
        "best_hyperparameters":  BEST_PARAMS,
        "model_metrics":         MODEL_METRICS,
        "features_after_preprocessing": FEAT_NAMES_PROCESSED,
    }


@app.get("/quartiers", tags=["quartiers"])
def list_quartiers(q: Optional[str] = Query(None, description="Filtre par nom (insensible à la casse)")):
    """Liste des 57 quartiers disponibles pour la prédiction."""
    if q:
        result = [qrt for qrt in QUARTIERS if q.lower() in qrt.lower()]
    else:
        result = QUARTIERS
    return {"count": len(result), "quartiers": result}


@app.post("/predict", response_model=PredictResponse, tags=["predict"])
def predict(req: PredictRequest):
    """
    Estime le prix au m² d'un appartement.

    Seuls **surface_m2** et **quartier** sont obligatoires.
    Les variables géospatiales (dist_tram_m, tt_centre_min…) utilisent
    les médianes Casablanca si non renseignées.
    """
    df        = _build_row(req)
    prix_m2   = _predict(df)
    prix_total = prix_m2 * req.surface_m2

    return PredictResponse(
        prix_m2          = round(prix_m2, 0),
        fourchette_basse = round(prix_m2 * 0.80, 0),
        fourchette_haute = round(prix_m2 * 1.20, 0),
        prix_total       = round(prix_total, 0),
        prix_total_bas   = round(prix_total * 0.80, 0),
        prix_total_haut  = round(prix_total * 1.20, 0),
        surface_m2       = req.surface_m2,
        quartier         = req.quartier,
        etage            = req.etage,
        type_de_bien     = req.type_de_bien,
        condition        = req.condition,
        standing         = req.standing,
        features_used    = {c: float(df[c].iloc[0]) for c in ALL_COLS},
    )


@app.post("/predict/explain", response_model=ExplainResponse, tags=["predict"])
def predict_explain(
    req: PredictRequest,
    top_k: int = Query(10, ge=1, le=34, description="Nombre de contributions SHAP à retourner"),
):
    """
    Estime le prix au m² **et** explique la prédiction avec TreeSHAP.

    Chaque contribution indique l'impact d'une feature sur le prix/m²
    et sur le prix total (DH).
    """
    df         = _build_row(req)
    prix_m2    = _predict(df)
    prix_total = prix_m2 * req.surface_m2

    contribs_arr, baseline_m2 = _shap_contribs(df)
    baseline_total = baseline_m2 * req.surface_m2

    contributions: List[ShapContribution] = []
    for feat, shap_val in zip(FEAT_NAMES_PROCESSED, contribs_arr):
        contributions.append(ShapContribution(
            feature   = feat,
            value     = float(df[feat].iloc[0]) if feat in df.columns else 0.0,
            shap_m2   = round(float(shap_val), 2),
            impact_dh = round(float(shap_val) * req.surface_m2, 0),
        ))

    contributions.sort(key=lambda c: abs(c.shap_m2), reverse=True)

    return ExplainResponse(
        prix_m2          = round(prix_m2, 0),
        fourchette_basse = round(prix_m2 * 0.80, 0),
        fourchette_haute = round(prix_m2 * 1.20, 0),
        prix_total       = round(prix_total, 0),
        prix_total_bas   = round(prix_total * 0.80, 0),
        prix_total_haut  = round(prix_total * 1.20, 0),
        surface_m2       = req.surface_m2,
        quartier         = req.quartier,
        etage            = req.etage,
        type_de_bien     = req.type_de_bien,
        condition        = req.condition,
        standing         = req.standing,
        features_used    = {c: float(df[c].iloc[0]) for c in ALL_COLS},
        baseline_m2      = round(baseline_m2, 0),
        baseline_total   = round(baseline_total, 0),
        contributions    = contributions[:top_k],
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    uvicorn.run("app_apparts:app", host="0.0.0.0", port=port, reload=False)
