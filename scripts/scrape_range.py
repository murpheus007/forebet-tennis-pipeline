#!/usr/bin/env python3
"""
Scrape date range - entry point for scraping.
"""
#!/usr/bin/env python3
import sys
import os
import asyncio

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.scraper import scrape_date_range
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape Forebet historical tennis data")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", nargs="?", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    end_date = args.end_date or args.start_date
    asyncio.run(scrape_date_range(args.start_date, end_date))