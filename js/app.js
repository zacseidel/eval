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

  // Show most recent data date in the header
  const allDates = Object.values(strategyReturns).filter(Array.isArray).flat().map(d => d.date);
  const maxDate = allDates.reduce((a, b) => (a > b ? a : b), "");
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
