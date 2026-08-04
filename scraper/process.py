"""Build auditable trade ledgers and portfolio returns from scraped reports.

Reports are the only source of entries and security selection. Rank-model exits
also come from report membership; market data supplies the Munger EMA exit,
execution prices, and daily valuation.
"""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from polygon_client import CACHE_SCHEMA_VERSION, get_daily_bars, get_execution_price

SCRAPED_DIR = Path(__file__).parent.parent / "data" / "scraped"
PROCESSED_DIR = Path(__file__).parent.parent / "data" / "processed"
load_dotenv(Path(__file__).parent.parent / ".env")

MUNGER_EMA_SPAN = 21
MUNGER_EMA_LOOKBACK_DAYS = 120
SMA_EXIT_WINDOW = 10
SMA_EXIT_LOOKBACK_DAYS = 30
SMA10_SUFFIX = "_sma10"
KELLY_MIN_CLOSED_TRADES = 20
RISK_FREE_RATE_ANNUAL = 0.05
PROCESSOR_VERSION = 5

# Every selection below is made only from ranks or membership in a scraped
# report. The legacy IDs are retained so existing links keep working.
BASE_STRATEGIES = {
    "sp500_top5":       {"section": "sp500",   "ranks": range(1, 6)},
    "sp500_next5":      {"section": "sp500",   "ranks": range(6, 11)},
    "megacap_top5":     {"section": "megacap", "ranks": range(1, 6)},
    "megacap_next5":    {"section": "megacap", "ranks": range(6, 11)},
    "sp400_mcap5":      {"section": "sp400",   "ranks": range(1, 6)},
    "sp400_mcap_next5": {"section": "sp400",   "ranks": range(6, 11)},
    "munger":           {"section": "munger",  "ranks": None},
}
STRATEGIES = dict(BASE_STRATEGIES)
STRATEGIES.update({
    f"{strategy_id}{SMA10_SUFFIX}": {
        **config,
        "variant_of": strategy_id,
        "exit_model": "sma10",
    }
    for strategy_id, config in BASE_STRATEGIES.items()
})


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _atomic_write_json(path: Path, value) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(value, indent=2) + "\n")
    temp_path.replace(path)


def load_reports(as_of: Optional[str] = None) -> list[dict]:
    reports = []
    for path in sorted(SCRAPED_DIR.glob("*.json")):
        try:
            report = json.loads(path.read_text())
        except Exception as exc:
            raise RuntimeError(f"Could not load {path.name}: {exc}") from exc
        if as_of is None or report["date"] <= as_of:
            reports.append(report)
    reports.sort(key=lambda report: report["date"])
    return reports


def _selected_entries(report: dict, strategy_id: str) -> list[dict]:
    cfg = STRATEGIES[strategy_id]
    entries = report.get(cfg["section"], [])
    if cfg["ranks"] is None:
        return sorted(entries, key=lambda entry: (entry.get("rank", 999), entry["ticker"]))
    return sorted(
        (entry for entry in entries if entry.get("rank") in cfg["ranks"]),
        key=lambda entry: (entry.get("rank", 999), entry["ticker"]),
    )


def build_signal_snapshots(reports: list[dict]) -> list[dict]:
    """Persist every selected report row so positions can be audited to inputs."""
    snapshots = []
    previous = {strategy_id: set() for strategy_id in STRATEGIES}
    for report in reports:
        for strategy_id in STRATEGIES:
            entries = _selected_entries(report, strategy_id)
            current = {entry["ticker"] for entry in entries}
            for entry in entries:
                ticker = entry["ticker"]
                snapshots.append({
                    "strategy": strategy_id,
                    "ticker": ticker,
                    "report_date": report["date"],
                    "signal_state": "new" if ticker not in previous[strategy_id] else "continuing",
                    "source_entry_date": entry.get("entry_date"),
                    "source_new_entrant": bool(entry.get("new_entrant")),
                    "rank": entry.get("rank"),
                    "source_price": entry.get("price"),
                    "source_sma_200": entry.get("sma_200"),
                    "source_return_12m": entry.get("return_12m"),
                    "source_return_1w": entry.get("return_1w"),
                })
            previous[strategy_id] = current
    return snapshots


def _execution_price(ticker: str, signal_date: str, as_of: str) -> tuple[str, float]:
    execution_date, price = get_execution_price(ticker, signal_date, as_of=as_of)
    if execution_date is None or price is None or price <= 0:
        raise RuntimeError(f"Missing execution price for {ticker} on/after {signal_date}")
    return execution_date, float(price)


def _last_bar(ticker: str, start: str, as_of: str) -> dict:
    bars = get_daily_bars(ticker, start, as_of)
    if not bars:
        raise RuntimeError(f"Missing valuation bars for {ticker} from {start} through {as_of}")
    return bars[-1]


def _new_position(strategy_id: str, ticker: str, signal_date: str,
                  execution_date: str, entry_price: float) -> dict:
    return {
        "trade_id": f"{strategy_id}:{ticker}:{signal_date}",
        "strategy": strategy_id,
        "ticker": ticker,
        "signal_date": signal_date,
        "entry_date": execution_date,
        "entry_price": round(entry_price, 4),
        "exit_signal_date": None,
        "exit_signal_close": None,
        "exit_signal_ema_21": None,
        "exit_signal_sma_10": None,
        "exit_date": None,
        "exit_price": None,
        "current_date": None,
        "current_price": None,
        "hold_days": None,
        "return_pct": None,
        "status": "open",
    }


def _close_position(position: dict, exit_signal_date: str,
                    exit_date: str, exit_price: float) -> dict:
    entry_price = position["entry_price"]
    return {
        **position,
        "exit_signal_date": exit_signal_date,
        "exit_date": exit_date,
        "exit_price": round(exit_price, 4),
        "current_date": None,
        "current_price": None,
        "hold_days": (_parse_date(exit_date) - _parse_date(position["entry_date"])).days,
        "return_pct": round((exit_price / entry_price - 1) * 100, 2),
        "status": "closed",
    }


def _mark_open_position_from_bar(position: dict, bar: dict) -> dict:
    current_price = float(bar["close"])
    return {
        **position,
        "current_date": bar["date"],
        "current_price": current_price,
        "hold_days": (_parse_date(bar["date"]) - _parse_date(position["entry_date"])).days,
        "return_pct": round((current_price / position["entry_price"] - 1) * 100, 2),
    }


def _mark_open_position(position: dict, as_of: str) -> dict:
    bar = _last_bar(position["ticker"], position["entry_date"], as_of)
    return _mark_open_position_from_bar(position, bar)


def _build_rank_positions(reports: list[dict], strategy_id: str, as_of: str) -> list[dict]:
    positions = []
    open_positions: dict[str, dict] = {}

    for report in reports:
        report_date = report["date"]
        selected = {entry["ticker"] for entry in _selected_entries(report, strategy_id)}

        # Membership in the next report is the sole exit signal.
        for ticker in sorted(set(open_positions) - selected):
            position = open_positions.pop(ticker)
            exit_date, exit_price = _execution_price(ticker, report_date, as_of)
            positions.append(_close_position(position, report_date, exit_date, exit_price))

        # Membership while flat is the sole entry signal.
        for ticker in sorted(selected - set(open_positions)):
            entry_date, entry_price = _execution_price(ticker, report_date, as_of)
            open_positions[ticker] = _new_position(
                strategy_id, ticker, report_date, entry_date, entry_price
            )

    positions.extend(
        _mark_open_position(open_positions[ticker], as_of)
        for ticker in sorted(open_positions)
    )
    return positions


def _ema_by_date(bars: list[dict], span: int = MUNGER_EMA_SPAN) -> dict[str, float]:
    """Return a close-based EMA only after at least ``span`` observations."""
    alpha = 2 / (span + 1)
    ema = None
    values = {}
    for index, bar in enumerate(bars, start=1):
        close = float(bar["close"])
        ema = close if ema is None else alpha * close + (1 - alpha) * ema
        if index >= span:
            values[bar["date"]] = ema
    return values


def _sma_by_date(bars: list[dict], window: int = SMA_EXIT_WINDOW) -> dict[str, float]:
    """Return a trailing close-based simple moving average."""
    closes = []
    values = {}
    for bar in bars:
        closes.append(float(bar["close"]))
        if len(closes) > window:
            closes.pop(0)
        if len(closes) == window:
            values[bar["date"]] = sum(closes) / window
    return values


def _build_price_exit_ticker_positions(strategy_id: str, ticker: str,
                                       signal_dates: list[str], bars: list[dict],
                                       as_of: str, exit_levels: dict[str, float],
                                       exit_level_field: str) -> list[dict]:
    """Simulate report entries and next-session price-indicator exits."""
    positions = []
    open_position = None
    queued_signal = None
    pending_exit = None
    signals_by_date = set(signal_dates)
    bars_by_date = {bar["date"]: bar for bar in bars}
    timeline = sorted(signals_by_date | set(bars_by_date))

    for event_date in timeline:
        if event_date > as_of:
            break

        # Reports are available before the trading session. Only a ticker that
        # is flat at signal time can queue an entry. A pending technical exit
        # still counts as open and cannot create a same-session round trip.
        if event_date in signals_by_date:
            if open_position is None and pending_exit is None and queued_signal is None:
                queued_signal = event_date

        bar = bars_by_date.get(event_date)
        if bar is None:
            continue

        if pending_exit is not None:
            exit_price = _bar_execution_price(bar)
            positions.append(_close_position(
                open_position,
                pending_exit["date"],
                event_date,
                exit_price,
            ))
            open_position = None
            pending_exit = None

        if queued_signal is not None and open_position is None:
            open_position = _new_position(
                strategy_id, ticker, queued_signal, event_date, _bar_execution_price(bar)
            )
            queued_signal = None

        exit_level = exit_levels.get(event_date)
        close = float(bar["close"])
        if open_position is not None and exit_level is not None and close < exit_level:
            open_position["exit_signal_date"] = event_date
            open_position["exit_signal_close"] = round(close, 4)
            open_position[exit_level_field] = round(exit_level, 4)
            pending_exit = {"date": event_date}

    if open_position is not None:
        current_bar = next(
            bar for bar in reversed(bars)
            if open_position["entry_date"] <= bar["date"] <= as_of
        )
        positions.append(_mark_open_position_from_bar(open_position, current_bar))
    return positions


def _build_munger_ticker_positions(ticker: str, signal_dates: list[str],
                                    bars: list[dict], as_of: str) -> list[dict]:
    """Simulate report entries and next-session EMA exits for one ticker."""
    return _build_price_exit_ticker_positions(
        "munger",
        ticker,
        signal_dates,
        bars,
        as_of,
        _ema_by_date(bars),
        "exit_signal_ema_21",
    )


def _build_munger_positions(reports: list[dict], as_of: str) -> list[dict]:
    """Buy from report membership; exit after a close below the 21-day EMA."""
    first_date = _parse_date(reports[0]["date"])
    ema_start = (first_date - timedelta(days=MUNGER_EMA_LOOKBACK_DAYS)).isoformat()
    signal_dates_by_ticker = defaultdict(list)
    for report in reports:
        for entry in _selected_entries(report, "munger"):
            signal_dates_by_ticker[entry["ticker"]].append(report["date"])

    positions = []
    for ticker in sorted(signal_dates_by_ticker):
        bars = get_daily_bars(ticker, ema_start, as_of)
        positions.extend(_build_munger_ticker_positions(
            ticker, signal_dates_by_ticker[ticker], bars, as_of
        ))
    return positions


def _build_sma10_positions(reports: list[dict], strategy_id: str,
                           as_of: str) -> list[dict]:
    """Buy from the base report signal; exit after a close below SMA10."""
    first_date = _parse_date(reports[0]["date"])
    sma_start = (first_date - timedelta(days=SMA_EXIT_LOOKBACK_DAYS)).isoformat()
    signal_dates_by_ticker = defaultdict(list)
    for report in reports:
        for entry in _selected_entries(report, strategy_id):
            signal_dates_by_ticker[entry["ticker"]].append(report["date"])

    positions = []
    for ticker in sorted(signal_dates_by_ticker):
        bars = get_daily_bars(ticker, sma_start, as_of)
        positions.extend(_build_price_exit_ticker_positions(
            strategy_id,
            ticker,
            signal_dates_by_ticker[ticker],
            bars,
            as_of,
            _sma_by_date(bars),
            "exit_signal_sma_10",
        ))
    return positions


def build_positions(reports: list[dict], as_of: str) -> list[dict]:
    positions = []
    for strategy_id in STRATEGIES:
        if strategy_id.endswith(SMA10_SUFFIX):
            positions.extend(_build_sma10_positions(reports, strategy_id, as_of))
        elif strategy_id == "munger":
            positions.extend(_build_munger_positions(reports, as_of))
        else:
            positions.extend(_build_rank_positions(reports, strategy_id, as_of))
    positions.sort(key=lambda p: (p["strategy"], p["entry_date"], p["ticker"]))
    return positions


def validate_positions(positions: list[dict], as_of: str,
                       signals: Optional[list[dict]] = None) -> None:
    seen_ids = set()
    signal_keys = {
        (signal["strategy"], signal["ticker"], signal["report_date"])
        for signal in (signals or [])
    }
    by_ticker = defaultdict(list)
    for position in positions:
        trade_id = position["trade_id"]
        if trade_id in seen_ids:
            raise ValueError(f"Duplicate trade id: {trade_id}")
        seen_ids.add(trade_id)
        by_ticker[(position["strategy"], position["ticker"])].append(position)
        if signals is not None and (
            position["strategy"], position["ticker"], position["signal_date"]
        ) not in signal_keys:
            raise ValueError(f"Trade has no scraped report signal: {trade_id}")
        if position["entry_price"] <= 0:
            raise ValueError(f"Invalid entry price: {trade_id}")
        if _parse_date(position["entry_date"]).weekday() >= 5:
            raise ValueError(f"Weekend entry date: {trade_id} {position['entry_date']}")
        if position["entry_date"] > as_of:
            raise ValueError(f"Future entry date: {trade_id}")
        technical_level_field = None
        if position["strategy"] == "munger":
            technical_level_field = "exit_signal_ema_21"
        elif position["strategy"].endswith(SMA10_SUFFIX):
            technical_level_field = "exit_signal_sma_10"
        if position["status"] == "closed":
            if position["exit_price"] is None or position["exit_price"] <= 0:
                raise ValueError(f"Invalid exit price: {trade_id}")
            if _parse_date(position["exit_date"]).weekday() >= 5:
                raise ValueError(f"Weekend exit date: {trade_id} {position['exit_date']}")
            if position["exit_date"] < position["entry_date"]:
                raise ValueError(f"Exit precedes entry: {trade_id}")
            if technical_level_field:
                if not position["exit_signal_date"]:
                    raise ValueError(f"Technical exit has no signal: {trade_id}")
                if position["exit_signal_date"] >= position["exit_date"]:
                    raise ValueError(f"Technical exit is not after its signal: {trade_id}")
                if not position["exit_signal_close"] < position[technical_level_field]:
                    raise ValueError(f"Invalid technical exit signal: {trade_id}")
        elif technical_level_field and position["exit_signal_date"]:
            if not position["exit_signal_close"] < position[technical_level_field]:
                raise ValueError(f"Invalid pending technical exit signal: {trade_id}")
            if position["exit_signal_date"] > position["current_date"]:
                raise ValueError(f"Future pending technical exit signal: {trade_id}")

    for key, ticker_positions in by_ticker.items():
        ordered = sorted(ticker_positions, key=lambda p: p["entry_date"])
        for previous, current in zip(ordered, ordered[1:]):
            if previous["exit_date"] is None or previous["exit_date"] > current["entry_date"]:
                raise ValueError(f"Overlapping trades for {key}: {previous['trade_id']}, {current['trade_id']}")


def compute_trade_stats(positions: list[dict]) -> dict:
    """Summarize closed outcomes and estimate a long-only half-Kelly risk fraction."""
    stats = {}
    for strategy_id in STRATEGIES:
        closed = [
            position for position in positions
            if position["strategy"] == strategy_id
            and position["status"] == "closed"
            and position["return_pct"] is not None
        ]
        winners = [position["return_pct"] for position in closed if position["return_pct"] > 0]
        losers = [position["return_pct"] for position in closed if position["return_pct"] < 0]
        breakeven_count = len(closed) - len(winners) - len(losers)
        decided_count = len(winners) + len(losers)
        average_win = sum(winners) / len(winners) if winners else None
        average_loss = sum(losers) / len(losers) if losers else None
        payoff_ratio = (
            average_win / abs(average_loss)
            if average_win is not None and average_loss is not None and average_loss != 0
            else None
        )

        full_kelly = None
        half_kelly = None
        if (
            len(closed) >= KELLY_MIN_CLOSED_TRADES
            and decided_count > 0
            and payoff_ratio is not None
            and payoff_ratio > 0
        ):
            win_probability = len(winners) / decided_count
            loss_probability = len(losers) / decided_count
            full_kelly = win_probability - loss_probability / payoff_ratio
            half_kelly = max(0.0, full_kelly / 2)

        if len(closed) < KELLY_MIN_CLOSED_TRADES:
            kelly_status = "insufficient_sample"
        elif payoff_ratio is None or decided_count == 0:
            kelly_status = "needs_winners_and_losers"
        else:
            kelly_status = "calculated"

        stats[strategy_id] = {
            "closed_count": len(closed),
            "winner_count": len(winners),
            "loser_count": len(losers),
            "breakeven_count": breakeven_count,
            "winner_pct": round(len(winners) / len(closed) * 100, 2) if closed else None,
            "loser_pct": round(len(losers) / len(closed) * 100, 2) if closed else None,
            "average_win_pct": round(average_win, 2) if average_win is not None else None,
            "average_loss_pct": round(average_loss, 2) if average_loss is not None else None,
            "payoff_ratio": round(payoff_ratio, 4) if payoff_ratio is not None else None,
            "full_kelly_pct": round(full_kelly * 100, 2) if full_kelly is not None else None,
            "half_kelly_pct": round(half_kelly * 100, 2) if half_kelly is not None else None,
            "minimum_closed_trades": KELLY_MIN_CLOSED_TRADES,
            "minimum_sample_met": len(closed) >= KELLY_MIN_CLOSED_TRADES,
            "kelly_status": kelly_status,
        }
    return stats


def prefetch_all_tickers(reports: list[dict], as_of: str,
                         ticker_offset: int = 0,
                         ticker_limit: Optional[int] = None) -> None:
    if not reports:
        return
    first_date = reports[0]["date"]
    ticker_start = (_parse_date(first_date) - timedelta(days=30)).isoformat()
    munger_start = (
        _parse_date(first_date) - timedelta(days=MUNGER_EMA_LOOKBACK_DAYS)
    ).isoformat()
    spy_start = (_parse_date(first_date) - timedelta(days=396)).isoformat()
    tickers = sorted({
        entry["ticker"]
        for report in reports
        for section in ("sp500", "megacap", "sp400", "munger")
        for entry in report.get(section, [])
        if entry.get("ticker")
    })
    munger_tickers = {
        entry["ticker"]
        for report in reports
        for entry in report.get("munger", [])
        if entry.get("ticker")
    }

    print(f"  Prefetching SPY bars {spy_start} → {as_of}...")
    get_daily_bars("SPY", spy_start, as_of)
    selected_tickers = tickers[ticker_offset:]
    if ticker_limit is not None:
        selected_tickers = selected_tickers[:ticker_limit]
    print(
        f"  Prefetching bars for {len(selected_tickers)} of {len(tickers)} signal tickers "
        f"through {as_of}..."
    )
    for ticker in selected_tickers:
        start = munger_start if ticker in munger_tickers else ticker_start
        get_daily_bars(ticker, start, as_of)


def _bar_execution_price(bar: dict) -> float:
    if bar.get("vwap"):
        return float(bar["vwap"])
    if bar.get("open") and bar.get("close"):
        return (float(bar["open"]) + float(bar["close"])) / 2
    if bar.get("close"):
        return float(bar["close"])
    raise ValueError(f"Bar has no usable price: {bar}")


def _add_return_windows(series: list[dict]) -> None:
    for index, point in enumerate(series):
        current_date = _parse_date(point["date"])
        for days, field, benchmark_field in (
            (91, "rolling_3m", "spy_rolling_3m"),
            (365, "return_12m", "spy_12m"),
        ):
            cutoff = (current_date - timedelta(days=days)).isoformat()
            past = next(
                (series[j] for j in range(index - 1, -1, -1) if series[j]["date"] <= cutoff),
                None,
            )
            if past is None:
                point[field] = None
                point[benchmark_field] = None
                continue
            point[field] = round((point["value"] / past["value"] - 1) * 100, 2)
            point[benchmark_field] = round(
                (point["spy_value"] / past["spy_value"] - 1) * 100, 2
            )


def build_strategy_returns(reports: list[dict], positions: list[dict],
                           spy_bars: list[dict], as_of: str) -> dict:
    """Simulate an equal-weight portfolio rebalanced only on trade-event days."""
    if not reports or not spy_bars:
        return {}
    first_report_date = reports[0]["date"]
    trading_dates = [
        bar["date"] for bar in spy_bars
        if first_report_date <= bar["date"] <= as_of
    ]
    if not trading_dates:
        return {}

    all_tickers = sorted({position["ticker"] for position in positions})
    bar_maps = {
        ticker: {
            bar["date"]: bar
            for bar in get_daily_bars(ticker, first_report_date, as_of)
        }
        for ticker in all_tickers
    }
    spy_map = {bar["date"]: bar for bar in spy_bars}
    spy_base = float(spy_map[trading_dates[0]]["close"])
    result = {}

    for strategy_id in STRATEGIES:
        strategy_positions = [p for p in positions if p["strategy"] == strategy_id]
        entries_by_date = defaultdict(list)
        exits_by_date = defaultdict(list)
        for position in strategy_positions:
            entries_by_date[position["entry_date"]].append(position)
            if position["exit_date"]:
                exits_by_date[position["exit_date"]].append(position)

        cash = 100.0
        holdings: dict[str, float] = {}
        last_closes: dict[str, float] = {}
        series = []

        for trading_date in trading_dates:
            for ticker in all_tickers:
                bar = bar_maps[ticker].get(trading_date)
                if bar and bar.get("close"):
                    last_closes[ticker] = float(bar["close"])

            day_entries = entries_by_date.get(trading_date, [])
            day_exits = exits_by_date.get(trading_date, [])
            if day_entries or day_exits:
                for position in day_exits:
                    shares = holdings.pop(position["ticker"], 0.0)
                    cash += shares * float(position["exit_price"])

                active_tickers = {
                    p["ticker"] for p in strategy_positions
                    if p["entry_date"] <= trading_date
                    and (p["exit_date"] is None or p["exit_date"] > trading_date)
                }
                execution_prices = {}
                for ticker in active_tickers:
                    bar = bar_maps[ticker].get(trading_date)
                    if bar is None:
                        raise RuntimeError(
                            f"Missing {ticker} bar needed to rebalance {strategy_id} on {trading_date}"
                        )
                    execution_prices[ticker] = _bar_execution_price(bar)

                nav_at_execution = cash + sum(
                    shares * execution_prices[ticker]
                    for ticker, shares in holdings.items()
                )
                if active_tickers:
                    target_value = nav_at_execution / len(active_tickers)
                    holdings = {
                        ticker: target_value / execution_prices[ticker]
                        for ticker in sorted(active_tickers)
                    }
                    cash = 0.0
                else:
                    holdings = {}
                    cash = nav_at_execution

            missing_marks = [ticker for ticker in holdings if ticker not in last_closes]
            if missing_marks:
                raise RuntimeError(
                    f"Missing close marks for {strategy_id} on {trading_date}: {missing_marks}"
                )
            nav = cash + sum(
                shares * last_closes[ticker] for ticker, shares in holdings.items()
            )
            spy_value = float(spy_map[trading_date]["close"]) / spy_base * 100
            series.append({
                "date": trading_date,
                "value": round(nav, 4),
                "spy_value": round(spy_value, 4),
                "rolling_3m": None,
                "spy_rolling_3m": None,
                "return_12m": None,
                "spy_12m": None,
            })

        _add_return_windows(series)
        result[strategy_id] = series

    spy_series = [{
        "date": trading_date,
        "value": round(float(spy_map[trading_date]["close"]) / spy_base * 100, 4),
        "spy_value": round(float(spy_map[trading_date]["close"]) / spy_base * 100, 4),
        "rolling_3m": None,
        "spy_rolling_3m": None,
        "return_12m": None,
        "spy_12m": None,
    } for trading_date in trading_dates]
    _add_return_windows(spy_series)
    result["spy"] = spy_series
    return result


def _sharpe_from_series(series: list[dict], cutoff: str, min_returns: int) -> Optional[float]:
    values = [point["value"] for point in series if point["date"] >= cutoff]
    returns = [
        math.log(values[index] / values[index - 1])
        for index in range(1, len(values))
        if values[index] > 0 and values[index - 1] > 0
    ]
    if len(returns) < min_returns:
        return None
    daily_rfr = math.log1p(RISK_FREE_RATE_ANNUAL) / 252
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    if variance <= 0:
        return None
    return round((mean_return - daily_rfr) / math.sqrt(variance) * math.sqrt(252), 2)


def compute_sharpe(strategy_returns: dict, as_of: str) -> dict:
    as_of_date = _parse_date(as_of)
    cutoff_12m = (as_of_date - timedelta(days=365)).isoformat()
    cutoff_3m = (as_of_date - timedelta(days=91)).isoformat()
    return {
        strategy_id: {
            "12m": _sharpe_from_series(series, cutoff_12m, 200),
            "3m": _sharpe_from_series(series, cutoff_3m, 40),
        }
        for strategy_id, series in strategy_returns.items()
        if isinstance(series, list)
    }


def _source_manifest(reports: list[dict], as_of: str,
                     positions: list[dict], signals: list[dict],
                     market_data_through: str) -> dict:
    report_sources = []
    included_dates = {report["date"] for report in reports}
    for path in sorted(SCRAPED_DIR.glob("*.json")):
        if path.stem not in included_dates:
            continue
        report_sources.append({
            "date": path.stem,
            "path": str(path.relative_to(SCRAPED_DIR.parent.parent)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return {
        "processor_version": PROCESSOR_VERSION,
        "price_cache_schema_version": CACHE_SCHEMA_VERSION,
        "as_of": as_of,
        "latest_report_date": reports[-1]["date"],
        "market_data_through": market_data_through,
        "entry_signal_source": "scraped_reports_only",
        "rank_exit_signal_source": "scraped_report_membership_only",
        "execution_price": "report entries on first available session; technical exits on next session; VWAP with midpoint fallback",
        "portfolio_policy": "equal weight, rebalanced on trade-event dates",
        "munger": {
            "entry": "qualifying scraped report membership while flat",
            "exit_signal": "daily adjusted close below close-based 21-day EMA",
            "exit_execution": "next available trading session after the signal",
            "ema_span": MUNGER_EMA_SPAN,
            "ema_lookback_calendar_days": MUNGER_EMA_LOOKBACK_DAYS,
        },
        "sma10_variants": {
            "strategy_suffix": SMA10_SUFFIX,
            "entry": "same scraped report membership as the corresponding base strategy while flat",
            "report_disappearance_exit": False,
            "exit_signal": "daily adjusted close below trailing close-based 10-session SMA",
            "exit_execution": "next available trading session after the signal",
            "sma_window": SMA_EXIT_WINDOW,
            "lookback_calendar_days": SMA_EXIT_LOOKBACK_DAYS,
            "reentry": "a later qualifying report while flat opens a new trade",
        },
        "strategy_families": {
            "base": list(BASE_STRATEGIES),
            "sma10": [f"{strategy_id}{SMA10_SUFFIX}" for strategy_id in BASE_STRATEGIES],
        },
        "kelly": {
            "formula": "0.5 * max(0, p - q / b)",
            "p_q_denominator": "closed non-breakeven trades",
            "b": "average winner / absolute average loser",
            "display_percentages_denominator": "all closed trades",
            "minimum_closed_trades": KELLY_MIN_CLOSED_TRADES,
            "scope": "historical long-only capital-at-risk estimate",
        },
        "strategy_trade_stats": compute_trade_stats(positions),
        "report_count": len(reports),
        "signal_snapshot_count": len(signals),
        "position_count": len(positions),
        "positions_by_strategy": {
            strategy_id: {
                "open": sum(
                    p["strategy"] == strategy_id and p["status"] == "open"
                    for p in positions
                ),
                "closed": sum(
                    p["strategy"] == strategy_id and p["status"] == "closed"
                    for p in positions
                ),
            }
            for strategy_id in STRATEGIES
        },
        "reports": report_sources,
    }


def process_all(as_of: Optional[str] = None, prefetch_only: bool = False,
                ticker_offset: int = 0,
                ticker_limit: Optional[int] = None) -> None:
    as_of = as_of or date.today().isoformat()
    _parse_date(as_of)  # validate before doing network or filesystem work
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading scraped reports through {as_of}...")
    reports = load_reports(as_of)
    if not reports:
        raise RuntimeError("No scraped reports available")
    print(f"  {len(reports)} reports loaded.")

    print("Prefetching execution and valuation bars...")
    prefetch_all_tickers(reports, as_of, ticker_offset, ticker_limit)
    if prefetch_only:
        return

    print("Building report signal snapshots...")
    signals = build_signal_snapshots(reports)
    print(f"  {len(signals)} signal snapshots.")

    print("Building trade positions...")
    positions = build_positions(reports, as_of)
    validate_positions(positions, as_of, signals)
    print(f"  {len(positions)} validated positions.")

    first_date = reports[0]["date"]
    spy_start = (_parse_date(first_date) - timedelta(days=396)).isoformat()
    spy_bars = get_daily_bars("SPY", spy_start, as_of)
    print("Building transaction-ledger portfolio series...")
    strategy_returns = build_strategy_returns(reports, positions, spy_bars, as_of)
    strategy_returns["_sharpe"] = compute_sharpe(strategy_returns, as_of)

    manifest = _source_manifest(reports, as_of, positions, signals, spy_bars[-1]["date"])
    _atomic_write_json(PROCESSED_DIR / "signals.json", signals)
    _atomic_write_json(PROCESSED_DIR / "positions.json", positions)
    _atomic_write_json(PROCESSED_DIR / "strategy_returns.json", strategy_returns)
    _atomic_write_json(PROCESSED_DIR / "manifest.json", manifest)
    print(f"  Wrote audited outputs to {PROCESSED_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", help="Deterministic processing cutoff (YYYY-MM-DD)")
    parser.add_argument("--prefetch-only", action="store_true")
    parser.add_argument("--ticker-offset", type=int, default=0)
    parser.add_argument("--ticker-limit", type=int)
    arguments = parser.parse_args()
    process_all(
        arguments.as_of,
        prefetch_only=arguments.prefetch_only,
        ticker_offset=arguments.ticker_offset,
        ticker_limit=arguments.ticker_limit,
    )
