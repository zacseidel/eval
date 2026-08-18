import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRAPER_DIR = Path(__file__).resolve().parents[1] / "scraper"
sys.path.insert(0, str(SCRAPER_DIR))

import polygon_client


class PolygonCacheTests(unittest.TestCase):
    def setUp(self):
        polygon_client._refreshed_tickers.clear()

    @staticmethod
    def _aggregate(day, price):
        timestamp = datetime.strptime(day, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        ).timestamp() * 1000
        return {
            "t": timestamp,
            "o": price,
            "h": price,
            "l": price,
            "c": price,
            "v": 1000,
            "vw": price,
        }

    def test_current_market_day_is_not_durable_before_close(self):
        before_close = datetime(2026, 8, 4, 14, 0, tzinfo=timezone.utc)
        after_close = datetime(2026, 8, 4, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(
            "2026-08-03",
            polygon_client._durable_coverage_end("2026-08-04", before_close),
        )
        self.assertEqual(
            "2026-08-04",
            polygon_client._durable_coverage_end("2026-08-04", after_close),
        )

    def test_polygon_timestamp_is_converted_in_utc(self):
        timestamp = datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc).timestamp() * 1000
        response = {
            "status": "OK",
            "results": [{
                "t": timestamp,
                "o": 10.0,
                "h": 11.0,
                "l": 9.0,
                "c": 10.5,
                "v": 1000,
                "vw": 10.25,
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
             patch.object(polygon_client, "_get", return_value=response):
            bars = polygon_client.get_daily_bars("AAA", "2026-03-09", "2026-03-09")
            raw = json.loads((Path(temp_dir) / "AAA.json").read_text())

        self.assertEqual("2026-03-09", bars[0]["date"])
        self.assertEqual("UTC", raw["_date_timezone"])
        self.assertEqual(polygon_client.CACHE_SCHEMA_VERSION, raw["_schema_version"])

    def test_failed_fetch_does_not_advance_coverage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "AAA.json"
            cache_path.write_text(json.dumps({
                "_schema_version": polygon_client.CACHE_SCHEMA_VERSION,
                "_date_timezone": "UTC",
                "_fetched_from": "2026-03-01",
                "_fetched_through": "2026-03-05",
            }))
            with patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
                 patch.object(polygon_client, "_get", side_effect=RuntimeError("network down")):
                polygon_client.get_daily_bars("AAA", "2026-03-01", "2026-03-10")
            raw = json.loads(cache_path.read_text())

        self.assertEqual("2026-03-05", raw["_fetched_through"])

    def test_successful_left_extension_survives_failed_right_extension(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "AAA.json"
            cache_path.write_text(json.dumps({
                "_schema_version": polygon_client.CACHE_SCHEMA_VERSION,
                "_date_timezone": "UTC",
                "_fetched_from": "2026-03-05",
                "_fetched_through": "2026-03-10",
            }))
            with patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
                 patch.object(polygon_client, "_get", side_effect=[
                     {"status": "OK", "results": []},
                     RuntimeError("right edge failed"),
                 ]):
                polygon_client.get_daily_bars("AAA", "2026-03-01", "2026-03-15")
            raw = json.loads(cache_path.read_text())

        self.assertEqual("2026-03-01", raw["_fetched_from"])
        self.assertEqual("2026-03-10", raw["_fetched_through"])

    def test_changed_adjusted_overlap_refreshes_the_complete_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "AAA.json"
            cache_path.write_text(json.dumps({
                "_schema_version": polygon_client.CACHE_SCHEMA_VERSION,
                "_date_timezone": "UTC",
                "_fetched_from": "2026-01-02",
                "_fetched_through": "2026-01-06",
                "2026-01-02": {
                    "open": 100.0, "high": 100.0, "low": 100.0,
                    "close": 100.0, "volume": 1000, "vwap": 100.0,
                },
                "2026-01-05": {
                    "open": 110.0, "high": 110.0, "low": 110.0,
                    "close": 110.0, "volume": 1000, "vwap": 110.0,
                },
                "2026-01-06": {
                    "open": 120.0, "high": 120.0, "low": 120.0,
                    "close": 120.0, "volume": 1000, "vwap": 120.0,
                },
            }))
            overlap = {
                "status": "OK",
                "results": [
                    self._aggregate("2026-01-05", 55.0),
                    self._aggregate("2026-01-06", 60.0),
                    self._aggregate("2026-01-07", 61.0),
                ],
            }
            complete = {
                "status": "OK",
                "results": [
                    self._aggregate("2026-01-02", 50.0),
                    self._aggregate("2026-01-05", 55.0),
                    self._aggregate("2026-01-06", 60.0),
                    self._aggregate("2026-01-07", 61.0),
                ],
            }

            with patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
                 patch.object(polygon_client, "_get", side_effect=[overlap, complete]) as get:
                bars = polygon_client.get_daily_bars("AAA", "2026-01-02", "2026-01-07")
            raw = json.loads(cache_path.read_text())

        self.assertEqual(2, get.call_count)
        self.assertEqual(50.0, raw["2026-01-02"]["close"])
        self.assertEqual(61.0, raw["2026-01-07"]["close"])
        self.assertEqual([50.0, 55.0, 60.0, 61.0], [bar["close"] for bar in bars])

    def test_unchanged_overlap_is_checked_only_once_per_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "AAA.json"
            cache_path.write_text(json.dumps({
                "_schema_version": polygon_client.CACHE_SCHEMA_VERSION,
                "_date_timezone": "UTC",
                "_fetched_from": "2026-01-05",
                "_fetched_through": "2026-01-05",
                "2026-01-05": {
                    "open": 100.0, "high": 100.0, "low": 100.0,
                    "close": 100.0, "volume": 1000, "vwap": 100.0,
                },
            }))
            unchanged = {
                "status": "OK",
                "results": [self._aggregate("2026-01-05", 100.0)],
            }

            with patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
                 patch.object(polygon_client, "_get", return_value=unchanged) as get:
                polygon_client.get_daily_bars("AAA", "2026-01-05", "2026-01-05")
                polygon_client.get_daily_bars("AAA", "2026-01-05", "2026-01-05")

        self.assertEqual(1, get.call_count)

    def test_failed_full_refresh_keeps_the_original_cache_scale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "AAA.json"
            original = {
                "_schema_version": polygon_client.CACHE_SCHEMA_VERSION,
                "_date_timezone": "UTC",
                "_fetched_from": "2026-01-05",
                "_fetched_through": "2026-01-06",
                "2026-01-05": {
                    "open": 100.0, "high": 100.0, "low": 100.0,
                    "close": 100.0, "volume": 1000, "vwap": 100.0,
                },
                "2026-01-06": {
                    "open": 110.0, "high": 110.0, "low": 110.0,
                    "close": 110.0, "volume": 1000, "vwap": 110.0,
                },
            }
            cache_path.write_text(json.dumps(original))
            changed_overlap = {
                "status": "OK",
                "results": [
                    self._aggregate("2026-01-05", 50.0),
                    self._aggregate("2026-01-06", 55.0),
                    self._aggregate("2026-01-07", 56.0),
                ],
            }

            with patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
                 patch.object(
                     polygon_client,
                     "_get",
                     side_effect=[changed_overlap, RuntimeError("full refresh failed")],
                 ):
                polygon_client.get_daily_bars("AAA", "2026-01-05", "2026-01-07")
            raw = json.loads(cache_path.read_text())

        self.assertEqual(original, raw)

    def test_grouped_update_extends_all_tickers_with_two_market_calls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for ticker in ("AAA", "BBB"):
                (Path(temp_dir) / f"{ticker}.json").write_text(json.dumps({
                    "_schema_version": polygon_client.CACHE_SCHEMA_VERSION,
                    "_date_timezone": "UTC",
                    "_fetched_from": "2026-03-01",
                    "_fetched_through": "2026-03-06",
                    "2026-03-06": {
                        "open": 100.0, "high": 100.0, "low": 100.0,
                        "close": 100.0, "volume": 1000, "vwap": 100.0,
                    },
                }))
            monday = {
                "status": "OK",
                "results": [
                    {**self._aggregate("2026-03-09", 101.0), "T": "AAA"},
                    {**self._aggregate("2026-03-09", 201.0), "T": "BBB"},
                ],
            }
            tuesday = {
                "status": "OK",
                "results": [
                    {**self._aggregate("2026-03-10", 102.0), "T": "AAA"},
                    {**self._aggregate("2026-03-10", 202.0), "T": "BBB"},
                ],
            }

            with patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
                 patch.object(
                     polygon_client,
                     "_get",
                     side_effect=[{"status": "OK", "results": []}, monday, tuesday],
                 ) as get:
                summary = polygon_client.update_grouped_daily_bars(
                    {"AAA", "BBB"}, "2026-03-10"
                )

            aaa = json.loads((Path(temp_dir) / "AAA.json").read_text())
            bbb = json.loads((Path(temp_dir) / "BBB.json").read_text())

        self.assertEqual(3, get.call_count)  # one split query + two grouped days
        self.assertEqual(2, summary["grouped_calls"])
        self.assertEqual("2026-03-10", summary["through"])
        self.assertEqual(102.0, aaa["2026-03-10"]["close"])
        self.assertEqual(202.0, bbb["2026-03-10"]["close"])
        self.assertEqual("2026-03-10", aaa["_fetched_through"])

    def test_grouped_update_does_not_advance_on_unpublished_latest_day(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "AAA.json"
            cache_path.write_text(json.dumps({
                "_schema_version": polygon_client.CACHE_SCHEMA_VERSION,
                "_date_timezone": "UTC",
                "_fetched_from": "2026-03-01",
                "_fetched_through": "2026-03-09",
                "2026-03-09": {
                    "open": 100.0, "high": 100.0, "low": 100.0,
                    "close": 100.0, "volume": 1000, "vwap": 100.0,
                },
            }))
            with patch.object(polygon_client, "CACHE_DIR", Path(temp_dir)), \
                 patch.object(polygon_client, "_get", side_effect=[
                     {"status": "OK", "results": []},
                     {"status": "OK", "results": []},
                 ]):
                summary = polygon_client.update_grouped_daily_bars(
                    {"AAA"}, "2026-03-10"
                )
            raw = json.loads(cache_path.read_text())

        self.assertEqual("2026-03-09", summary["through"])
        self.assertEqual("2026-03-09", raw["_fetched_through"])


if __name__ == "__main__":
    unittest.main()
