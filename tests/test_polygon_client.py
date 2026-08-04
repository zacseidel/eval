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


if __name__ == "__main__":
    unittest.main()
