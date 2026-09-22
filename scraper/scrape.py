"""Scrapes Momentum weekly reports and saves raw data as JSON."""
import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://zacseidel.github.io/momentum"
INDEX_URL = f"{BASE_URL}/"
SCRAPED_DIR = Path(__file__).parent.parent / "data" / "scraped"
SECTION_IDS = {
    "munger":  "summary-munger",
    "munger400l": "summary-munger400l",
    "munger400r": "summary-munger400r",
    "megacap": "summary-megacap",
    "sp500":   "summary-sp500",
    "sp400":   "summary-sp400",
}

SECTION_TITLE_PREFIXES = {
    "munger400l": "munger400l",
    "munger400r": "munger400r",
}


def report_url(report_date):
    return f"{BASE_URL}/reports/momentum_{report_date}.html"


def fetch(url):
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def industry_report_url(report_date):
    return f"{BASE_URL}/reports/industry_{report_date}.html"


def get_report_dates(soup):
    momentum_dates, _industry_dates = get_linked_reports(soup)
    return momentum_dates


def get_linked_reports(soup):
    """Return sorted momentum dates and industry-rank dates linked from the index."""
    momentum = set()
    industry = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        momentum_match = re.search(r"reports/momentum_(\d{4}-\d{2}-\d{2})\.html", href)
        if momentum_match:
            momentum.add(momentum_match.group(1))
        industry_match = re.search(r"reports/industry_(\d{4}-\d{2}-\d{2})\.html", href)
        if industry_match:
            industry.add(industry_match.group(1))
    return sorted(momentum), sorted(industry)


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


_TICKER_AT_END = re.compile(r"\(([A-Z][A-Z0-9.\-]*)\)\s*$")


def _heading_text(tag):
    return tag.get_text(" ", strip=True)


def _signed_rank_change(cell):
    text = cell.get_text(" ", strip=True)
    magnitude_match = re.search(r"(\d+)", text)
    if not magnitude_match:
        return None
    magnitude = int(magnitude_match.group(1))
    classes = cell.get("class") or []
    if "down" in classes or "↓" in text:
        return -magnitude
    if "up" in classes or "↑" in text:
        return magnitude
    return 0


def _parse_rank_change_row(cells):
    entity = cells[0].get_text(" ", strip=True)
    ticker_match = _TICKER_AT_END.search(entity)
    if not ticker_match:
        raise ValueError(f"Industry rank-change row has no ticker: {entity}")
    previous_match = re.search(r"(\d+)", cells[1].get_text(" ", strip=True))
    current_match = re.search(r"(\d+)", cells[2].get_text(" ", strip=True))
    return_match = re.search(r"([+-]?[0-9.]+)\s*%", cells[4].get_text(" ", strip=True))
    rank_change = _signed_rank_change(cells[3])
    if rank_change is None or previous_match is None or current_match is None:
        raise ValueError(f"Industry rank-change row is incomplete: {entity}")
    return {
        "ticker": ticker_match.group(1),
        "previous_rank": int(previous_match.group(1)),
        "current_rank": int(current_match.group(1)),
        "rank_change": rank_change,
        "return_12m": float(return_match.group(1)) if return_match else None,
    }


def parse_industry_stock_rank_changes(soup):
    """Parse Stocks → Largest rank changes into the published top 5 each way.

    Returns (positive_rows, negative_rows), or None when that Stocks block
    is not on the page. Table order is kept. A sixth name in either direction
    is ignored so the evaluation stays on the published top 5.
    """
    stocks_heading = next(
        (
            heading for heading in soup.find_all("h3")
            if _heading_text(heading).casefold() == "stocks"
        ),
        None,
    )
    if stocks_heading is None:
        return None

    largest_heading = None
    for sibling in stocks_heading.next_siblings:
        if getattr(sibling, "name", None) in {"h2", "h3"}:
            break
        if (
            getattr(sibling, "name", None) == "h4"
            and _heading_text(sibling).casefold() == "largest rank changes"
        ):
            largest_heading = sibling
            break
    if largest_heading is None:
        return None

    table = None
    for sibling in largest_heading.next_siblings:
        if getattr(sibling, "name", None) in {"h2", "h3", "h4"}:
            break
        if getattr(sibling, "name", None) == "table":
            table = sibling
            break

    up = []
    down = []
    if table is not None:
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            parsed = _parse_rank_change_row(cells)
            if parsed["rank_change"] > 0 and len(up) < 5:
                up.append(parsed)
            elif parsed["rank_change"] < 0 and len(down) < 5:
                down.append(parsed)

    for rank, row in enumerate(up, start=1):
        row["rank"] = rank
    for rank, row in enumerate(down, start=1):
        row["rank"] = rank
    return up, down


def apply_industry_rank_changes(report, soup, industry_url):
    parsed = parse_industry_stock_rank_changes(soup)
    if parsed is None:
        raise RuntimeError(
            "Industry report has no Stocks → Largest rank changes block"
        )
    up, down = parsed
    report["industry_source_url"] = industry_url
    report["industry_rank_up"] = up
    report["industry_rank_down"] = down
    sections = report.setdefault("sections_present", [])
    for section in ("industry_rank_up", "industry_rank_down"):
        if section not in sections:
            sections.append(section)
    return report


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


def parse_report(date, soup, source_url=None):
    result = {
        "date": date,
        "source_url": source_url or report_url(date),
        "sections_present": [],
        "sp500": [],
        "megacap": [],
        "sp400": [],
        "munger": [],
        "munger400l": [],
        "munger400r": [],
    }

    for section, h2_id in SECTION_IDS.items():
        h2 = soup.find("h2", id=h2_id)
        if not h2 and section in SECTION_TITLE_PREFIXES:
            expected_prefix = SECTION_TITLE_PREFIXES[section]
            h2 = next(
                (
                    heading for heading in soup.find_all("h2")
                    if heading.get_text(" ", strip=True).casefold().startswith(expected_prefix)
                ),
                None,
            )
        if not h2:
            continue
        result["sections_present"].append(section)
        if section in {"munger", "munger400l", "munger400r"}:
            result[section] = parse_munger_section(h2, date)
        else:
            result[section] = parse_leaders_section(h2, date)

    result["universe_updates"] = parse_universe_updates(soup)
    return result


def _load_snapshot(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _empty_report(report_date):
    return {
        "date": report_date,
        "source_url": None,
        "sections_present": [],
        "sp500": [],
        "megacap": [],
        "sp400": [],
        "munger": [],
        "munger400l": [],
        "munger400r": [],
        "universe_updates": [],
    }


def _copy_industry_fields(source, target):
    for key in ("industry_source_url", "industry_rank_up", "industry_rank_down"):
        if key in source:
            target[key] = source[key]
    sections = target.setdefault("sections_present", [])
    for section in source.get("sections_present", []):
        if section.startswith("industry_rank") and section not in sections:
            sections.append(section)


def scrape_all(force=False):
    SCRAPED_DIR.mkdir(parents=True, exist_ok=True)
    index_soup = fetch(INDEX_URL)
    momentum_dates, industry_dates = get_linked_reports(index_soup)
    dates = sorted(set(momentum_dates) | set(industry_dates))
    print(
        f"Found {len(momentum_dates)} momentum reports and "
        f"{len(industry_dates)} industry-rank reports on the index page."
    )

    updated_dates = []
    for date in dates:
        out_path = SCRAPED_DIR / f"{date}.json"
        momentum_url = report_url(date) if date in momentum_dates else None
        industry_url = industry_report_url(date) if date in industry_dates else None
        existing = None if force or not out_path.exists() else _load_snapshot(out_path)
        need_momentum = bool(
            momentum_url and (existing is None or existing.get("source_url") != momentum_url)
        )
        need_industry = bool(
            industry_url and (
                existing is None or existing.get("industry_source_url") != industry_url
            )
        )
        if not need_momentum and not need_industry:
            continue
        print(f"  Scraping {date}...")
        try:
            if need_momentum:
                data = parse_report(date, fetch(momentum_url), source_url=momentum_url)
                if existing and not need_industry:
                    _copy_industry_fields(existing, data)
            elif existing:
                data = existing
            else:
                data = _empty_report(date)
            if need_industry:
                apply_industry_rank_changes(data, fetch(industry_url), industry_url)
            out_path.write_text(json.dumps(data, indent=2))
            updated_dates.append(date)
            time.sleep(0.3)
        except Exception as e:
            print(f"  ERROR scraping {date}: {e}")
    return updated_dates


if __name__ == "__main__":
    updated = scrape_all()
    print(f"Scraped or refreshed {len(updated)} reports.")
