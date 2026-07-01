import math, json, sys
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from catboost import CatBoostRegressor, Pool

SCORE_API_DIR = Path(__file__).parent.parent / "score_attractivité" / "api"
sys.path.insert(0, str(SCORE_API_DIR))
from scoring import (
    ALL_VARS, DIMENSIONS, DIMENSIONS_VAR_SIGN, WEIGHTS,
    all_zone_scores, get_zone_features,
    load_coordinates, load_zone_data,
    score_from_variables,
)

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, float) and math.isnan(obj): return None
        return super().default(obj)

def _clean_nan(obj):
    """json.dumps sérialise nativement NaN/Infinity en littéraux non-standard (NaN, Infinity)
    que JSON.parse() côté navigateur rejette. On les remplace par None avant sérialisation."""
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj

def safe_json(data):
    return Response(content=json.dumps(_clean_nan(data), cls=NumpyEncoder), media_type="application/json")

BASE_DIR = Path(__file__).parent
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"


class SegmentModel:
    """Charge un modèle CatBoost de prix/m² entraîné dans 'Nouveau dossier' (apparts/villas/terrains)."""

    def __init__(self, name: str, cbm_path: Path, df: pd.DataFrame, quartier_col: str, predicts_total_price: bool = False):
        self.name = name
        self.model = CatBoostRegressor()
        self.model.load_model(str(cbm_path))
        self.df = df
        self.quartier_col = quartier_col
        # Les modèles villas/terrains ont été entraînés sur le prix total (prix_mad / price_dh),
        # pas sur le prix au m², malgré le champ "target": "prix_m2" dans leurs meta.json.
        self.predicts_total_price = predicts_total_price

        self.feature_names = self.model.feature_names_
        self.cat_features = [self.feature_names[i] for i in self.model.get_cat_feature_indices()]
        self.num_features = [f for f in self.feature_names if f not in self.cat_features]

        self.quartier_medians = {
            q: {k: float(v) for k, v in vals.items()}
            for q, vals in df.groupby(quartier_col)[self.num_features].median().round(4).to_dict(orient="index").items()
        }
        self.quartier_modes = {}
        for q, grp in df.groupby(quartier_col):
            modes = {}
            for f in self.cat_features:
                if f == quartier_col:
                    continue
                col = grp[f].dropna()
                if not col.empty:
                    modes[f] = str(col.mode().iloc[0])
            self.quartier_modes[q] = modes

        self.quartiers = sorted(df[quartier_col].dropna().unique().tolist())
        self.categorical_values = {
            f: sorted(df[f].dropna().unique().tolist())
            for f in self.cat_features if f != quartier_col
        }
        self.baseline_dh = float(df["prix_m2"].median())

        # Stats prix/m² par quartier (pour la carte des prix) : toujours en DH/m²,
        # même pour les segments villas/terrains dont le modèle prédit un prix total.
        stats = df.groupby(quartier_col)["prix_m2"].agg(["median", "min", "max", "count"])
        self.quartier_price_stats = {
            q: {
                "prix_m2_median": round(float(r["median"]), 0),
                "prix_m2_min": round(float(r["min"]), 0),
                "prix_m2_max": round(float(r["max"]), 0),
                "n_annonces": int(r["count"]),
            }
            for q, r in stats.iterrows() if r["count"] > 0
        }

    def build_row(self, quartier: str, overrides: dict) -> dict:
        medians = dict(self.quartier_medians.get(quartier, {}))
        modes = self.quartier_modes.get(quartier, {})
        row = {}
        for f in self.feature_names:
            if f == self.quartier_col:
                row[f] = quartier
            elif f in overrides and overrides[f] is not None:
                row[f] = overrides[f]
            elif f in self.cat_features:
                row[f] = modes.get(f, "")
            else:
                row[f] = float(medians.get(f, 0.0))
        return row

    def predict(self, row: dict):
        pool = Pool(data=pd.DataFrame([row]), cat_features=self.cat_features, feature_names=self.feature_names)
        prix_m2 = float(self.model.predict(pool)[0])
        shap_row = self.model.get_feature_importance(pool, type="ShapValues")[0]
        contribs = []
        for i, fname in enumerate(self.feature_names):
            sv = float(shap_row[i])
            if sv == 0:
                continue
            val = row[fname]
            if isinstance(val, float):
                val = round(val, 2)
            contribs.append({"feature": fname, "value": val, "impact_dh": round(sv, 1)})
        contribs.sort(key=lambda c: abs(c["impact_dh"]), reverse=True)
        return prix_m2, contribs


# Mapping appliqué dans apparts.ipynb avant l'entraînement : le modèle ne connaît
# que ces 3 catégories d'état du bien (confirmé par les categories_ de
# ordinal_encoder_quartiers_apparts.pkl). Toute autre valeur passée au modèle
# tomberait dans une catégorie inconnue et fausserait la prédiction.
ETAT_BIEN_MAPPING = {
    "Neuf": "Neuf / Rénové",
    "Jamais habité / rénové": "Neuf / Rénové",
    "Refait à neuf": "Neuf / Rénové",
    "Bon état / habitable": "Bon état",
    "Correct": "Bon état",
    "Bon": "Bon état",
    "À rénover": "À rénover",
    "Travaux à prévoir": "À rénover",
}


def load_segment(name: str, cbm_file: str, xlsx_file: str, quartier_col: str,
                  price_total_col: Optional[str] = None, predicts_total_price: bool = False,
                  etat_bien_mapping: Optional[dict] = None) -> SegmentModel:
    df = pd.read_excel(DATA_DIR / xlsx_file)
    if "prix_m2" not in df.columns:
        if price_total_col and price_total_col in df.columns and "surface_m2" in df.columns:
            df["prix_m2"] = df[price_total_col] / df["surface_m2"]
        else:
            raise ValueError(f"Impossible de déterminer prix_m2 pour le segment {name}")
    if etat_bien_mapping and "etat_bien" in df.columns:
        df["etat_bien"] = df["etat_bien"].map(etat_bien_mapping)
    return SegmentModel(name, MODELS_DIR / cbm_file, df, quartier_col, predicts_total_price=predicts_total_price)


# ── MODÈLES ─────────────────────────────────────────────────────────────────
# apparts : modèle entraîné sur prix_par_m2*0.9 -> prédit un prix au m²
APPARTS = load_segment("apparts", "catboost_prix_m2_apparts.cbm", "apparts.xlsx", "quartier",
                        price_total_col="prix_mad", predicts_total_price=False,
                        etat_bien_mapping=ETAT_BIEN_MAPPING)
# villas/terrains : modèles entraînés sur prix_mad*0.9 / price_dh*0.9 -> prédisent un prix total
VILLAS  = load_segment("villas", "catboost_prix_villa.cbm", "villas.xlsx", "quartier",
                        price_total_col="prix_mad", predicts_total_price=True)
TERRAINS = load_segment("terrains", "catboost_prix_terrains.cbm", "terrains.xlsx", "quartier_clean",
                         price_total_col="price_dh", predicts_total_price=True)

# ── APP ───────────────────────────────────────────────────────────────────────
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── Schémas ───────────────────────────────────────────────────────────────────
class AppartRequest(BaseModel):
    quartier: str
    type_bien: Optional[str] = None
    etat_bien: Optional[str] = None
    surface_m2: float = 100.0
    chambres_total: Optional[float] = None
    salles_de_bain: Optional[float] = None
    etage_num: Optional[float] = None
    ascenseur: Optional[float] = None
    securite: Optional[float] = None
    parking_places: Optional[float] = None
    top_k: int = 8

class VillaRequest(BaseModel):
    quartier: str
    surface_m2: float = 300.0
    chambres_total: Optional[float] = None
    salles_de_bain: Optional[float] = None
    piscine: Optional[float] = None
    jardin: Optional[float] = None
    garage: Optional[float] = None
    securite: Optional[float] = None
    top_k: int = 8

class TerrainRequest(BaseModel):
    quartier: str
    surface_m2: float = 200.0
    top_k: int = 8

class ScoreRequest(BaseModel):
    values: Dict[str, float]


def segment_endpoints(seg: SegmentModel, prefix: str, req_model):
    @app.get(f"{prefix}/health", name=f"{seg.name}_health")
    def health():
        return safe_json({"status": "ok", "n_quartiers": len(seg.quartiers), "baseline_dh": seg.baseline_dh})

    @app.get(f"{prefix}/metadata", name=f"{seg.name}_metadata")
    def metadata():
        return safe_json({
            "quartiers": seg.quartiers,
            "categorical_features": seg.categorical_values,
            "numeric_features": seg.num_features,
            "baseline_dh": seg.baseline_dh,
        })

    @app.get(f"{prefix}/autofill", name=f"{seg.name}_autofill")
    def autofill(quartier: str = Query(...)):
        if quartier not in seg.quartier_medians:
            raise HTTPException(404, "Quartier inconnu")
        return safe_json({
            "quartier": quartier,
            "median_features": seg.quartier_medians[quartier],
            "modal_categorical": seg.quartier_modes.get(quartier, {}),
        })

    @app.post(f"{prefix}/predict", name=f"{seg.name}_predict")
    def predict(req: req_model):
        if req.quartier not in seg.quartiers:
            raise HTTPException(400, f"Quartier '{req.quartier}' non disponible pour le segment {seg.name}")
        raw_overrides = req.dict(exclude={"quartier", "top_k"})
        overrides = {}
        for k, v in raw_overrides.items():
            if v is None:
                continue
            overrides[k] = v if k in seg.cat_features else float(v)
        overrides["surface_m2"] = float(req.surface_m2)
        row = seg.build_row(req.quartier, overrides)
        prediction, contribs = seg.predict(row)

        if seg.predicts_total_price:
            prix_total = prediction
            prix_m2 = prediction / req.surface_m2 if req.surface_m2 else 0.0
        else:
            prix_m2 = prediction
            prix_total = prediction * req.surface_m2

        return safe_json({
            "segment": seg.name,
            "prix_m2_dh": round(prix_m2, 0),
            "prix_total_dh": round(prix_total, 0),
            "baseline_dh": round(seg.baseline_dh, 0),
            "quartier": req.quartier,
            "surface_m2": req.surface_m2,
            "contributions": contribs[:req.top_k],
            **{k: row[k] for k in seg.cat_features if k != seg.quartier_col},
        })


segment_endpoints(APPARTS, "", AppartRequest)
segment_endpoints(VILLAS, "/villa", VillaRequest)
segment_endpoints(TERRAINS, "/terrain", TerrainRequest)

# ── CARTE DES PRIX PAR QUARTIER ────────────────────────────────────────────────
# Coordonnées géocodées (Nominatim, filtrées sur la bounding box de Casablanca,
# complétées à la main pour quelques quartiers absents d'OSM). Cf. data/quartier_coords.json.
with open(DATA_DIR / "quartier_coords.json", encoding="utf-8") as f:
    QUARTIER_COORDS: Dict[str, list] = json.load(f)

@app.get("/map/quartiers")
def map_quartiers(segment: str = Query("apparts", pattern="^(apparts|villas)$")):
    seg = APPARTS if segment == "apparts" else VILLAS
    out = []
    for q, coord in QUARTIER_COORDS.items():
        stats = seg.quartier_price_stats.get(q)
        if not stats:
            continue
        out.append({
            "quartier": q,
            "lat": coord[0],
            "lng": coord[1],
            **stats,
        })
    return safe_json({"segment": segment, "count": len(out), "quartiers": out})

@app.get("/map/quartiers/{quartier}")
def map_quartier_detail(quartier: str):
    result = {"quartier": quartier, "coord": QUARTIER_COORDS.get(quartier)}
    for seg, label in [(APPARTS, "apparts"), (VILLAS, "villas")]:
        stats = seg.quartier_price_stats.get(quartier)
        if stats:
            result[label] = stats
    if "apparts" not in result and "villas" not in result:
        raise HTTPException(404, "Quartier inconnu")
    return safe_json(result)

# ── ENDPOINTS SCORE ATTRACTIVITÉ ──────────────────────────────────────────────
@app.get("/api/dimensions")
def get_dims():
    return safe_json([
        {
            "dimension": d,
            "poids_dimension": round(WEIGHTS[d] * 100, 0),
            "variables": [
                {"name": v, "sign": sign, "direction": "négatif" if sign == "neg" else "positif", "poids_intra": round(w_intra * 100, 0)}
                for v, (sign, w_intra) in vd.items()
            ]
        }
        for d, vd in DIMENSIONS.items()
    ])

@app.get("/api/weights")
def get_weights():
    return safe_json([{"dimension": d, "poids": round(WEIGHTS[d] * 100, 2)} for d in DIMENSIONS])

@app.get("/api/zones")
def list_zones():
    df = load_zone_data()
    cols = [c for c in ["Code Zone", "Zone", "Arrondissement"] if c in df.columns]
    return safe_json(df[cols].fillna("").drop_duplicates("Code Zone").to_dict(orient="records"))

@app.get("/api/zones/scores")
def all_scores():
    return safe_json(all_zone_scores().to_dict(orient="records"))

@app.get("/api/zones/map")
def zones_map():
    coords = load_coordinates()
    if coords.empty:
        return safe_json({"available": False, "message": "Coordonnées indisponibles.", "zones": []})
    scores = all_zone_scores()
    merged = scores.merge(coords, on="Code Zone", how="left")
    if "lat_x" in merged.columns:
        merged["lat"] = merged["lat_x"].combine_first(merged["lat_y"])
        merged["lng"] = merged["lng_x"].combine_first(merged["lng_y"])
        merged = merged.drop(columns=["lat_x", "lat_y", "lng_x", "lng_y"], errors="ignore")
    merged = merged.dropna(subset=["lat", "lng"])
    return safe_json({"available": True, "count": int(len(merged)), "zones": merged.fillna(0).to_dict(orient="records")})

@app.get("/api/zones/{code_zone}")
def zone_detail(code_zone: str):
    try:
        features = get_zone_features(code_zone)
    except KeyError as e:
        raise HTTPException(404, str(e))
    scores = all_zone_scores()
    row = scores[scores["Code Zone"] == code_zone]
    if row.empty:
        raise HTTPException(404, "Zone non scorée")
    info = row.iloc[0].to_dict()
    return safe_json({
        "code_zone": code_zone,
        "zone": info.get("Zone"),
        "arrondissement": info.get("Arrondissement"),
        "prix_m2_moyen": info.get("prix_m2_moyen"),
        "rang": int(info.get("Rang", 0)),
        "scores": {**{f"Score_{d}": info.get(f"Score_{d}", 0) for d in DIMENSIONS}, "Score_Attractivite": info.get("Score_Attractivite", 0)},
        "features": features,
    })

@app.post("/api/score")
def compute_score(req: ScoreRequest):
    try:
        result = score_from_variables(req.values)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return safe_json(result)

@app.get("/")
def root():
    index = BASE_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return safe_json({"message": "API opérationnelle. Voir /docs"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
