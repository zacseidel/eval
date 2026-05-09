import { renderStrategies } from "./strategies.js";
import { renderPositions } from "./positions.js";
import { renderCharts } from "./charts.js";

const DATA_BASE = "./data/processed";

async function loadJSON(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`Failed to load ${path}: ${resp.status}`);
  return resp.json();
}

async function init() {
  let positions, strategyReturns;
  try {
    [positions, strategyReturns] = await Promise.all([
      loadJSON(`${DATA_BASE}/positions.json`),
      loadJSON(`${DATA_BASE}/strategy_returns.json`),
    ]);
  } catch (e) {
    document.querySelector("main").innerHTML =
      `<div style="padding:40px;color:var(--muted)">
        No data yet — run <code>python scraper/main.py</code> to generate portfolio data.
       </div>`;
    return;
  }

  renderStrategies(positions, strategyReturns);
  renderPositions(positions);
  renderCharts(strategyReturns);

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
