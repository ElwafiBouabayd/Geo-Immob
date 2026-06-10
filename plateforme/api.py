import pickle, math, json, sys
from pathlib import Path
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from catboost import CatBoostRegressor, Pool

SCORE_API_DIR = Path(__file__).parent.parent / "score_attractivité" / "api"
sys.path.insert(0, str(SCORE_API_DIR))
from scoring import (
    ALL_VARS, DIMENSIONS, DIMENSIONS_VAR_SIGN,
    all_zone_scores, get_zone_features,
    load_coordinates, load_weights, load_zones,
    score_from_variables,
)

class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer): return int(obj)
        if isinstance(obj, np.floating): return float(obj)
        if isinstance(obj, np.ndarray): return obj.tolist()
        if isinstance(obj, float) and math.isnan(obj): return None
        return super().default(obj)

def safe_json(data):
    return Response(content=json.dumps(data, cls=NumpyEncoder), media_type="application/json")

BASE_DIR        = Path(__file__).parent
ESTIM_DATA_DIR  = Path(__file__).parent.parent / "estimation_residentielle"
with open(ESTIM_DATA_DIR/"catboost_final_model.pkl","rb") as f:
    MODEL = pickle.load(f)
DF = pd.read_csv(ESTIM_DATA_DIR/"data_annonces.csv")
FEATURE_NAMES = MODEL.feature_names_
CAT_FEATURES  = [FEATURE_NAMES[i] for i in MODEL.get_cat_feature_indices()]
NUM_FEATURES  = [f for f in FEATURE_NAMES if f not in CAT_FEATURES]
QUARTIER_MEDIANS = {q:{k:float(v) for k,v in vals.items()} for q,vals in DF.groupby("quartier")[NUM_FEATURES].median().round(4).to_dict(orient="index").items()}
QUARTIER_MODES = {}
for q,grp in DF.groupby("quartier"):
    QUARTIER_MODES[q]={"type_bien":str(grp["type_bien"].mode().iloc[0]),"etat":str(grp["etat"].mode().iloc[0]),"haut standing":str(grp["haut standing"].mode().iloc[0])}
QUARTIERS=sorted(DF["quartier"].unique().tolist())
TYPE_BIENS=sorted(DF["type_bien"].unique().tolist())
ETATS=sorted(DF["etat"].unique().tolist())
BASELINE_DH=float(DF["prix_m2"].median())

app=FastAPI()
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])

class PredictRequest(BaseModel):
    quartier:str;type_bien:str;etat:str;haut_standing:str="0";surface_m2:float=100.0
    feature_overrides:Optional[Dict[str,float]]=None;top_k:int=8

class ScoreRequest(BaseModel):
    values:Dict[str,float]

@app.get("/health")
def health():return safe_json({"status":"ok","n_quartiers":len(QUARTIERS),"baseline_dh":BASELINE_DH})
@app.get("/metadata")
def metadata():return safe_json({"quartiers":QUARTIERS,"categorical_features":{"type_bien":TYPE_BIENS,"etat":ETATS},"numeric_features":NUM_FEATURES})
@app.get("/autofill")
def autofill(quartier:str=Query(...)):
    if quartier not in QUARTIER_MEDIANS:raise HTTPException(404,"Inconnu")
    return safe_json({"quartier":quartier,"median_features":QUARTIER_MEDIANS[quartier],"modal_categorical":QUARTIER_MODES[quartier]})
@app.post("/predict")
def predict(req:PredictRequest):
    num_vals=dict(QUARTIER_MEDIANS.get(req.quartier,{}))
    if req.feature_overrides:num_vals.update({k:v for k,v in req.feature_overrides.items() if k in NUM_FEATURES})
    num_vals["log_surface_m2"]=math.log(max(req.surface_m2,1))
    row={}
    for f in FEATURE_NAMES:
        if f=="quartier":row[f]=req.quartier
        elif f=="type_bien":row[f]=req.type_bien
        elif f=="etat":row[f]=req.etat
        elif f=="haut standing":row[f]=str(req.haut_standing)
        else:row[f]=float(num_vals.get(f,0.0))
    pool=Pool(data=pd.DataFrame([row]),cat_features=CAT_FEATURES,feature_names=FEATURE_NAMES)
    log_pred=float(MODEL.predict(pool)[0]);prix_m2=math.exp(log_pred)
    shap_row=MODEL.get_feature_importance(pool,type="ShapValues")[0]
    contribs=[]
    for i,fname in enumerate(FEATURE_NAMES):
        sv=float(shap_row[i])
        if sv==0:continue
        val=row[fname];
        if isinstance(val,float):val=round(val,2)
        contribs.append({"feature":fname,"value":val,"impact_dh":round(prix_m2-math.exp(log_pred-sv),1)})
    contribs.sort(key=lambda c:abs(c["impact_dh"]),reverse=True)
    return safe_json({"prix_m2_dh":round(prix_m2,0),"baseline_dh":round(BASELINE_DH,0),"type_bien":req.type_bien,"etat":req.etat,"quartier":req.quartier,"surface_m2":req.surface_m2,"contributions":contribs[:req.top_k]})

@app.get("/api/dimensions")
def get_dims():
    return safe_json([{"dimension":d,"variables":[{"name":v,"sign":s,"direction":"négatif" if s=="neg" else "positif"} for v,s in vd.items()]} for d,vd in DIMENSIONS.items()])
@app.get("/api/weights")
def get_weights():
    w=load_weights();return safe_json([{"dimension":d,"poids":round(w[d]*100,2)} for d in DIMENSIONS])
@app.get("/api/zones")
def list_zones():
    z=load_zones()[["Code Zone","Zone déchiffrée","Arrondissement","Préfecture"]].rename(columns={"Zone déchiffrée":"Zone","Préfecture":"Prefecture"})
    return safe_json(z.to_dict(orient="records"))
@app.get("/api/zones/scores")
def all_scores():return safe_json(all_zone_scores().to_dict(orient="records"))
SCORES_CSV = Path(__file__).parent.parent / "score_attractivité" / "score_attractivite_zones_avec_coords.csv"

@app.get("/api/zones/map")
def zones_map():
    if not SCORES_CSV.exists():
        return safe_json({"available":False,"zones":[]})
    df = pd.read_csv(SCORES_CSV, encoding='utf-8-sig')
    df.columns = [c.strip() for c in df.columns]
    df = df.dropna(subset=["lat","lng"])
    records=[]
    for _,r in df.iterrows():
        records.append({
            "code_zone":           str(r["Code Zone"]),
            "zone":                str(r["Zone"]),
            "arrondissement":      str(r["Arrondissement"]),
            "rang":                int(r["Rang"]),
            "score_attractivite":  float(r["Score_Attractivite"]),
            "score_accessibilite": float(r["Score_Accessibilite"]),
            "score_amenites":      float(r["Score_Amenites"]),
            "score_environnement": float(r["Score_Environnement"]),
            "score_prestige":      float(r["Score_Prestige"]),
            "lat":                 float(r["lat"]),
            "lng":                 float(r["lng"]),
        })
    return safe_json({"available":True,"count":len(records),"zones":records})
@app.get("/api/zones/{code_zone}")
def zone_detail(code_zone:str):
    try:features=get_zone_features(code_zone)
    except KeyError as e:raise HTTPException(404,str(e))
    scores=all_zone_scores();row=scores[scores["Code Zone"]==code_zone]
    if row.empty:raise HTTPException(404,"Zone non scorée")
    info=row.iloc[0].to_dict()
    return safe_json({"code_zone":code_zone,"zone":info.get("Zone"),"arrondissement":info.get("Arrondissement"),"prix_m2":info.get("Prix m2 (DH)"),"rang":int(info.get("Rang",0)),"scores":{**{f"Score_{d}":info[f"Score_{d}"] for d in DIMENSIONS},"Score_Attractivite":info["Score_Attractivite"]},"features":features})
@app.post("/api/score")
def compute_score(req:ScoreRequest):
    try:result=score_from_variables(req.values)
    except ValueError as e:raise HTTPException(400,str(e))
    return safe_json(result)

@app.get("/")
def root():
    index=BASE_DIR/"index.html"
    if index.exists():return FileResponse(str(index))
    return safe_json({"message":"API opérationnelle. Voir /docs"})

if __name__=="__main__":
    import uvicorn
    uvicorn.run("api:app",host="0.0.0.0",port=8000,reload=False)
