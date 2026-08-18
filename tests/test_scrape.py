import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

SCRAPER_DIR = Path(__file__).resolve().parents[1] / "scraper"
sys.path.insert(0, str(SCRAPER_DIR))

import scrape


def soup(html):
    return BeautifulSoup(html, "html.parser")


REPORT_HTML = """
<h2 id="summary-munger">Munger Strategy</h2>
<div><a>AAA</a><span>($100.00 | 200SMA: $90.00) - New Entrant</span></div>
<h2 id="summary-munger400l">Munger400L</h2>
<div><a>EEE</a><span>($50.00 | MDY Weight: 0.5% (#1) | 200SMA: $45.00) - New Entrant</span></div>
<h2 id="summary-munger400r">Munger400R</h2>
<div><a>FFF</a><span>($60.00 | Best 12M: 80.0% (#2/400) | 200SMA: $55.00) - New Entrant</span></div>
<h2 id="summary-megacap">Megacap Leaders</h2>
<div><a>BBB</a><span>($200.00 | 25.0% 12M, +2.0% 1W) - New Entrant</span></div>
<h2 id="summary-sp500">SP500 Leaders</h2>
<div><a>CCC</a><span>($300.00 | 20.0% 12M, +1.0% 1W) - New Entrant</span></div>
<h2 id="summary-sp400">SP400 Leaders</h2>
<div><a>DDD</a><span>($400.00 | 15.0% 12M, +0.5% 1W) - New Entrant</span></div>
"""


class MomentumSourceTests(unittest.TestCase):
    def test_report_url_uses_momentum_page(self):
        self.assertEqual(
            "https://zacseidel.github.io/momentum/reports/momentum_2026-08-18.html",
            scrape.report_url("2026-08-18"),
        )

    def test_current_and_munger400_split_section_ids_parse(self):
        parsed = scrape.parse_report("2026-08-18", soup(REPORT_HTML))

        self.assertEqual(scrape.report_url("2026-08-18"), parsed["source_url"])
        self.assertEqual("AAA", parsed["munger"][0]["ticker"])
        self.assertEqual("EEE", parsed["munger400l"][0]["ticker"])
        self.assertEqual("FFF", parsed["munger400r"][0]["ticker"])
        self.assertEqual("BBB", parsed["megacap"][0]["ticker"])
        self.assertEqual("CCC", parsed["sp500"][0]["ticker"])
        self.assertEqual("DDD", parsed["sp400"][0]["ticker"])
        self.assertIn("munger400l", parsed["sections_present"])
        self.assertIn("munger400r", parsed["sections_present"])

    def test_historical_report_without_munger400_models_is_marked_absent(self):
        parsed = scrape.parse_report(
            "2026-08-18",
            soup('<h2 id="summary-munger">Munger Strategy</h2>'),
        )

        self.assertEqual([], parsed["munger400l"])
        self.assertEqual([], parsed["munger400r"])
        self.assertNotIn("munger400l", parsed["sections_present"])
        self.assertNotIn("munger400r", parsed["sections_present"])

    def test_munger400_title_prefixes_are_fallbacks_when_ids_are_missing(self):
        parsed = scrape.parse_report(
            "2026-08-21",
            soup(
                "<h2>Munger400L — Large Midcap Mean Reversion</h2>"
                "<div><a>MID</a><span>($50.00 | 200SMA: $45.00)"
                " - New Entrant</span></div>"
                "<h2>Munger400R — Former Return Leaders</h2>"
                "<div><a>RET</a><span>($60.00 | 200SMA: $55.00)"
                " - New Entrant</span></div>"
            ),
        )

        self.assertEqual("MID", parsed["munger400l"][0]["ticker"])
        self.assertEqual("RET", parsed["munger400r"][0]["ticker"])
        self.assertIn("munger400l", parsed["sections_present"])
        self.assertIn("munger400r", parsed["sections_present"])

    def test_snapshot_from_a_different_source_is_refreshed(self):
        report_date = "2026-08-18"
        index = soup(
            '<a href="reports/momentum_2026-08-18.html">August 18 report</a>'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / f"{report_date}.json"
            output.write_text(json.dumps({
                "date": report_date,
                "source_url": (
                    "https://legacy.example/"
                    "reports/momentum_2026-08-18.html"
                ),
            }))
            with patch.object(scrape, "SCRAPED_DIR", Path(temp_dir)), \
                 patch.object(scrape, "fetch", side_effect=[index, soup(REPORT_HTML)]) \
                    as fetch_mock, \
                 patch.object(scrape.time, "sleep"):
                updated = scrape.scrape_all()

            saved = json.loads(output.read_text())

        self.assertEqual([report_date], updated)
        self.assertEqual(scrape.report_url(report_date), saved["source_url"])
        self.assertEqual(scrape.report_url(report_date), fetch_mock.call_args_list[1].args[0])

    def test_current_source_snapshot_is_skipped(self):
        report_date = "2026-08-18"
        index = soup(
            '<a href="reports/momentum_2026-08-18.html">August 18 report</a>'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / f"{report_date}.json"
            output.write_text(json.dumps({
                "date": report_date,
                "source_url": scrape.report_url(report_date),
            }))
            with patch.object(scrape, "SCRAPED_DIR", Path(temp_dir)), \
                 patch.object(scrape, "fetch", return_value=index) as fetch_mock:
                updated = scrape.scrape_all()

        self.assertEqual([], updated)
        fetch_mock.assert_called_once_with(scrape.INDEX_URL)



if __name__ == "__main__":
    unittest.main()
