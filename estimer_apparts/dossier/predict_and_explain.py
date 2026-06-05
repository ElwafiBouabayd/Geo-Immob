"""
Script de test, prédiction et explication SHAP
Modèle XGBoost — Prix/m² appartements Casablanca (Avito)
=========================================================

Ce script :
  1. Charge le pipeline XGBoost sauvegardé
  2. Reconstruit et sauvegarde les LabelEncoders (artefact manquant)
  3. Définit predict_prix_m2() — fonction de prédiction prête à l'emploi
  4. Teste le modèle sur 4 cas concrets (Maarif, Aïn Diab, Sidi Othmane, Californie)
  5. Génère les analyses SHAP (summary, bar plot, waterfall par appartement)

Dépendances :
  pip install xgboost>=3 scikit-learn>=1.8 shap joblib pandas matplotlib

Auteur : ebbe / 2026-05-22
"""

import warnings
warnings.filterwarnings("ignore")

import os
import json
import textwrap
import inspect
import types
import joblib
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # sauvegarde PNG sans affichage
import matplotlib.pyplot as plt

# ─────────────────────────────────────────────────────────────────────────────
# PATCH SHAP — compatibilité XGBoost ≥ 3.x
#   XGBoost 3 stocke base_score comme "[3.0680967E4]" (notation tableau)
#   au lieu de "30680.967". SHAP 0.49 ne gère pas ce format → on le fixe.
# ─────────────────────────────────────────────────────────────────────────────
import shap
import shap.explainers._tree as _shap_tree

_orig_src  = textwrap.dedent(inspect.getsource(_shap_tree.XGBTreeModelLoader.__init__))
_patched   = _orig_src.replace(
    'float(learner_model_param["base_score"])',
    'float(str(learner_model_param["base_score"]).strip("[]"))',
)
exec(_patched, _shap_tree.XGBTreeModelLoader.__init__.__globals__)
_shap_tree.XGBTreeModelLoader.__init__ = \
    _shap_tree.XGBTreeModelLoader.__init__.__globals__["__init__"]
# ─────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────
# 0. Chemins
# ──────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS  = os.path.join(SCRIPT_DIR, "model_artifacts")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "outputs_shap")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ──────────────────────────────────────────
# 1. Chargement des artefacts
# ──────────────────────────────────────────
print("=" * 62)
print("  Chargement du modèle")
print("=" * 62)

pipeline    = joblib.load(os.path.join(ARTIFACTS, "best_xgboost_pipeline.joblib"))
best_params = joblib.load(os.path.join(ARTIFACTS, "best_hyperparams_xgboost.joblib"))

preprocessor = pipeline.named_steps["preprocessor"]
xgb_model    = pipeline.named_steps["model"]

print(f"  Étapes du pipeline : {[s[0] for s in pipeline.steps]}")
print(f"  Meilleurs hyperparamètres XGBoost :")
for k, v in best_params.items():
    print(f"    {k:<35s}: {v}")


# ──────────────────────────────────────────
# 2. Reconstruction & sauvegarde des LabelEncoders
#    (non inclus dans les artefacts d'origine)
# ──────────────────────────────────────────
print("\n" + "─" * 62)
print("  Reconstruction des LabelEncoders")
print("─" * 62)

from sklearn.preprocessing import LabelEncoder

# Valeurs uniques extraites des données d'entraînement
CAT_UNIQUE = {
    "Secteur/Quartier": [
        "2 Mars", "Abdelmoumen", "Ain Sebaa", "Al Fida",
        "Al Madina Aljadida", "Al Mostakbal", "Al Qods", "Almaz",
        "Anfa", "Autre Secteur", "Autre secteur",
        "Aïn Borja", "Aïn Chock", "Aïn Diab",
        "Bachkou", "Beauséjour", "Belvédère",
        "Ben Ejdia", "Ben M Sick", "Bourgogne", "Bournazil",
        "C.I.L", "Californie", "Casablanca Finance City",
        "Centre Ville", "Derb Ghallef", "Errahma",
        "Ferme Bretone", "Franceville", "Gauthier",
        "Hay Albaraka", "Hay Hassani", "Hay Inara",
        "Hay Mohammadi", "Hay Moulay Rachid",
        "La Gironde", "Les Princesses", "Lissasfa",
        "Maarif", "Maârif Extension", "Mers Sultan",
        "Nassim", "Oasis", "Oulfa", "Palmier",
        "Quartier Des Hôpitaux", "Racine", "Riviera",
        "Roches Noires", "Route d'Azemmour", "Sbata",
        "Sidi Bernoussi", "Sidi Maarouf", "Sidi Moumen",
        "Sidi Othmane", "Triangle d'Or", "Val Fleuri",
    ],
    "Type de bien": ["Appartement", "Studio"],
    "Condition":    ["A renover", "Bon etat", "Neuf"],
    "Standing":     ["Economique", "Haut standing", "Moyen standing"],
}

label_encoders: dict[str, LabelEncoder] = {}
for col, values in CAT_UNIQUE.items():
    le = LabelEncoder()
    le.fit(sorted(values))          # LabelEncoder trie alphabétiquement
    label_encoders[col] = le
    mapping = {v: int(i) for i, v in enumerate(le.classes_)}
    print(f"  {col}: {mapping}")

le_path = os.path.join(ARTIFACTS, "label_encoders.joblib")
joblib.dump(label_encoders, le_path)
print(f"\n  ✓ label_encoders.joblib sauvegardé ({os.path.getsize(le_path)//1024} Ko)")


# ──────────────────────────────────────────
# 3. Constantes du pipeline
# ──────────────────────────────────────────
NUM_COLS = [
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
CAT_COLS = ["Secteur/Quartier", "Type de bien", "Condition", "Standing"]

ETAGE_MAP = {
    "RDC": 0,
    **{f"Etage {i}": i for i in range(1, 12)},
}

FEAT_NAMES = [
    f.replace("num__", "").replace("remainder__", "")
    for f in preprocessor.get_feature_names_out()
]


# ──────────────────────────────────────────
# 4. Fonction de prédiction
# ──────────────────────────────────────────
def predict_prix_m2(
    surface_m2:           float,
    quartier:             str,
    etage:                str   = "Etage 2",
    chambres:             int   = 2,
    salles_de_bain:       int   = 1,
    type_de_bien:         str   = "Appartement",
    condition:            str   = "Bon etat",
    standing:             str   = "Moyen standing",
    latitude:             float = 33.5731,
    longitude:            float = -7.5898,
    dist_tram_m:          float = 800.0,
    tram_500m:            int   = 0,
    intersect_500m:       int   = 1,
    tt_centre_min:        float = 10.0,
    tt_cfc_min:           float = 20.0,
    tt_maarif_min:        float = 15.0,
    tt_sidi_maarouf_min:  float = 25.0,
    tt_port_min:          float = 18.0,
    n_ecoles_500m:        int   = 2,
    n_ecoles_1km:         int   = 6,
    dist_ecole_m:         float = 200.0,
    n_banques_500m:       int   = 1,
    n_banques_1km:        int   = 4,
    dist_banque_m:        float = 350.0,
    n_malls_500m:         int   = 0,
    n_malls_1km:          int   = 1,
    dist_mall_m:          float = 900.0,
    dist_mer_m:           float = 5000.0,
    dist_parc_m:          float = 400.0,
    n_parcs_1km:          int   = 2,
    surface_verte_m2_1km: float = 30000.0,
    nuisance_route_500m:  float = 1500.0,
    n_industries_500m:    int   = 0,
    n_fuel_500m:          int   = 0,
) -> dict:
    """
    Prédit le prix au m² d'un appartement à Casablanca.

    Paramètres obligatoires
    -----------------------
    surface_m2 : surface en m²
    quartier   : quartier de l'annonce (voir CAT_UNIQUE pour les valeurs valides)

    Retour
    ------
    dict {
        "prix_m2"          : float  – prix estimé en DH/m²
        "prix_total"       : float  – prix total (DH)
        "fourchette_basse" : float  – -20 % (approximation MAPE modèle ≈ 25 %)
        "fourchette_haute" : float  – +20 %
        "input_df"         : pd.DataFrame  – données encodées passées au pipeline
    }
    """
    # Validation des variables catégorielles
    valid_quartiers = list(label_encoders["Secteur/Quartier"].classes_)
    if quartier not in valid_quartiers:
        raise ValueError(
            f"Quartier inconnu : '{quartier}'\n"
            f"Quartiers valides : {sorted(valid_quartiers)}"
        )
    for col, val in [("Type de bien", type_de_bien),
                     ("Condition", condition),
                     ("Standing", standing)]:
        valid = list(label_encoders[col].classes_)
        if val not in valid:
            raise ValueError(f"'{col}' inconnu : '{val}'. Valeurs valides : {valid}")

    etage_int = ETAGE_MAP.get(etage)
    if etage_int is None:
        raise ValueError(f"Etage inconnu : '{etage}'. Valeurs : {list(ETAGE_MAP)}")

    # Construction du DataFrame
    row = dict(
        zip(
            NUM_COLS,
            [surface_m2, etage_int, chambres, salles_de_bain,
             latitude, longitude, dist_tram_m, tram_500m, intersect_500m,
             tt_centre_min, tt_cfc_min, tt_maarif_min, tt_sidi_maarouf_min, tt_port_min,
             n_ecoles_500m, n_ecoles_1km, dist_ecole_m,
             n_banques_500m, n_banques_1km, dist_banque_m,
             n_malls_500m, n_malls_1km, dist_mall_m,
             dist_mer_m, dist_parc_m, n_parcs_1km,
             surface_verte_m2_1km, nuisance_route_500m,
             n_industries_500m, n_fuel_500m],
        )
    )
    row["Secteur/Quartier"] = quartier
    row["Type de bien"]     = type_de_bien
    row["Condition"]        = condition
    row["Standing"]         = standing

    df = pd.DataFrame([row])

    # LabelEncoding des variables catégorielles
    for col in CAT_COLS:
        df[col] = label_encoders[col].transform(df[col].astype(str))

    df = df[NUM_COLS + CAT_COLS]   # ordre attendu par le pipeline

    # Prédiction
    prix_m2   = float(pipeline.predict(df)[0])
    prix_total = prix_m2 * surface_m2

    return {
        "prix_m2":         round(prix_m2, 0),
        "prix_total":      round(prix_total, 0),
        "fourchette_basse": round(prix_m2 * 0.80, 0),
        "fourchette_haute": round(prix_m2 * 1.20, 0),
        "input_df":        df,
    }


# ──────────────────────────────────────────
# 5. Tests de la fonction de prédiction
# ──────────────────────────────────────────
print("\n" + "=" * 62)
print("  TESTS DE PRÉDICTION")
print("=" * 62)

cas_tests = [
    dict(
        _label       = "Maarif · 80 m² · Etage 3 · Bon état · Moyen standing",
        surface_m2   = 80,    quartier = "Maarif",
        etage        = "Etage 3", chambres = 2, salles_de_bain = 1,
        type_de_bien = "Appartement", condition = "Bon etat", standing = "Moyen standing",
        latitude = 33.5900,  longitude = -7.6300,
        dist_tram_m = 400,   tram_500m = 1,    intersect_500m = 2,
        tt_centre_min = 8,   tt_cfc_min = 12,  tt_maarif_min = 2,
        tt_sidi_maarouf_min = 22, tt_port_min = 15,
        n_ecoles_500m = 3,  n_ecoles_1km = 8,  dist_ecole_m = 120,
        n_banques_500m = 2, n_banques_1km = 6, dist_banque_m = 200,
        n_malls_500m = 1,   n_malls_1km = 2,   dist_mall_m = 300,
        dist_mer_m = 3500,  dist_parc_m = 250, n_parcs_1km = 3,
        surface_verte_m2_1km = 25000, nuisance_route_500m = 1800,
        n_industries_500m = 0, n_fuel_500m = 1,
    ),
    dict(
        _label       = "Aïn Diab · 120 m² · Etage 5 · Neuf · Haut standing",
        surface_m2   = 120,   quartier = "Aïn Diab",
        etage        = "Etage 5", chambres = 3, salles_de_bain = 2,
        type_de_bien = "Appartement", condition = "Neuf", standing = "Haut standing",
        latitude = 33.5980,  longitude = -7.6900,
        dist_tram_m = 1200,  tram_500m = 0,    intersect_500m = 1,
        tt_centre_min = 15,  tt_cfc_min = 20,  tt_maarif_min = 18,
        tt_sidi_maarouf_min = 30, tt_port_min = 22,
        n_ecoles_500m = 1,  n_ecoles_1km = 4,  dist_ecole_m = 450,
        n_banques_500m = 1, n_banques_1km = 3, dist_banque_m = 500,
        n_malls_500m = 0,   n_malls_1km = 1,   dist_mall_m = 700,
        dist_mer_m = 400,   dist_parc_m = 150, n_parcs_1km = 2,
        surface_verte_m2_1km = 80000, nuisance_route_500m = 2000,
        n_industries_500m = 0, n_fuel_500m = 0,
    ),
    dict(
        _label       = "Sidi Othmane · 35 m² · RDC · À rénover · Économique",
        surface_m2   = 35,    quartier = "Sidi Othmane",
        etage        = "RDC", chambres = 1, salles_de_bain = 1,
        type_de_bien = "Studio", condition = "A renover", standing = "Economique",
        latitude = 33.5500,  longitude = -7.5700,
        dist_tram_m = 900,   tram_500m = 0,    intersect_500m = 0,
        tt_centre_min = 20,  tt_cfc_min = 30,  tt_maarif_min = 25,
        tt_sidi_maarouf_min = 35, tt_port_min = 28,
        n_ecoles_500m = 1,  n_ecoles_1km = 3,  dist_ecole_m = 300,
        n_banques_500m = 0, n_banques_1km = 2, dist_banque_m = 600,
        n_malls_500m = 0,   n_malls_1km = 0,   dist_mall_m = 1500,
        dist_mer_m = 5500,  dist_parc_m = 700, n_parcs_1km = 1,
        surface_verte_m2_1km = 15000, nuisance_route_500m = 800,
        n_industries_500m = 1, n_fuel_500m = 0,
    ),
    dict(
        _label       = "Californie · 150 m² · Etage 7 · Neuf · Haut standing",
        surface_m2   = 150,   quartier = "Californie",
        etage        = "Etage 7", chambres = 4, salles_de_bain = 2,
        type_de_bien = "Appartement", condition = "Neuf", standing = "Haut standing",
        latitude = 33.5800,  longitude = -7.6500,
        dist_tram_m = 600,   tram_500m = 0,    intersect_500m = 1,
        tt_centre_min = 12,  tt_cfc_min = 8,   tt_maarif_min = 10,
        tt_sidi_maarouf_min = 18, tt_port_min = 18,
        n_ecoles_500m = 2,  n_ecoles_1km = 5,  dist_ecole_m = 250,
        n_banques_500m = 1, n_banques_1km = 4, dist_banque_m = 400,
        n_malls_500m = 0,   n_malls_1km = 2,   dist_mall_m = 600,
        dist_mer_m = 2000,  dist_parc_m = 300, n_parcs_1km = 3,
        surface_verte_m2_1km = 45000, nuisance_route_500m = 2500,
        n_industries_500m = 0, n_fuel_500m = 1,
    ),
]

input_dfs   = []
short_labels = []

for cas in cas_tests:
    label = cas.pop("_label")
    short_labels.append(label.split("·")[0].strip())
    result = predict_prix_m2(**cas)
    input_dfs.append(result["input_df"])
    s = cas["surface_m2"]
    print(f"\n  ► {label}")
    print(f"    Prix/m²    : {result['prix_m2']:>10,.0f} DH/m²")
    print(f"    Fourchette : [{result['fourchette_basse']:>10,.0f} — {result['fourchette_haute']:>10,.0f}] DH/m²")
    print(f"    Prix total : {result['prix_total']:>10,.0f} DH  "
          f"  [{result['fourchette_basse']*s:,.0f} — {result['fourchette_haute']*s:,.0f}]")


# ──────────────────────────────────────────
# 6. Analyse SHAP
# ──────────────────────────────────────────
print("\n" + "=" * 62)
print("  ANALYSE SHAP")
print("=" * 62)

# Données background = les 4 cas de test (diversité maximale)
background_df          = pd.concat(input_dfs, ignore_index=True)[NUM_COLS + CAT_COLS]
background_transformed = preprocessor.transform(background_df)

explainer  = shap.TreeExplainer(xgb_model, background_transformed)
shap_vals  = explainer(background_transformed)

# Nommer les features pour les plots
shap_vals.feature_names = FEAT_NAMES

# ── 6a. Summary plot (beeswarm) ──────────────────────────────────────────────
print("\n  Génération des graphiques SHAP…")

fig, _ = plt.subplots(figsize=(10, 8))
shap.summary_plot(
    shap_vals.values, background_transformed,
    feature_names=FEAT_NAMES, show=False, plot_size=(10, 8),
)
plt.title("SHAP — Impact des variables sur le prix/m²", fontsize=13, pad=14)
plt.tight_layout()
path_summary = os.path.join(OUTPUT_DIR, "shap_summary.png")
plt.savefig(path_summary, dpi=150, bbox_inches="tight")
plt.close()
print(f"  ✓ shap_summary.png")

# ── 6b. Bar plot — importance absolue moyenne ────────────────────────────────
fig, _ = plt.subplots(figsize=(10, 7))
shap.summary_plot(
    shap_vals.values, background_transformed,
    feature_names=FEAT_NAMES, plot_type="bar", show=False,
)
plt.title("SHAP — Importance moyenne |SHAP|", fontsize=13, pad=14)
plt.tight_layout()
path_bar = os.path.join(OUTPUT_DIR, "shap_importance_bar.png")
plt.savefig(path_bar, dpi=150, bbox_inches="tight")
plt.close()
print(f"  ✓ shap_importance_bar.png")

# ── 6c. Waterfall — explication par prédiction ──────────────────────────────
for i, lbl in enumerate(short_labels):
    fig, _ = plt.subplots(figsize=(10, 6))
    shap.waterfall_plot(shap_vals[i], max_display=12, show=False)
    plt.title(f"SHAP Waterfall — {lbl}", fontsize=12, pad=10)
    plt.tight_layout()
    fname = f"shap_waterfall_{i+1}_{lbl.replace(' ','_')}.png"
    path  = os.path.join(OUTPUT_DIR, fname)
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  ✓ {fname}")

# ── 6d. Tableau Top-10 features ─────────────────────────────────────────────
mean_abs = np.abs(shap_vals.values).mean(axis=0)
top10 = (
    pd.DataFrame({"Feature": FEAT_NAMES, "Mean |SHAP|": mean_abs})
    .sort_values("Mean |SHAP|", ascending=False)
    .head(10)
    .reset_index(drop=True)
)
top10.index += 1
print("\n  Top-10 variables (importance SHAP moyenne) :")
print(top10.to_string())


# ──────────────────────────────────────────
# 7. Résumé
# ──────────────────────────────────────────
print("\n" + "=" * 62)
print("  ARTEFACTS DISPONIBLES")
print("=" * 62)
for f in sorted(os.listdir(ARTIFACTS)):
    p = os.path.join(ARTIFACTS, f)
    print(f"  {f:<45s} {os.path.getsize(p)/1024:>7.1f} Ko")

print(f"\n  Graphiques SHAP → {OUTPUT_DIR}/")
for f in sorted(os.listdir(OUTPUT_DIR)):
    if f.endswith(".png"):
        print(f"  {f}")

print("\n✓  Script terminé avec succès.\n")
