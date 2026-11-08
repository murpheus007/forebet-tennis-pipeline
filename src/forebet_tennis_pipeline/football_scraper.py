#!/usr/bin/env python3
"""
Forebet Football 1x2 Historical Scraper
Scrapes football 1x2 predictions for past dates.
"""

import asyncio
import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from playwright.async_api import async_playwright

# Configuration
BASE_URL = "https://www.forebet.com/en/football-predictions/predictions-1x2/"
OUTPUT_BASE = Path(__file__).parent.parent.parent / "data" / "json"
OUTPUT_BASE.mkdir(parents=True, exist_ok=True)

WORKERS = 3
HEADLESS = False
SCRAPE_DELAY = 3


async def setup_stealth_context(context):
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        window.chrome = { runtime: {} };
    """)


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


def parse_rcnt_block(text: str) -> dict | None:
    """
    Parse .rcnt text block for football 1x2 format.
    Format:
    Line 0: League code
    Line 1: Team1 Team2 Date Time
    Line 2: Match ID
    Line 3: Prediction code
    Line 4: Predicted score
    Line 5: Coef/odds
    Line 6: Temperature
    Line 7: Avg goals
    Line 8: 1x2 odds (home draw away)
    Line 9: Flags
    Line 10: Status
    Line 11: Final score (with half-time)
    Line 12: -
    Line 13: Additional flags
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 10:
        return None

    match: dict = {"raw_lines": lines}

    # League
    match["league"] = lines[0]

    # Parse teams and datetime from line 1
    line1 = lines[1]
    dt_match = re.search(r"(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2})", line1)
    if dt_match:
        match["datetime"] = dt_match.group(1)
        # Remove datetime from line to get teams
        teams_part = line1[:line1.rfind(dt_match.group(1))].strip()
    else:
        teams_part = line1

    # Split teams - they're concatenated like "Team1 Team2"
    # Try to split on known patterns
    # Usually format: "HomeTeam AwayTeam" but names can have spaces
    # We'll store the raw and try to split later
    match["teams_raw"] = teams_part

    # Match ID
    if len(lines) > 2 and re.match(r"^\d{4,}$", lines[2]):
        match["match_id"] = lines[2]

    # Prediction code
    if len(lines) > 3:
        match["prediction_code"] = lines[3]

    # Predicted score
    if len(lines) > 4:
        match["pred_score"] = lines[4]

    # Coef/odds
    if len(lines) > 5:
        try:
            match["coef"] = float(lines[5])
        except ValueError:
            pass

    # Temperature
    if len(lines) > 6:
        temp_match = re.search(r"(\d+)°", lines[6])
        if temp_match:
            match["temperature"] = int(temp_match.group(1))

    # Avg goals
    if len(lines) > 7:
        try:
            match["avg_goals"] = float(lines[7])
        except ValueError:
            pass

    # 1x2 odds
    if len(lines) > 8:
        # Line 8 contains odds + flags, e.g., "2.20 3.60 3.10 no no no" or "2.75 3.40 2.50 up down down"
        odds_line = lines[8]
        # Extract first three numbers (odds)
        odds_nums = re.findall(r"(\d+\.\d+)", odds_line)
        if len(odds_nums) >= 3:
            try:
                match["odds_home"] = float(odds_nums[0])
                match["odds_draw"] = float(odds_nums[1])
                match["odds_away"] = float(odds_nums[2])
            except ValueError:
                pass

    # Status
    if len(lines) > 9:
        match["status"] = lines[9]

    # Final score with half-time
    if len(lines) > 10:
        score_line = lines[10]
        # Format: "3 - 0(1 - 0)" -> final 3-0, HT 1-0
        ft_match = re.search(r"(\d+)\s*-\s*(\d+)\s*\((\d+)\s*-\s*(\d+)\)", score_line)
        if ft_match:
            match["actual_score"] = f"{ft_match.group(1)}-{ft_match.group(2)}"
            match["ht_score"] = f"{ft_match.group(3)}-{ft_match.group(4)}"
            match["actual_winner"] = 1 if int(ft_match.group(1)) > int(ft_match.group(2)) else (2 if int(ft_match.group(2)) > int(ft_match.group(1)) else 0)
        else:
            # Simple format: "3 - 0"
            simple_match = re.search(r"(\d+)\s*-\s*(\d+)", score_line)
            if simple_match:
                match["actual_score"] = f"{simple_match.group(1)}-{simple_match.group(2)}"
                match["actual_winner"] = 1 if int(simple_match.group(1)) > int(simple_match.group(2)) else (2 if int(simple_match.group(2)) > int(simple_match.group(1)) else 0)

    # Prediction winner from prediction code
    pred_code = match.get("prediction_code", "")
    # Format: FT-HT (e.g., X1-1, 12-1, 21-2)
    # First part: full-time prediction (1, X, 2, 1X, X2, 12)
    # Second part: half-time prediction (1, X, 2)
    pred_map = {"1": 1, "X": 0, "2": 2}
    if pred_code and "-" in pred_code:
        ft_pred = pred_code.split("-")[0]
        # Get first character of full-time prediction
        first_char = ft_pred[0]
        match["pred_winner"] = pred_map.get(first_char, 0)
    elif pred_code:
        first_char = pred_code[0]
        match["pred_winner"] = pred_map.get(first_char, 0)

    # Calculate probabilities from 1x2 odds if available
    if all(k in match for k in ["odds_home", "odds_draw", "odds_away"]):
        h, d, a = match["odds_home"], match["odds_draw"], match["odds_away"]
        total_inv = 1/h + 1/d + 1/a
        match["p1_prob"] = round((1/h) / total_inv * 100)
        match["p_draw_prob"] = round((1/d) / total_inv * 100)
        match["p2_prob"] = round((1/a) / total_inv * 100)
        # Favorite probabilities
        probs = {"1": match["p1_prob"], "X": match["p_draw_prob"], "2": match["p2_prob"]}
        fav = max(probs, key=probs.get)
        match["favorite"] = fav
        match["favorite_prob"] = probs[fav]

    return match


async def scrape_date(page, date_str: str) -> dict:
    """Scrape a single date."""
    url = f"{BASE_URL}{date_str}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)

    cf_passed = await wait_for_cloudflare(page, max_wait=120)
    if not cf_passed:
        return {"date": date_str, "error": "Cloudflare challenge failed", "matches": []}

    await asyncio.sleep(2)

    rcnt_elements = await page.locator(".rcnt").all()
    matches = []

    for i, el in enumerate(rcnt_elements):
        try:
            text = await el.inner_text()
            if not text.strip():
                continue
            parsed = parse_rcnt_block(text)
            if parsed:
                parsed["index"] = i
                matches.append(parsed)
            else:
                print(f"  ✗ Parse failed for match {i}")
        except Exception as e:
            print(f"  ✗ Error parsing match {i}: {e}")

    result = {
        "url": url,
        "scraped_at": date.today().isoformat(),
        "match_count": len(matches),
        "matches": matches,
        "page_title": await page.title(),
    }

    # Save to file
    out_file = OUTPUT_BASE / f"{date_str}_football_1x2.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"  ✓ {len(matches)} matches saved to {out_file}")
    return result


async def scrape_date_range(start_date: str, end_date: str, workers: int = WORKERS) -> list:
    """Scrape a range of dates."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]

    print(f"{'='*60}")
    print(f"Scraping range: {start_date} to {end_date}")
    print(f"Output: {OUTPUT_BASE}")
    print(f"Workers: {workers}")
    print(f"{'='*60}\n")

    task_queue = asyncio.Queue()
    for d in dates:
        await task_queue.put((d.isoformat(), OUTPUT_BASE))

    results = []
    semaphore = asyncio.Semaphore(workers)

    async def worker(worker_id: int):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=HEADLESS,
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

            # Block unnecessary resources
            await page.route("**/*", lambda route: (
                route.abort() if route.request.resource_type in ["stylesheet", "image", "font", "media", "script"] and not route.request.url.endswith(".js") else route.continue_()
            ))

            while True:
                try:
                    target_date, out_dir = await asyncio.wait_for(task_queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    break

                out_file = out_dir / f"{target_date}_football_1x2.json"
                if out_file.exists():
                    print(f"  [W{worker_id}] {target_date} - SKIP (exists)")
                    task_queue.task_done()
                    continue

                print(f"  [W{worker_id}] Scraping {target_date}...")

                async with semaphore:
                    result = await scrape_date(page, target_date)

                if "error" not in result:
                    with open(out_dir / f"{target_date}_football_1x2.json", "w", encoding="utf-8") as f:
                        json.dump(result, f, indent=2, ensure_ascii=False)

                results.append(result)
                task_queue.task_done()
                await asyncio.sleep(1)

            await browser.close()

    await asyncio.gather(*[worker(i) for i in range(workers)])
    await task_queue.join()

    print(f"\n✓ Range complete: {len(results)} days scraped")
    return results


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Forebet football 1x2 predictions")
    parser.add_argument("start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", nargs="?", help="End date (YYYY-MM-DD), defaults to start_date")
    parser.add_argument("--workers", type=int, default=3, help="Number of parallel workers")
    args = parser.parse_args()

    start = args.start_date
    end = args.end_date or start

    await scrape_date_range(start, end, args.workers)


if __name__ == "__main__":
    asyncio.run(main())


def cli_main():
    """Sync wrapper for console script entry point."""
    import asyncio
    asyncio.run(main())