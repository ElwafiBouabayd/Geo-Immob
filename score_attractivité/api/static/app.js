/* ============================================================
   Score d'attractivité v2 — frontend
   ============================================================ */

const API = "";   // même origine

const state = { zones: [], dimensions: [], map: null, markers: {} };

/* ── Utilitaires ─────────────────────────────────────────── */
function $(sel, root = document) { return root.querySelector(sel); }
function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

function fmt(n, d = 1) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "–";
    return Number(n).toLocaleString("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function scoreColor(s) {
    if (s === null || s === undefined || Number.isNaN(s)) return "#9aa3b1";
    const t = Math.max(0, Math.min(1, s / 100));
    const stops = [
        { p: 0.00, c: [140,  58,  46] },
        { p: 0.25, c: [201, 122,  74] },
        { p: 0.50, c: [216, 185, 106] },
        { p: 0.75, c: [ 95, 165, 126] },
        { p: 1.00, c: [ 31,  92,  63] },
    ];
    let a = stops[0], b = stops[stops.length - 1];
    for (let i = 0; i < stops.length - 1; i++) {
        if (t >= stops[i].p && t <= stops[i + 1].p) { a = stops[i]; b = stops[i + 1]; break; }
    }
    const k = (t - a.p) / (b.p - a.p || 1);
    return `rgb(${Math.round(a.c[0]+k*(b.c[0]-a.c[0]))},${Math.round(a.c[1]+k*(b.c[1]-a.c[1]))},${Math.round(a.c[2]+k*(b.c[2]-a.c[2]))})`;
}

async function api(path, opts = {}) {
    const res = await fetch(API + path, opts);
    if (!res.ok) { const t = await res.text(); throw new Error(`${res.status} — ${t}`); }
    return res.json();
}

function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g,
        c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

/* ── Onglets ──────────────────────────────────────────────── */
function activateTab(name) {
    $$(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
    $$(".panel").forEach(p => p.classList.toggle("hidden", p.id !== `tab-${name}`));
    if (name === "map" && state.map) setTimeout(() => state.map.invalidateSize(), 50);
}
$$(".tab-btn").forEach(b => b.addEventListener("click", () => activateTab(b.dataset.tab)));

/* ── Labels lisibles pour chaque variable ─────────────────── */
const LABELS = {
    // Dynamisme
    prix_m2_moyen:  "Prix moyen au m² (DH)",
    nb_annonces:    "Volume d'annonces",
    // Socio-éco
    Densite_pop_km2:                         "Densité population (hab/km²)",
    Taille_de_menage:                        "Taille du ménage (pers.)",
    Taux_activite:                           "Taux d'activité (%)",
    Part_salaries_parmi_actifs:              "Part salariés parmi actifs (%)",
    Part_population_niveau_etudes_superieur: "Part études supérieures (%)",
    // Accessibilité
    distance_vol_oiseau_km:   "Distance centre-ville (km)",
    temps_voiture_estime_min: "Temps trajet estimé (min)",
    nb_arrets_bus_500m:       "Arrêts bus dans 500 m",
    nb_arrets_tramway_500m:   "Arrêts tramway dans 500 m",
    nb_taxis_500m:            "Stations taxi dans 500 m",
    // Équipements
    nb_ecoles_1km:       "Écoles dans 1 km",
    nb_sante_2km:        "Établissements santé dans 2 km",
    nb_pharmacies_500m:  "Pharmacies dans 500 m",
    nb_commerce_500m:    "Commerces dans 500 m",
    nb_banques_1km:      "Banques dans 1 km",
    nb_mosquees_500m:    "Mosquées dans 500 m",
    nb_restaurants_500m: "Restaurants dans 500 m",
};

/* ── Init ─────────────────────────────────────────────────── */
async function init() {
    try {
        const [zones, dims] = await Promise.all([
            api("/api/zones"),
            api("/api/dimensions"),
        ]);
        state.zones      = zones;
        state.dimensions = dims;
        populatePrefill();
        buildForm();
        await initMap();
    } catch (e) {
        console.error(e);
        alert("Erreur d'initialisation : " + e.message);
    }
}

/* ── Sélecteur pré-remplissage ────────────────────────────── */
function populatePrefill() {
    const sel = $("#prefill-select");
    const opts = state.zones
        .sort((a, b) => String(a["Code Zone"]).localeCompare(String(b["Code Zone"])))
        .map(z => {
            const arr = z["Arrondissement"] ? ` (${z["Arrondissement"]})` : "";
            const lbl = `${z["Code Zone"]} — ${z["Zone"] || ""}${arr}`;
            return `<option value="${esc(z["Code Zone"])}">${esc(lbl)}</option>`;
        }).join("");
    sel.insertAdjacentHTML("beforeend", opts);

    $("#prefill-btn").addEventListener("click", () => prefill(sel.value));
    $("#reset-btn").addEventListener("click",   resetForm);
    $("#calc-btn").addEventListener("click",    calcScore);
}

async function prefill(code) {
    if (!code) return;
    try {
        const d = await api(`/api/zones/${encodeURIComponent(code)}`);
        Object.entries(d.features).forEach(([k, v]) => {
            const el = document.getElementById(`f-${k}`);
            if (el) el.value = Number(v).toFixed(2);
        });
    } catch (e) { alert("Erreur : " + e.message); }
}

function resetForm() {
    $$("#custom-form input").forEach(i => i.value = "");
    $("#custom-result").classList.add("hidden");
}

/* ── Formulaire ───────────────────────────────────────────── */
function buildForm() {
    const form = $("#custom-form");
    form.innerHTML = state.dimensions.map(dim => `
        <div class="dim-card">
            <h3>
                ${esc(dim.dimension)}
                <span class="dim-weight">${dim.poids_dimension}%</span>
            </h3>
            ${dim.variables.map(v => `
                <div class="var-field">
                    <label for="f-${v.name}">
                        ${esc(LABELS[v.name] || v.name)}
                        <span class="sign ${v.direction}" title="${v.direction}">
                            ${v.direction === "negatif" ? "↓ négatif" : "↑ positif"}
                        </span>
                    </label>
                    <input type="number" step="any"
                           id="f-${v.name}" name="${v.name}"
                           placeholder="valeur" />
                </div>
            `).join("")}
        </div>
    `).join("");
}

async function calcScore() {
    const values = {};
    const missing = [];
    $$("#custom-form input").forEach(i => {
        if (i.value === "" || i.value === null) missing.push(i.name);
        else values[i.name] = Number(i.value);
    });
    if (missing.length) {
        alert("Variables manquantes :\n" + missing.map(m => "  • " + (LABELS[m] || m)).join("\n"));
        return;
    }
    try {
        const r = await api("/api/score", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ values }),
        });
        renderResult($("#custom-result"), { title: "Point hypothétique", scores: r });
    } catch (e) { alert("Erreur : " + e.message); }
}

/* ── Carte ────────────────────────────────────────────────── */
async function initMap() {
    const data = await api("/api/zones/map");
    if (!data.available || !data.zones.length) {
        const msg = data.message || "Aucune zone avec coordonnées.";
        $("#map-empty-msg").textContent = msg;
        $("#map-empty").classList.remove("hidden");
        $("#map").style.display = "none";
        return;
    }

    state.map = L.map("map", { zoomControl: true }).setView([33.57, -7.59], 12);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(state.map);

    const group = L.featureGroup();
    data.zones.forEach(z => {
        const lat = Number(z.lat), lng = Number(z.lng);
        if (!isFinite(lat) || !isFinite(lng)) return;
        const score = Number(z.Score_Attractivite) || 0;
        const color = scoreColor(score);
        const marker = L.circleMarker([lat, lng], {
            radius:      8 + score / 100 * 7,
            fillColor:   color,
            color:       "#15294a",
            weight:      1,
            opacity:     1,
            fillOpacity: 0.85,
        });
        marker.bindPopup(`
            <div class="popup-title">${esc(z["Code Zone"])} — ${esc(z["Zone"] || "")}</div>
            <div class="popup-meta">${esc(z["Arrondissement"] || "")}</div>
            <div class="popup-score">Score global : <strong style="color:${color}">${fmt(score)}</strong> / 100</div>
            <div class="popup-meta" style="margin-top:8px">
                ${state.dimensions.map(dim =>
                    `<div>${esc(dim.dimension)} : <b>${fmt(z["Score_"+dim.dimension])}</b></div>`
                ).join("")}
            </div>
            <div class="popup-meta" style="margin-top:6px">Rang : ${z.Rang || "—"} / ${data.count}</div>
        `);
        marker.addTo(state.map);
        group.addLayer(marker);
        state.markers[z["Code Zone"]] = { marker, lat, lng };
    });
    if (group.getLayers().length) state.map.fitBounds(group.getBounds().pad(0.15));

    buildZoneList(data.zones);
}

function buildZoneList(zones) {
    const ul = $("#zone-list");
    const sorted = [...zones].sort((a, b) => Number(b.Score_Attractivite) - Number(a.Score_Attractivite));

    function render(list) {
        ul.innerHTML = list.map(z => {
            const score = Number(z.Score_Attractivite) || 0;
            const color = scoreColor(score);
            return `
                <li class="zone-item" data-code="${esc(z["Code Zone"])}">
                    <span class="zone-rank">${z.Rang || "—"}</span>
                    <div class="zone-info">
                        <div class="zone-name">${esc(z["Zone"] || z["Code Zone"])}</div>
                        <div class="zone-arr">${esc(z["Arrondissement"] || "")}</div>
                    </div>
                    <span class="zone-score" style="color:${color}">${fmt(score)}</span>
                </li>`;
        }).join("");

        ul.querySelectorAll(".zone-item").forEach(li => {
            li.addEventListener("click", () => {
                const code = li.dataset.code;
                const m = state.markers[code];
                if (!m) return;
                state.map.setView([m.lat, m.lng], 15);
                m.marker.openPopup();
                ul.querySelectorAll(".zone-item").forEach(x => x.classList.remove("active"));
                li.classList.add("active");
            });
        });
    }

    render(sorted);

    $("#zone-search").addEventListener("input", e => {
        const q = e.target.value.toLowerCase();
        const filtered = sorted.filter(z =>
            (z["Zone"] || "").toLowerCase().includes(q) ||
            (z["Code Zone"] || "").toLowerCase().includes(q) ||
            (z["Arrondissement"] || "").toLowerCase().includes(q)
        );
        render(filtered);
    });
}

/* ── Rendu résultat ───────────────────────────────────────── */
function renderResult(host, data) {
    const s = data.scores;
    const score = s.Score_Attractivite;
    const color = scoreColor(score);
    const rankHtml = data.rank
        ? `<div class="result-global-rank">Rang : <strong>${data.rank}</strong> / ${data.total}</div>`
        : "";

    host.innerHTML = `
        <h3>${esc(data.title || "")}</h3>
        <div class="result-global" style="border-left:4px solid ${color}">
            <div>
                <div class="result-global-label">Score d'attractivité</div>
                <div class="result-global-value" style="color:${color}">${fmt(score)}<span style="font-size:14px;color:var(--text-muted);font-weight:500"> / 100</span></div>
            </div>
            ${rankHtml}
        </div>
        <div class="subscores">
            ${Object.entries(s)
                .filter(([k]) => k !== "Score_Attractivite")
                .map(([k, v]) => subscoreBlock(k.replace(/^Score_/, ""), v))
                .join("")}
        </div>
    `;
    host.classList.remove("hidden");
}

function subscoreBlock(label, value) {
    const v = Math.max(0, Math.min(100, Number(value) || 0));
    const color = scoreColor(v);
    return `
        <div class="subscore">
            <div class="subscore-label">${esc(label)}</div>
            <div class="subscore-value" style="color:${color}">${fmt(value)}</div>
            <div class="subscore-bar">
                <div class="subscore-bar-fill" style="width:${v}%;background:${color}"></div>
            </div>
        </div>
    `;
}

init();
