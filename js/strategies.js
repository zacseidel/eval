const STRATEGY_META = {
  sp500_top5:   { label: "S&P 500 Top 5",        color: "#6c8ef7" },
  sp500_next5:  { label: "S&P 500 Next 5",        color: "#a78bfa" },
  megacap_top5: { label: "Megacap Top 5",          color: "#34d399" },
  megacap_next5:{ label: "Megacap Next 5",         color: "#10b981" },
  sp400_mcap5:      { label: "S&P 400 Mkt Cap Top 5",  color: "#fb923c" },
  sp400_mcap_next5: { label: "S&P 400 Mkt Cap Next 5", color: "#fbbf24" },
  munger:           { label: "Munger Mean Reversion",   color: "#f472b6" },
};

const SIZE_ORDER = {
  megacap_top5: 0, megacap_next5: 1, munger: 2,
  sp500_top5: 3, sp500_next5: 4,
  sp400_mcap5: 5, sp400_mcap_next5: 6,
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

function formatSharpe(val) {
  if (val == null) return `<span class="value neutral">—</span>`;
  const cls = val >= 1.0 ? "positive" : val >= 0 ? "neutral" : "negative";
  return `<span class="value ${cls}">${val.toFixed(2)}</span>`;
}

function formatSharpeBenchmark(val) {
  if (val == null) return `<span class="bmark-value">SPY —</span>`;
  return `<span class="bmark-value">SPY ${val.toFixed(2)}</span>`;
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

let _cardData = [];

function buildCard(item) {
  const { sid, meta, ret12m, ret3m, spy12m, spy3m,
          stratSharpe12m, stratSharpe3m, spySharpe12m, spySharpe3m,
          openCount, closedCount, openTickers } = item;

  const card = document.createElement("div");
  card.className = "strategy-card";
  card.dataset.strategy = sid;
  card.innerHTML = `
    <div class="card-header">
      <div class="dot" style="background:${meta.color}"></div>
      <h3>${meta.label}</h3>
    </div>
    <div class="card-metrics">
      <div class="metric-group">
        <div class="metric">
          <span class="label">12M Return</span>
          ${formatPct(ret12m)}
          ${formatBenchmark(spy12m)}
        </div>
        <div class="metric">
          <span class="label">Sharpe 12M</span>
          ${formatSharpe(stratSharpe12m)}
          ${formatSharpeBenchmark(spySharpe12m)}
        </div>
      </div>
      <div class="metric-group">
        <div class="metric">
          <span class="label">Rolling 3M</span>
          ${formatPct(ret3m)}
          ${formatBenchmark(spy3m)}
        </div>
        <div class="metric">
          <span class="label">Sharpe 3M</span>
          ${formatSharpe(stratSharpe3m)}
          ${formatSharpeBenchmark(spySharpe3m)}
        </div>
      </div>
    </div>
    <div class="card-footer">
      <span>${openCount} open</span>
      <span>${closedCount} closed</span>
    </div>
    ${openTickers.length ? `<div class="ticker-tags">${openTickers.map(t => `<span class="ticker-tag">${t}</span>`).join("")}</div>` : ""}
  `;

  card.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.add("hidden"));
    document.querySelector('[data-tab="positions"]').classList.add("active");
    document.getElementById("tab-positions").classList.remove("hidden");
    document.getElementById("filter-strategy").value = sid;
    document.getElementById("filter-strategy").dispatchEvent(new Event("change"));
  });

  return card;
}

function renderCards(sortKey) {
  const grid = document.getElementById("strategy-grid");
  const sorted = [..._cardData].sort((a, b) => {
    if (sortKey === "return") {
      const av = a.ret12m ?? -Infinity;
      const bv = b.ret12m ?? -Infinity;
      return bv - av;
    }
    if (sortKey === "risk") {
      const av = a.stratSharpe12m ?? -Infinity;
      const bv = b.stratSharpe12m ?? -Infinity;
      return bv - av;
    }
    return (SIZE_ORDER[a.sid] ?? 99) - (SIZE_ORDER[b.sid] ?? 99);
  });

  grid.innerHTML = "";
  sorted.forEach(item => grid.appendChild(buildCard(item)));
}

export function renderStrategies(positions, strategyReturns) {
  const sharpeMap = strategyReturns["_sharpe"] || {};

  _cardData = Object.entries(STRATEGY_META).map(([sid, meta]) => {
    const series = strategyReturns[sid] || [];
    const stratPositions = positions.filter(p => p.strategy === sid);
    const openPositions = stratPositions.filter(p => p.status === "open");
    return {
      sid, meta,
      ret12m:        get12mReturn(series),
      ret3m:         getLatest3mReturn(series),
      spy12m:        getLatest3mReturn(series, "spy_12m"),
      spy3m:         getLatest3mReturn(series, "spy_rolling_3m"),
      stratSharpe12m: sharpeMap[sid]?.["12m"] ?? null,
      stratSharpe3m:  sharpeMap[sid]?.["3m"]  ?? null,
      spySharpe12m:   sharpeMap["spy"]?.["12m"] ?? null,
      spySharpe3m:    sharpeMap["spy"]?.["3m"]  ?? null,
      openCount:  openPositions.length,
      closedCount: stratPositions.filter(p => p.status === "closed").length,
      openTickers: openPositions.map(p => p.ticker),
    };
  });

  renderCards("return");

  document.querySelectorAll(".sort-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".sort-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      renderCards(btn.dataset.sort);
    });
  });
}
