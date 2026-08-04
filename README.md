# Momentum Strategy Evaluator

A GitHub Pages site that evaluates portfolio strategies derived from [momentum9](https://zacseidel.github.io/momentum9/) reports. Scraped reports are the sole source of entries and security selection. Polygon market data supplies execution prices, daily valuation, and the price-based EMA/SMA exit signals.

**Live site:** https://zacseidel.github.io/eval/

---

## How it works

### Data pipeline

```
momentum9 weekly reports
        │
        ▼
  scraper/scrape.py   ← parses HTML, saves data/scraped/YYYY-MM-DD.json
        │
        ▼
  scraper/process.py  ← builds positions + return series, prices via Polygon API
        │              ← caches bars to data/price_cache/{TICKER}.json
        ▼
  data/processed/
    signals.json            ← auditable selected rows from every report
    positions.json          ← one row per executed trade lifecycle
    strategy_returns.json   ← daily portfolio NAV + SPY benchmark
    manifest.json           ← input hashes, rules, cutoff, and data versions
        │
        ▼
  index.html + js/    ← vanilla JS + Chart.js, reads JSON via fetch()
```

GitHub Actions runs the pipeline every **Tuesday and Friday at 7 PM MDT**, after the US market close, and commits updated data back to the repo. GitHub Pages then deploys the audited JSON automatically.

### Strategies

| ID | Description |
|----|-------------|
| `sp500_top5` | Ranks 1–5 in the S&P 500 Leaders table (sorted by 12M return) |
| `sp500_next5` | Ranks 6–10 in the S&P 500 Leaders table |
| `megacap_top5` | Ranks 1–5 in the Megacap Leaders table |
| `megacap_next5` | Ranks 6–10 in the Megacap Leaders table |
| `sp400_mcap5` | Ranks 1–5 in the scraped S&P 400 Leaders table (legacy ID retained) |
| `sp400_mcap_next5` | Ranks 6–10 in the scraped S&P 400 Leaders table (legacy ID retained) |
| `munger` | Buy a qualifying report signal while flat; exit after a daily close below its 21-day EMA |

All entries use the first trading session on or after the report signal (VWAP when available, else midpoint of open/close). Rank-based positions close when a later report drops the ticker from the selected slot. For Munger, each completed daily adjusted close is compared with its close-based 21-day EMA; a close below the EMA signals an exit for the next available trading session. This one-session delay prevents look-ahead. Portfolios are equal-weighted and rebalanced on trade-event dates, then marked daily at adjusted closes.

### Parallel 10-day SMA evaluations

Every base strategy also has a parallel evaluation whose ID adds `_sma10` (for example, `sp500_top5_sma10` and `munger_sma10`). These variants use exactly the same scraped-report selections as their corresponding base strategies. A ticker is bought on the first available session after a qualifying report signal while the strategy is flat. Disappearing from a later report does not close an SMA10 trade.

After entry, each completed adjusted close is compared with the arithmetic mean of that session and the prior nine trading-session closes. A close below that trailing 10-session SMA signals a mandatory sale on the next available trading session. If a new report recommendation is available on that exit session, the sale executes first and the recommendation opens a new trade at that session's execution price. This keeps both the technical exit and the scraped buy signal auditable without using future data. The calculation follows [NIST's definition of a simple moving average](https://www.itl.nist.gov/div898/handbook/pmc/section4/pmc421.htm).

The overview keeps the original seven cards under **Primary exit rules** and displays the seven `_sma10` cards separately under **10-day SMA exit variants**. Each card has its own returns, Sharpe ratios, open/closed counts, pending exits, and half-Kelly estimate.

### Half-Kelly sizing

Each strategy card estimates a historical half-Kelly risk fraction from closed trades. Winners have positive realized returns, losers have negative realized returns, and break-even trades are reported but excluded from the Kelly odds. With `p` as the non-break-even win probability, `q` as the loss probability, and `b` as average winner divided by the absolute average loser, the displayed value is `0.5 × max(0, p − q/b)`. At least 20 closed trades are required. This is a descriptive estimate based on historical outcomes, not a guarantee or individualized investment recommendation. The criterion originates with [J. L. Kelly Jr.'s 1956 paper](https://www.nokia.com/bell-labs/publications-and-media/publications/a-new-interpretation-of-information-rate/).

---

## Repository layout

```
eval/
├── index.html                  # Single-page frontend shell
├── css/style.css               # Dark-theme styles
├── js/
│   ├── app.js                  # Bootstrap: loads JSON, wires tabs
│   ├── strategies.js           # Strategy cards with 12M / 3M metrics
│   ├── positions.js            # Filterable positions table
│   └── charts.js               # Chart.js portfolio value + rolling 3M charts
├── scraper/
│   ├── main.py                 # Entry point: runs scrape → process
│   ├── scrape.py               # HTML scraper for momentum9 reports
│   ├── process.py              # Position builder + return series calculator
│   ├── polygon_client.py       # Polygon API client (cached, rate-limited)
│   └── requirements.txt
├── data/
│   ├── scraped/                # Raw per-report JSON (YYYY-MM-DD.json)
│   ├── processed/              # Outputs consumed by the frontend
│   │   ├── signals.json
│   │   ├── positions.json
│   │   ├── strategy_returns.json
│   │   └── manifest.json
│   └── price_cache/            # Polygon bar cache ({TICKER}.json)
└── .github/workflows/
    └── update-data.yml         # Scheduled CI pipeline
```

---

## Local setup

**Prerequisites:** Python 3.9+, a [Polygon.io](https://polygon.io) free-tier API key.

```bash
# Clone and install dependencies
git clone https://github.com/zacseidel/eval.git
cd eval
pip install -r scraper/requirements.txt

# Add your API key
echo "POLYGON_API_KEY=your_key_here" > .env

# Run the full pipeline (scrape + process)
python scraper/main.py
```

Then open `index.html` in a browser (or serve the directory locally — the frontend uses ES modules so it needs an HTTP server, not `file://`):

```bash
python -m http.server 8080
# open http://localhost:8080
```

### Running steps individually

```bash
# Scrape only (skips reports already in data/scraped/)
python scraper/scrape.py

# Process only (rebuild positions + returns from cached scrapes)
python scraper/process.py

# Deterministic historical rebuild
python scraper/process.py --as-of 2026-07-31

# Regression suite (no network required)
python -m unittest discover -s tests -v
```

---

## Polygon API usage

- **Rate limit:** 5 requests/minute on the free tier — the client enforces a 12.5-second delay between calls.
- **Disk cache:** Every bar range is stored in `data/price_cache/{TICKER}.json`. The cache tracks `_fetched_from` and `_fetched_through` metadata so only genuinely new date ranges hit the API on subsequent runs.
- **Execution price:** Report entries and rank exits use the first session on or after the report signal. Munger EMA and SMA10 exits execute on the first session after the triggering close. VWAP is used when available, with midpoint fallback.
- **EMA history:** Munger tickers receive 120 calendar days of pre-report history so the 21-day EMA is warm before any trade can exit.
- **SMA history:** All signal tickers receive at least 30 calendar days of pre-report history so the trailing 10-session SMA is warm before any trade can exit.
- **Bar dates:** Polygon timestamps are converted in UTC. Cache schema versions prevent legacy timezone-shifted bars from mixing with corrected data.
- **Failure handling:** Failed API requests do not advance cache coverage; missing execution data fails processing instead of fabricating a flat return.

---

## GitHub Actions

The workflow (`.github/workflows/update-data.yml`) runs on a schedule and can also be triggered manually from the Actions tab:

1. Checks out the repo
2. Installs Python dependencies
3. Runs `python scraper/main.py` with `POLYGON_API_KEY` from repository secrets
4. Commits any changes to `data/` with `[skip ci]` to prevent a re-trigger loop
5. Pushes — GitHub Pages picks up the new JSON automatically

**Required secret:** `POLYGON_API_KEY` — add it under *Settings → Secrets and variables → Actions*.

---

## Frontend

No build step. The frontend is three ES modules loaded directly by `index.html`:

- **Strategies tab** — one card per strategy showing 12M return when a full year exists (otherwise since inception), rolling 3M return, open/closed position counts, current holdings, closed-trade win/loss statistics, and the historical half-Kelly estimate. Munger separately shows latest report buy signals and currently open positions.
- **Positions tab** — sortable, filterable table of all trades with entry/exit dates, prices, Munger EMA trigger values, hold duration, and return %.
- **Charts tab** — Chart.js line charts of portfolio value (normalized to 100 at first report) and rolling 3-month return, both overlaid with an SPY benchmark.

---

## Source reports

Reports are scraped from [zacseidel.github.io/momentum9](https://zacseidel.github.io/momentum9/) at paths like `/reports/momentum_YYYY-MM-DD.html`. Each report contains four sections: **SP500 Leaders**, **Megacap Leaders**, **SP400 Leaders**, and **Munger Strategy**.
