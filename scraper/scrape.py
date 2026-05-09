"""Scrapes momentum9 weekly reports and saves raw data as JSON."""
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://zacseidel.github.io/momentum9"
INDEX_URL = f"{BASE_URL}/"
SCRAPED_DIR = Path(__file__).parent.parent / "data" / "scraped"

SECTION_IDS = {
    "munger":  "summary-munger",
    "megacap": "summary-megacap",
    "sp500":   "summary-sp500",
    "sp400":   "summary-sp400",
}


def fetch(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def get_report_dates(soup):
    dates = []
    for a in soup.find_all("a", href=True):
        m = re.search(r"reports/momentum_(\d{4}-\d{2}-\d{2})\.html", a["href"])
        if m:
            dates.append(m.group(1))
    return sorted(set(dates))


def _parse_entry_divs(h2_tag, report_date):
    """
    Collect all <div style='margin-bottom: 4px;'> siblings after h2_tag
    until the next <h2>. Returns list of raw (ticker, span_text) pairs.
    """
    entries = []
    for sib in h2_tag.next_siblings:
        if sib.name == "h2":
            break
        if sib.name != "div":
            continue
        a = sib.find("a")
        if not a:
            continue
        ticker = a.get_text(strip=True)
        span_text = sib.get_text(" ", strip=True)
        if ticker:
            entries.append((ticker, span_text))
    return entries


def parse_leaders_section(h2_tag, report_date):
    """
    Parse a Leaders section (megacap, sp500, sp400).
    Span text format: ($646.63 | 682.7% 12M, +25.0% 1W) - 🔥 since 2026-01-07
                  or: ($287.44 | 46.5% 12M, +5.9% 1W) - ✨ New Entrant
    """
    rows = []
    for rank, (ticker, text) in enumerate(_parse_entry_divs(h2_tag, report_date), start=1):
        price_m = re.search(r"\(\$([0-9,.]+)", text)
        ret12_m = re.search(r"\|\s*([0-9.]+)%\s*12M", text)
        ret1w_m = re.search(r"([+-][0-9.]+)%\s*1W", text)
        date_m  = re.search(r"since\s+(\d{4}-\d{2}-\d{2})", text)
        status  = "🔥" if "🔥" in text else ("✨" if "✨" in text else None)
        new_entrant = "New Entrant" in text

        entry_date = date_m.group(1) if date_m else (report_date if new_entrant else None)

        rows.append({
            "rank": rank,
            "ticker": ticker,
            "price": float(price_m.group(1).replace(",", "")) if price_m else None,
            "return_12m": float(ret12_m.group(1)) if ret12_m else None,
            "return_1w": float(ret1w_m.group(1)) if ret1w_m else None,
            "entry_date": entry_date,
            "status": status,
            "new_entrant": new_entrant,
        })
    return rows


def parse_munger_section(h2_tag, report_date):
    """
    Parse the Munger section.
    Span text format: ($420.77 | 200SMA: $476.43) - 🔥 since 2026-04-14
                  or: ($475.08 | 200SMA: $489.85) - ✨ New Entrant
    """
    rows = []
    for rank, (ticker, text) in enumerate(_parse_entry_divs(h2_tag, report_date), start=1):
        price_m  = re.search(r"\(\$([0-9,.]+)", text)
        sma_m    = re.search(r"200SMA:\s*\$([0-9,.]+)", text)
        date_m   = re.search(r"since\s+(\d{4}-\d{2}-\d{2})", text)
        status   = "🔥" if "🔥" in text else ("✨" if "✨" in text else None)
        new_entrant = "New Entrant" in text

        entry_date = date_m.group(1) if date_m else (report_date if new_entrant else None)

        rows.append({
            "rank": rank,
            "ticker": ticker,
            "price": float(price_m.group(1).replace(",", "")) if price_m else None,
            "sma_200": float(sma_m.group(1).replace(",", "")) if sma_m else None,
            "entry_date": entry_date,
            "status": status,
            "new_entrant": new_entrant,
        })
    return rows


def parse_universe_updates(soup):
    updates = []
    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        if "cohort" not in headers:
            continue
        col = {h: i for i, h in enumerate(headers)}
        for tr in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) >= 3:
                updates.append({
                    "cohort": cells[col.get("cohort", 0)],
                    "ticker": cells[col.get("ticker", 1)],
                    "action": cells[col.get("action", 2)],
                })
    return updates


def parse_report(date, soup):
    result = {"date": date, "sp500": [], "megacap": [], "sp400": [], "munger": []}

    for section, h2_id in SECTION_IDS.items():
        h2 = soup.find("h2", id=h2_id)
        if not h2:
            continue
        if section == "munger":
            result[section] = parse_munger_section(h2, date)
        else:
            result[section] = parse_leaders_section(h2, date)

    result["universe_updates"] = parse_universe_updates(soup)
    return result


def scrape_all(force=False):
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    soup = fetch(INDEX_URL)
    dates = get_report_dates(soup)
    print(f"Found {len(dates)} reports on index page.")

    new_dates = []
    for date in dates:
        out_path = SCRAPED_DIR / f"{date}.json"
        if out_path.exists() and not force:
            continue
        url = f"{BASE_URL}/reports/momentum_{date}.html"
        print(f"  Scraping {date}...")
        try:
            report_soup = fetch(url)
            data = parse_report(date, report_soup)
            out_path.write_text(json.dumps(data, indent=2))
            new_dates.append(date)
            time.sleep(0.3)
        except Exception as e:
            print(f"  ERROR scraping {date}: {e}")
    return new_dates


if __name__ == "__main__":
    new = scrape_all()
    print(f"Scraped {len(new)} new reports.")
