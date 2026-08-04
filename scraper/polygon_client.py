"""Polygon.io API client with disk caching and rate limiting."""
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import requests

CACHE_DIR = Path(__file__).parent.parent / "data" / "price_cache"
API_BASE = "https://api.polygon.io"
CALL_INTERVAL = 12.5  # seconds between calls to stay under 5/min on free tier
CACHE_SCHEMA_VERSION = 2

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
    key = _api_key()
    _throttle()
    params = params or {}
    params["apiKey"] = key
    resp = requests.get(f"{API_BASE}{path}", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _durable_coverage_end(fetch_to: str, market_now: Optional[datetime] = None) -> str:
    """Avoid treating an in-progress US trading day as permanently fetched."""
    market_timezone = ZoneInfo("America/New_York")
    market_now = market_now or datetime.now(market_timezone)
    if market_now.tzinfo is None:
        market_now = market_now.replace(tzinfo=market_timezone)
    else:
        market_now = market_now.astimezone(market_timezone)
    market_date = market_now.date()
    if fetch_to >= market_date.isoformat() and market_now.hour < 18:
        return (market_date - timedelta(days=1)).isoformat()
    return fetch_to


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

    schema_version = raw.pop("_schema_version", None)
    fetched_from = raw.pop("_fetched_from", None)
    fetched_through = raw.pop("_fetched_through", None)
    raw.pop("_date_timezone", None)

    # Version 1 used datetime.fromtimestamp(), which interpreted Polygon's
    # timestamp in the host timezone and shifted US market bars to the prior
    # calendar day on machines west of UTC. Never mix those bars with UTC data.
    if schema_version != CACHE_SCHEMA_VERSION:
        fetched_from = None
        fetched_through = None
        cached = {}
    else:
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

    successful_ranges = []
    for fetch_from, fetch_to in ranges_to_fetch:
        print(f"    Fetching {ticker} bars {fetch_from} → {fetch_to}...")
        path = f"/v2/aggs/ticker/{ticker}/range/1/day/{fetch_from}/{fetch_to}"
        try:
            data = _get(path, {"adjusted": "true", "sort": "asc", "limit": 5000})
            if data.get("status") == "ERROR" or data.get("error"):
                raise RuntimeError(data.get("error") or "Polygon returned an error response")
            durable_through = _durable_coverage_end(fetch_to)
            for bar in data.get("results", []):
                bar_date = datetime.fromtimestamp(
                    bar["t"] / 1000, tz=timezone.utc
                ).date().isoformat()
                if bar_date > durable_through:
                    continue
                if datetime.strptime(bar_date, "%Y-%m-%d").weekday() >= 5:
                    raise ValueError(f"Polygon returned an equity bar on weekend date {bar_date}")
                cached[bar_date] = {
                    "open": bar.get("o"),
                    "high": bar.get("h"),
                    "low": bar.get("l"),
                    "close": bar.get("c"),
                    "volume": bar.get("v"),
                    "vwap": bar.get("vw"),
                }
            # Do not claim durable coverage for the current calendar day. A
            # pre-close run may receive no daily aggregate (or an incomplete
            # one); the next run must be allowed to request that date again.
            if durable_through >= fetch_from:
                successful_ranges.append((fetch_from, durable_through))
        except Exception as e:
            print(f"    WARNING: could not fetch bars for {ticker}: {e}")

    if successful_ranges:
        # Each requested extension is adjacent to the existing continuous
        # coverage window. Preserve a successful left extension even if a
        # separate right extension fails (and vice versa); only the failed edge
        # remains eligible for retry.
        new_from = min([r[0] for r in successful_ranges] + ([fetched_from] if fetched_from else []))
        new_through = max([r[1] for r in successful_ranges] + ([fetched_through] if fetched_through else []))
        out = {
            "_schema_version": CACHE_SCHEMA_VERSION,
            "_date_timezone": "UTC",
        }
        if new_from is not None:
            out["_fetched_from"] = new_from
        if new_through is not None:
            out["_fetched_through"] = new_through
        out.update({k: cached[k] for k in sorted(cached)})
        temp_file = cache_file.with_suffix(".json.tmp")
        temp_file.write_text(json.dumps(out, indent=2))
        temp_file.replace(cache_file)

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


def get_execution_price(ticker: str, report_date: str, as_of: Optional[str] = None):
    """
    Returns (trade_date, price) for the first trading day on or after report_date.
    Reports are calculated from the prior day's close, so report_date itself is the
    first valid execution session. Uses VWAP when available, falls back to (open+close)/2.
    """
    today = as_of or date.today().strftime("%Y-%m-%d")
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
