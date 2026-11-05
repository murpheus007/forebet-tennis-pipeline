"""
ETL module for Forebet Tennis Pipeline.
Loads JSON files into PostgreSQL database.
"""

import json
import os
from pathlib import Path
from datetime import datetime
from psycopg2.extras import Json
from forebet_tennis_pipeline.database import Database

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
JSON_DIR = DATA_DIR / "json"


def parse_datetime(dt_str: str) -> datetime | None:
    """Parse 'DD/MM/YYYY HH:MM' to datetime."""
    if not dt_str:
        return None
    try:
        return datetime.strptime(dt_str, "%d/%m/%Y %H:%M")
    except ValueError:
        return None


class ETL:
    def __init__(self):
        self.db = Database()

    def parse_datetime(self, dt_str: str) -> datetime | None:
        """Parse 'DD/MM/YYYY HH:MM' to datetime."""
        return parse_datetime(dt_str)

    def process_file(self, filepath: Path) -> dict:
        """Process a single JSON file."""
        print(f"Processing {filepath.name}...")

        # Read JSON file
        try:
            with filepath.open() as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ERROR reading {filepath.name}: {e}")
            return {"processed": 0, "failed": 1}

        matches = data.get("matches", [])
        if not matches:
            print(f"  No matches in {filepath.name}")
            return {"processed": 0, "failed": 0}

        processed = 0
        failed = 0

        # Database connection
        try:
            with self.db.get_connection() as conn:
                with conn.cursor() as cur:
                    for m in matches:
                        player1 = m.get("player1", "").strip()
                        player2 = m.get("player2", "").strip()
                        if not player1 or not player2:
                            continue

                        dt = self.parse_datetime(m.get("datetime", ""))
                        if not dt:
                            continue

                        match_date = dt.date()
                        match_id = m.get("match_id", "").strip()
                        prediction_code = m.get("prediction_code", "").strip()
                        pred_winner = m.get("pred_winner")
                        pred_score = m.get("pred_score", "").strip()
                        actual_winner = m.get("actual_winner")
                        actual_score = m.get("actual_score", "").strip()
                        status = m.get("status", "").strip()
                        coef = m.get("coef")
                        avg_games = m.get("avg_games_per_set")
                        set_scores = m.get("set_scores", [])
                        p1_prob = m.get("p1_prob")
                        p2_prob = m.get("p2_prob")
                        p1_favorite = m.get("p1_favorite")
                        p2_favorite = m.get("p2_favorite")

                        try:
                            cur.execute("""
                                INSERT INTO matches (
                                    match_date, match_id, player1, player2,
                                    datetime, prediction_code, pred_winner, pred_score,
                                    actual_winner, actual_score, status,
                                    coef, avg_games_per_set, set_scores,
                                    p1_prob, p2_prob, p1_favorite, p2_favorite
                                ) VALUES (
                                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                )
                                ON CONFLICT (match_date, match_id, player1, player2) DO UPDATE SET
                                    datetime = EXCLUDED.datetime,
                                    prediction_code = EXCLUDED.prediction_code,
                                    pred_winner = EXCLUDED.pred_winner,
                                    pred_score = EXCLUDED.pred_score,
                                    actual_winner = EXCLUDED.actual_winner,
                                    actual_score = EXCLUDED.actual_score,
                                    status = EXCLUDED.status,
                                    coef = EXCLUDED.coef,
                                    avg_games_per_set = EXCLUDED.avg_games_per_set,
                                    set_scores = EXCLUDED.set_scores,
                                    p1_prob = EXCLUDED.p1_prob,
                                    p2_prob = EXCLUDED.p2_prob,
                                    p1_favorite = EXCLUDED.p1_favorite,
                                    p2_favorite = EXCLUDED.p2_favorite
                            """, (
                                match_date, match_id, player1, player2,
                                dt, prediction_code, pred_winner, pred_score,
                                actual_winner, actual_score, status,
                                coef, avg_games, set_scores,
                                p1_prob, p2_prob, p1_favorite, p2_favorite
                            ))
                            processed += 1
                        except Exception as e:
                            print(f"  ERROR upserting {player1} vs {player2}: {e}")
                            failed += 1
                            continue

                conn.commit()
                print(f"  {filepath.name}: {processed} matches processed, {failed} failed")
                return {"processed": processed, "failed": failed}

        except Exception as e:
            print(f"  ERROR processing {filepath.name}: {e}")
            return {"processed": 0, "failed": 1}

    def process_all(self, pattern: str = "*_historical.json") -> dict:
        """Process all JSON files matching pattern."""
        total_processed = 0
        total_failed = 0

        # Look in data/json/ and subdirectories
        files = sorted(JSON_DIR.rglob(pattern))

        if not files:
            print(f"No files found matching pattern: {pattern}")
            return {"processed": 0, "failed": 0}

        print(f"Found {len(files)} files to process")

        for filepath in files:
            result = self.process_file(filepath)
            total_processed += result["processed"]
            total_failed += result["failed"]

        print(f"\n=== ETL COMPLETE ===")
        print(f"Total processed: {total_processed}")
        print(f"Total failed: {total_failed}")

        return {"processed": total_processed, "failed": total_failed}


def main():
    """Entry point for console script."""
    import argparse
    parser = argparse.ArgumentParser(description="Load JSON files into PostgreSQL")
    parser.add_argument("--pattern", default="*_historical.json", help="File pattern to process")
    parser.add_argument("--file", help="Process single file")
    args = parser.parse_args()

    etl = ETL()
    if args.file:
        etl.process_file(Path(args.file))
    else:
        etl.process_all(args.pattern)


if __name__ == "__main__":
    main()