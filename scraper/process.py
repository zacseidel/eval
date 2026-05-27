"""
Portfolio simulation: loads scraped reports, computes positions and strategy returns.
"""
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from polygon_client import get_daily_bars, get_ticker_details, get_execution_price

SCRAPED_DIR = Path(__file__).parent.parent / "data" / "scraped"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"

STRATEGIES = {
    "sp500_top5":        {"section": "sp500",   "ranks": range(1, 6)},
    "sp500_next5":       {"section": "sp500",   "ranks": range(6, 11)},
    "megacap_top5":      {"section": "megacap", "ranks": range(1, 6)},
    "megacap_next5":     {"section": "megacap", "ranks": range(6, 11)},
    "sp400_mcap5":       {"section": "sp400",   "ranks": None, "mcap_range": range(1, 6)},
    "sp400_mcap_next5":  {"section": "sp400",   "ranks": None, "mcap_range": range(6, 11)},
    "munger":            {"section": "munger",  "ranks": None},
}


def load_reports() -> list[dict]:
    files = sorted(SCRAPED_DIR.glob("*.json"))
    reports = []
    for f in files:
        try:
            reports.append(json.loads(f.read_text()))
        except Exception as e:
            print(f"  WARNING: could not load {f.name}: {e}")
    return reports


def compute_sma(bars: list[dict], window: int = 10) -> dict:
    """Returns {date: sma} for each date in bars (None if insufficient history)."""
    result = {}
    closes = []
    for bar in sorted(bars, key=lambda b: b["date"]):
        closes.append(bar["close"])
        if len(closes) >= window:
            result[bar["date"]] = sum(closes[-window:]) / window
        else:
            result[bar["date"]] = None
    return result


def estimate_market_cap(ticker: str, report_price: float) -> float:
    """
    Returns estimated market cap. Uses Polygon ticker details if available,
    falls back to price × recent volume.
    """
    if report_price is None:
        return 0.0
    details = get_ticker_details(ticker)
    mc = details.get("market_cap")
    if mc and mc > 0:
        return float(mc)
    # Fallback: price × volume from most recent available bar
    today = date.today().strftime("%Y-%m-%d")
    week_ago = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    bars = get_daily_bars(ticker, week_ago, today)
    if bars:
        last = bars[-1]
        return report_price * (last.get("volume") or 0)
    return report_price * 1_000_000  # last resort: treat as small cap


def rank_sp400_by_mcap(entries: list[dict]) -> list[dict]:
    """Re-sort SP400 entries by estimated market cap (descending)."""
    ranked = []
    for entry in entries:
        mc = estimate_market_cap(entry["ticker"], entry["price"])
        ranked.append({**entry, "_market_cap": mc})
    ranked.sort(key=lambda x: x["_market_cap"], reverse=True)
    for i, r in enumerate(ranked):
        r["rank"] = i + 1
    return ranked


def find_munger_exit(ticker: str, entry_date: str, today: str) -> tuple:
    """
    Returns (exit_date, exit_price) — the first day after entry_date where
    close < 10-day SMA. Returns (None, None) if still open.
    """
    # Fetch enough history before entry_date to compute 10d SMA on the entry day
    start = (datetime.strptime(entry_date, "%Y-%m-%d") - timedelta(days=20)).strftime("%Y-%m-%d")
    bars = get_daily_bars(ticker, start, today)
    sma_map = compute_sma(bars, window=10)

    entry_dt = datetime.strptime(entry_date, "%Y-%m-%d").date()
    for bar in sorted(bars, key=lambda b: b["date"]):
        bar_dt = datetime.strptime(bar["date"], "%Y-%m-%d").date()
        if bar_dt <= entry_dt:
            continue
        sma = sma_map.get(bar["date"])
        close = bar.get("close")
        if sma is not None and close is not None and close < sma:
            return bar["date"], close
    return None, None


def build_positions(reports: list[dict]) -> list[dict]:
    positions = []
    today = date.today().strftime("%Y-%m-%d")

    # --- Rank-based strategies (sp500, megacap, sp400) ---
    for strategy_id, cfg in STRATEGIES.items():
        if strategy_id == "munger":
            continue

        section = cfg["section"]
        rank_range = cfg["ranks"]
        open_positions: dict[str, dict] = {}  # ticker → position

        for report in reports:
            report_date = report["date"]
            entries = report.get(section, [])

            if "mcap_range" in cfg:
                entries = rank_sp400_by_mcap(entries)
                top_tickers = {e["ticker"] for e in entries if e["rank"] in cfg["mcap_range"]}
            else:
                top_tickers = {e["ticker"] for e in entries if e["rank"] in rank_range}

            price_map = {e["ticker"]: e["price"] for e in entries if e.get("price") is not None}

            # Close positions that left the slot
            for ticker in list(open_positions.keys()):
                if ticker not in top_tickers:
                    pos = open_positions.pop(ticker)
                    exec_date, exit_price = get_execution_price(ticker, report_date)
                    if exit_price is None:
                        exit_price = price_map.get(ticker) or pos["entry_price"]
                        exec_date = report_date
                    entry_price = pos["entry_price"]
                    if exit_price and entry_price:
                        ret = round((exit_price - entry_price) / entry_price * 100, 2)
                    else:
                        ret = None
                    entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d").date()
                    exit_dt = datetime.strptime(exec_date, "%Y-%m-%d").date()
                    positions.append({
                        **pos,
                        "exit_date": exec_date,
                        "exit_price": exit_price,
                        "hold_days": (exit_dt - entry_dt).days,
                        "return_pct": ret,
                        "status": "closed",
                    })

            # Open positions for new entrants
            for ticker in top_tickers:
                if ticker not in open_positions:
                    exec_date, entry_price = get_execution_price(ticker, report_date)
                    if entry_price is None:
                        exec_date, entry_price = report_date, price_map.get(ticker)
                    open_positions[ticker] = {
                        "strategy": strategy_id,
                        "ticker": ticker,
                        "entry_date": exec_date,
                        "entry_price": entry_price,
                        "exit_date": None,
                        "exit_price": None,
                        "hold_days": None,
                        "return_pct": None,
                        "status": "open",
                    }

        # Remaining open positions
        last_report = reports[-1] if reports else None
        for ticker, pos in open_positions.items():
            current_price = None
            if last_report:
                entries = last_report.get(section, [])
                if section == "sp400":
                    entries = rank_sp400_by_mcap(entries)
                price_map = {e["ticker"]: e["price"] for e in entries}
                current_price = price_map.get(ticker)
            if current_price and pos["entry_price"]:
                ret = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
                entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d").date()
                today_dt = datetime.strptime(today, "%Y-%m-%d").date()
                pos = {
                    **pos,
                    "hold_days": (today_dt - entry_dt).days,
                    "return_pct": round(ret, 2),
                }
            positions.append(pos)

    # --- Munger strategy ---
    munger_open: dict[str, dict] = {}
    munger_seen: set[str] = set()

    for report in reports:
        report_date = report["date"]
        entries = report.get("munger", [])
        current_tickers = {e["ticker"] for e in entries}
        price_map = {e["ticker"]: e["price"] for e in entries if e.get("price") is not None}

        for ticker in current_tickers:
            if ticker not in munger_seen:
                munger_seen.add(ticker)
                exec_date, entry_price = get_execution_price(ticker, report_date)
                if entry_price is None:
                    exec_date, entry_price = report_date, price_map.get(ticker)
                munger_open[ticker] = {
                    "strategy": "munger",
                    "ticker": ticker,
                    "entry_date": exec_date,
                    "entry_price": entry_price,
                    "exit_date": None,
                    "exit_price": None,
                    "hold_days": None,
                    "return_pct": None,
                    "status": "open",
                }

    # Determine Munger exits via 10-day SMA check
    print(f"  Checking 10d SMA exits for {len(munger_open)} Munger positions...")
    for ticker, pos in munger_open.items():
        entry_price = pos["entry_price"]
        exit_date, exit_price = find_munger_exit(ticker, pos["entry_date"], today)
        if exit_date:
            ret = round((exit_price - entry_price) / entry_price * 100, 2) if (exit_price and entry_price) else None
            entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d").date()
            exit_dt = datetime.strptime(exit_date, "%Y-%m-%d").date()
            positions.append({
                **pos,
                "exit_date": exit_date,
                "exit_price": exit_price,
                "hold_days": (exit_dt - entry_dt).days,
                "return_pct": ret,
                "status": "closed",
            })
        else:
            # Still open — compute unrealized return from last known price
            bars = get_daily_bars(ticker, pos["entry_date"], today)
            if bars:
                last_close = bars[-1]["close"]
                ret = round((last_close - entry_price) / entry_price * 100, 2) if (last_close and entry_price) else None
                entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d").date()
                today_dt = datetime.strptime(today, "%Y-%m-%d").date()
                positions.append({
                    **pos,
                    "exit_price": last_close,
                    "hold_days": (today_dt - entry_dt).days,
                    "return_pct": ret,
                    "status": "open",
                })
            else:
                positions.append(pos)

    return positions


def build_strategy_returns(reports: list[dict], positions: list[dict], spy_bars: list[dict]) -> dict:
    """
    For each strategy, compute a portfolio value time series normalized to 100
    at the first report date. Uses log returns averaged across active positions.
    Also tracks a per-strategy SPY benchmark using the same approach: SPY's log
    return for each period, compounded only when positions are active.
    """
    today = date.today().strftime("%Y-%m-%d")
    report_dates = [r["date"] for r in reports]
    if not report_dates:
        return {}

    spy_price_map = {b["date"]: b["close"] for b in spy_bars}

    def spy_price_on_or_before(d):
        if d in spy_price_map:
            return spy_price_map[d]
        dt = datetime.strptime(d, "%Y-%m-%d")
        for i in range(1, 6):
            prev = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
            if prev in spy_price_map:
                return spy_price_map[prev]
        return None

    result = {sid: [] for sid in STRATEGIES}

    for strategy_id in STRATEGIES:
        strategy_positions = [p for p in positions if p["strategy"] == strategy_id]
        if not strategy_positions:
            continue

        portfolio_value = 100.0
        spy_value = 100.0
        prev_date = None

        for report_date in report_dates:
            active = []
            for pos in strategy_positions:
                entry = pos["entry_date"]
                exit_ = pos.get("exit_date")
                if entry <= report_date and (exit_ is None or exit_ >= report_date):
                    active.append(pos)

            if not active:
                result[strategy_id].append({
                    "date": report_date,
                    "value": round(portfolio_value, 4),
                    "spy_value": round(spy_value, 4),
                    "rolling_3m": None,
                    "spy_rolling_3m": None,
                    "spy_12m": None,
                })
                prev_date = report_date
                continue

            if prev_date is None:
                result[strategy_id].append({
                    "date": report_date,
                    "value": round(portfolio_value, 4),
                    "spy_value": round(spy_value, 4),
                    "rolling_3m": None,
                    "spy_rolling_3m": None,
                    "spy_12m": None,
                })
                prev_date = report_date
                continue

            # Average log return across active positions
            period_returns = []
            for pos in active:
                prev_price = _price_at_date(pos["ticker"], pos["strategy"], prev_date, reports)
                curr_price = _price_at_date(pos["ticker"], pos["strategy"], report_date, reports)
                if prev_price and curr_price and prev_price > 0 and curr_price > 0:
                    period_returns.append(math.log(curr_price / prev_price))

            if period_returns:
                avg_log_return = sum(period_returns) / len(period_returns)
                portfolio_value *= math.exp(avg_log_return)

            # SPY benchmark: same log-return compounding, only when positions are active
            spy_prev = spy_price_on_or_before(prev_date)
            spy_curr = spy_price_on_or_before(report_date)
            if spy_prev and spy_curr and spy_prev > 0 and spy_curr > 0:
                spy_value *= spy_curr / spy_prev

            result[strategy_id].append({
                "date": report_date,
                "value": round(portfolio_value, 4),
                "spy_value": round(spy_value, 4),
                "rolling_3m": None,
                "spy_rolling_3m": None,
                "spy_12m": None,
            })
            prev_date = report_date

        # Compute rolling 3M for strategy and SPY benchmark
        series = result[strategy_id]
        for i, point in enumerate(series):
            dt = datetime.strptime(point["date"], "%Y-%m-%d").date()
            lookback_str = (dt - timedelta(days=91)).strftime("%Y-%m-%d")
            past_val = None
            past_spy = None
            for j in range(i - 1, -1, -1):
                if series[j]["date"] <= lookback_str:
                    past_val = series[j]["value"]
                    past_spy = series[j]["spy_value"]
                    break
            if past_val and past_val > 0:
                point["rolling_3m"] = round((point["value"] - past_val) / past_val * 100, 2)
            if past_spy and past_spy > 0:
                point["spy_rolling_3m"] = round((point["spy_value"] - past_spy) / past_spy * 100, 2)

        # Compute spy_12m: actual SPY price % change over past 365 days (raw prices, not indexed)
        for point in series:
            dt = datetime.strptime(point["date"], "%Y-%m-%d").date()
            lookback_12m = (dt - timedelta(days=365)).strftime("%Y-%m-%d")
            spy_curr = spy_price_on_or_before(point["date"])
            spy_year_ago = spy_price_on_or_before(lookback_12m)
            if spy_curr and spy_year_ago and spy_year_ago > 0:
                point["spy_12m"] = round((spy_curr - spy_year_ago) / spy_year_ago * 100, 2)

    return result


def build_spy_benchmark(reports: list[dict], spy_bars: list[dict]) -> list[dict]:
    """Normalize pre-fetched SPY bars to 100 at first report date for the chart overlay."""
    if not reports or not spy_bars:
        return []

    first_date = reports[0]["date"]
    price_map = {b["date"]: b["close"] for b in spy_bars}

    def price_on_or_before(d):
        if d in price_map:
            return price_map[d]
        dt = datetime.strptime(d, "%Y-%m-%d")
        for i in range(1, 6):
            prev = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
            if prev in price_map:
                return price_map[prev]
        return None

    base_price = price_on_or_before(first_date)
    if not base_price:
        return []

    series = []
    for r in reports:
        p = price_on_or_before(r["date"])
        value = round((p / base_price) * 100, 4) if p else None
        series.append({"date": r["date"], "value": value, "rolling_3m": None})

    for i, point in enumerate(series):
        if point["value"] is None:
            continue
        dt = datetime.strptime(point["date"], "%Y-%m-%d").date()
        lookback_str = (dt - timedelta(days=91)).strftime("%Y-%m-%d")
        for j in range(i - 1, -1, -1):
            if series[j]["date"] <= lookback_str and series[j]["value"]:
                past_val = series[j]["value"]
                point["rolling_3m"] = round((point["value"] - past_val) / past_val * 100, 2)
                break

    return series


def _price_at_date(ticker: str, strategy_id: str, report_date: str, reports: list[dict]):
    """Look up a ticker's price in a specific report's section."""
    section_map = {
        "sp500_top5": "sp500", "sp500_next5": "sp500",
        "megacap_top5": "megacap", "megacap_next5": "megacap",
        "sp400_mcap5": "sp400", "sp400_mcap_next5": "sp400",
        "munger": "munger",
    }
    section = section_map.get(strategy_id, "sp500")
    for r in reports:
        if r["date"] == report_date:
            for entry in r.get(section, []):
                if entry["ticker"] == ticker:
                    return entry["price"]
    return None


def prefetch_all_tickers(reports: list[dict]) -> None:
    """Bulk-fetch full bar history for every ticker before processing begins,
    so all get_execution_price() and find_munger_exit() calls are cache hits."""
    if not reports:
        return
    today = date.today().strftime("%Y-%m-%d")
    start = (datetime.strptime(reports[0]["date"], "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    spy_start = (datetime.strptime(reports[0]["date"], "%Y-%m-%d") - timedelta(days=396)).strftime("%Y-%m-%d")

    tickers: set[str] = set()
    for r in reports:
        for section in ("sp500", "megacap", "sp400", "munger"):
            for entry in r.get(section, []):
                if entry.get("ticker"):
                    tickers.add(entry["ticker"])

    print(f"  Prefetching SPY bars back 13 months ({spy_start} → {today})...")
    get_daily_bars("SPY", spy_start, today)
    print(f"  Prefetching bars for {len(tickers)} other tickers ({start} → {today})...")
    for ticker in sorted(tickers):
        get_daily_bars(ticker, start, today)


def process_all():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading scraped reports...")
    reports = load_reports()
    print(f"  {len(reports)} reports loaded.")

    print("Prefetching price bars for all tickers...")
    prefetch_all_tickers(reports)

    print("Building positions...")
    positions = build_positions(reports)
    print(f"  {len(positions)} positions computed.")

    first_date = reports[0]["date"]
    today = date.today().strftime("%Y-%m-%d")
    spy_start = (datetime.strptime(first_date, "%Y-%m-%d") - timedelta(days=396)).strftime("%Y-%m-%d")
    spy_bars = get_daily_bars("SPY", spy_start, today)  # cache hit after prefetch

    print("Building strategy return time series...")
    strategy_returns = build_strategy_returns(reports, positions, spy_bars)

    print("Building SPY chart overlay...")
    spy_series = build_spy_benchmark(reports, spy_bars)
    strategy_returns["spy"] = spy_series
    print(f"  SPY overlay: {len(spy_series)} data points.")

    positions_path = PROCESSED_DIR / "positions.json"
    returns_path = PROCESSED_DIR / "strategy_returns.json"
    positions_path.write_text(json.dumps(positions, indent=2))
    returns_path.write_text(json.dumps(strategy_returns, indent=2))
    print(f"  Wrote {positions_path}")
    print(f"  Wrote {returns_path}")


if __name__ == "__main__":
    process_all()
