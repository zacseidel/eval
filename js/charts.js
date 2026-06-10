const STRATEGY_COLORS = {
  sp500_top5:   "#6c8ef7",
  sp500_next5:  "#a78bfa",
  megacap_top5: "#34d399",
  megacap_next5:"#10b981",
  sp400_mcap5:      "#fb923c",
  sp400_mcap_next5: "#fbbf24",
  munger:           "#f472b6",
  spy:          "#888888",
};

const STRATEGY_LABELS = {
  sp500_top5:   "S&P 500 Top 5",
  sp500_next5:  "S&P 500 Next 5",
  megacap_top5: "Megacap Top 5",
  megacap_next5:"Megacap Next 5",
  sp400_mcap5:      "S&P 400 Mkt Cap Top 5",
  sp400_mcap_next5: "S&P 400 Mkt Cap Next 5",
  munger:           "Munger",
  spy:          "SPY",
};

let _strategyReturns = {};
let _cumulativeChart = null;
let _rolling3mChart = null;
let _scatterCharts = {};
let _currentRange = "all";

function filterByRange(series, range) {
  if (!series || range === "all") return series;
  const last = series[series.length - 1];
  if (!last) return series;
  const cutoff = new Date(last.date);
  const months = range === "12m" ? 12 : range === "6m" ? 6 : 3;
  cutoff.setMonth(cutoff.getMonth() - months);
  return series.filter(p => new Date(p.date) >= cutoff);
}

function normalizeToFirst(series) {
  if (!series || series.length === 0) return series;
  const first = series[0].value;
  if (!first) return series;
  return series.map(p => ({ ...p, value: (p.value / first) * 100 }));
}

function buildCumulativeChart(range) {
  const allDates = new Set();
  const datasets = [];

  const strategyOrder = ["sp500_top5", "sp500_next5", "megacap_top5", "megacap_next5", "sp400_mcap5", "munger"];
  const orderedEntries = [
    ...strategyOrder.filter(sid => _strategyReturns[sid]).map(sid => [sid, _strategyReturns[sid]]),
    ...Object.entries(_strategyReturns).filter(([sid, v]) => !strategyOrder.includes(sid) && Array.isArray(v)),
  ];

  for (const [sid, series] of orderedEntries) {
    const filtered = filterByRange(series, range);
    const normalized = normalizeToFirst(filtered);
    normalized.forEach(p => allDates.add(p.date));
    const isSpy = sid === "spy";
    datasets.push({
      label: STRATEGY_LABELS[sid] ?? sid,
      data: normalized.map(p => ({ x: p.date, y: p.value })),
      borderColor: STRATEGY_COLORS[sid] ?? "#888",
      backgroundColor: "transparent",
      tension: 0.3,
      pointRadius: isSpy ? 0 : 2,
      borderWidth: isSpy ? 1.5 : 2,
      borderDash: isSpy ? [6, 3] : [],
      order: isSpy ? 99 : 1,
    });
  }

  const ctx = document.getElementById("cumulative-chart").getContext("2d");
  if (_cumulativeChart) _cumulativeChart.destroy();
  _cumulativeChart = new Chart(ctx, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { color: "#7b7f9e", boxWidth: 12 } },
        tooltip: {
          callbacks: {
            label: ctx => {
              const pct = (ctx.parsed.y - 100).toFixed(1);
              const sign = pct >= 0 ? "+" : "";
              return ` ${ctx.dataset.label}: ${sign}${pct}%`;
            },
          },
        },
      },
      scales: {
        x: {
          type: "time",
          time: { unit: "month", tooltipFormat: "MMM d, yyyy" },
          ticks: { color: "#7b7f9e" },
          grid:  { color: "#2d3148" },
        },
        y: {
          type: "logarithmic",
          ticks: {
            color: "#7b7f9e",
            callback: v => {
              const pct = v - 100;
              return `${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%`;
            },
          },
          grid: { color: "#2d3148" },
        },
      },
    },
  });
}

function buildRolling3mChart() {
  const labels = [];
  const values = [];
  const colors = [];

  for (const [sid, series] of Object.entries(_strategyReturns)) {
    if (!Array.isArray(series) || series.length === 0) continue;
    const last = series[series.length - 1];
    const val = last.rolling_3m;
    labels.push(STRATEGY_LABELS[sid] ?? sid);
    values.push(val ?? 0);
    colors.push(STRATEGY_COLORS[sid] ?? "#888");
  }

  const ctx = document.getElementById("rolling3m-chart").getContext("2d");
  if (_rolling3mChart) _rolling3mChart.destroy();
  _rolling3mChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "Rolling 3M Return",
        data: values,
        backgroundColor: colors.map(c => c + "cc"),
        borderColor: colors,
        borderWidth: 1,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.parsed.y >= 0 ? "+" : ""}${ctx.parsed.y.toFixed(1)}%`,
          },
        },
      },
      scales: {
        x: { ticks: { color: "#7b7f9e" }, grid: { display: false } },
        y: {
          ticks: {
            color: "#7b7f9e",
            callback: v => `${v >= 0 ? "+" : ""}${v.toFixed(0)}%`,
          },
          grid: { color: "#2d3148" },
        },
      },
    },
  });
}

function buildScatterChart(sid, trades) {
  if (_scatterCharts[sid]) _scatterCharts[sid].destroy();
  const canvas = document.getElementById(`scatter-${sid}`);
  if (!canvas) return;

  const color = STRATEGY_COLORS[sid] ?? "#888";
  const data = trades.map(t => ({
    x: t.hold_days,
    y: Math.log(1 + t.return_pct / 100),
    label: t.ticker,
  }));

  _scatterCharts[sid] = new Chart(canvas.getContext("2d"), {
    type: "scatter",
    plugins: [ChartDataLabels],
    data: {
      datasets: [{
        data,
        backgroundColor: color + "cc",
        borderColor: color,
        pointRadius: 5,
        pointHoverRadius: 7,
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        datalabels: {
          formatter: val => val.label,
          color: "#c0c4e0",
          font: { size: 10 },
          anchor: "end",
          align: "top",
          offset: 2,
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const pt = ctx.raw;
              const pct = (Math.exp(pt.y) - 1) * 100;
              const sign = pct >= 0 ? "+" : "";
              return `${pt.label}: ${sign}${pct.toFixed(1)}% · ${pt.x}d held`;
            },
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: "Days Held", color: "#7b7f9e" },
          ticks: { color: "#7b7f9e" },
          grid: { color: "#2d3148" },
        },
        y: {
          title: { display: true, text: "Log Return", color: "#7b7f9e" },
          ticks: { color: "#7b7f9e", callback: v => v.toFixed(2) },
          grid: { color: ctx => ctx.tick.value === 0 ? "#666" : "#2d3148" },
        },
      },
    },
  });
}

export function renderScatterCharts(positions) {
  const closed = positions.filter(p => p.status === "closed" && p.exit_date);
  if (!closed.length) return;
  const latestExit = closed.reduce((a, b) => a.exit_date > b.exit_date ? a : b).exit_date;
  const cutoff = new Date(latestExit);
  cutoff.setFullYear(cutoff.getFullYear() - 1);
  const recent = closed.filter(p => new Date(p.exit_date) >= cutoff);

  for (const sid of Object.keys(STRATEGY_LABELS).filter(s => s !== "spy")) {
    buildScatterChart(sid, recent.filter(p => p.strategy === sid));
  }
}

export function renderCharts(strategyReturns) {
  _strategyReturns = strategyReturns;

  // Wait until Chart.js time adapter is available (CDN async)
  buildCumulativeChart(_currentRange);
  buildRolling3mChart();

  document.querySelectorAll(".range-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".range-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      _currentRange = btn.dataset.range;
      buildCumulativeChart(_currentRange);
    });
  });
}
