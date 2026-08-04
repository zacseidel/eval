import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

SCRAPER_DIR = Path(__file__).resolve().parents[1] / "scraper"
sys.path.insert(0, str(SCRAPER_DIR))

import process


def report(report_date, munger=None, sp500=None):
    return {
        "date": report_date,
        "munger": munger or [],
        "sp500": sp500 or [],
        "megacap": [],
        "sp400": [],
    }


def munger_entry(ticker, rank=1, new=True):
    return {
        "ticker": ticker,
        "rank": rank,
        "entry_date": None,
        "new_entrant": new,
    }


class MungerLifecycleTests(unittest.TestCase):
    @staticmethod
    def _ema_exit_bars():
        bars = []
        current = date(2025, 12, 1)
        closes = {
            "2026-01-05": 105.0,
            "2026-01-06": 80.0,
            "2026-01-07": 79.0,
            "2026-01-08": 120.0,
        }
        while current <= date(2026, 1, 8):
            if current.weekday() < 5:
                day = current.isoformat()
                close = closes.get(day, 100.0)
                bars.append({
                    "date": day,
                    "open": close,
                    "close": close,
                    "vwap": 90.0 if day == "2026-01-07" else close,
                })
            current += timedelta(days=1)
        return bars

    def test_ema_signal_exits_next_session_and_later_report_reenters(self):
        positions = process._build_munger_ticker_positions(
            "AAA",
            ["2026-01-05", "2026-01-07", "2026-01-08"],
            self._ema_exit_bars(),
            "2026-01-08",
        )

        self.assertEqual(2, len(positions))
        closed = next(p for p in positions if p["status"] == "closed")
        opened = next(p for p in positions if p["status"] == "open")
        self.assertEqual("2026-01-06", closed["exit_signal_date"])
        self.assertEqual("2026-01-07", closed["exit_date"])
        self.assertLess(closed["exit_signal_close"], closed["exit_signal_ema_21"])
        # The January 7 report arrived while the prior trade was exit-pending,
        # so only the later January 8 report can open the new trade.
        self.assertEqual("2026-01-08", opened["signal_date"])

    def test_final_day_ema_signal_remains_open_until_next_session(self):
        positions = process._build_munger_ticker_positions(
            "AAA", ["2026-01-05"], self._ema_exit_bars(), "2026-01-06"
        )

        self.assertEqual(1, len(positions))
        self.assertEqual("open", positions[0]["status"])
        self.assertEqual("2026-01-06", positions[0]["exit_signal_date"])
        self.assertIsNone(positions[0]["exit_date"])
        process.validate_positions(positions, "2026-01-06")

    def test_signal_snapshots_use_report_ranks_not_market_data(self):
        reports = [report("2026-01-02", sp500=[
            {"ticker": "AAA", "rank": 1, "entry_date": "2026-01-02", "new_entrant": True},
            {"ticker": "BBB", "rank": 6, "entry_date": "2026-01-02", "new_entrant": True},
        ])]
        snapshots = process.build_signal_snapshots(reports)
        top = [s for s in snapshots if s["strategy"] == "sp500_top5"]
        next_five = [s for s in snapshots if s["strategy"] == "sp500_next5"]
        self.assertEqual(["AAA"], [s["ticker"] for s in top])
        self.assertEqual(["BBB"], [s["ticker"] for s in next_five])

    def test_rank_membership_alone_opens_and_closes_trade(self):
        reports = [
            report("2026-01-02", sp500=[
                {"ticker": "AAA", "rank": 1, "entry_date": None, "new_entrant": True},
            ]),
            report("2026-01-09", sp500=[
                {"ticker": "BBB", "rank": 1, "entry_date": None, "new_entrant": True},
            ]),
        ]

        def execution(ticker, signal_date, as_of):
            return signal_date, 100.0 if ticker == "AAA" else 120.0

        def mark(position, as_of):
            return {
                **position,
                "current_date": as_of,
                "current_price": 125.0,
                "hold_days": 0,
                "return_pct": 4.17,
            }

        with patch.object(process, "_execution_price", side_effect=execution), \
             patch.object(process, "_mark_open_position", side_effect=mark):
            positions = process._build_rank_positions(reports, "sp500_top5", "2026-01-09")

        aaa = next(p for p in positions if p["ticker"] == "AAA")
        bbb = next(p for p in positions if p["ticker"] == "BBB")
        self.assertEqual("closed", aaa["status"])
        self.assertEqual("2026-01-09", aaa["exit_signal_date"])
        self.assertEqual("open", bbb["status"])

    def test_august_4_report_fixture_has_11_signals_and_36_open_trades(self):
        reports = process.load_reports("2026-08-04")
        flat_bars = []
        current = date(2025, 9, 1)
        while current <= date(2026, 8, 4):
            if current.weekday() < 5:
                flat_bars.append({
                    "date": current.isoformat(),
                    "open": 100.0,
                    "close": 100.0,
                    "vwap": 100.0,
                })
            current += timedelta(days=1)

        with patch.object(process, "get_daily_bars", return_value=flat_bars):
            positions = process._build_munger_positions(reports, "2026-08-04")

        self.assertEqual(65, len(reports))
        self.assertEqual(11, len(reports[-1]["munger"]))
        self.assertEqual(36, len(positions))
        self.assertTrue(all(p["status"] == "open" for p in positions))


class PortfolioLedgerTests(unittest.TestCase):
    def setUp(self):
        self.reports = [report("2026-02-27")]
        self.spy_bars = [
            {"date": "2026-02-27", "close": 100.0},
            {"date": "2026-03-02", "close": 100.0},
            {"date": "2026-03-03", "close": 100.0},
        ]
        self.ticker_bars = [
            {"date": "2026-02-27", "open": 84.0, "close": 84.59, "vwap": 84.3},
            {"date": "2026-03-02", "open": 99.0, "close": 101.0, "vwap": 100.0},
            {"date": "2026-03-03", "open": 98.0, "close": 97.0, "vwap": 98.0},
        ]

    def test_no_pre_entry_return_is_credited(self):
        position = process._new_position("munger", "NFLX", "2026-02-27", "2026-03-02", 100.0)
        position.update({
            "current_date": "2026-03-03",
            "current_price": 97.0,
            "hold_days": 1,
            "return_pct": -3.0,
        })

        with patch.object(process, "get_daily_bars", return_value=self.ticker_bars):
            result = process.build_strategy_returns(
                self.reports, [position], self.spy_bars, "2026-03-03"
            )

        series = result["munger"]
        self.assertEqual(100.0, series[0]["value"])
        self.assertEqual(101.0, series[1]["value"])
        self.assertEqual(97.0, series[2]["value"])

    def test_exit_loss_flows_into_nav(self):
        position = process._new_position("munger", "NFLX", "2026-02-27", "2026-03-02", 100.0)
        position["exit_signal_close"] = 89.0
        position["exit_signal_ema_21"] = 95.0
        position = process._close_position(position, "2026-03-02", "2026-03-03", 90.0)

        with patch.object(process, "get_daily_bars", return_value=self.ticker_bars):
            result = process.build_strategy_returns(
                self.reports, [position], self.spy_bars, "2026-03-03"
            )

        self.assertEqual(90.0, result["munger"][-1]["value"])


class KellySizingTests(unittest.TestCase):
    @staticmethod
    def _closed_returns(values):
        return [
            {"strategy": "munger", "status": "closed", "return_pct": value}
            for value in values
        ]

    def test_half_kelly_uses_win_rate_and_average_payoff(self):
        positions = self._closed_returns([10.0, -5.0, 20.0, -10.0, 0.0])
        with patch.object(process, "KELLY_MIN_CLOSED_TRADES", 1):
            stats = process.compute_trade_stats(positions)["munger"]

        self.assertEqual(40.0, stats["winner_pct"])
        self.assertEqual(40.0, stats["loser_pct"])
        self.assertEqual(15.0, stats["average_win_pct"])
        self.assertEqual(-7.5, stats["average_loss_pct"])
        self.assertEqual(25.0, stats["full_kelly_pct"])
        self.assertEqual(12.5, stats["half_kelly_pct"])

    def test_negative_half_kelly_is_floored_at_zero(self):
        positions = self._closed_returns([5.0, -10.0])
        with patch.object(process, "KELLY_MIN_CLOSED_TRADES", 1):
            stats = process.compute_trade_stats(positions)["munger"]

        self.assertEqual(-50.0, stats["full_kelly_pct"])
        self.assertEqual(0.0, stats["half_kelly_pct"])

    def test_small_sample_does_not_publish_kelly_size(self):
        stats = process.compute_trade_stats(
            self._closed_returns([10.0, -5.0])
        )["munger"]

        self.assertEqual("insufficient_sample", stats["kelly_status"])
        self.assertIsNone(stats["half_kelly_pct"])


class ValidationTests(unittest.TestCase):
    def test_weekend_execution_is_rejected(self):
        position = process._new_position("munger", "AAA", "2026-03-06", "2026-03-08", 100.0)
        with self.assertRaisesRegex(ValueError, "Weekend entry"):
            process.validate_positions([position], "2026-03-10")


if __name__ == "__main__":
    unittest.main()
