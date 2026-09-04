export const STRATEGY_META = {
  sp500_top5:   { label: "S&P 500 Top 5",        color: "#6c8ef7" },
  sp500_next5:  { label: "S&P 500 Next 5",        color: "#a78bfa" },
  megacap_top5: { label: "Megacap Top 5",          color: "#34d399" },
  megacap_next5:{ label: "Megacap Next 5",         color: "#10b981" },
  sp400_mcap5:      { label: "S&P 400 Top 5",  color: "#fb923c" },
  sp400_mcap_next5: { label: "S&P 400 Next 5", color: "#fbbf24" },
  munger:           { label: "Munger 21-Day EMA", color: "#f472b6" },
  munger400l:       { label: "Munger400L EMA21", color: "#22d3ee" },
  munger400r:       { label: "Munger400R EMA21", color: "#f59e0b" },
  sp500_top5_sma10:       { label: "S&P 500 Top 5 · SMA10", color: "#6c8ef7" },
  sp500_next5_sma10:      { label: "S&P 500 Next 5 · SMA10", color: "#a78bfa" },
  megacap_top5_sma10:     { label: "Megacap Top 5 · SMA10", color: "#34d399" },
  megacap_next5_sma10:    { label: "Megacap Next 5 · SMA10", color: "#10b981" },
  sp400_mcap5_sma10:      { label: "S&P 400 Top 5 · SMA10", color: "#fb923c" },
  sp400_mcap_next5_sma10: { label: "S&P 400 Next 5 · SMA10", color: "#fbbf24" },
  munger_sma10:           { label: "Munger Signals · SMA10", color: "#f472b6" },
  munger400l_sma10:       { label: "Munger400L SMA10", color: "#22d3ee" },
  munger400r_sma10:       { label: "Munger400R SMA10", color: "#f59e0b" },
};

function isSma10(sid) {
  return sid.endsWith("_sma10");
}

function isMungerFamily(sid) {
  return sid === "munger" || sid === "munger_sma10" ||
    sid === "munger400l" || sid === "munger400l_sma10" ||
    sid === "munger400r" || sid === "munger400r_sma10";
}

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

function formatStatPct(value, signed = false) {
  if (value == null) return "—";
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function getReturnSummary(series, field = "value") {
  if (!series || series.length < 2) return null;
  const first = series[0];
  const last = series[series.length - 1];
  if (last[field] == null) return null;
  if (field === "value" && last.return_12m != null)
    return { value: last.return_12m, label: "12M Price" };
  if (field === "spy_value" && last.spy_12m != null)
    return { value: last.spy_12m, label: "12M Price" };
  return {
    value: (last[field] / 100 - 1) * 100,
    label: `Since ${first.date}`,
  };
}

function getLatest3mReturn(series, field = "rolling_3m") {
  if (!series || series.length === 0) return null;
  const last = series[series.length - 1];
  return last[field] ?? null;
}

let _cardData = [];

export function getAvailableStrategyEntries(strategyReturns) {
  return Object.entries(STRATEGY_META)
    .filter(([sid]) => Array.isArray(strategyReturns[sid]));
}

function buildCard(item) {
  const { sid, meta, returnLabel, ret12m, ret3m, spy12m, spy3m,
          stratSharpe12m, stratSharpe3m, spySharpe12m, spySharpe3m,
          openCount, closedCount, pendingExitCount, openTickers, signalTickers,
          tradeStats } = item;

  const halfKelly = tradeStats?.half_kelly_pct;
  const kellyValue = halfKelly == null ? "—" : `${halfKelly.toFixed(1)}%`;
  const breakevenNote = tradeStats?.breakeven_count
    ? ` · ${tradeStats.breakeven_count} break-even`
    : "";
  let kellyNote;
  if (tradeStats?.kelly_status === "needs_winners_and_losers") {
    kellyNote = `${tradeStats.closed_count} closed trades · needs both wins and losses`;
  } else if (halfKelly == null) {
    kellyNote = `Needs at least ${tradeStats?.minimum_closed_trades ?? 20} closed trades (${tradeStats?.closed_count ?? 0} available)`;
  } else if (halfKelly === 0 && tradeStats.full_kelly_pct <= 0) {
    kellyNote = `${tradeStats.closed_count} closed trades${breakevenNote} · no positive historical Kelly edge`;
  } else {
    kellyNote = `${tradeStats.closed_count} closed trades${breakevenNote} · historical estimate`;
  }

  const card = document.createElement("div");
  card.className = "strategy-card";
  card.dataset.strategy = sid;
  card.innerHTML = `
    <div class="card-body">
      <div class="card-header">
        <div class="dot" style="background:${meta.color}"></div>
        <h3>${meta.label}</h3>
      </div>
      <div class="card-metrics">
        <div class="metric-group">
          <div class="metric">
            <span class="label">${returnLabel}</span>
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
            <span class="label">3M Price</span>
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
        ${isMungerFamily(sid) || isSma10(sid) ? `<span>${pendingExitCount} exit pending</span>` : ""}
        ${isMungerFamily(sid) ? `<span>${signalTickers.length} current signals</span>` : ""}
      </div>
      ${openTickers.length ? `<div class="ticker-tags">${openTickers.map(t => `<span class="ticker-tag">${t}</span>`).join("")}</div>` : ""}
      ${isMungerFamily(sid) && signalTickers.length ? `<div class="signal-note">Latest report buy signals: ${signalTickers.join(", ")}</div>` : ""}
    </div>
    <div class="kelly-panel" title="Half Kelly = 0.5 × max(0, win probability − loss probability ÷ payoff ratio)">
      <div class="kelly-heading">
        <span>Half-Kelly risk budget</span>
        <strong>${kellyValue}</strong>
      </div>
      <div class="kelly-stats">
        <span>Winners ${formatStatPct(tradeStats?.winner_pct)}</span>
        <span>Avg win ${formatStatPct(tradeStats?.average_win_pct, true)}</span>
        <span>Losers ${formatStatPct(tradeStats?.loser_pct)}</span>
        <span>Avg loss ${formatStatPct(tradeStats?.average_loss_pct)}</span>
      </div>
      <div class="kelly-note">${kellyNote}</div>
    </div>
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

function descendingNullable(a, b) {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return b - a;
}

export function compareCards(a, b, sortKey) {
  let comparison;
  if (sortKey === "return") {
    comparison = descendingNullable(a.ret12m, b.ret12m);
  } else if (sortKey === "sharpe") {
    comparison = descendingNullable(
      a.stratSharpe12m ?? a.stratSharpe3m,
      b.stratSharpe12m ?? b.stratSharpe3m,
    );
  } else if (sortKey === "size") {
    comparison = descendingNullable(
      a.tradeStats?.half_kelly_pct,
      b.tradeStats?.half_kelly_pct,
    );
  } else {
    comparison = 0;
  }
  return comparison || a.sid.localeCompare(b.sid);
}

function renderCards(sortKey) {
  const sorted = [..._cardData].sort((a, b) => compareCards(a, b, sortKey));

  const primaryGrid = document.getElementById("strategy-grid");
  const smaGrid = document.getElementById("sma-strategy-grid");
  primaryGrid.innerHTML = "";
  smaGrid.innerHTML = "";
  sorted.forEach(item => {
    const grid = isSma10(item.sid) ? smaGrid : primaryGrid;
    grid.appendChild(buildCard(item));
  });
}

export function renderStrategies(
  positions,
  strategyReturns,
  signals = [],
  latestReportDate = "",
  strategyTradeStats = {},
) {
  const sharpeMap = strategyReturns["_sharpe"] || {};

  _cardData = getAvailableStrategyEntries(strategyReturns)
    .map(([sid, meta]) => {
      const series = strategyReturns[sid] || [];
      const stratPositions = positions.filter(p => p.strategy === sid);
      const openPositions = stratPositions.filter(p => p.status === "open");
      const pendingExitPositions = openPositions.filter(p => p.exit_signal_date != null);
      const returnSummary = getReturnSummary(series);
      const spySummary = getReturnSummary(series, "spy_value");
      const signalTickers = signals
        .filter(s => s.strategy === sid && s.report_date === latestReportDate)
        .map(s => s.ticker);
      return {
        sid, meta,
        returnLabel:   returnSummary?.label ?? "Price Return",
        ret12m:        returnSummary?.value ?? null,
        ret3m:         getLatest3mReturn(series),
        spy12m:        spySummary?.value ?? null,
        spy3m:         getLatest3mReturn(series, "spy_rolling_3m"),
        stratSharpe12m: sharpeMap[sid]?.["12m"] ?? null,
        stratSharpe3m:  sharpeMap[sid]?.["3m"]  ?? null,
        spySharpe12m:   sharpeMap["spy"]?.["12m"] ?? null,
        spySharpe3m:    sharpeMap["spy"]?.["3m"]  ?? null,
        openCount:  openPositions.length,
        closedCount: stratPositions.filter(p => p.status === "closed").length,
        pendingExitCount: pendingExitPositions.length,
        openTickers: openPositions.map(p => p.ticker),
        signalTickers,
        tradeStats: strategyTradeStats[sid] || null,
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
