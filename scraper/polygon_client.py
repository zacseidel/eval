"""Polygon.io API client with disk caching and rate limiting."""
import json
import math
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
CACHE_REFRESH_OVERLAP_DAYS = 30
# Version 3 forces one complete refresh of caches that may predate the
# retroactive-adjustment guard introduced below.
CACHE_SCHEMA_VERSION = 3

_last_call_time: float = 0.0
_refreshed_tickers: set[str] = set()


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
    if not resp.ok:
        detail = ""
        try:
            payload = resp.json()
            detail = payload.get("error") or payload.get("message") or ""
        except ValueError:
            detail = (resp.text or "")[:300]
        raise requests.HTTPError(
            f"{resp.status_code} {resp.reason} for {path}"
            + (f": {detail}" if detail else ""),
            response=resp,
        )
    return resp.json()


def _grouped_session_unavailable(
    exc: Optional[BaseException] = None, data: Optional[dict] = None
) -> bool:
    """True when Polygon has not published (or will not serve) that session yet.

    Stocks Basic is end-of-day. Requesting the current trading day's grouped
    daily bars commonly returns HTTP 403 rather than an empty 200.
    """
    if exc is not None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in (403, 404):
            return True
        return False
    if not data:
        return False
    status = data.get("status")
    if status in ("NOT_AUTHORIZED", "NOT_FOUND"):
        return True
    error = str(data.get("error") or data.get("message") or "").lower()
    return "not entitled" in error or "doesn't include this data timeframe" in error


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


def _response_bars(data: dict, durable_through: str) -> dict[str, dict]:
    """Convert an aggregate response into validated UTC-dated cache rows."""
    bars = {}
    for bar in data.get("results", []):
        bar_date = datetime.fromtimestamp(
            bar["t"] / 1000, tz=timezone.utc
        ).date().isoformat()
        if bar_date > durable_through:
            continue
        if datetime.strptime(bar_date, "%Y-%m-%d").weekday() >= 5:
            raise ValueError(f"Polygon returned an equity bar on weekend date {bar_date}")
        bars[bar_date] = {
            "open": bar.get("o"),
            "high": bar.get("h"),
            "low": bar.get("l"),
            "close": bar.get("c"),
            "volume": bar.get("v"),
            "vwap": bar.get("vw"),
        }
    return bars


def _adjusted_prices_changed(cached: dict, refreshed: dict) -> bool:
    """Detect retroactive price adjustments, most importantly stock splits."""
    price_fields = ("open", "high", "low", "close", "vwap")
    for bar_date in cached.keys() & refreshed.keys():
        old = cached[bar_date]
        new = refreshed[bar_date]
        for field in price_fields:
            old_value = old.get(field)
            new_value = new.get(field)
            if old_value is None or new_value is None:
                if old_value != new_value:
                    return True
                continue
            if not math.isclose(float(old_value), float(new_value), rel_tol=1e-9, abs_tol=1e-9):
                return True
    return False


def _load_valid_cache(ticker: str) -> Optional[dict]:
    cache_file = CACHE_DIR / f"{ticker}.json"
    if not cache_file.exists():
        return None
    raw = json.loads(cache_file.read_text())
    if (
        raw.get("_schema_version") != CACHE_SCHEMA_VERSION
        or not raw.get("_fetched_from")
        or not raw.get("_fetched_through")
    ):
        return None
    return raw


def _write_cache(ticker: str, raw: dict) -> None:
    cache_file = CACHE_DIR / f"{ticker}.json"
    temp_file = cache_file.with_suffix(".json.tmp")
    metadata = {
        key: raw[key]
        for key in ("_schema_version", "_date_timezone", "_fetched_from", "_fetched_through")
        if key in raw
    }
    bars = {key: raw[key] for key in sorted(raw) if not key.startswith("_")}
    temp_file.write_text(json.dumps({**metadata, **bars}, indent=2))
    temp_file.replace(cache_file)


def _grouped_bar(row: dict) -> dict:
    return {
        "open": row.get("o"),
        "high": row.get("h"),
        "low": row.get("l"),
        "close": row.get("c"),
        "volume": row.get("v"),
        "vwap": row.get("vw"),
    }


def _split_tickers(from_date: str, to_date: str, tracked: set[str]) -> set[str]:
    data = _get("/v3/reference/splits", {
        "execution_date.gte": from_date,
        "execution_date.lte": to_date,
        "limit": 1000,
        "sort": "execution_date",
    })
    if data.get("status") == "ERROR" or data.get("error"):
        raise RuntimeError(data.get("error") or "Polygon returned a split-query error")
    return {
        split["ticker"]
        for split in data.get("results", [])
        if split.get("ticker") in tracked
    }


def update_grouped_daily_bars(tickers, through_date: str) -> dict:
    """Extend caches with one market-wide request per missing weekday.

    Scraped reports remain the source of signals. This fills daily OHLCV marks
    for every tracked ticker that appears in those sessions, including names
    that have no cache yet. Per-ticker requests are reserved for split
    refreshes and for tickers that never appear in the grouped responses.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tracked = set(tickers)
    caches = {
        ticker: raw
        for ticker in sorted(tracked)
        if (raw := _load_valid_cache(ticker)) is not None
    }
    if not caches:
        return {"grouped_calls": 0, "split_refreshes": 0, "through": None}

    earliest_edge = min(raw["_fetched_through"] for raw in caches.values())
    next_date = datetime.strptime(earliest_edge, "%Y-%m-%d").date() + timedelta(days=1)
    requested_through = datetime.strptime(through_date, "%Y-%m-%d").date()
    weekdays = []
    current = next_date
    while current <= requested_through:
        if current.weekday() < 5:
            weekdays.append(current.isoformat())
        current += timedelta(days=1)

    if not weekdays:
        uncached = tracked - caches.keys()
        if uncached:
            # Caches are already current, but new names still need a market-wide
            # session to seed from. Re-read the latest published weekday rather
            # than issuing one history request per ticker.
            weekdays = [earliest_edge]
        else:
            _refreshed_tickers.update(caches)
            return {
                "grouped_calls": 0,
                "split_refreshes": 0,
                "through": earliest_edge,
            }

    reseeding_latest = weekdays == [earliest_edge]
    split_tickers = (
        set()
        if reseeding_latest
        else _split_tickers(weekdays[0], weekdays[-1], tracked)
    )
    grouped_rows = {}
    grouped_calls = 0
    for bar_date in weekdays:
        print(f"    Fetching grouped stock bars for {bar_date}...")
        grouped_calls += 1
        try:
            data = _get(
                f"/v2/aggs/grouped/locale/us/market/stocks/{bar_date}",
                {"adjusted": "true", "include_otc": "true"},
            )
        except requests.HTTPError as exc:
            if _grouped_session_unavailable(exc=exc):
                print(
                    f"    Grouped bars for {bar_date} not available yet "
                    f"(HTTP {exc.response.status_code}); "
                    "keeping the last published session."
                )
                break
            raise
        if _grouped_session_unavailable(data=data):
            print(
                f"    Grouped bars for {bar_date} not available yet; "
                "keeping the last published session."
            )
            break
        if data.get("status") == "ERROR" or data.get("error"):
            raise RuntimeError(data.get("error") or "Polygon returned a grouped-bars error")
        rows = {
            row["T"]: _grouped_bar(row)
            for row in data.get("results", [])
            if row.get("T") in tracked
        }
        if rows:
            grouped_rows[bar_date] = rows

    # An empty latest response commonly means the provider has not published
    # that session yet. HTTP 403/404 on a later weekday is the same case for
    # end-of-day plans. Only advance through the latest date that returned any
    # tracked market data; earlier empty weekdays are holidays.
    coverage_through = max(grouped_rows, default=None)
    if coverage_through is None:
        _refreshed_tickers.update(caches)
        return {
            "grouped_calls": grouped_calls,
            "split_refreshes": 0,
            "through": earliest_edge,
        }

    for ticker, raw in caches.items():
        changed = False
        for bar_date, rows in grouped_rows.items():
            if bar_date > raw["_fetched_through"] and ticker in rows:
                raw[bar_date] = rows[ticker]
                changed = True
        new_through = max(raw["_fetched_through"], coverage_through)
        if new_through != raw["_fetched_through"]:
            raw["_fetched_through"] = new_through
            changed = True
        if changed:
            _write_cache(ticker, raw)

    for ticker in sorted(tracked - caches.keys()):
        dates_with_bars = [
            bar_date for bar_date, rows in grouped_rows.items() if ticker in rows
        ]
        if not dates_with_bars:
            continue
        raw = {
            "_schema_version": CACHE_SCHEMA_VERSION,
            "_date_timezone": "UTC",
            "_fetched_from": min(dates_with_bars),
            "_fetched_through": coverage_through,
        }
        for bar_date, rows in grouped_rows.items():
            if ticker in rows:
                raw[bar_date] = rows[ticker]
        _write_cache(ticker, raw)
        caches[ticker] = raw

    _refreshed_tickers.update(caches)
    for ticker in sorted(split_tickers & caches.keys()):
        raw = caches[ticker]
        _refreshed_tickers.discard(ticker)
        get_daily_bars(
            ticker,
            raw["_fetched_from"],
            coverage_through,
        )

    return {
        "grouped_calls": grouped_calls,
        "split_refreshes": len(split_tickers & caches.keys()),
        "through": coverage_through,
    }


def get_daily_bars(ticker: str, from_date: str, to_date: str) -> list[dict]:
    """
    Returns list of daily OHLCV bars for ticker between from_date and to_date (YYYY-MM-DD).
    Caches to data/price_cache/{ticker}.json. Tracks _fetched_from/_fetched_through metadata
    and refreshes a short overlap once per process. If adjusted prices in that overlap changed,
    the complete cached range is fetched again so pre- and post-split scales cannot be mixed.
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
    cached_before_fetch = {bar_date: dict(bar) for bar_date, bar in cached.items()}

    # Determine which ranges (if any) still need to be fetched.
    # If no metadata exists (legacy cache or first fetch), fetch the full requested
    # range in one call — avoids splitting into multiple calls around sparse cached windows.
    ranges_to_fetch = []
    refresh_ranges = set()
    if fetched_from is None or fetched_through is None:
        fetch_range = (from_date, to_date)
        ranges_to_fetch.append(fetch_range)
        refresh_ranges.add(fetch_range)
    else:
        grouped_fresh = ticker in _refreshed_tickers
        if from_date < fetched_from and not grouped_fresh:
            day_before = (datetime.strptime(fetched_from, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            ranges_to_fetch.append((from_date, day_before))
        refresh_right_edge = not grouped_fresh and to_date >= fetched_through
        if to_date > fetched_through and not grouped_fresh:
            day_after = (datetime.strptime(fetched_through, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            fetch_from = day_after
            if refresh_right_edge:
                overlap_start = (
                    datetime.strptime(fetched_through, "%Y-%m-%d")
                    - timedelta(days=CACHE_REFRESH_OVERLAP_DAYS)
                ).strftime("%Y-%m-%d")
                fetch_from = max(fetched_from, overlap_start)
            fetch_range = (fetch_from, to_date)
            ranges_to_fetch.append(fetch_range)
            if refresh_right_edge:
                refresh_ranges.add(fetch_range)
        elif refresh_right_edge:
            overlap_start = (
                datetime.strptime(fetched_through, "%Y-%m-%d")
                - timedelta(days=CACHE_REFRESH_OVERLAP_DAYS)
            ).strftime("%Y-%m-%d")
            fetch_range = (max(fetched_from, overlap_start), to_date)
            ranges_to_fetch.append(fetch_range)
            refresh_ranges.add(fetch_range)

    successful_ranges = []
    for fetch_from, fetch_to in ranges_to_fetch:
        print(f"    Fetching {ticker} bars {fetch_from} → {fetch_to}...")
        path = f"/v2/aggs/ticker/{ticker}/range/1/day/{fetch_from}/{fetch_to}"
        try:
            data = _get(path, {"adjusted": "true", "sort": "asc", "limit": 5000})
            if data.get("status") == "ERROR" or data.get("error"):
                raise RuntimeError(data.get("error") or "Polygon returned an error response")
            durable_through = _durable_coverage_end(fetch_to)
            refreshed_bars = _response_bars(data, durable_through)
            is_refresh = (fetch_from, fetch_to) in refresh_ranges
            if is_refresh and _adjusted_prices_changed(cached, refreshed_bars):
                full_from = min(fetched_from, from_date)
                print(
                    f"    Adjusted {ticker} history changed; refreshing "
                    f"{full_from} → {fetch_to}..."
                )
                full_path = f"/v2/aggs/ticker/{ticker}/range/1/day/{full_from}/{fetch_to}"
                try:
                    full_data = _get(
                        full_path,
                        {"adjusted": "true", "sort": "asc", "limit": 5000},
                    )
                    if full_data.get("status") == "ERROR" or full_data.get("error"):
                        raise RuntimeError(
                            full_data.get("error") or "Polygon returned an error response"
                        )
                    cached = _response_bars(full_data, durable_through)
                except Exception:
                    # Never retain a successful extension in a potentially new
                    # adjustment scale when the full consistency refresh failed.
                    cached = cached_before_fetch
                    successful_ranges = []
                    raise
                successful_ranges = [(full_from, durable_through)]
            else:
                cached.update(refreshed_bars)
            # Do not claim durable coverage for the current calendar day. A
            # pre-close run may receive no daily aggregate (or an incomplete
            # one); the next run must be allowed to request that date again.
            if durable_through >= fetch_from:
                successful_ranges.append((fetch_from, durable_through))
                if is_refresh:
                    _refreshed_tickers.add(ticker)
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
