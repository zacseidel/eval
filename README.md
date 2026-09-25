# Momentum Strategy Evaluator

A GitHub Pages site that evaluates portfolio strategies derived from [Momentum](https://zacseidel.github.io/momentum/) reports. Scraped reports are the sole source of entries and security selection. Polygon market data supplies execution prices, daily valuation, and the price-based EMA/SMA exit signals.

**Live site:** https://zacseidel.github.io/eval/

---

## How it works

### Data pipeline

```
Momentum weekly reports
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
| `munger400l` | Buy a qualifying Munger400L large-midcap report signal while flat; exit after a daily close below its 21-day EMA |
| `munger400r` | Buy a qualifying Munger400R former-return-leader report signal while flat; exit after a daily close below its 21-day EMA |
| `megalaggards2` | Buy the two worst names in the Mega Cap Laggards list as a new lot on every report; sell each lot 21 trading sessions after entry |
| `industry_up5` | Buy a top-5 positive stock rank change from the industry report while flat; exit after a daily close below its 10-day SMA |
| `industry_down5` | Buy a top-5 negative stock rank change from the industry report while flat; exit after a daily close above its 10-day SMA |

All entries use the first trading session on or after the report signal (VWAP when available, else midpoint of open/close). Rank-based positions close when a later report drops the ticker from the selected slot. For Munger and both Munger400 EMA21 models, each completed daily split-adjusted close is compared with its close-based 21-day EMA; a close below the EMA signals an exit for the next available trading session. This one-session delay prevents look-ahead. An entry session is eligible to create an EMA exit signal at that session's close, and continuing report membership can open a later trade once the prior trade has exited. Portfolios are equal-weighted and rebalanced on trade-event dates, then marked daily at split-adjusted closes. Weights are assigned per open trade; every strategy except `megalaggards2` holds at most one trade per ticker, so for them this is the same as equal weight per ticker.

Returns throughout the site are **price returns**. Cash dividends, fees, and slippage are not included. The same price-return basis is used for SPY so the comparison is internally consistent, but neither series should be interpreted as total return.

### Mega Cap Laggards lots

The Mega Cap Laggards section ranks the 10 largest S&P 500 stocks by 3-, 6-, and 12-month return and lists the three with the worst average rank, worst first. `megalaggards2` takes rows 1–2. Every report listing opens a new lot at the first session on or after the report, even when the strategy already holds that ticker. Each lot sells at the session 21 trading sessions after its entry, regardless of later reports. A name listed on consecutive reports therefore holds several overlapping lots, and the portfolio gives each open lot an equal weight. With two names per report, one ticker can reach at most half of the portfolio.

Overlapping lots of one ticker share most of their holding period, so their outcomes are correlated. The card's half-Kelly uses the closed lots, and its note reports `independent_run_count`, which merges overlapping lots of a ticker into continuous holding runs. Treat the Kelly estimate with caution while that count is small.

### Parallel 10-day SMA evaluations

Every base strategy also has a parallel evaluation whose ID adds `_sma10` (for example, `sp500_top5_sma10`, `munger_sma10`, `munger400l_sma10`, and `munger400r_sma10`). These variants use exactly the same scraped-report selections as their corresponding base strategies. `megalaggards2_sma10` does not stack lots: a repeated listing while holding the ticker is ignored, as in the other SMA10 variants. A ticker is bought on the first available session after a qualifying report signal while the strategy is flat. Disappearing from a later report does not close an SMA10 trade.

After entry, each completed adjusted close is compared with the arithmetic mean of that session and the prior nine trading-session closes. A close below that trailing 10-session SMA signals a mandatory sale on the next available trading session. If a new report recommendation is available on that exit session, the sale executes first and the recommendation opens a new trade at that session's execution price. This keeps both the technical exit and the scraped buy signal auditable without using future data. The calculation follows [NIST's definition of a simple moving average](https://www.itl.nist.gov/div898/handbook/pmc/section4/pmc421.htm).

The overview displays the ten base-strategy cards under **Primary exit rules** and the ten `_sma10` cards under **10-day SMA exit variants**. Each Munger400 model therefore has two cards. Every card has its own returns, Sharpe ratios, open/closed counts, pending exits, and half-Kelly estimate.

### Industry rank-change evaluations

Once an industry-rank report is published, two more cards appear under **Industry rank changes**. Both read the **Stocks → Largest rank changes** table and ignore the industry table above it. `industry_up5` buys the five largest positive rank changes. `industry_down5` buys the five largest negative rank changes. A name is bought on the first available session after a qualifying report while that strategy is flat. Leaving a later report's top 5 does not close the trade.

`industry_up5` sells on the next session after a completed close below the trailing 10-session SMA. `industry_down5` sells on the next session after a completed close above that same SMA. A close equal to the average does not exit. The sale is delayed one session, and a new report recommendation on the exit session opens a new trade only after the sale. Both cards use the same return, Sharpe, position-count, and half-Kelly figures as the other evaluations. A strategy's series starts on the first report that contains its table.

### Half-Kelly sizing

Each strategy card estimates a historical half-Kelly risk budget from closed trades. Winners have positive realized returns, losers have negative realized returns, and break-even trades are reported but excluded from the Kelly odds. With `p` as the non-break-even win probability, `q` as the loss probability, and `b` as average winner divided by the absolute average loser, the displayed value is `0.5 × max(0, p − q/b)`. At least 20 closed trades are required. The value is a capital-at-risk heuristic, not necessarily the notional percentage allocated to a position. It is a descriptive estimate based on historical outcomes, not a guarantee or individualized investment recommendation. The criterion originates with [J. L. Kelly Jr.'s 1956 paper](https://www.nokia.com/bell-labs/publications-and-media/publications/a-new-interpretation-of-information-rate/).

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
│   ├── scrape.py               # HTML scraper for Momentum reports
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

**Prerequisites:** Python 3.9+, Node.js 22+ for frontend tests, and a [Polygon.io](https://polygon.io) free-tier API key.

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
# Scrape only (skips reports already cached from the current source)
python scraper/scrape.py

# Process only (rebuild positions + returns from cached scrapes)
python scraper/process.py

# Deterministic historical rebuild
python scraper/process.py --as-of 2026-07-31

# Regression suite (no network required)
python -m unittest discover -s tests -v
npm test
```

---

## Polygon API usage

- **Rate limit:** 5 requests/minute on the free tier — the client enforces a 12.5-second delay between calls.
- **Disk cache:** Every bar range is stored in `data/price_cache/{TICKER}.json`. The cache tracks `_fetched_from` and `_fetched_through` metadata. Routine updates use one grouped daily aggregate request per missing weekday plus one split-reference request. New signal tickers are seeded from those same grouped sessions. Per-ticker requests are reserved for split-adjusted history refreshes and for tickers that never appear in the grouped responses.
- **Execution price:** Report entries and rank exits use the first session on or after the report signal. Munger EMA and SMA10 exits execute on the first session after the triggering close. VWAP is used when available, with midpoint fallback.
- **EMA history:** A close-based 21-day EMA is published only after 21 cached sessions. Existing Munger caches keep their pre-report history; new names warm up from grouped daily sessions going forward.
- **SMA history:** A trailing 10-session SMA is published only after 10 cached sessions. The same grouped daily path supplies those closes.
- **Bar dates:** Polygon timestamps are converted in UTC. Cache schema versions prevent legacy timezone-shifted bars from mixing with corrected data.
- **Failure handling:** Failed API requests do not advance cache coverage; missing execution data fails processing instead of fabricating a flat return. Grouped daily 403/404 (or a not-entitled body) on a session the plan has not published yet is treated as unpublished: earlier successful days are kept and later dates are retried on the next run.

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

- **Strategies tab** — one card per available strategy showing 12M price return when a full year exists (otherwise since inception), rolling 3M price return, open/closed position counts, current holdings, closed-trade win/loss statistics, and the historical half-Kelly risk budget. Cards can be sorted by return, available Sharpe ratio, or half-Kelly size. Munger, Munger400L, and Munger400R separately show latest report buy signals and currently open positions. Mega Laggards 2 · Hold 21 shows each held ticker with its open lot count. A strategy first appears when its source section first appears in a report.
- **Positions tab** — sortable, filterable table of all trades with entry/exit dates, prices, Munger EMA trigger values, hold duration, and return %.
- **Charts tab** — Chart.js line charts of portfolio NAV on its original 100 baseline for all-history views (range-relative views rebase to 100) and rolling 3-month price return, both overlaid with an SPY benchmark.

---

## Source reports

Reports are scraped from [zacseidel.github.io/momentum](https://zacseidel.github.io/momentum/) at paths like `/reports/momentum_YYYY-MM-DD.html`. Industry rank changes come from the linked `/reports/industry_YYYY-MM-DD.html` pages and are stored on the same date snapshot. Each raw snapshot records its `source_url`; an industry page also records `industry_source_url`. A snapshot is refreshed when either URL is missing or out of date, or when its `parser_version` is older than `SNAPSHOT_PARSER_VERSION` in `scrape.py`. Bump that constant when a new report section is parsed, so reports that were backfilled with the section are re-read. The evaluator recognizes **SP500 Leaders**, **Megacap Leaders**, **SP400 Leaders**, **Munger Strategy**, **Munger400L** (`summary-munger400l`), **Munger400R** (`summary-munger400r`), **Mega Cap Laggards** (`summary-megalaggards`), and the industry report's **Stocks → Largest rank changes** table. Each evaluation begins with the first report containing its source section.
