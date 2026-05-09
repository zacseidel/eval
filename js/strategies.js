const STRATEGY_META = {
  sp500_top5:   { label: "S&P 500 Top 5",        color: "#6c8ef7" },
  sp500_next5:  { label: "S&P 500 Next 5",        color: "#a78bfa" },
  megacap_top5: { label: "Megacap Top 5",          color: "#34d399" },
  megacap_next5:{ label: "Megacap Next 5",         color: "#10b981" },
  sp400_mcap5:      { label: "S&P 400 Mkt Cap Top 5",  color: "#fb923c" },
  sp400_mcap_next5: { label: "S&P 400 Mkt Cap Next 5", color: "#fbbf24" },
  munger:           { label: "Munger Mean Reversion",   color: "#f472b6" },
};

function formatPct(val) {
  if (val == null) return "—";
  const cls = val >= 0 ? "positive" : "negative";
  return `<span class="value ${cls}">${val >= 0 ? "+" : ""}${val.toFixed(1)}%</span>`;
}

function formatBenchmark(val) {
  if (val == null) return `<span class="bmark-value">SPY —</span>`;
  const cls = val >= 0 ? "positive" : "negative";
  return `<span class="bmark-value ${cls}">SPY ${val >= 0 ? "+" : ""}${val.toFixed(1)}%</span>`;
}

function get12mReturn(series, field = "value") {
  if (!series || series.length < 2) return null;
  const last = series[series.length - 1];
  if (last[field] == null) return null;
  const lastDate = new Date(last.date);
  const cutoff = new Date(lastDate);
  cutoff.setFullYear(cutoff.getFullYear() - 1);
  const past = series.find(p => new Date(p.date) >= cutoff && p[field] != null);
  if (!past || past[field] === 0) return null;
  return (last[field] - past[field]) / past[field] * 100;
}

function getLatest3mReturn(series, field = "rolling_3m") {
  if (!series || series.length === 0) return null;
  const last = series[series.length - 1];
  return last[field] ?? null;
}

export function renderStrategies(positions, strategyReturns) {
  const grid = document.getElementById("strategy-grid");
  grid.innerHTML = "";

  for (const [sid, meta] of Object.entries(STRATEGY_META)) {
    const series = strategyReturns[sid] || [];
    const ret12m = get12mReturn(series);
    const ret3m = getLatest3mReturn(series);
    const spy12m = get12mReturn(series, "spy_value");
    const spy3m = getLatest3mReturn(series, "spy_rolling_3m");
    const stratPositions = positions.filter(p => p.strategy === sid);
    const openPositions = stratPositions.filter(p => p.status === "open");
    const openCount = openPositions.length;
    const closedCount = stratPositions.filter(p => p.status === "closed").length;
    const openTickers = openPositions.map(p => p.ticker);

    const card = document.createElement("div");
    card.className = "strategy-card";
    card.dataset.strategy = sid;
    card.innerHTML = `
      <div class="card-header">
        <div class="dot" style="background:${meta.color}"></div>
        <h3>${meta.label}</h3>
      </div>
      <div class="card-metrics">
        <div class="metric">
          <span class="label">12M Return</span>
          ${formatPct(ret12m)}
          ${formatBenchmark(spy12m)}
        </div>
        <div class="metric">
          <span class="label">Rolling 3M</span>
          ${formatPct(ret3m)}
          ${formatBenchmark(spy3m)}
        </div>
      </div>
      <div class="card-footer">
        <span>${openCount} open</span>
        <span>${closedCount} closed</span>
      </div>
      ${openTickers.length ? `<div class="ticker-tags">${openTickers.map(t => `<span class="ticker-tag">${t}</span>`).join("")}</div>` : ""}
    `;

    // Click card → switch to Positions tab filtered to this strategy
    card.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.add("hidden"));
      document.querySelector('[data-tab="positions"]').classList.add("active");
      document.getElementById("tab-positions").classList.remove("hidden");
      document.getElementById("filter-strategy").value = sid;
      document.getElementById("filter-strategy").dispatchEvent(new Event("change"));
    });

    grid.appendChild(card);
  }
}
