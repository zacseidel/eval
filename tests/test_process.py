import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

SCRAPER_DIR = Path(__file__).resolve().parents[1] / "scraper"
sys.path.insert(0, str(SCRAPER_DIR))

import process


def report(report_date, munger=None, sp500=None, munger400l=None, munger400r=None,
           industry_rank_up=None, industry_rank_down=None, megalaggards=None):
    result = {
        "date": report_date,
        "munger": munger or [],
        "sp500": sp500 or [],
        "megacap": [],
        "sp400": [],
    }
    if munger400l is not None:
        result["munger400l"] = munger400l
    if munger400r is not None:
        result["munger400r"] = munger400r
    if megalaggards is not None:
        result["megalaggards"] = megalaggards
    if industry_rank_up is not None:
        result["industry_rank_up"] = industry_rank_up
    if industry_rank_down is not None:
        result["industry_rank_down"] = industry_rank_down
    return result


def rank_change(ticker, rank, change):
    return {
        "ticker": ticker,
        "rank": rank,
        "rank_change": change,
        "return_12m": 10.0,
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

    def test_munger400_models_use_the_same_ema21_lifecycle(self):
        positions = process._build_ema21_ticker_positions(
            "munger400r",
            "AAA",
            ["2026-01-05"],
            self._ema_exit_bars(),
            "2026-01-07",
        )

        self.assertEqual(1, len(positions))
        self.assertEqual("munger400r", positions[0]["strategy"])
        self.assertEqual("2026-01-06", positions[0]["exit_signal_date"])
        self.assertEqual("2026-01-07", positions[0]["exit_date"])
        process.validate_positions(positions, "2026-01-07")

    def test_close_can_be_above_sma10_and_below_ema21_on_entry_session(self):
        bars = []
        current = date(2025, 11, 3)
        while current <= date(2025, 12, 29):
            if current.weekday() < 5:
                bars.append({
                    "date": current.isoformat(),
                    "open": 110.0,
                    "close": 110.0,
                    "vwap": 110.0,
                })
            current += timedelta(days=1)
        for bar in bars[-9:]:
            bar.update({"open": 90.0, "close": 90.0, "vwap": 90.0})
        bars.append({
            "date": "2025-12-30",
            "open": 95.0,
            "close": 95.0,
            "vwap": 95.0,
        })

        sma10 = process._sma_by_date(bars)["2025-12-30"]
        ema21 = process._ema_by_date(bars)["2025-12-30"]
        positions = process._build_munger_ticker_positions(
            "AAA", ["2025-12-30"], bars, "2025-12-30"
        )

        self.assertGreater(95.0, sma10)
        self.assertLess(95.0, ema21)
        self.assertEqual("2025-12-30", positions[0]["entry_date"])
        self.assertEqual("2025-12-30", positions[0]["exit_signal_date"])
        self.assertIsNone(positions[0]["exit_date"])

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

    def test_munger400_signals_begin_only_when_each_section_appears(self):
        reports = [
            report("2026-08-18"),
            report("2026-08-21", munger400l=[munger_entry("MID")]),
            report("2026-08-25", munger400r=[munger_entry("RET")]),
        ]

        snapshots = process.build_signal_snapshots(reports)
        l_ema = [s for s in snapshots if s["strategy"] == "munger400l"]
        r_ema = [s for s in snapshots if s["strategy"] == "munger400r"]

        self.assertEqual(["2026-08-21"], [s["report_date"] for s in l_ema])
        self.assertEqual(["MID"], [s["ticker"] for s in l_ema])
        self.assertEqual(["2026-08-25"], [s["report_date"] for s in r_ema])
        self.assertEqual(["RET"], [s["ticker"] for s in r_ema])

    def test_build_positions_routes_both_munger400_models_to_ema21_exit(self):
        reports = [report(
            "2026-01-05",
            munger400l=[munger_entry("AAA")],
            munger400r=[munger_entry("BBB")],
        )]

        with patch.object(process, "get_daily_bars", return_value=self._ema_exit_bars()):
            positions = process.build_positions(reports, "2026-01-07")
        signals = process.build_signal_snapshots(reports)

        self.assertEqual(
            {"munger400l", "munger400r"},
            {position["strategy"] for position in positions},
        )
        process.validate_positions(positions, "2026-01-07", signals)

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

    def test_august_4_report_fixture_has_9_signals_and_36_open_trades(self):
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
        self.assertEqual(9, len(reports[-1]["munger"]))
        self.assertEqual(36, len(positions))
        self.assertTrue(all(p["status"] == "open" for p in positions))

    def test_prefetch_uses_last_published_grouped_date_for_targeted_reads(self):
        reports = [report("2026-03-10", sp500=[munger_entry("AAA")])]

        with patch.object(
            process,
            "update_grouped_daily_bars",
            return_value={
                "grouped_calls": 1,
                "split_refreshes": 0,
                "through": "2026-03-09",
            },
        ), patch.object(process, "get_daily_bars", return_value=[]) as get:
            through = process.prefetch_all_tickers(reports, "2026-03-10")

        self.assertEqual("2026-03-09", through)
        self.assertTrue(get.call_args_list)
        self.assertTrue(all(call.args[2] == "2026-03-09" for call in get.call_args_list))


class SmaExitLifecycleTests(unittest.TestCase):
    @staticmethod
    def _bars():
        bars = []
        current = date(2025, 12, 15)
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

    def test_sma10_exit_executes_next_session_and_same_day_report_reenters(self):
        bars = self._bars()
        positions = process._build_price_exit_ticker_positions(
            "industry_up5",
            "AAA",
            ["2026-01-05", "2026-01-07", "2026-01-08"],
            bars,
            "2026-01-08",
            process._sma_by_date(bars),
            "exit_signal_sma_10",
            reenter_on_exit_session=True,
        )

        self.assertEqual(3, len(positions))
        closed = [p for p in positions if p["status"] == "closed"]
        opened = next(p for p in positions if p["status"] == "open")
        self.assertEqual("2026-01-06", closed[0]["exit_signal_date"])
        self.assertEqual("2026-01-07", closed[0]["exit_date"])
        self.assertLess(closed[0]["exit_signal_close"], closed[0]["exit_signal_sma_10"])
        self.assertEqual("2026-01-07", closed[1]["signal_date"])
        self.assertEqual("2026-01-07", closed[1]["entry_date"])
        self.assertEqual("2026-01-08", closed[1]["exit_date"])
        self.assertEqual("2026-01-08", opened["signal_date"])

    def test_report_disappearance_does_not_close_sma10_trade(self):
        bars = [
            {**bar, "open": 100.0, "close": 100.0, "vwap": 100.0}
            for bar in self._bars()
        ]
        positions = process._build_price_exit_ticker_positions(
            "industry_up5",
            "AAA",
            ["2026-01-05"],
            bars,
            "2026-01-08",
            process._sma_by_date(bars),
            "exit_signal_sma_10",
        )

        self.assertEqual(1, len(positions))
        self.assertEqual("open", positions[0]["status"])
        self.assertIsNone(positions[0]["exit_signal_date"])

    def test_sma_is_trailing_mean_of_ten_closes(self):
        bars = [
            {"date": f"2026-01-{index:02d}", "close": float(index)}
            for index in range(1, 12)
        ]
        values = process._sma_by_date(bars)

        self.assertNotIn("2026-01-09", values)
        self.assertEqual(5.5, values["2026-01-10"])
        self.assertEqual(6.5, values["2026-01-11"])


class IndustryRankChangeTests(unittest.TestCase):
    @staticmethod
    def _flat_then_jump():
        bars = []
        current = date(2026, 8, 17)
        while current <= date(2026, 9, 11):
            if current.weekday() < 5:
                close = 130.0 if current >= date(2026, 9, 9) else 100.0
                bars.append({
                    "date": current.isoformat(),
                    "open": close,
                    "close": close,
                    "vwap": close,
                })
            current += timedelta(days=1)
        return bars

    def test_positive_rank_changes_exit_after_a_close_below_sma10(self):
        bars = self._flat_then_jump()
        # 90 is below the trailing average of 100s; 100 equal to it is not.
        bars[-1]["close"] = 90.0
        bars[-1]["open"] = 90.0
        bars[-1]["vwap"] = 90.0
        reports = [report(
            "2026-09-08",
            industry_rank_up=[rank_change("UP", 1, 400)],
        )]

        with patch.object(process, "get_daily_bars", return_value=bars):
            positions = process._build_sma10_positions(
                reports, "industry_up5", "2026-09-11"
            )

        self.assertEqual(1, len(positions))
        self.assertEqual("industry_up5", positions[0]["strategy"])
        self.assertEqual("2026-09-08", positions[0]["signal_date"])
        self.assertEqual("2026-09-08", positions[0]["entry_date"])
        self.assertEqual("2026-09-11", positions[0]["exit_signal_date"])
        self.assertIsNone(positions[0]["exit_date"])
        self.assertLess(
            positions[0]["exit_signal_close"],
            positions[0]["exit_signal_sma_10"],
        )
        process.validate_positions(positions, "2026-09-11")

    def test_negative_rank_changes_exit_the_next_session_after_a_close_above_sma10(self):
        bars = self._flat_then_jump()
        reports = [
            report("2026-09-04", industry_rank_down=[rank_change("DOWN", 1, -300)]),
            report("2026-09-08"),
            report("2026-09-11", industry_rank_down=[rank_change("DOWN", 1, -280)]),
        ]

        with patch.object(process, "get_daily_bars", return_value=bars):
            positions = process._build_sma10_positions(
                reports, "industry_down5", "2026-09-11"
            )

        self.assertEqual(2, len(positions))
        closed, reopened = positions
        self.assertEqual("2026-09-09", closed["exit_signal_date"])
        self.assertEqual("2026-09-10", closed["exit_date"])
        self.assertGreater(closed["exit_signal_close"], closed["exit_signal_sma_10"])
        self.assertEqual(30.0, closed["return_pct"])
        # The September 8 report has no rank-loss row. The next qualifying
        # report, after the sale, opens a new trade.
        self.assertEqual("2026-09-11", reopened["signal_date"])
        self.assertEqual("2026-09-11", reopened["entry_date"])
        self.assertEqual("open", reopened["status"])
        signals = process.build_signal_snapshots(reports)
        process.validate_positions(positions, "2026-09-11", signals)
        down_signals = [s for s in signals if s["strategy"] == "industry_down5"]
        self.assertEqual(
            ["2026-09-04", "2026-09-11"],
            [s["report_date"] for s in down_signals],
        )
        self.assertEqual(-300, down_signals[0]["source_rank_change"])

    def test_leaving_the_top_five_does_not_close_an_industry_rank_trade(self):
        bars = [
            {**bar, "open": 100.0, "close": 100.0, "vwap": 100.0}
            for bar in self._flat_then_jump()
        ]
        reports = [
            report("2026-09-08", industry_rank_up=[rank_change("UP", 1, 400)]),
            report("2026-09-11", industry_rank_up=[rank_change("OTHER", 1, 500)]),
        ]

        with patch.object(process, "get_daily_bars", return_value=bars):
            positions = process.build_positions(reports, "2026-09-11")

        held = next(
            p for p in positions
            if p["strategy"] == "industry_up5" and p["ticker"] == "UP"
        )
        self.assertEqual("open", held["status"])
        self.assertIsNone(held["exit_date"])
        self.assertIsNone(held["exit_signal_date"])

    def test_rank_six_and_reports_without_the_section_are_ignored(self):
        reports = [
            report("2026-09-01"),
            report("2026-09-08", industry_rank_up=[
                rank_change("UP", 1, 400),
                rank_change("SKIP", 6, 100),
            ], industry_rank_down=[
                rank_change("DOWN", 1, -300),
            ]),
        ]

        snapshots = process.build_signal_snapshots(reports)
        self.assertEqual(
            [],
            [s for s in snapshots if s["report_date"] == "2026-09-01" and s["strategy"].startswith("industry_")],
        )
        self.assertEqual(
            ["UP"],
            [s["ticker"] for s in snapshots if s["strategy"] == "industry_up5"],
        )
        self.assertEqual(
            ["DOWN"],
            [s["ticker"] for s in snapshots if s["strategy"] == "industry_down5"],
        )

    def test_above_sma_exit_rejects_a_close_below_the_average(self):
        position = process._new_position(
            "industry_down5", "DOWN", "2026-09-08", "2026-09-08", 100.0
        )
        position["exit_signal_date"] = "2026-09-09"
        position["exit_signal_close"] = 90.0
        position["exit_signal_sma_10"] = 100.0
        position = process._close_position(position, "2026-09-09", "2026-09-10", 90.0)

        with self.assertRaises(ValueError):
            process.validate_positions([position], "2026-09-10")


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

    def test_munger400r_return_series_starts_at_first_section_report(self):
        reports = [
            report("2026-02-27"),
            report("2026-03-02", munger400r=[munger_entry("NFLX")]),
        ]
        position = process._new_position(
            "munger400r", "NFLX", "2026-03-02", "2026-03-02", 100.0
        )
        position.update({
            "current_date": "2026-03-03",
            "current_price": 97.0,
            "hold_days": 1,
            "return_pct": -3.0,
        })

        with patch.object(process, "get_daily_bars", return_value=self.ticker_bars):
            result = process.build_strategy_returns(
                reports, [position], self.spy_bars, "2026-03-03"
            )

        self.assertEqual("2026-03-02", result["munger400r"][0]["date"])
        self.assertEqual(2, len(result["munger400r"]))
        self.assertEqual(97.0, result["munger400r"][-1]["value"])

    def test_new_strategy_has_empty_series_before_first_completed_session(self):
        reports = [
            report("2026-02-27"),
            report("2026-03-02", munger400l=[munger_entry("NFLX")]),
        ]

        result = process.build_strategy_returns(
            reports, [], self.spy_bars[:1], "2026-02-27"
        )

        self.assertIn("munger400l", result)
        self.assertEqual([], result["munger400l"])


def weekday_bars(start, count, close=100.0):
    bars = []
    current = date.fromisoformat(start)
    while len(bars) < count:
        if current.weekday() < 5:
            bars.append({
                "date": current.isoformat(),
                "open": close,
                "close": close,
                "vwap": close,
            })
        current += timedelta(days=1)
    return bars


class MegaLaggardsHoldTests(unittest.TestCase):
    def setUp(self):
        # Price rises by 1 each session so every lot's return is traceable.
        self.bars = [
            {**bar, "open": 100.0 + i, "close": 100.0 + i, "vwap": 100.0 + i}
            for i, bar in enumerate(weekday_bars("2026-03-02", 40))
        ]
        self.by_date = {bar["date"]: bar for bar in self.bars}

    def _bars(self, _ticker, start, end):
        return [bar for bar in self.bars if start <= bar["date"] <= end]

    def _execution(self, _ticker, signal_date, as_of=None):
        bar = next(bar for bar in self.bars if bar["date"] >= signal_date)
        return bar["date"], bar["vwap"]

    def _build(self, reports, as_of):
        with patch.object(process, "get_daily_bars", side_effect=self._bars), \
             patch.object(process, "get_execution_price", side_effect=self._execution):
            return process._build_hold_positions(reports, "megalaggards2", as_of)

    def test_worst_two_config_holds_21_sessions(self):
        base = process.STRATEGIES["megalaggards2"]
        self.assertEqual("megalaggards", base["section"])
        self.assertEqual([1, 2], list(base["ranks"]))
        self.assertEqual("hold21", base["exit_model"])
        self.assertFalse(any(sid.endswith("_sma10") for sid in process.STRATEGIES))

    def test_each_listing_opens_a_lot_that_sells_21_sessions_later(self):
        reports = [
            report("2026-03-02", megalaggards=[
                munger_entry("AAA", 1), munger_entry("BBB", 2), munger_entry("CCC", 3),
            ]),
            report("2026-03-05", megalaggards=[munger_entry("AAA", 1)]),
        ]
        positions = self._build(reports, self.bars[-1]["date"])

        self.assertEqual(
            ["AAA:2026-03-02", "BBB:2026-03-02", "AAA:2026-03-05"],
            [f"{p['ticker']}:{p['signal_date']}" for p in positions],
        )
        first = positions[0]
        self.assertEqual("closed", first["status"])
        self.assertEqual(self.bars[21]["date"], first["exit_date"])
        self.assertIsNone(first["exit_signal_date"])
        self.assertEqual(21.0, round(first["exit_price"] - first["entry_price"], 4))
        repeat = positions[2]
        self.assertEqual(self.bars[3]["date"], repeat["entry_date"])
        self.assertEqual(self.bars[24]["date"], repeat["exit_date"])
        process.validate_positions(positions, self.bars[-1]["date"])

    def test_lot_stays_open_until_its_21st_session_trades(self):
        reports = [report("2026-03-02", megalaggards=[munger_entry("AAA")])]
        still_open = self._build(reports, self.bars[20]["date"])
        closed = self._build(reports, self.bars[21]["date"])

        self.assertEqual("open", still_open[0]["status"])
        self.assertEqual(self.bars[20]["date"], still_open[0]["current_date"])
        self.assertEqual("closed", closed[0]["status"])

    def test_overlap_is_still_rejected_for_single_position_strategies(self):
        first = process._new_position("munger", "AAA", "2026-03-02", "2026-03-02", 100.0)
        second = process._new_position("munger", "AAA", "2026-03-03", "2026-03-03", 100.0)
        with self.assertRaisesRegex(ValueError, "Overlapping trades"):
            process.validate_positions([first, second], "2026-03-04")

    def test_portfolio_weights_each_open_lot_equally(self):
        spy = [{"date": bar["date"], "close": 100.0} for bar in self.bars[:3]]
        flat = [{**bar, "open": 100.0, "close": 100.0, "vwap": 100.0} for bar in self.bars[:3]]
        doubles = [{**bar, "close": 200.0 if i == 2 else 100.0} for i, bar in enumerate(flat)]
        day0, day1, day2 = (bar["date"] for bar in flat)
        lots = [
            process._new_position("megalaggards2", "AAA", day0, day0, 100.0),
            process._new_position("megalaggards2", "AAA", day1, day1, 100.0),
            process._new_position("megalaggards2", "BBB", day1, day1, 100.0),
        ]
        bars = {"AAA": doubles, "BBB": flat}
        reports = [report(day0, megalaggards=[munger_entry("AAA")])]

        with patch.object(process, "get_daily_bars",
                          side_effect=lambda ticker, *_: bars[ticker]):
            series = process.build_strategy_returns(reports, lots, spy, day2)["megalaggards2"]

        # AAA holds two of three lots, so doubling AAA adds 2/3 of NAV.
        self.assertAlmostEqual(100.0 + 200.0 / 3, series[-1]["value"], places=3)

    def test_trade_stats_count_overlapping_lots_as_one_run(self):
        def lot(ticker, entry, exit_date, ret):
            return {"strategy": "megalaggards2", "ticker": ticker, "status": "closed",
                    "entry_date": entry, "exit_date": exit_date, "return_pct": ret}
        positions = [
            lot("AAA", "2026-03-02", "2026-03-31", 5.0),
            lot("AAA", "2026-03-05", "2026-04-03", 4.0),
            lot("AAA", "2026-04-10", "2026-05-11", -2.0),
            lot("BBB", "2026-03-02", "2026-03-31", 1.0),
        ]
        stats = process.compute_trade_stats(positions)

        self.assertEqual(4, stats["megalaggards2"]["closed_count"])
        self.assertEqual(3, stats["megalaggards2"]["independent_run_count"])
        self.assertIsNone(stats["munger"]["independent_run_count"])


class RankMomentumNextSessionExitTests(unittest.TestCase):
    def setUp(self):
        self.bars = [
            {**bar, "open": 100.0 + i, "close": 100.0 + i, "vwap": 100.0 + i}
            for i, bar in enumerate(weekday_bars("2026-03-02", 15))
        ]

    def _execution(self, _ticker, start, as_of=None):
        bar = next(
            (bar for bar in self.bars if start <= bar["date"] <= (as_of or "9999")),
            None,
        )
        return (bar["date"], bar["vwap"]) if bar else (None, None)

    def _build(self, reports, strategy_id, as_of):
        with patch.object(process, "get_execution_price", side_effect=self._execution), \
             patch.object(process, "get_daily_bars",
                          side_effect=lambda _t, start, end: [
                              b for b in self.bars if start <= b["date"] <= end
                          ]):
            return process._build_next_session_rank_positions(reports, strategy_id, as_of)

    @staticmethod
    def _report(report_date, tickers):
        return {
            "date": report_date,
            "rankmom500": [munger_entry(t, rank) for rank, t in enumerate(tickers, start=1)],
        }

    def test_top_and_next_five_configs(self):
        for sid, section, ranks in (
            ("rankmom500_top5", "rankmom500", [1, 2, 3, 4, 5]),
            ("rankmom500_next5", "rankmom500", [6, 7, 8, 9, 10]),
            ("rankmom400_top5", "rankmom400", [1, 2, 3, 4, 5]),
            ("rankmom400_next5", "rankmom400", [6, 7, 8, 9, 10]),
        ):
            config = process.STRATEGIES[sid]
            self.assertEqual(section, config["section"])
            self.assertEqual(ranks, list(config["ranks"]))
            self.assertEqual("rank_next_session", config["exit_model"])

    def test_drop_sells_the_session_after_the_drop_report(self):
        # Mar 2 lists AAA; the Mar 5 report drops it, so it sells Mar 6.
        reports = [self._report("2026-03-02", ["AAA"]), self._report("2026-03-05", ["BBB"])]
        positions = self._build(reports, "rankmom500_top5", "2026-03-13")

        aaa = next(p for p in positions if p["ticker"] == "AAA")
        self.assertEqual("2026-03-02", aaa["entry_date"])
        self.assertEqual("2026-03-05", aaa["exit_signal_date"])
        self.assertEqual("2026-03-06", aaa["exit_date"])
        self.assertEqual("closed", aaa["status"])
        bbb = next(p for p in positions if p["ticker"] == "BBB")
        self.assertEqual("2026-03-05", bbb["entry_date"])
        self.assertEqual("open", bbb["status"])
        process.validate_positions(positions, "2026-03-13")

    def test_relisting_before_the_sale_session_cancels_it(self):
        # Dropped Mar 5 (sale due Mar 6), relisted in the Mar 6 report.
        reports = [
            self._report("2026-03-02", ["AAA"]),
            self._report("2026-03-05", ["BBB"]),
            self._report("2026-03-06", ["AAA"]),
        ]
        positions = self._build(reports, "rankmom500_top5", "2026-03-13")

        aaa = [p for p in positions if p["ticker"] == "AAA"]
        self.assertEqual(1, len(aaa))
        self.assertEqual("open", aaa[0]["status"])
        self.assertIsNone(aaa[0]["exit_signal_date"])

    def test_sale_after_the_last_session_stays_pending(self):
        reports = [self._report("2026-03-02", ["AAA"]), self._report("2026-03-05", [])]
        positions = self._build(reports, "rankmom500_top5", "2026-03-05")

        self.assertEqual(1, len(positions))
        self.assertEqual("open", positions[0]["status"])
        self.assertEqual("2026-03-05", positions[0]["exit_signal_date"])
        self.assertEqual("2026-03-05", positions[0]["current_date"])

    def test_next_five_ignores_top_five_rows(self):
        reports = [self._report("2026-03-02", [f"T{i}" for i in range(1, 11)])]
        positions = self._build(reports, "rankmom500_next5", "2026-03-03")

        self.assertEqual(["T10", "T6", "T7", "T8", "T9"], sorted(p["ticker"] for p in positions))

    def test_validation_rejects_a_same_session_rank_exit(self):
        trade = process._new_position("rankmom500_top5", "AAA", "2026-03-02", "2026-03-02", 100.0)
        closed = process._close_position(trade, "2026-03-05", "2026-03-05", 101.0)
        with self.assertRaisesRegex(ValueError, "not after its drop report"):
            process.validate_positions([closed], "2026-03-06")


class SharpeTests(unittest.TestCase):
    def test_full_period_sharpe_uses_each_series_and_its_own_spy_window(self):
        days = [bar["date"] for bar in weekday_bars("2026-01-05", 60)]
        series = [
            {"date": day, "value": 100.0 * (1.01 if i % 2 else 1.0) + i,
             "spy_value": 100.0 + (i % 3)}
            for i, day in enumerate(days)
        ]
        short = series[-20:]
        sharpe = process.compute_sharpe({"long": series, "short": short}, days[-1])

        self.assertIsNone(sharpe["long"]["12m"])
        self.assertEqual(
            process._sharpe_from_series(series, "", 40), sharpe["long"]["inception"]
        )
        self.assertEqual(
            process._sharpe_from_series(series, "", 40, "spy_value"),
            sharpe["long"]["spy_inception"],
        )
        self.assertNotIn("3m", sharpe["long"])
        # Fewer than 40 daily returns publishes no full-period Sharpe.
        self.assertIsNone(sharpe["short"]["inception"])
        self.assertIsNone(sharpe["short"]["spy_inception"])


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
