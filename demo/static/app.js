const statusEl = document.getElementById("status");
const generatedAtEl = document.getElementById("generated-at");
const listEl = document.getElementById("carparks");
const mapEl = document.getElementById("map");
const mapStatusEl = document.getElementById("map-status");
const attributionEl = document.getElementById("attribution");

const SINGAPORE_CENTER = [1.3521, 103.8198];
const DEFAULT_ZOOM = 12;

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

// Same hex values as frontend/src/lib/colorTokens.ts's light-theme tier colors.
const TIER_COLOR = {
  plenty: "#1a7f37",
  limited: "#9a6700",
  very_limited: "#cf222e",
};

let map = null;
let markerLayer = null;

function initMap() {
  if (typeof L === "undefined") {
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
    return true;
  } catch (err) {
    map = null;
    markerLayer = null;
    return false;
  }
}

function popupHtml(carpark) {
  const tierLabel = carpark.tier ? TIER_LABEL[carpark.tier] ?? carpark.tier : "No forecast yet";
  const forecastLots = carpark.forecast_lots ?? "—";
  return `
    <div class="popup">
      <strong>${carpark.name}</strong>
      <div class="popup-tier">${tierLabel}</div>
      <div class="popup-lots">forecast ${forecastLots} · live ${carpark.live_lots}</div>
    </div>
  `;
}

function addMarker(carpark, lat, lng) {
  if (!markerLayer) {
    return null;
  }
  const fillColor = carpark.tier ? TIER_COLOR[carpark.tier] ?? "#5c564c" : "#5c564c";
  const marker = L.circleMarker([lat, lng], {
    radius: 8,
    color: "#1c1917",
    weight: 1,
    fillColor,
    fillOpacity: 0.9,
  });
  marker.bindPopup(popupHtml(carpark));
  marker.addTo(markerLayer);
  return marker;
}

function renderListItem(carpark, marker) {
  const li = document.createElement("li");
  li.className = "carpark";

  const tierClass = carpark.tier ? `tier-${carpark.tier}` : "tier-none";
  const tierLabel = carpark.tier ? TIER_LABEL[carpark.tier] ?? carpark.tier : "—";
  const stateLabel = STATE_LABEL[carpark.state] ?? carpark.state;
  const forecastLots = carpark.forecast_lots ?? "—";

  li.innerHTML = `
    <span class="name">${carpark.name}</span>
    <span class="tier ${tierClass}">${tierLabel}</span>
    <span class="lots">forecast ${forecastLots} · live ${carpark.live_lots}</span>
    <span class="state">${stateLabel}</span>
  `;

  if (marker) {
    li.classList.add("has-marker");
    li.addEventListener("click", () => {
      map.panTo(marker.getLatLng());
      marker.openPopup();
    });
  }

  return li;
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

async function load() {
  const mapReady = initMap();
  if (!mapReady) {
    mapStatusEl.textContent = "Map failed to load; showing the list only.";
  }

  const [forecastResult, geoResult] = await Promise.all([
    fetchJson("/api/forecast"),
    fetchJson("/api/carparks-geo"),
  ]);

  renderAttribution(false);

  if (!forecastResult.ok) {
    statusEl.textContent = forecastResult.data?.message || "Predictions temporarily unavailable.";
    if (mapReady) {
      mapStatusEl.textContent = "No forecast data to plot yet.";
    }
    return;
  }

  const data = forecastResult.data;
  statusEl.remove();
  generatedAtEl.textContent = `Generated: ${new Date(data.generated_at).toLocaleString()}`;

  const hasMlModel = data.carparks.some((carpark) => carpark.state === "ml");
  renderAttribution(hasMlModel);

  const geoById = {};
  if (geoResult.ok && geoResult.data && Array.isArray(geoResult.data.carparks)) {
    for (const row of geoResult.data.carparks) {
      geoById[row.carpark_id] = row;
    }
  } else if (mapReady) {
    mapStatusEl.textContent = "Map locations are temporarily unavailable; showing the list only.";
  }

  listEl.innerHTML = "";
  let plotted = 0;
  for (const carpark of data.carparks) {
    const geo = geoById[carpark.carpark_id];
    let marker = null;
    if (mapReady && geo && typeof geo.latitude === "number" && typeof geo.longitude === "number") {
      marker = addMarker(carpark, geo.latitude, geo.longitude);
      plotted += 1;
    }
    listEl.appendChild(renderListItem(carpark, marker));
  }

  if (mapReady && plotted > 0) {
    mapStatusEl.remove();
  } else if (mapReady && plotted === 0 && geoResult.ok) {
    mapStatusEl.textContent = "No carpark locations to plot yet.";
  }
}

load();
