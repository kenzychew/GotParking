const statusEl = document.getElementById("status");
const generatedAtEl = document.getElementById("generated-at");
const listEl = document.getElementById("carparks");

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

function renderCarpark(carpark) {
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
  return li;
}

async function loadForecast() {
  let response;
  try {
    response = await fetch("/api/forecast");
  } catch (err) {
    statusEl.textContent = "Could not reach the forecast API.";
    return;
  }

  const data = await response.json();

  if (!response.ok) {
    statusEl.textContent = data.message || "Predictions temporarily unavailable.";
    return;
  }

  statusEl.remove();
  generatedAtEl.textContent = `Generated: ${new Date(data.generated_at).toLocaleString()}`;
  listEl.innerHTML = "";
  for (const carpark of data.carparks) {
    listEl.appendChild(renderCarpark(carpark));
  }
}

loadForecast();
