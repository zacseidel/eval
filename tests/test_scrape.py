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
<h2 id="summary-megalaggards">Mega Cap Laggards</h2>
<p>The 10 largest S&amp;P 500 stocks ranked by 3-, 6-, and 12-month return.</p>
<div><a>LAG</a><span>($375.30 | 3M -6.3% / 6M 2.0% / 12M -11.9% | Avg Rank 9.3 of 10) - 🔥 since 2026-08-14</span></div>
<div><a>SLO</a><span>($120.00 | 3M 1.5% / 6M -4.0% / 12M 3.0% | Avg Rank 8.7 of 10) - ✨ New Entrant</span></div>
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

    def test_mega_cap_laggards_parse_in_published_worst_first_order(self):
        parsed = scrape.parse_report("2026-09-22", soup(REPORT_HTML))

        self.assertIn("megalaggards", parsed["sections_present"])
        first, second = parsed["megalaggards"]
        self.assertEqual(("LAG", 1), (first["ticker"], first["rank"]))
        self.assertEqual(-6.3, first["return_3m"])
        self.assertEqual(2.0, first["return_6m"])
        self.assertEqual(-11.9, first["return_12m"])
        self.assertEqual(9.3, first["avg_rank"])
        self.assertEqual("2026-08-14", first["entry_date"])
        self.assertEqual(("SLO", 2), (second["ticker"], second["rank"]))
        self.assertEqual("2026-09-22", second["entry_date"])
        self.assertTrue(second["new_entrant"])
        # The Leaders parser must not swallow the laggards rows.
        self.assertEqual(["BBB"], [row["ticker"] for row in parsed["megacap"]])

    def test_report_without_laggards_is_marked_absent(self):
        parsed = scrape.parse_report(
            "2026-01-13",
            soup('<h2 id="summary-megacap">Megacap Leaders</h2>'),
        )

        self.assertEqual([], parsed["megalaggards"])
        self.assertNotIn("megalaggards", parsed["sections_present"])

    def test_snapshot_from_an_older_parser_is_refreshed(self):
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
                 patch.object(scrape, "fetch", side_effect=[index, soup(REPORT_HTML)]), \
                 patch.object(scrape.time, "sleep"):
                updated = scrape.scrape_all()
            saved = json.loads(output.read_text())

        self.assertEqual([report_date], updated)
        self.assertEqual(scrape.SNAPSHOT_PARSER_VERSION, saved["parser_version"])
        self.assertEqual("LAG", saved["megalaggards"][0]["ticker"])

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
                "parser_version": scrape.SNAPSHOT_PARSER_VERSION,
            }))
            with patch.object(scrape, "SCRAPED_DIR", Path(temp_dir)), \
                 patch.object(scrape, "fetch", return_value=index) as fetch_mock:
                updated = scrape.scrape_all()

        self.assertEqual([], updated)
        fetch_mock.assert_called_once_with(scrape.INDEX_URL)



INDUSTRY_HTML = """
<h3>Industries</h3>
<h4>Largest rank changes</h4>
<table>
  <tr>
    <td>Food (FOOD)</td><td class="num">#31</td><td class="num">#20</td>
    <td class="num up">↑ 11</td><td class="num">+14.0%</td>
  </tr>
</table>
<h3>Stocks</h3>
<h4>Top-five comparison</h4>
<table>
  <tr>
    <td>Sandisk Corporation Common Stock (SNDK)</td>
    <td class="num">#1</td><td class="num">#1</td>
    <td class="num">— 0</td><td class="num">+1.0%</td>
  </tr>
</table>
<h4>Largest rank changes</h4>
<table>
  <thead><tr><th>Entity</th><th>Previous</th><th>Current</th><th>Change</th><th>Current return</th></tr></thead>
  <tbody>
    <tr>
      <td>IES Holdings, Inc. Common Stock (IESC)</td>
      <td class="num">#529</td><td class="num">#74</td>
      <td class="num up">↑ 455</td><td class="num">+69.6%</td>
    </tr>
    <tr>
      <td>Amphenol Corporation (APH)</td>
      <td class="num">#828</td><td class="num">#214</td>
      <td class="num up">↑ 614</td><td class="num">+30.3%</td>
    </tr>
    <tr>
      <td>CNH INDUSTRIAL N.V. (CNH)</td>
      <td class="num">#604</td><td class="num">#255</td>
      <td class="num up">↑ 349</td><td class="num">+22.9%</td>
    </tr>
    <tr>
      <td>Keurig Dr Pepper Inc. (KDP)</td>
      <td class="num">#694</td><td class="num">#350</td>
      <td class="num up">↑ 344</td><td class="num">+12.9%</td>
    </tr>
    <tr>
      <td>Skyworks Solutions Inc (SWKS)</td>
      <td class="num">#698</td><td class="num">#359</td>
      <td class="num up">↑ 339</td><td class="num">+12.4%</td>
    </tr>
    <tr>
      <td>Extra Gain (EXTRA)</td>
      <td class="num">#10</td><td class="num">#1</td>
      <td class="num up">↑ 900</td><td class="num">+1.0%</td>
    </tr>
    <tr>
      <td>Casey's General Stores Inc (CASY)</td>
      <td class="num">#108</td><td class="num">#432</td>
      <td class="num down">↓ 324</td><td class="num">+7.0%</td>
    </tr>
    <tr>
      <td>PG&amp;E Corporation (PCG)</td>
      <td class="num">#359</td><td class="num">#685</td>
      <td class="num down">↓ 326</td><td class="num">-13.2%</td>
    </tr>
  </tbody>
</table>
"""


class IndustryRankScrapeTests(unittest.TestCase):
    def test_stock_largest_rank_changes_keep_published_order_and_top_five(self):
        up, down = scrape.parse_industry_stock_rank_changes(soup(INDUSTRY_HTML))

        self.assertEqual(
            ["IESC", "APH", "CNH", "KDP", "SWKS"],
            [row["ticker"] for row in up],
        )
        self.assertEqual(1, up[0]["rank"])
        self.assertEqual(455, up[0]["rank_change"])
        self.assertEqual(529, up[0]["previous_rank"])
        self.assertEqual(74, up[0]["current_rank"])
        self.assertEqual(69.6, up[0]["return_12m"])
        self.assertEqual(614, up[1]["rank_change"])
        self.assertNotIn("EXTRA", [row["ticker"] for row in up])
        self.assertNotIn("FOOD", [row["ticker"] for row in up + down])
        self.assertNotIn("SNDK", [row["ticker"] for row in up + down])
        self.assertEqual(["CASY", "PCG"], [row["ticker"] for row in down])
        self.assertEqual(-324, down[0]["rank_change"])
        self.assertEqual(-13.2, down[1]["return_12m"])

    def test_empty_stock_rank_change_block_is_present(self):
        parsed = scrape.parse_industry_stock_rank_changes(soup(
            "<h3>Stocks</h3><h4>Largest rank changes</h4>"
            "<p class='empty'>No qualifying names this week.</p>"
        ))

        self.assertEqual(([], []), parsed)

    def test_industry_table_without_the_stocks_block_is_absent(self):
        parsed = scrape.parse_industry_stock_rank_changes(soup(
            "<h3>Industries</h3><h4>Largest rank changes</h4><table></table>"
        ))

        self.assertIsNone(parsed)

    def test_cached_momentum_snapshot_still_fetches_a_new_industry_report(self):
        report_date = "2026-09-08"
        index = soup(
            '<a href="reports/momentum_2026-09-08.html">September 8</a>'
            '<a href="reports/industry_2026-09-08.html">Industry ranks</a>'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / f"{report_date}.json"
            output.write_text(json.dumps({
                "date": report_date,
                "source_url": scrape.report_url(report_date),
                "parser_version": scrape.SNAPSHOT_PARSER_VERSION,
                "sections_present": ["sp500"],
                "sp500": [{"rank": 1, "ticker": "AAA"}],
            }))
            with patch.object(scrape, "SCRAPED_DIR", Path(temp_dir)), \
                 patch.object(
                     scrape, "fetch",
                     side_effect=[index, soup(INDUSTRY_HTML)],
                 ) as fetch_mock, \
                 patch.object(scrape.time, "sleep"):
                updated = scrape.scrape_all()
            saved = json.loads(output.read_text())

            with patch.object(scrape, "SCRAPED_DIR", Path(temp_dir)), \
                 patch.object(scrape, "fetch", return_value=index) as second_fetch, \
                 patch.object(scrape.time, "sleep"):
                second = scrape.scrape_all()

        self.assertEqual([report_date], updated)
        self.assertEqual([], second)
        self.assertEqual("AAA", saved["sp500"][0]["ticker"])
        self.assertEqual(
            scrape.industry_report_url(report_date),
            saved["industry_source_url"],
        )
        self.assertEqual("IESC", saved["industry_rank_up"][0]["ticker"])
        self.assertIn("industry_rank_up", saved["sections_present"])
        self.assertIn("industry_rank_down", saved["sections_present"])
        self.assertEqual(
            scrape.industry_report_url(report_date),
            fetch_mock.call_args_list[1].args[0],
        )
        second_fetch.assert_called_once_with(scrape.INDEX_URL)


if __name__ == "__main__":
    unittest.main()
