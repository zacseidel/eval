import { renderStrategies } from "./strategies.js";
import { renderPositions } from "./positions.js";
import { renderCharts, renderScatterCharts } from "./charts.js";

const DATA_BASE = "./data/processed";

async function loadJSON(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`Failed to load ${path}: ${resp.status}`);
  return resp.json();
}

async function init() {
  let positions, strategyReturns, signals, manifest;
  try {
    [positions, strategyReturns, signals, manifest] = await Promise.all([
      loadJSON(`${DATA_BASE}/positions.json`),
      loadJSON(`${DATA_BASE}/strategy_returns.json`),
      loadJSON(`${DATA_BASE}/signals.json`),
      loadJSON(`${DATA_BASE}/manifest.json`),
    ]);
  } catch (e) {
    document.querySelector("main").innerHTML =
      `<div style="padding:40px;color:var(--muted)">
        No data yet — run <code>python scraper/main.py</code> to generate portfolio data.
       </div>`;
    return;
  }

  const latestReport = manifest.reports[manifest.reports.length - 1]?.date ?? "";
  renderStrategies(
    positions,
    strategyReturns,
    signals,
    latestReport,
    manifest.strategy_trade_stats || {},
  );
  renderPositions(positions);
  renderCharts(strategyReturns);
  renderScatterCharts(positions);

  // Show most recent data date in the header
  const maxDate = manifest.market_data_through ?? manifest.as_of;
  if (maxDate) {
    const formatted = new Date(maxDate + "T00:00:00").toLocaleDateString("en-US", {
      year: "numeric", month: "long", day: "numeric",
    });
    document.getElementById("last-updated").textContent = `· Last updated ${formatted}`;
  }

  // Tab switching
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.add("hidden"));
      btn.classList.add("active");
      document.getElementById(`tab-${btn.dataset.tab}`).classList.remove("hidden");
    });
  });
}

init();
