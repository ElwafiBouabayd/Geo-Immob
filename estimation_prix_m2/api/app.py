"""
API FastAPI pour l'estimation du prix au m² à Casablanca.

Modèle : XGBoost régressant log(Prix Terrain au m² (DH)).
Artéfacts utilisés : model_artifacts/final_model_XGBoost.joblib
                     model_artifacts/all_label_encoders.pkl

Endpoints
---------
GET  /                  Redirection vers l'interface /ui
GET  /ui                Interface web professionnelle (temps réel)
GET  /docs              Documentation Swagger
GET  /health            Healthcheck (modèle chargé, version)
GET  /metadata          Liste des features, classes catégorielles, métriques modèle
GET  /zones             Liste des zones disponibles (Code Zone -> infos)
GET  /autofill          Pré-remplit les variables géo à partir d'un Code Zone
POST /predict           Estimation du prix au m² (DH)
POST /predict/explain   Estimation + contributions TreeSHAP par feature

Auteur : généré pour Elwafi - mai 2026
"""
from __future__ import annotations

import json
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

warnings.filterwarnings("ignore", category=UserWarning)

# ----------------------------------------------------------------------------
# Chargement des artéfacts
# ----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = BASE_DIR / "model_artifacts"
DATA_PATH = BASE_DIR / "data_finale.csv"
STATIC_DIR = Path(__file__).resolve().parent / "static"

MODEL = joblib.load(ARTIFACTS_DIR / "final_model_XGBoost.joblib")
ENCODERS: Dict[str, Any] = joblib.load(ARTIFACTS_DIR / "all_label_encoders.pkl")

with open(ARTIFACTS_DIR / "best_hyperparams.json", encoding="utf-8") as f:
    BEST_HP = json.load(f)

COMPARAISON = pd.read_csv(ARTIFACTS_DIR / "comparaison_finale.csv")
SHAP_GLOBAL = pd.read_csv(ARTIFACTS_DIR / "shap_importance.csv")
DATA = pd.read_csv(DATA_PATH)
DATA["Condition / Zonage"] = DATA["Condition / Zonage"].fillna("Aucune")

FEATURE_COLS: List[str] = list(MODEL.feature_names_in_)
CATEGORICAL_COLS = [c for c in FEATURE_COLS if c in ENCODERS]
NUMERIC_COLS = [c for c in FEATURE_COLS if c not in ENCODERS]

ZONE_INDEX = (
    DATA.groupby("Code Zone")
    .agg(
        Prefecture=("Préfecture", "first"),
        Arrondissement=("Arrondissement", "first"),
        Zone=("Zone déchiffrée", "first"),
        n=("Code Zone", "size"),
    )
    .reset_index()
)
ZONE_MEDIANS = DATA.groupby("Code Zone")[NUMERIC_COLS].median(numeric_only=True)
ZONE_CAT_MODE = (
    DATA.groupby("Code Zone")[CATEGORICAL_COLS]
    .agg(lambda s: s.mode().iloc[0] if not s.mode().empty else s.iloc[0])
)

BOOSTER = MODEL.get_booster()


# ----------------------------------------------------------------------------
# Schémas Pydantic
# ----------------------------------------------------------------------------
class PredictRequest(BaseModel):
    code_zone: Optional[str] = Field(
        None, description="Code Zone (ex: 'CC-SB1'). Si fourni, autofill géo."
    )
    type_de_bien: Optional[str] = Field(
        None, description="Appartement, Villa, Maison, Terrain ZI, ..."
    )
    etat: Optional[str] = Field(None, description="Ancien, Récent, Neuf, Loti")
    condition_zonage: Optional[str] = Field(
        None, description="Ex: '<= à R+5', '> à R+3', 'Aucune'"
    )
    features: Optional[Dict[str, float]] = Field(
        default=None,
        description="Override d'une ou plusieurs features numériques par nom.",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "code_zone": "CC-SB1",
                "type_de_bien": "Appartement",
                "etat": "Récent",
                "condition_zonage": "<= à R+5",
                "features": {"dist_mer": 250.0},
            }
        }


class PredictResponse(BaseModel):
    prix_m2_dh: float
    log_prediction: float
    code_zone: Optional[str]
    type_de_bien: str
    etat: str
    condition_zonage: str
    features_used: Dict[str, float]


class ShapContribution(BaseModel):
    feature: str
    value: float
    shap_log: float
    impact_dh: float


class ExplainResponse(PredictResponse):
    baseline_log: float
    baseline_dh: float
    contributions: List[ShapContribution]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _build_feature_row(req: PredictRequest) -> pd.Series:
    if req.code_zone and req.code_zone in ZONE_MEDIANS.index:
        row_num = ZONE_MEDIANS.loc[req.code_zone].copy()
        row_cat = ZONE_CAT_MODE.loc[req.code_zone].copy()
    else:
        row_num = DATA[NUMERIC_COLS].median(numeric_only=True)
        row_cat = pd.Series({c: DATA[c].mode().iloc[0] for c in CATEGORICAL_COLS})

    if req.type_de_bien is not None:
        row_cat["Type de bien"] = req.type_de_bien
    if req.etat is not None:
        row_cat["État"] = req.etat
    if req.condition_zonage is not None:
        row_cat["Condition / Zonage"] = req.condition_zonage

    if req.features:
        for k, v in req.features.items():
            if k not in NUMERIC_COLS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Feature inconnue '{k}'. Liste valide via GET /metadata.",
                )
            row_num[k] = float(v)

    full = pd.Series(index=FEATURE_COLS, dtype=object)
    for c in FEATURE_COLS:
        full[c] = row_cat[c] if c in CATEGORICAL_COLS else row_num[c]

    for col, enc in ENCODERS.items():
        v = full[col]
        if v not in enc.classes_:
            raise HTTPException(
                status_code=422,
                detail=f"Valeur '{v}' invalide pour '{col}'. "
                f"Valeurs autorisées: {list(enc.classes_)}",
            )
        full[col] = int(enc.transform([v])[0])

    return full.astype(float)


def _predict_log(row: pd.Series) -> float:
    X = pd.DataFrame([row.values], columns=FEATURE_COLS)
    return float(MODEL.predict(X)[0])


def _shap_contribs(row: pd.Series) -> np.ndarray:
    X = pd.DataFrame([row.values], columns=FEATURE_COLS)
    dmat = xgb.DMatrix(X, feature_names=FEATURE_COLS)
    return BOOSTER.predict(dmat, pred_contribs=True)[0]


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
app = FastAPI(
    title="Estimation Prix m² Casablanca",
    description=(
        "API d'estimation du prix du terrain au m² à Casablanca (XGBoost, "
        "R²≈0.80 en CV).\n\nWorkflow recommandé : `/zones` -> `/predict`."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Frontend statique (interface temps réel)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui")


@app.get("/ui", include_in_schema=False)
def ui():
    """Interface web professionnelle (estimation temps réel)."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "UI non installée")
    return FileResponse(str(index))


@app.get("/health", tags=["meta"])
def health():
    return {
        "status": "ok",
        "model": "XGBoost",
        "n_features": len(FEATURE_COLS),
        "model_r2_cv": float(
            COMPARAISON.loc[COMPARAISON["Modèle"] == "XGBoost", "R² CV"].iloc[0]
        ),
        "n_zones": int(ZONE_INDEX.shape[0]),
    }


@app.get("/metadata", tags=["meta"])
def metadata():
    return {
        "features": FEATURE_COLS,
        "numeric_features": NUMERIC_COLS,
        "categorical_features": {
            col: list(enc.classes_) for col, enc in ENCODERS.items()
        },
        "best_hyperparameters": BEST_HP.get("XGBoost", {}),
        "model_comparison": COMPARAISON.to_dict(orient="records"),
        "shap_global_importance": SHAP_GLOBAL.to_dict(orient="records"),
    }


@app.get("/zones", tags=["zones"])
def list_zones(
    prefecture: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    df = ZONE_INDEX
    if prefecture:
        df = df[df["Prefecture"].str.contains(prefecture, case=False, na=False)]
    if q:
        mask = (
            df["Zone"].str.contains(q, case=False, na=False)
            | df["Code Zone"].str.contains(q, case=False, na=False)
            | df["Arrondissement"].str.contains(q, case=False, na=False)
        )
        df = df[mask]
    return df.to_dict(orient="records")


@app.get("/autofill", tags=["zones"])
def autofill(code_zone: str = Query(...)):
    if code_zone not in ZONE_MEDIANS.index:
        raise HTTPException(404, f"Code Zone '{code_zone}' inconnu")
    out = ZONE_MEDIANS.loc[code_zone].to_dict()
    info = ZONE_INDEX[ZONE_INDEX["Code Zone"] == code_zone].iloc[0].to_dict()
    return {
        "code_zone": code_zone,
        "prefecture": info["Prefecture"],
        "arrondissement": info["Arrondissement"],
        "zone": info["Zone"],
        "n_samples": int(info["n"]),
        "median_features": {k: float(v) for k, v in out.items()},
        "modal_categorical": {
            c: ZONE_CAT_MODE.loc[code_zone, c] for c in CATEGORICAL_COLS
        },
    }


@app.post("/predict", response_model=PredictResponse, tags=["predict"])
def predict(req: PredictRequest):
    row = _build_feature_row(req)
    log_pred = _predict_log(row)
    price_dh = float(np.exp(log_pred))
    decoded_cat = {
        col: ENCODERS[col].inverse_transform([int(row[col])])[0] for col in CATEGORICAL_COLS
    }
    return PredictResponse(
        prix_m2_dh=round(price_dh, 2),
        log_prediction=round(log_pred, 4),
        code_zone=req.code_zone,
        type_de_bien=decoded_cat["Type de bien"],
        etat=decoded_cat["État"],
        condition_zonage=decoded_cat["Condition / Zonage"],
        features_used={k: float(row[k]) for k in FEATURE_COLS},
    )


@app.post("/predict/explain", response_model=ExplainResponse, tags=["predict"])
def predict_explain(req: PredictRequest, top_k: int = Query(15, ge=1, le=44)):
    row = _build_feature_row(req)
    log_pred = _predict_log(row)
    price_dh = float(np.exp(log_pred))

    contribs = _shap_contribs(row)
    bias_log = float(contribs[-1])
    bias_dh = float(np.exp(bias_log))

    contributions: List[ShapContribution] = []
    for i, feat in enumerate(FEATURE_COLS):
        shap_val = float(contribs[i])
        impact_dh = float(np.exp(bias_log + shap_val) - np.exp(bias_log))
        contributions.append(
            ShapContribution(
                feature=feat.strip(),
                value=float(row[feat]),
                shap_log=round(shap_val, 5),
                impact_dh=round(impact_dh, 2),
            )
        )
    contributions.sort(key=lambda x: abs(x.shap_log), reverse=True)

    decoded_cat = {
        col: ENCODERS[col].inverse_transform([int(row[col])])[0] for col in CATEGORICAL_COLS
    }

    return ExplainResponse(
        prix_m2_dh=round(price_dh, 2),
        log_prediction=round(log_pred, 4),
        baseline_log=round(bias_log, 4),
        baseline_dh=round(bias_dh, 2),
        code_zone=req.code_zone,
        type_de_bien=decoded_cat["Type de bien"],
        etat=decoded_cat["État"],
        condition_zonage=decoded_cat["Condition / Zonage"],
        features_used={k: float(row[k]) for k in FEATURE_COLS},
        contributions=contributions[:top_k],
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
