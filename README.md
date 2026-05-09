# Momentum Strategy Evaluator

A GitHub Pages site that backtests portfolio strategies derived from weekly [momentum9](https://zacseidel.github.io/momentum9/) reports. The scraper pulls each report, simulates seven strategies, prices every position via Polygon.io, and writes JSON that the frontend renders as interactive charts and a positions table.

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
    positions.json          ← one row per trade (entry, exit, return)
    strategy_returns.json   ← weekly portfolio value time series + SPY benchmark
        │
        ▼
  index.html + js/    ← vanilla JS + Chart.js, reads JSON via fetch()
```

GitHub Actions runs the pipeline every **Tuesday and Friday at 7 AM MDT** and commits updated data back to the repo, which re-deploys the GitHub Pages site automatically.

### Strategies

| ID | Description |
|----|-------------|
| `sp500_top5` | Ranks 1–5 in the S&P 500 Leaders table (sorted by 12M return) |
| `sp500_next5` | Ranks 6–10 in the S&P 500 Leaders table |
| `megacap_top5` | Ranks 1–5 in the Megacap Leaders table |
| `megacap_next5` | Ranks 6–10 in the Megacap Leaders table |
| `sp400_mcap5` | Top 5 S&P 400 stocks re-ranked by estimated market cap (Polygon) |
| `sp400_mcap_next5` | Ranks 6–10 after the same market-cap re-sort |
| `munger` | Buy on first appearance; sell when daily close drops below 10-day SMA |

All rank-based strategies trade on the report date itself (VWAP when available, else midpoint of open/close). A position is held until the stock drops out of its slot at the next report.

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
│   │   ├── positions.json
│   │   └── strategy_returns.json
│   └── price_cache/            # Polygon bar cache ({TICKER}.json)
└── .github/workflows/
    └── scrape.yml              # Scheduled CI pipeline
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
```

---

## Polygon API usage

- **Rate limit:** 5 requests/minute on the free tier — the client enforces a 12.5-second delay between calls.
- **Disk cache:** Every bar range is stored in `data/price_cache/{TICKER}.json`. The cache tracks `_fetched_from` and `_fetched_through` metadata so only genuinely new date ranges hit the API on subsequent runs.
- **Execution price:** For each trade entry/exit, the client fetches up to 6 calendar days forward from the report date to find the first real trading session (handles weekends and holidays), capped at today to avoid requesting future dates.
- **Market cap:** Used to re-rank SP400 stocks. Prefers Polygon's `market_cap` field from the reference endpoint; falls back to `price × volume` if unavailable.

---

## GitHub Actions

The workflow (`.github/workflows/scrape.yml`) runs on a schedule and can also be triggered manually from the Actions tab:

1. Checks out the repo
2. Installs Python dependencies
3. Runs `python scraper/main.py` with `POLYGON_API_KEY` from repository secrets
4. Commits any changes to `data/` with `[skip ci]` to prevent a re-trigger loop
5. Pushes — GitHub Pages picks up the new JSON automatically

**Required secret:** `POLYGON_API_KEY` — add it under *Settings → Secrets and variables → Actions*.

---

## Frontend

No build step. The frontend is three ES modules loaded directly by `index.html`:

- **Strategies tab** — one card per strategy showing 12M return, rolling 3M return, open/closed position counts, and current holdings. Clicking a card navigates to the Positions tab filtered to that strategy.
- **Positions tab** — sortable, filterable table of all trades with entry/exit dates, prices, hold duration, and return %.
- **Charts tab** — Chart.js line charts of portfolio value (normalized to 100 at first report) and rolling 3-month return, both overlaid with an SPY benchmark.

---

## Source reports

Reports are scraped from [zacseidel.github.io/momentum9](https://zacseidel.github.io/momentum9/) at paths like `/reports/momentum_YYYY-MM-DD.html`. Each report contains four sections: **SP500 Leaders**, **Megacap Leaders**, **SP400 Leaders**, and **Munger Strategy**.
