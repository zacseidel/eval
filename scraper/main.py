"""Entry point: runs scrape then process pipeline."""
import sys
from pathlib import Path

# Allow running as `python scraper/main.py` from project root
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from scrape import scrape_all
from process import process_all

if __name__ == "__main__":
    print("=== Step 1: Scraping momentum9 reports ===")
    new_reports = scrape_all()
    print(f"New reports scraped: {len(new_reports)}\n")

    print("=== Step 2: Processing portfolio positions ===")
    process_all()
    print("\nDone.")
