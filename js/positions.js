const STRATEGY_LABELS = {
  sp500_top5:   "S&P 500 Top 5",
  sp500_next5:  "S&P 500 Next 5",
  megacap_top5: "Megacap Top 5",
  megacap_next5:"Megacap Next 5",
  sp400_mcap5:      "S&P 400 Mkt Cap Top 5",
  sp400_mcap_next5: "S&P 400 Mkt Cap Next 5",
  munger:           "Munger",
};

let _allPositions = [];
let _sortKey = null;   // null = use defaultSort
let _sortDir = 1;

function fmtPrice(v) {
  return v != null ? `$${Number(v).toFixed(2)}` : "—";
}

function fmtExitPrice(p) {
  if (p.status !== "open") return fmtPrice(p.exit_price);
  const price = p.exit_price ??
    (p.entry_price != null && p.return_pct != null
      ? p.entry_price * (1 + p.return_pct / 100)
      : null);
  return price != null ? `<em>${fmtPrice(price)}</em>` : "—";
}

function fmtReturn(v) {
  if (v == null) return "—";
  const cls = v >= 0 ? "ret-pos" : "ret-neg";
  return `<span class="${cls}">${v >= 0 ? "+" : ""}${v.toFixed(2)}%</span>`;
}

function filtered() {
  const strat = document.getElementById("filter-strategy").value;
  const status = document.getElementById("filter-status").value;
  const ticker = document.getElementById("filter-ticker").value.toUpperCase().trim();

  return _allPositions.filter(p => {
    if (strat && p.strategy !== strat) return false;
    if (status && p.status !== status) return false;
    if (ticker && !p.ticker.includes(ticker)) return false;
    return true;
  });
}

function defaultSort(data) {
  const tickerHasOpen = {};
  const tickerLatest = {};
  for (const p of data) {
    if (p.status === "open") tickerHasOpen[p.ticker] = true;
    const d = p.entry_date ?? "";
    if (!tickerLatest[p.ticker] || d > tickerLatest[p.ticker])
      tickerLatest[p.ticker] = d;
  }
  return [...data].sort((a, b) => {
    // Tickers with open positions first
    const aOpen = tickerHasOpen[a.ticker] ? 1 : 0;
    const bOpen = tickerHasOpen[b.ticker] ? 1 : 0;
    if (aOpen !== bOpen) return bOpen - aOpen;
    // Ticker group ordered by most recent entry date descending
    const latA = tickerLatest[a.ticker] ?? "";
    const latB = tickerLatest[b.ticker] ?? "";
    if (latA !== latB) return latA > latB ? -1 : 1;
    // Keep same ticker together (alphabetical tiebreak between groups)
    if (a.ticker !== b.ticker) return a.ticker < b.ticker ? -1 : 1;
    // Within ticker: open before closed, then entry date descending
    if (a.status !== b.status) return a.status === "open" ? -1 : 1;
    const edA = a.entry_date ?? "";
    const edB = b.entry_date ?? "";
    return edA > edB ? -1 : edA < edB ? 1 : 0;
  });
}

function sortedData(data) {
  if (_sortKey === null) return defaultSort(data);
  return [...data].sort((a, b) => {
    let va = a[_sortKey] ?? "";
    let vb = b[_sortKey] ?? "";
    if (typeof va === "string") va = va.toLowerCase();
    if (typeof vb === "string") vb = vb.toLowerCase();
    if (va < vb) return -1 * _sortDir;
    if (va > vb) return 1 * _sortDir;
    return 0;
  });
}

function render() {
  const tbody = document.getElementById("positions-tbody");
  const data = sortedData(filtered());
  tbody.innerHTML = data.map(p => `
    <tr>
      <td>${STRATEGY_LABELS[p.strategy] ?? p.strategy}</td>
      <td><strong>${p.ticker}</strong></td>
      <td>${p.entry_date ?? "—"}</td>
      <td>${fmtPrice(p.entry_price)}</td>
      <td>${p.status === "open" ? "" : (p.exit_date ?? "—")}</td>
      <td>${fmtExitPrice(p)}</td>
      <td>${p.hold_days != null ? p.hold_days + "d" : "—"}</td>
      <td>${fmtReturn(p.return_pct)}</td>
      <td><span class="badge badge-${p.status}">${p.status}</span></td>
    </tr>
  `).join("");
}

export function renderPositions(positions) {
  _allPositions = positions;

  // Filters
  ["filter-strategy", "filter-status"].forEach(id => {
    document.getElementById(id).addEventListener("change", render);
  });
  document.getElementById("filter-ticker").addEventListener("input", render);

  // Sortable headers
  document.querySelectorAll("thead th[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (_sortKey === key) {
        _sortDir *= -1;
      } else {
        _sortKey = key;
        _sortDir = 1;
      }
      render();
    });
  });

  render();
}
