#!/usr/bin/env python3
"""
Forebet Tennis Live Scraper - For current/future dates.
"""

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

OUTPUT_DIR = Path(__file__).parent.parent / "data" / "json"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
async def wait_for_cloudflare(page, max_wait: int = 120) -> bool:
    print(f"  Waiting for Cloudflare challenge (max {max_wait}s)...")
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < max_wait:
        title = await page.title()
        if "Just a moment" not in title and "Attention Required" not in title:
            # Also verify content is actually loaded
            rcnt_count = await page.locator(".rcnt").count()
            if rcnt_count > 0:
                print(f"  ✓ Cloudflare passed - Title: {title}, rcnt elements: {rcnt_count}")
                return True
            else:
                print(f"  Title changed but no content yet... waiting")
        await asyncio.sleep(3)
    print("  ✗ Cloudflare timeout")
    return False

def parse_match_block(text: str) -> dict | None:
    """
    Parse CONCATENATED .rcnt text format (live page format).
    Format:
    [0] "L. TienA. Blockx 21/12/2025 18:10" - players + datetime
    [1] "5644" - match_id
    [2] "12-0" - prediction_code
    [3] "2" - pred_sets_p1
    [4] "0" - pred_sets_p2
    [5] "7.8" - avg_games_per_set
    [6] "1.50" - coef
    [7+] set scores... then FT, actual_p1, actual_p2
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 7:
        return None

    match = {"raw_lines": lines}

    # Find datetime line
    dt_idx = -1
    for i, line in enumerate(lines):
        if re.search(r"\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}", line):
            dt_idx = i
            match["datetime"] = re.search(r"(\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2})", line).group(1)
            break
    
    if dt_idx != 0:
        return None

    # CONCATENATED format parsing
    first_line = lines[0]
    first_line = first_line[:first_line.rfind(match["datetime"])].strip()
    
    # Pattern: Initial. Surname (multi-word) followed by another Initial. or end
    name_pattern = r"([A-Z]\.\s*[A-Za-z]+(?:\s+[A-Za-z]+)*(?=[A-Z]\.|$))"
    name_matches = list(re.finditer(name_pattern, first_line))
    if len(name_matches) >= 2:
        match["player1"] = name_matches[0].group(1).strip()
        match["player2"] = name_matches[1].group(1).strip()
    else:
        parts = [p for p in re.split(r"(?=[A-Z]\.\s)", first_line) if p.strip()]
        if len(parts) >= 2:
            match["player1"] = parts[0].strip()
            match["player2"] = parts[1].strip()
        else:
            match["player1"] = first_line
            match["player2"] = ""
    
    # Match ID at line 1
    if len(lines) > 1 and re.match(r"^\d{4,}$", lines[1]):
        match["match_id"] = lines[1]
    
    # Prediction code at line 2
    if len(lines) > 2:
        match["prediction_code"] = lines[2]
        pred_code = lines[2]
        pred_match = re.match(r"^(\d)(\d)-(\d)$", pred_code)
        if pred_match:
            match["pred_winner"] = int(pred_match.group(1))
            match["pred_score"] = f"{pred_match.group(2)}-{pred_match.group(3)}"
        elif re.match(r"^\d\d-\d$", pred_code):
            match["pred_winner"] = int(pred_code[0])
            match["pred_score"] = f"{pred_code[1]}-{pred_code[2]}"
        elif re.match(r"^\d+\.\d+\.\d+$", pred_code):
            parts = pred_code.split('.')
            if len(parts) == 3:
                try:
                    set1 = int(parts[1])
                    set2 = int(parts[2])
                    match["pred_winner"] = 1 if set1 > set2 else 2
                    match["pred_score"] = f"{set1}-{set2}"
                except ValueError:
                    pass
    
    # Lines 3,4 = predicted sets for p1, p2
    if len(lines) > 3 and lines[3].isdigit():
        match["pred_sets_p1"] = int(lines[3])
    if len(lines) > 4 and lines[4].isdigit():
        match["pred_sets_p2"] = int(lines[4])
    
    # Lines 5,6 = avg_games_per_set, coef
    if len(lines) > 5:
        try:
            match["avg_games_per_set"] = float(lines[5])
        except ValueError:
            pass
    if len(lines) > 6:
        try:
            match["coef"] = float(lines[6])
        except ValueError:
            pass
    
    # Parse set scores from line 7 onwards
    set_scores = []
    tiebreak_data = []
    
    score_lines = lines[7:]
    status_idx = -1
    for i, line in enumerate(score_lines):
        if line in ("FT", "Cancl.", "HT", "Live", "Postp."):
            status_idx = i
            match["status"] = line
            break
    
    # Parse set scores up to status
    score_data = score_lines[:status_idx] if status_idx >= 0 else score_lines
    i = 0
    while i + 1 < len(score_data):
        home_raw = score_data[i]
        away_raw = score_data[i + 1]
        
        if home_raw.isdigit() and away_raw.isdigit():
            if len(home_raw) == 2 and len(away_raw) == 2:
                # Both have tiebreak: e.g., "47", "34"
                home_games = int(home_raw[0])
                home_tb = int(home_raw[1])
                away_games = int(away_raw[0])
                away_tb = int(away_raw[1])
                set_scores.append(home_games)
                set_scores.append(away_games)
                tiebreak_data.append({
                    "home_games": home_games, "away_games": away_games,
                    "home_tiebreak": home_tb, "away_tiebreak": away_tb,
                    "winner": 1 if home_games > away_games else 2,
                    "has_tiebreak": True
                })
            elif len(home_raw) == 1 and len(away_raw) == 1:
                # No tiebreak: e.g., "4", "2"
                home_games = int(home_raw)
                away_games = int(away_raw)
                set_scores.append(home_games)
                set_scores.append(away_games)
                tiebreak_data.append({
                    "home_games": home_games, "away_games": away_games,
                    "home_tiebreak": None, "away_tiebreak": None,
                    "winner": 1 if home_games > away_games else 2,
                    "has_tiebreak": False
                })
            else:
                # Mismatched lengths - store as single digits
                set_scores.append(int(home_raw))
                set_scores.append(int(away_raw))
        i += 2
    
    if set_scores:
        match["set_scores"] = set_scores
    if tiebreak_data:
        match["tiebreak_details"] = tiebreak_data
    
    # Actual winner from LAST two integers after status (FT)
    if status_idx >= 0:
        post_status = score_lines[status_idx + 1:]
        post_status_ints = [int(l) for l in post_status if l.isdigit() and int(l) <= 20]
        if len(post_status_ints) >= 2:
            actual_sets_home = post_status_ints[-2]
            actual_sets_away = post_status_ints[-1]
            match["actual_winner"] = 1 if actual_sets_home > actual_sets_away else 2
            match["actual_score"] = f"{actual_sets_home}-{actual_sets_away}"
    
    # Football filtering: prediction codes with X or team names
    pred_code = match.get("prediction_code", "")
    if not pred_code or re.search(r"[A-Za-z]", pred_code):
        return None
    
    return match


async def scrape_date(page, date_str: str) -> dict:
    """Scrape a single date."""
    url = f"https://www.forebet.com/en/tennis/predictions/{date_str}"
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await wait_for_cloudflare(page, max_wait=60)
    await asyncio.sleep(3)

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
        except Exception as e:
            print(f"  ✗ Error parsing match {i}: {e}")

    return {
        "url": f"https://www.forebet.com/en/tennis/predictions/{date_str}",
        "scraped_at": datetime.now().isoformat(),
        "match_count": len(matches),
        "matches": matches,
        "page_title": await page.title(),
    }


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape Forebet live tennis data")
    parser.add_argument("date", nargs="?", help="Date (YYYY-MM-DD), defaults to today")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    output_file = OUTPUT_DIR / f"{date_str}_live.json"

    print(f"Scraping {date_str}...")

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
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        page = await context.new_page()

        try:
            result = await scrape_date(page, date_str)
            
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            print(f"✓ Done! Saved to: {output_file}")
            print(f"  Matches extracted: {result['match_count']}")

        except Exception as e:
            print(f"Error: {e}")
            raise
        finally:
            await browser.close()


async def wait_for_cloudflare(page, max_wait: int = 120) -> bool:
    print(f"  Waiting for Cloudflare challenge (max {max_wait}s)...")
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < max_wait:
        title = await page.title()
        if "Just a moment" not in title and "Attention Required" not in title:
            # Also verify content is actually loaded
            rcnt_count = await page.locator(".rcnt").count()
            if rcnt_count > 0:
                print(f"  ✓ Cloudflare passed - Title: {title}, rcnt elements: {rcnt_count}")
                return True
            else:
                print(f"  Title changed but no content yet... waiting")
        await asyncio.sleep(3)
    print("  ✗ Cloudflare timeout")
    return False


def cli_main():
    """Sync wrapper for console script entry point."""
    import asyncio
    asyncio.run(main())


if __name__ == "__main__":
    cli_main()