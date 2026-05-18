# API Score d'attractivite - Casablanca

API FastAPI et interface web pour le calcul du score d'attractivite des zones
de Casablanca, base sur l'analyse hedonique du notebook
`score_attractivite.ipynb`.

## Structure

```
score_attractivite/
|-- api/
|   |-- main.py            # FastAPI - endpoints
|   |-- scoring.py         # Calcul du score (normalisation + ponderation)
|   |-- requirements.txt
|   |-- README.md
|   `-- static/
|       |-- index.html     # Interface web
|       |-- style.css
|       `-- app.js
|-- data_finale.csv
|-- poids_dimensions.csv
|-- score_attractivite_zones.csv
`-- coordonnees_zones.csv   # A AJOUTER (pour la carte)
```

## Installation

```bash
cd score_attractivite/api
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Lancement

```bash
cd score_attractivite/api
uvicorn main:app --reload --port 8000
```

Interface :    http://localhost:8000/
Documentation : http://localhost:8000/docs

## Endpoints principaux

| Methode | URL                       | Description                              |
| ------- | ------------------------- | ---------------------------------------- |
| GET     | /api/health               | Verification du service                  |
| GET     | /api/dimensions           | Liste des dimensions et variables        |
| GET     | /api/weights              | Poids des dimensions (en %)              |
| GET     | /api/zones                | Liste des zones (selecteurs)             |
| GET     | /api/zones/scores         | Tableau complet des scores               |
| GET     | /api/zones/map            | Zones + lat/lng + score (carte)          |
| GET     | /api/zones/{code}         | Detail d'une zone + features             |
| POST    | /api/score                | Calcul d'un score sur valeurs custom     |

### Exemple : score custom

```bash
curl -X POST http://localhost:8000/api/score \
     -H "Content-Type: application/json" \
     -d '{
       "values": {
         "dist_tram": 500,
         "dist_voie_primaire_min_m": 200,
         "dist_voie_secondaire_min_m": 150,
         "temps_transport_centre": 20,
         "temps_CFC": 25,
         "temps_Maarif": 10,
         "temps_SidiMaarouf": 15,
         "temps_port": 30,
         "nb_ecoles_1km": 8,
         "nb_sante_1km": 4,
         "nb_commerces_1km": 30,
         "nb_restaurants_500m": 15,
         "nb_banques_1km": 6,
         "dist_mer": 1000,
         "dist_parc": 300,
         "surface_verte_1km": 50000,
         "nb_nuisance_500m": 2,
         "Taux_activité": 55,
         "Part_population_niveau_études_supérieur": 25,
         "Taux_chômage": 12,
         "Taux_croissance": 1.5
       }
     }'
```

## Carte des zones

Pour activer l'onglet **Cartographie**, ajoutez un fichier
`coordonnees_zones.csv` a la racine du dossier `score_attractivite/` avec au
minimum les colonnes suivantes :

```csv
Code Zone,lat,lng
CC-SB1,33.5938,-7.6193
CC-SB2,33.5905,-7.6118
...
```

Au prochain rafraichissement de la page, les zones apparaissent sur la carte
avec un degrade de couleur proportionnel au score (rouge fonce 0 -> vert
fonce 100).

## Methodologie

- variables reparties sur 2 dimensions : Accessibilite, Amenites.
- Chaque variable est normalisee sur [0, 1] par MinMax (les bornes sont
  celles observees sur les zones de reference) et inversee si elle exprime
  une contrainte (distance, temps, nuisance).
- Sous-score = moyenne des variables normalisees de la dimension.
- Score global = combinaison ponderee des sous-scores (poids issus de la
  regression hedonique Ridge -- `poids_dimensions.csv`) puis re-echelonnage
  sur [0, 100].
