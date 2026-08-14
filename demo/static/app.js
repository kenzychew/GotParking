// Destination-search-led demo frontend. Search leads; a selected carpark's
// forecast, nearby alternatives, and typical-availability trend chart come
// next. The Leaflet map is available behind a toggle, not the primary view.

const statusEl = document.getElementById("status");
const generatedAtEl = document.getElementById("generated-at");
const attributionEl = document.getElementById("attribution");

const searchInputEl = document.getElementById("search-input");
const searchResultsEl = document.getElementById("search-results");
const emptyStateEl = document.getElementById("empty-state");
const resultSectionEl = document.getElementById("result-section");

const primaryNameEl = document.getElementById("primary-name");
const primaryTierEl = document.getElementById("primary-tier");
const primaryLotsEl = document.getElementById("primary-lots");
const primaryStateEl = document.getElementById("primary-state");
const chartContainerEl = document.getElementById("chart-container");
const alternativesListEl = document.getElementById("alternatives-list");

const mapToggleEl = document.getElementById("map-toggle");
const mapPanelEl = document.getElementById("map-panel");
const mapEl = document.getElementById("map");
const mapStatusEl = document.getElementById("map-status");

const SINGAPORE_CENTER = [1.3521, 103.8198];
const DEFAULT_ZOOM = 12;
const EARTH_RADIUS_METERS = 6_371_000;
const MAX_SEARCH_RESULTS = 8;
const MAX_ALTERNATIVES = 5;

const TIER_LABEL = {
  plenty: "Plenty",
  limited: "Limited",
  very_limited: "Very limited",
};

const STATE_LABEL = {
  ml: "ML forecast",
  baseline: "Baseline average",
  cold_start: "Warming up",
};

let carparks = []; // [{id, name, lat, lng, forecast_lots, tier, live_lots, state}]
let carparksById = {};
let selectedId = null;

let map = null;
let markerLayer = null;
let mapInitialized = false;
let markersByCarparkId = {};
let latestForecastResult = null;
let latestGeoResult = null;

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function toRadians(degrees) {
  return (degrees * Math.PI) / 180;
}

/** Great-circle distance in meters (haversine), ported from frontend/src/lib/haversine.ts. */
function haversineDistanceMeters(lat1, lon1, lat2, lon2) {
  const dLat = toRadians(lat2 - lat1);
  const dLon = toRadians(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRadians(lat1)) * Math.cos(toRadians(lat2)) * Math.sin(dLon / 2) ** 2;
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return EARTH_RADIUS_METERS * c;
}

function formatDistance(meters) {
  if (meters < 1000) {
    return `${Math.round(meters)} m`;
  }
  return `${(meters / 1000).toFixed(1)} km`;
}

function tierClass(tier) {
  return tier ? `tier-${tier}` : "tier-none";
}

function tierLabel(tier) {
  return tier ? TIER_LABEL[tier] ?? tier : "No forecast yet";
}

async function fetchJson(url) {
  try {
    const response = await fetch(url);
    const data = await response.json();
    return { ok: response.ok, data };
  } catch (err) {
    return { ok: false, data: null };
  }
}

// --- Search ------------------------------------------------------------

/**
 * Score `name` against `query` for a lightweight client-side fuzzy match.
 * Lower is better. A substring match scores by its position (earlier is
 * better); failing that, an in-order subsequence match (all query
 * characters appear in `name`, in order, not necessarily contiguous) scores
 * worse but still counts as a match. Returns null for no match at all.
 */
function fuzzyScore(query, name) {
  const q = query.toLowerCase().trim();
  if (!q) {
    return null;
  }
  const n = name.toLowerCase();
  const idx = n.indexOf(q);
  if (idx !== -1) {
    return idx;
  }
  let qi = 0;
  for (let i = 0; i < n.length && qi < q.length; i++) {
    if (n[i] === q[qi]) {
      qi += 1;
    }
  }
  if (qi === q.length) {
    return 1000 + n.length;
  }
  return null;
}

function searchCarparks(query) {
  const scored = [];
  for (const carpark of carparks) {
    const score = fuzzyScore(query, carpark.name);
    if (score !== null) {
      scored.push({ carpark, score });
    }
  }
  scored.sort((a, b) => a.score - b.score || a.carpark.name.localeCompare(b.carpark.name));
  return scored.slice(0, MAX_SEARCH_RESULTS).map((s) => s.carpark);
}

function renderSearchResults(matches) {
  if (matches.length === 0) {
    searchResultsEl.hidden = true;
    searchResultsEl.innerHTML = "";
    return;
  }
  searchResultsEl.innerHTML = matches
    .map(
      (carpark, i) => `
        <li class="search-result-item" data-carpark-id="${escapeHtml(carpark.id)}" data-index="${i}">
          <span class="search-result-name">${escapeHtml(carpark.name)}</span>
          <span class="tier ${tierClass(carpark.tier)}">${escapeHtml(tierLabel(carpark.tier))}</span>
        </li>
      `
    )
    .join("");
  searchResultsEl.hidden = false;

  for (const li of searchResultsEl.querySelectorAll(".search-result-item")) {
    li.addEventListener("click", () => {
      selectCarpark(li.dataset.carparkId);
      searchInputEl.value = carparksById[li.dataset.carparkId]?.name ?? "";
      renderSearchResults([]);
    });
  }
}

searchInputEl.addEventListener("input", () => {
  const matches = searchCarparks(searchInputEl.value);
  renderSearchResults(matches);
});

searchInputEl.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    const first = searchResultsEl.querySelector(".search-result-item");
    if (first) {
      selectCarpark(first.dataset.carparkId);
      searchInputEl.value = carparksById[first.dataset.carparkId]?.name ?? "";
      renderSearchResults([]);
    }
  } else if (event.key === "Escape") {
    renderSearchResults([]);
  }
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".search-box")) {
    renderSearchResults([]);
  }
});

// --- Primary result + alternatives --------------------------------------

function renderPrimaryResult(carpark) {
  primaryNameEl.textContent = carpark.name;
  primaryTierEl.textContent = tierLabel(carpark.tier);
  primaryTierEl.className = `tier ${tierClass(carpark.tier)}`;
  const forecastLots = carpark.forecast_lots ?? "?";
  const liveLots = carpark.live_lots ?? "?";
  primaryLotsEl.textContent = `forecast ${forecastLots} lots (live ${liveLots})`;
  primaryStateEl.textContent = carpark.state ? STATE_LABEL[carpark.state] ?? carpark.state : "";
}

function renderAlternatives(carpark) {
  if (typeof carpark.lat !== "number" || typeof carpark.lng !== "number") {
    alternativesListEl.innerHTML =
      '<li class="alternatives-empty">Nearby alternatives need this carpark\'s location, which isn\'t available yet.</li>';
    return;
  }

  const nearby = carparks
    .filter((c) => c.id !== carpark.id && typeof c.lat === "number" && typeof c.lng === "number")
    .map((c) => ({
      carpark: c,
      distanceMeters: haversineDistanceMeters(carpark.lat, carpark.lng, c.lat, c.lng),
    }))
    .sort((a, b) => a.distanceMeters - b.distanceMeters)
    .slice(0, MAX_ALTERNATIVES);

  if (nearby.length === 0) {
    alternativesListEl.innerHTML = '<li class="alternatives-empty">No other carparks with a known location yet.</li>';
    return;
  }

  alternativesListEl.innerHTML = nearby
    .map(
      ({ carpark: c, distanceMeters }) => `
        <li class="alternative-item" data-carpark-id="${escapeHtml(c.id)}">
          <div class="alternative-head">
            <span class="name">${escapeHtml(c.name)}</span>
            <span class="tier ${tierClass(c.tier)}">${escapeHtml(tierLabel(c.tier))}</span>
          </div>
          <div class="alternative-meta">
            ${escapeHtml(formatDistance(distanceMeters))} away · forecast ${c.forecast_lots ?? "?"} lots
          </div>
        </li>
      `
    )
    .join("");

  for (const li of alternativesListEl.querySelectorAll(".alternative-item")) {
    li.addEventListener("click", () => {
      selectCarpark(li.dataset.carparkId);
      searchInputEl.value = carparksById[li.dataset.carparkId]?.name ?? "";
    });
  }
}

// --- Trend chart ---------------------------------------------------------

function slotLabel(slot) {
  const totalMinutes = slot * 15;
  const hh = Math.floor(totalMinutes / 60) % 24;
  const mm = totalMinutes % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

function buildChartSvg(slots, currentSlot, liveValue) {
  const width = 640;
  const height = 200;
  const padding = { top: 16, right: 16, bottom: 26, left: 16 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  const sorted = [...slots].sort((a, b) => a.slot_of_day - b.slot_of_day);
  const values = sorted.map((s) => s.avg_available_lots);
  let minV = Math.min(...values);
  let maxV = Math.max(...values);
  if (typeof liveValue === "number") {
    minV = Math.min(minV, liveValue);
    maxV = Math.max(maxV, liveValue);
  }
  if (minV === maxV) {
    minV -= 1;
    maxV += 1;
  }
  const rangePad = (maxV - minV) * 0.1;
  minV = Math.max(0, minV - rangePad);
  maxV += rangePad;

  const xForSlot = (slot) => padding.left + (slot / 95) * plotW;
  const yForValue = (v) => padding.top + plotH - ((v - minV) / (maxV - minV)) * plotH;

  let pathD = "";
  let prevSlot = null;
  for (const point of sorted) {
    const x = xForSlot(point.slot_of_day).toFixed(1);
    const y = yForValue(point.avg_available_lots).toFixed(1);
    if (prevSlot === null || point.slot_of_day - prevSlot > 1) {
      pathD += `M ${x} ${y} `;
    } else {
      pathD += `L ${x} ${y} `;
    }
    prevSlot = point.slot_of_day;
  }

  const gridLines = [0, 0.5, 1]
    .map((t) => {
      const y = (padding.top + plotH * t).toFixed(1);
      return `<line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" class="chart-grid" />`;
    })
    .join("");

  const ticks = [0, 24, 48, 72]
    .map((slot) => {
      const x = xForSlot(slot).toFixed(1);
      return `<text x="${x}" y="${height - 6}" class="chart-tick" text-anchor="middle">${slotLabel(slot)}</text>`;
    })
    .join("");

  let nowMarker = "";
  const exact = sorted.find((s) => s.slot_of_day === currentSlot);
  const nearest =
    exact ??
    sorted.reduce((best, s) => {
      if (!best) return s;
      return Math.abs(s.slot_of_day - currentSlot) < Math.abs(best.slot_of_day - currentSlot) ? s : best;
    }, null);
  if (nearest) {
    const x = xForSlot(currentSlot).toFixed(1);
    const y = yForValue(nearest.avg_available_lots).toFixed(1);
    nowMarker = `<circle cx="${x}" cy="${y}" r="4" class="chart-now-typical" />`;
  }

  let liveMarker = "";
  if (typeof liveValue === "number") {
    const x = xForSlot(currentSlot).toFixed(1);
    const y = yForValue(liveValue).toFixed(1);
    liveMarker = `<circle cx="${x}" cy="${y}" r="5" class="chart-now-live" />`;
  }

  return `
    <svg viewBox="0 0 ${width} ${height}" class="chart-svg" role="img" aria-label="Typical carpark availability by time of day, today's live forecast marked as now">
      ${gridLines}
      <path d="${pathD.trim()}" class="chart-line" fill="none" />
      ${nowMarker}
      ${liveMarker}
      ${ticks}
    </svg>
  `;
}

function renderChartLegend() {
  return `
    <div class="chart-legend">
      <span class="chart-legend-item"><span class="chart-legend-swatch chart-legend-typical"></span>Typical for this time</span>
      <span class="chart-legend-item"><span class="chart-legend-swatch chart-legend-live"></span>Now (live forecast)</span>
    </div>
  `;
}

async function loadAndRenderChart(carpark) {
  chartContainerEl.innerHTML = '<p class="chart-message">Loading trend...</p>';
  const result = await fetchJson(`/api/carpark-baseline/${encodeURIComponent(carpark.id)}`);

  // The carpark might have changed while this request was in flight.
  if (selectedId !== carpark.id) {
    return;
  }

  if (!result.ok) {
    chartContainerEl.innerHTML =
      '<p class="chart-message">Typical-availability data isn\'t available yet for this carpark.</p>';
    return;
  }

  const data = result.data;
  if (!data.slots || data.slots.length === 0) {
    chartContainerEl.innerHTML =
      '<p class="chart-message">Not enough history yet to show a typical-availability curve.</p>';
    return;
  }

  const liveValue = carpark.forecast_lots ?? carpark.live_lots ?? null;
  chartContainerEl.innerHTML =
    buildChartSvg(data.slots, data.current_slot_of_day, liveValue) + renderChartLegend();
}

// --- Selection -------------------------------------------------------------

function selectCarpark(carparkId) {
  const carpark = carparksById[carparkId];
  if (!carpark) {
    return;
  }
  selectedId = carparkId;
  emptyStateEl.hidden = true;
  resultSectionEl.hidden = false;

  renderPrimaryResult(carpark);
  renderAlternatives(carpark);
  loadAndRenderChart(carpark);

  if (mapInitialized && typeof carpark.lat === "number" && typeof carpark.lng === "number") {
    map.panTo([carpark.lat, carpark.lng]);
    markersByCarparkId[carpark.id]?.openPopup();
  }
}

// --- Map (behind a toggle, not the default view) --------------------------

function popupHtml(carpark) {
  const forecastLots = carpark.forecast_lots ?? "?";
  return `
    <div class="popup">
      <strong>${escapeHtml(carpark.name)}</strong>
      <div class="popup-tier">${escapeHtml(tierLabel(carpark.tier))}</div>
      <div class="popup-lots">forecast ${escapeHtml(String(forecastLots))} · live ${escapeHtml(String(carpark.live_lots ?? "?"))}</div>
    </div>
  `;
}

function initMapIfNeeded() {
  if (mapInitialized) {
    return true;
  }
  if (typeof L === "undefined") {
    mapStatusEl.textContent = "Map failed to load.";
    return false;
  }
  try {
    map = L.map(mapEl, { scrollWheelZoom: false }).setView(SINGAPORE_CENTER, DEFAULT_ZOOM);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
    }).addTo(map);
    markerLayer = L.layerGroup().addTo(map);
    mapInitialized = true;

    let plotted = 0;
    for (const carpark of carparks) {
      if (typeof carpark.lat === "number" && typeof carpark.lng === "number") {
        const fillColor = carpark.tier ? TIER_COLOR[carpark.tier] ?? "#5c564c" : "#5c564c";
        const marker = L.circleMarker([carpark.lat, carpark.lng], {
          radius: 8,
          color: "#1c1917",
          weight: 1,
          fillColor,
          fillOpacity: 0.9,
        });
        marker.bindPopup(popupHtml(carpark));
        marker.on("click", () => selectCarpark(carpark.id));
        marker.addTo(markerLayer);
        markersByCarparkId[carpark.id] = marker;
        plotted += 1;
      }
    }
    mapStatusEl.textContent = plotted > 0 ? "" : "No carpark locations to plot yet.";
    return true;
  } catch (err) {
    mapStatusEl.textContent = "Map failed to load.";
    return false;
  }
}

const TIER_COLOR = {
  plenty: "#1a7f37",
  limited: "#9a6700",
  very_limited: "#cf222e",
};

mapToggleEl.addEventListener("click", () => {
  const showing = !mapPanelEl.hidden;
  if (showing) {
    mapPanelEl.hidden = true;
    mapToggleEl.textContent = "Show map";
    mapToggleEl.setAttribute("aria-expanded", "false");
    return;
  }
  mapPanelEl.hidden = false;
  mapToggleEl.textContent = "Hide map";
  mapToggleEl.setAttribute("aria-expanded", "true");
  initMapIfNeeded();
  if (map) {
    // Leaflet sizes itself from the container's dimensions at init time;
    // the panel was `hidden` (0x0) then, so it needs one resize pass now.
    setTimeout(() => map.invalidateSize(), 0);
  }
});

// --- Load ------------------------------------------------------------------

function renderAttribution(hasMlModel) {
  const year = new Date().getFullYear();
  let text =
    `Contains information from LTA DataMall's Carpark Availability dataset, accessed ${year}, ` +
    "made available under the Singapore Open Data Licence v1.0.";
  if (hasMlModel) {
    text +=
      " Also contains information from the SINPA historical carpark dataset, made available " +
      "under the Singapore Open Data Licence v1.0, used to pretrain the forecasting model.";
  }
  attributionEl.textContent = text;
}

function buildCarparkIndex(forecastResult, geoResult) {
  const geoById = {};
  if (geoResult.ok && geoResult.data && Array.isArray(geoResult.data.carparks)) {
    for (const row of geoResult.data.carparks) {
      geoById[row.carpark_id] = row;
    }
  }

  const list = [];
  if (forecastResult.ok && forecastResult.data && Array.isArray(forecastResult.data.carparks)) {
    for (const row of forecastResult.data.carparks) {
      const geo = geoById[row.carpark_id];
      list.push({
        id: row.carpark_id,
        name: row.name,
        lat: typeof geo?.latitude === "number" ? geo.latitude : null,
        lng: typeof geo?.longitude === "number" ? geo.longitude : null,
        forecast_lots: row.forecast_lots,
        tier: row.tier,
        live_lots: row.live_lots,
        state: row.state,
      });
    }
  }
  return list;
}

async function load() {
  const [forecastResult, geoResult] = await Promise.all([
    fetchJson("/api/forecast"),
    fetchJson("/api/carparks-geo"),
  ]);
  latestForecastResult = forecastResult;
  latestGeoResult = geoResult;

  if (!forecastResult.ok) {
    statusEl.textContent = forecastResult.data?.message || "Predictions temporarily unavailable.";
    emptyStateEl.hidden = true;
    renderAttribution(false);
    return;
  }

  carparks = buildCarparkIndex(forecastResult, geoResult);
  carparksById = Object.fromEntries(carparks.map((c) => [c.id, c]));

  statusEl.remove();
  generatedAtEl.textContent = `Generated: ${new Date(forecastResult.data.generated_at).toLocaleString()}`;

  const hasMlModel = forecastResult.data.carparks.some((c) => c.state === "ml");
  renderAttribution(hasMlModel);

  if (!geoResult.ok) {
    mapStatusEl.textContent = "Carpark locations are temporarily unavailable; nearby alternatives may be limited.";
  }

  searchInputEl.disabled = false;
  searchInputEl.focus();
}

load();
