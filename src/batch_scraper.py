#!/usr/bin/env python3
"""
Parallel batch scraper for Forebet tennis predictions.
Uses 3 browser workers for speed.
Organizes JSON by year: data/json/2025/, data/json/2026/
"""

import asyncio
import json
import sys
import queue
import threading
from datetime import date, timedelta
from pathlib import Path
from playwright.async_api import async_playwright

# Import parser from scraper module
sys.path.insert(0, str(Path(__file__).parent))
from scraper import parse_match_block

# ─── Config ───
BASE_URL = "https://www.forebet.com/en/tennis/predictions/"
OUTPUT_BASE = Path(__file__).parent.parent / "data" / "json"
WORKERS = 3

async def wait_for_cloudflare(page, max_wait: int = 120) -> bool:
    print(f"  Waiting for Cloudflare challenge (max {max_wait}s)...")
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < max_wait:
        title = await page.title()
        if "Just a moment" not in title and "Attention Required" not in title:
            rcnt_count = await page.locator(".rcnt").count()
            if rcnt_count > 0:
                print(f"  ✓ Cloudflare passed - Title: {title}, rcnt elements: {rcnt_count}")
                return True
            else:
                print(f"  Title changed but no content yet... waiting")
        await asyncio.sleep(3)
    print("  ✗ Cloudflare timeout")
    return False

async def setup_stealth_context(context):
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = { runtime: {} };
    """)

async def scrape_day(page, target_date: date) -> dict:
    url = f"{BASE_URL}{target_date.isoformat()}"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        return {"date": target_date.isoformat(), "error": f"Navigation failed: {e}", "matches": []}

    cf_passed = await wait_for_cloudflare(page, max_wait=120)
    if not cf_passed:
        try:
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(3)
            cf_passed = await wait_for_cloudflare(page, max_wait=60)
        except Exception:
            pass

    if not cf_passed:
        return {"date": target_date.isoformat(), "error": "Cloudflare challenge failed", "matches": []}

    await asyncio.sleep(2)

    rcnt_elements = await page.locator(".rcnt").all()
    prob_elements = await page.locator(".fprc.bsk").all()

    probs = []
    for el in prob_elements:
        spans = await el.locator("span").all()
        if len(spans) >= 2:
            p1_text = await spans[0].inner_text()
            p2_text = await spans[1].inner_text()
            if p1_text in ("1", "2") and p2_text in ("1", "2"):
                continue
            p1_class = await spans[0].get_attribute("class")
            p2_class = await spans[1].get_attribute("class")
            probs.append({
                "p1_prob": int(p1_text) if p1_text.isdigit() else None,
                "p2_prob": int(p2_text) if p2_text.isdigit() else None,
                "p1_favorite": "fpr" in (p1_class or ""),
                "p2_favorite": "fpr" in (p2_class or ""),
            })

    matches = []
    for i, el in enumerate(rcnt_elements):
        try:
            text = await el.inner_text()
            if not text or len(text.strip()) < 10:
                continue
            parsed = parse_match_block(text)
            if parsed:
                parsed["index"] = i
                if i < len(probs):
                    parsed.update(probs[i])
                matches.append(parsed)
            else:
                print(f"  ✗ Parse failed for match {i}")
        except Exception as e:
            print(f"  ✗ Error parsing match {i}: {e}")

    return {
        "date": target_date.isoformat(),
        "url": f"{BASE_URL}{target_date.isoformat()}",
        "match_count": len(matches),
        "matches": matches,
        "page_title": await page.title(),
    }

async def worker(worker_id: int, task_queue: asyncio.Queue, results: list, semaphore: asyncio.Semaphore):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        await setup_stealth_context(context)
        page = await context.new_page()

        while True:
            try:
                target_date, out_dir = await asyncio.wait_for(task_queue.get(), timeout=5)
            except asyncio.TimeoutError:
                break

            out_file = out_dir / f"{target_date.isoformat()}.json"

            if out_file.exists():
                print(f"  [W{worker_id}] {target_date.isoformat()} - SKIP (exists)")
                task_queue.task_done()
                continue

            print(f"  [W{worker_id}] Scraping {target_date.isoformat()}...")

            async with semaphore:
                result = await scrape_day(page, target_date)

            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            count = result.get("match_count", 0)
            print(f"  [W{worker_id}] ✓ {target_date.isoformat()}: {count} matches")

            results.append(result)
            task_queue.task_done()

            await asyncio.sleep(1)

        await browser.close()

async def scrape_year(year: int):
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    out_dir = OUTPUT_BASE / str(year)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Scraping {year}: {start} to {end}")
    print(f"Output: {out_dir}")
    print(f"Workers: {WORKERS}")
    print(f"{'='*60}\n")

    task_queue = asyncio.Queue()
    current = start
    while current <= end:
        await task_queue.put((current, out_dir))
        current += timedelta(days=1)

    results = []
    semaphore = asyncio.Semaphore(WORKERS)

    workers = [
        worker(i, task_queue, results, semaphore)
        for i in range(WORKERS)
    ]

    await asyncio.gather(*workers)
    await task_queue.join()

    print(f"\n✓ {year} complete: {len(results)} days scraped")
    return results

async def scrape_date_range(start, end):
    """Scrape a custom date range."""
    out_dir = OUTPUT_BASE / "custom"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"Scraping range: {start} to {end}")
    print(f"Output: {out_dir}")
    print(f"Workers: {WORKERS}")
    print(f"{'='*60}\n")

    task_queue = asyncio.Queue()
    current = start
    while current <= end:
        await task_queue.put((current, out_dir))
        current += timedelta(days=1)

    results = []
    semaphore = asyncio.Semaphore(WORKERS)

    workers = [
        worker(i, task_queue, results, semaphore)
        for i in range(WORKERS)
    ]

    await asyncio.gather(*workers)
    await task_queue.join()

    print(f"\n✓ Range complete: {len(results)} days scraped")
    return results

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Parallel batch scraper for Forebet tennis")
    parser.add_argument("--year", type=int, help="Scrape full year (e.g., 2025)")
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", help="End date YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=3, help="Number of parallel workers")
    args = parser.parse_args()

    if args.year:
        await scrape_year(args.year)
    elif args.start and args.end:
        from datetime import date
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
        global WORKERS
        WORKERS = args.workers
        await scrape_date_range(start, end)
    else:
        for year in [2025, 2026]:
            await scrape_year(year)

if __name__ == "__main__":
    asyncio.run(main())


def cli_main():
    """Sync wrapper for console script entry point."""
    import asyncio
    asyncio.run(main())