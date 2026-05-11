/* ============================================================================
   Score d'attractivite - front-end
   ============================================================================ */

const API = ""; // meme origine

const state = {
    zones: [],
    dimensions: [],
    map: null,
    mapLayer: null,
};

/* ----------------------------------------------------------------------------
   Utilitaires
   ---------------------------------------------------------------------------- */
function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

function fmt(n, d = 1) {
    if (n === null || n === undefined || Number.isNaN(n)) return "-";
    return Number(n).toLocaleString("fr-FR", {
        minimumFractionDigits: d,
        maximumFractionDigits: d,
    });
}
function fmtInt(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return "-";
    return Number(n).toLocaleString("fr-FR", { maximumFractionDigits: 0 });
}

/** Couleur d'un score 0-100 sur un degrade rouge-or-vert. */
function scoreColor(s) {
    if (s === null || s === undefined || Number.isNaN(s)) return "#9aa3b1";
    const t = Math.max(0, Math.min(1, s / 100));
    const stops = [
        { p: 0.00, c: [140,  58,  46] }, // rouge fonce
        { p: 0.25, c: [201, 122,  74] },
        { p: 0.50, c: [216, 185, 106] }, // or
        { p: 0.75, c: [ 95, 165, 126] },
        { p: 1.00, c: [ 31,  92,  63] }, // vert fonce
    ];
    let a = stops[0], b = stops[stops.length - 1];
    for (let i = 0; i < stops.length - 1; i++) {
        if (t >= stops[i].p && t <= stops[i + 1].p) { a = stops[i]; b = stops[i + 1]; break; }
    }
    const k = (t - a.p) / (b.p - a.p || 1);
    const r = Math.round(a.c[0] + k * (b.c[0] - a.c[0]));
    const g = Math.round(a.c[1] + k * (b.c[1] - a.c[1]));
    const bl = Math.round(a.c[2] + k * (b.c[2] - a.c[2]));
    return `rgb(${r}, ${g}, ${bl})`;
}

async function api(path, opts = {}) {
    const res = await fetch(API + path, opts);
    if (!res.ok) {
        const detail = await res.text();
        throw new Error(`${res.status} ${detail}`);
    }
    return res.json();
}

/* ----------------------------------------------------------------------------
   Onglets
   ---------------------------------------------------------------------------- */
function activateTab(name) {
    $$(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    $$(".panel").forEach(p => p.classList.toggle("hidden", p.id !== `tab-${name}`));
    if (name === "map" && state.map) {
        // Leaflet a besoin d'un invalidateSize quand le conteneur etait cache
        setTimeout(() => state.map.invalidateSize(), 50);
    }
}

$$(".tab-btn").forEach(b => b.addEventListener("click", () => activateTab(b.dataset.tab)));

/* ----------------------------------------------------------------------------
   Initialisation
   ---------------------------------------------------------------------------- */
async function init() {
    try {
        const [zones, dims] = await Promise.all([
            api("/api/zones"),
            api("/api/dimensions"),
        ]);
        state.zones = zones;
        state.dimensions = dims;

        populateZoneSelects();
        buildCustomForm();
        await initMap();
    } catch (e) {
        console.error(e);
        alert("Echec d'initialisation : " + e.message);
    }
}

/* ----------------------------------------------------------------------------
   Selecteurs de zone
   ---------------------------------------------------------------------------- */
function populateZoneSelects() {
    const prefillSel = $("#prefill-select");

    const opts = state.zones
        .slice()
        .sort((a, b) => a["Code Zone"].localeCompare(b["Code Zone"]))
        .map(z => {
            const lab = `${z["Code Zone"]} - ${z["Zone"]} (${z["Arrondissement"]})`;
            return `<option value="${z["Code Zone"]}">${escapeHtml(lab)}</option>`;
        }).join("");
    prefillSel.insertAdjacentHTML("beforeend", opts);

    $("#prefill-btn").addEventListener("click", () => prefillCustom(prefillSel.value));
    $("#reset-btn").addEventListener("click", resetCustomForm);
    $("#calc-btn").addEventListener("click", calcCustom);
}

function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, c => (
        { "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c]
    ));
}

/* ----------------------------------------------------------------------------
   Carte
   ---------------------------------------------------------------------------- */
async function initMap() {
    const data = await api("/api/zones/map");
    if (!data.available) {
        $("#map-empty-msg").textContent = data.message;
        $("#map-empty").classList.remove("hidden");
        $("#map").style.display = "none";
        return;
    }
    if (!data.zones.length) {
        $("#map-empty-msg").textContent =
            "Aucune zone ne dispose de coordonnees valides dans coordonnees_zones.csv.";
        $("#map-empty").classList.remove("hidden");
        $("#map").style.display = "none";
        return;
    }

    state.map = L.map("map", { zoomControl: true }).setView([33.57, -7.59], 12);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(state.map);

    const group = L.featureGroup();
    data.zones.forEach(z => {
        const lat = Number(z.lat), lng = Number(z.lng);
        if (!Number.isFinite(lat) || !Number.isFinite(lng)) return;
        const c = scoreColor(z.Score_Attractivite);
        const marker = L.circleMarker([lat, lng], {
            radius: 9 + (z.Score_Attractivite / 100) * 7,
            fillColor: c,
            color: "#15294a",
            weight: 1,
            opacity: 1,
            fillOpacity: 0.85,
        });
        marker.bindPopup(`
            <div class="popup-title">${escapeHtml(z["Code Zone"])} - ${escapeHtml(z["Zone"] || "")}</div>
            <div class="popup-meta">${escapeHtml(z.Arrondissement || "")}</div>
            <div class="popup-score">Score global : <strong>${fmt(z.Score_Attractivite)}</strong> / 100</div>
            <div class="popup-meta" style="margin-top:6px">
                Accessibilite ${fmt(z.Score_Accessibilite)}<br>
                Amenites ${fmt(z.Score_Amenites)}<br>
                Environnement ${fmt(z.Score_Environnement)}<br>
                Socio-demographie ${fmt(z.Score_SocioDemo)}
            </div>
            <div class="popup-meta" style="margin-top:6px">Rang : ${z.Rang} / ${data.count}</div>
        `);
        marker.addTo(state.map);
        group.addLayer(marker);
    });
    state.mapLayer = group;
    if (group.getLayers().length > 0) {
        state.map.fitBounds(group.getBounds().pad(0.15));
    }
}

/* ----------------------------------------------------------------------------
   Formulaire de calcul du score
   ---------------------------------------------------------------------------- */
function buildCustomForm() {
    const form = $("#custom-form");
    const labels = {
        // Accessibilite
        dist_tram: "Distance au tramway (m)",
        dist_voie_primaire_min_m: "Distance voie primaire (m)",
        dist_voie_secondaire_min_m: "Distance voie secondaire (m)",
        temps_transport_centre: "Temps trajet centre (min)",
        temps_CFC: "Temps trajet CFC (min)",
        temps_Maarif: "Temps trajet Maarif (min)",
        temps_SidiMaarouf: "Temps trajet Sidi Maarouf (min)",
        temps_port: "Temps trajet port (min)",
        // Amenites
        nb_ecoles_1km: "Nb ecoles dans 1 km",
        nb_sante_1km: "Nb etablissements sante dans 1 km",
        nb_commerces_1km: "Nb commerces dans 1 km",
        nb_restaurants_500m: "Nb restaurants dans 500 m",
        nb_banques_1km: "Nb banques dans 1 km",
        // Environnement
        dist_mer: "Distance a la mer (m)",
        dist_parc: "Distance au parc (m)",
        surface_verte_1km: "Surface verte 1 km (m2)",
        nb_nuisance_500m: "Nb nuisances dans 500 m",
        // SocioDemo
        "Taux_activité": "Taux d'activite (%)",
        "Part_population_niveau_études_supérieur": "Part population etudes superieures (%)",
        "Taux_chômage": "Taux de chomage (%)",
        "Taux_croissance": "Taux de croissance (%)",
    };

    form.innerHTML = state.dimensions.map(dim => `
        <div class="dim-card">
            <h3>${dim.dimension}</h3>
            ${dim.variables.map(v => `
                <div class="var-field">
                    <label for="f-${v.name}">
                        ${escapeHtml(labels[v.name] || v.name)}
                        <span class="sign" title="Signe : ${v.direction}">${v.sign}</span>
                    </label>
                    <input type="number" step="any" id="f-${v.name}" name="${v.name}" />
                </div>
            `).join("")}
        </div>
    `).join("");
}

async function prefillCustom(code) {
    if (!code) return;
    try {
        const d = await api(`/api/zones/${encodeURIComponent(code)}`);
        Object.entries(d.features).forEach(([k, v]) => {
            const el = $(`#f-${cssEscape(k)}`);
            if (el) el.value = Number(v).toFixed(2);
        });
    } catch (e) {
        alert("Erreur : " + e.message);
    }
}

function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_\-]/g, c => "\\" + c);
}

function resetCustomForm() {
    $$("#custom-form input").forEach(i => i.value = "");
    $("#custom-result").classList.add("hidden");
}

async function calcCustom() {
    const values = {};
    let missing = [];
    $$("#custom-form input").forEach(i => {
        if (i.value === "" || i.value === null) {
            missing.push(i.name);
        } else {
            values[i.name] = Number(i.value);
        }
    });
    if (missing.length) {
        alert("Variables manquantes : " + missing.join(", "));
        return;
    }
    try {
        const r = await api("/api/score", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ values }),
        });
        renderResult($("#custom-result"), {
            title: "Point hypothetique",
            meta: "Score calcule a partir des valeurs saisies",
            scores: r,
        });
    } catch (e) {
        alert("Erreur : " + e.message);
    }
}

/* ----------------------------------------------------------------------------
   Affichage d'un resultat de score
   ---------------------------------------------------------------------------- */
function renderResult(host, data) {
    const s = data.scores;
    const c = scoreColor(s.Score_Attractivite);
    const rankHtml = data.rank
        ? `<div class="result-global-rank">Rang : <strong>${data.rank}</strong> / ${data.total}</div>`
        : "";
    host.innerHTML = `
        <h3>${escapeHtml(data.title)}</h3>
        <div class="result-meta">${escapeHtml(data.meta)}</div>

        <div class="result-global" style="border-left:4px solid ${c}">
            <div>
                <div class="result-global-label">Score d'attractivite</div>
                <div class="result-global-value" style="color:${c}">${fmt(s.Score_Attractivite)}<span style="font-size:14px;color:var(--text-muted);font-weight:500"> / 100</span></div>
            </div>
            ${rankHtml}
        </div>

        <div class="subscores">
            ${subscoreBlock("Accessibilite", s.Score_Accessibilite)}
            ${subscoreBlock("Amenites", s.Score_Amenites)}
            ${subscoreBlock("Environnement", s.Score_Environnement)}
            ${subscoreBlock("Socio-demographie", s.Score_SocioDemo)}
        </div>
    `;
    host.classList.remove("hidden");
}

function subscoreBlock(label, value) {
    const v = Math.max(0, Math.min(100, value || 0));
    return `
        <div class="subscore">
            <div class="subscore-label">${label}</div>
            <div class="subscore-value">${fmt(value)}</div>
            <div class="subscore-bar"><div class="subscore-bar-fill" style="width:${v}%"></div></div>
        </div>
    `;
}

/* ----------------------------------------------------------------------------
   Go
   ---------------------------------------------------------------------------- */
init();
