"""Polygon.io API client with disk caching and rate limiting."""
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).parent.parent / "data" / "price_cache"
API_BASE = "https://api.polygon.io"
CALL_INTERVAL = 12.5  # seconds between calls to stay under 5/min on free tier

_last_call_time: float = 0.0


def _throttle():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < CALL_INTERVAL:
        time.sleep(CALL_INTERVAL - elapsed)
    _last_call_time = time.time()


def _api_key() -> str:
    key = os.environ.get("POLYGON_API_KEY", "")
    if not key:
        raise RuntimeError("POLYGON_API_KEY environment variable not set.")
    return key


def _get(path: str, params: dict = None) -> dict:
    _throttle()
    params = params or {}
    params["apiKey"] = _api_key()
    resp = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_daily_bars(ticker: str, from_date: str, to_date: str) -> list[dict]:
    """
    Returns list of daily OHLCV bars for ticker between from_date and to_date (YYYY-MM-DD).
    Caches to data/price_cache/{ticker}.json. Tracks _fetched_from/_fetched_through metadata
    so only genuinely new date ranges hit the API — weekends/holidays never re-trigger fetches.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{ticker}.json"

    raw: dict = {}
    if cache_file.exists():
        raw = json.loads(cache_file.read_text())

    fetched_from = raw.pop("_fetched_from", None)
    fetched_through = raw.pop("_fetched_through", None)
    cached = raw

    # Determine which ranges (if any) still need to be fetched.
    # If no metadata exists (legacy cache or first fetch), fetch the full requested
    # range in one call — avoids splitting into multiple calls around sparse cached windows.
    ranges_to_fetch = []
    if fetched_from is None or fetched_through is None:
        ranges_to_fetch.append((from_date, to_date))
    else:
        if from_date < fetched_from:
            day_before = (datetime.strptime(fetched_from, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            ranges_to_fetch.append((from_date, day_before))
        if to_date > fetched_through:
            day_after = (datetime.strptime(fetched_through, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            ranges_to_fetch.append((day_after, to_date))

    for fetch_from, fetch_to in ranges_to_fetch:
        print(f"    Fetching {ticker} bars {fetch_from} → {fetch_to}...")
        path = f"/v2/aggs/ticker/{ticker}/range/1/day/{fetch_from}/{fetch_to}"
        try:
            data = _get(path, {"adjusted": "true", "sort": "asc", "limit": 5000})
            for bar in data.get("results", []):
                bar_date = datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
                cached[bar_date] = {
                    "open": bar.get("o"),
                    "high": bar.get("h"),
                    "low": bar.get("l"),
                    "close": bar.get("c"),
                    "volume": bar.get("v"),
                    "vwap": bar.get("vw"),
                }
        except Exception as e:
            print(f"    WARNING: could not fetch bars for {ticker}: {e}")

    if ranges_to_fetch:
        new_from = min(from_date, fetched_from or from_date)
        new_through = max(to_date, fetched_through or to_date)
        out = {"_fetched_from": new_from, "_fetched_through": new_through}
        out.update({k: cached[k] for k in sorted(cached)})
        cache_file.write_text(json.dumps(out, indent=2))

    # Return bars in the requested range
    from_dt = datetime.strptime(from_date, "%Y-%m-%d").date()
    to_dt = datetime.strptime(to_date, "%Y-%m-%d").date()
    result = []
    d = from_dt
    while d <= to_dt:
        ds = d.strftime("%Y-%m-%d")
        if ds in cached:
            result.append({"date": ds, **cached[ds]})
        d += timedelta(days=1)
    return result


def get_execution_price(ticker: str, report_date: str):
    """
    Returns (trade_date, price) for the first trading day on or after report_date.
    Reports are calculated from the prior day's close, so report_date itself is the
    first valid execution session. Uses VWAP when available, falls back to (open+close)/2.
    """
    today = date.today().strftime("%Y-%m-%d")
    start = report_date
    end   = min(
        (datetime.strptime(report_date, "%Y-%m-%d") + timedelta(days=6)).strftime("%Y-%m-%d"),
        today,
    )
    bars = get_daily_bars(ticker, start, end)
    if not bars:
        return None, None
    bar = bars[0]
    price = bar.get("vwap") or ((bar["open"] + bar["close"]) / 2 if bar.get("open") and bar.get("close") else bar.get("close"))
    return bar["date"], price


def get_ticker_details(ticker: str) -> dict:
    """
    Returns ticker reference details including market_cap.
    Caches to data/price_cache/{ticker}_details.json.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{ticker}_details.json"

    # Details are refreshed if cached file is older than 7 days
    if cache_file.exists():
        mtime = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if (datetime.now() - mtime).days < 7:
            return json.loads(cache_file.read_text())

    print(f"    Fetching details for {ticker}...")
    try:
        data = _get(f"/v3/reference/tickers/{ticker}")
        result = data.get("results", {})
        cache_file.write_text(json.dumps(result, indent=2))
        return result
    except Exception as e:
        print(f"    WARNING: could not fetch details for {ticker}: {e}")
        return {}
