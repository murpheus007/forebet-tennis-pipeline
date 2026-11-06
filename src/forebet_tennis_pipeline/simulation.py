"""
Forebet Tennis Simulation Module
Simulates betting strategies on historical tennis match data.
"""

import os
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv


# Load .env from config directory
config_path = Path(__file__).parent.parent.parent / "config" / ".env"
load_dotenv(config_path)


class Database:
    """Database connection manager."""

    def __init__(self):
        self.conn_params = {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", 5432)),
            "database": os.getenv("DB_NAME", "forebet_tennis"),
            "user": os.getenv("DB_USER", "postgres"),
            "password": os.getenv("DB_PASSWORD", ""),
        }

    def get_connection(self):
        return psycopg2.connect(**self.conn_params)


def run_simulation(
    min_prob: int = 70,
    min_coef: float = 1.50,
    max_coef: float = 1.80,
    bankroll: float = 100000,
    kelly_cap: float = 0.02,
    kelly_fraction: float = 0.5,  # Half-Kelly
) -> dict:
    """
    Run comprehensive betting simulation.

    Args:
        min_prob: Minimum predicted probability (%)
        min_coef: Minimum coefficient
        max_coef: Maximum coefficient
        bankroll: Starting bankroll for Kelly calculations
        kelly_cap: Maximum Kelly fraction (e.g., 0.02 = 2%)
        kelly_fraction: Kelly fraction multiplier (0.5 = Half-Kelly)

    Returns:
        Dictionary with simulation results
    """
    db = Database()

    query = """
    WITH filtered AS (
        SELECT
            m.match_date,
            m.coef,
            m.p1_prob,
            m.p2_prob,
            m.pred_winner,
            m.actual_winner,
            m.player1,
            m.player2,
            CASE WHEN m.pred_winner = 1 THEN m.p1_prob ELSE m.p2_prob END as prob,
            CASE
                WHEN m.pred_winner = 1 AND m.actual_winner = 1 THEN 1
                WHEN m.pred_winner = 2 AND m.actual_winner = 2 THEN 1
                ELSE 0
            END as won,
            DATE_TRUNC('month', m.match_date)::date as month_start
        FROM matches m
        WHERE
            m.p1_prob IS NOT NULL
            AND m.p2_prob IS NOT NULL
            AND m.coef IS NOT NULL
            AND m.actual_winner IS NOT NULL
            AND m.coef >= %s
            AND m.coef <= %s
            AND (CASE WHEN m.pred_winner = 1 THEN m.p1_prob ELSE m.p2_prob END) >= %s
    )
    SELECT * FROM filtered ORDER BY match_date;
    """

    with db.get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, (min_coef, max_coef, min_prob))
            rows = cur.fetchall()

    if not rows:
        return {"error": "No matches found matching criteria"}

    # Calculate Kelly stake for each bet
    for row in rows:
        prob = row["prob"] / 100.0
        coef = float(row["coef"])
        if coef > 1 and prob > 0 and prob < 1:
            # Kelly formula: (bp - q) / b where b = coef - 1, p = prob, q = 1 - prob
            kelly_raw = ((coef - 1) * prob - (1 - prob)) / (coef - 1)
            kelly_raw = max(0, kelly_raw)  # No negative stakes
            kelly_capped = min(kelly_raw * kelly_fraction, kelly_cap)
            row["kelly_fraction"] = kelly_capped
            row["kelly_stake"] = kelly_capped * bankroll
        else:
            row["kelly_fraction"] = 0
            row["kelly_stake"] = 0

    # Overall metrics
    total_bets = len(rows)
    wins = sum(r["won"] for r in rows)
    win_rate = wins / total_bets * 100

    # Fixed staking (1 unit per bet)
    fixed_profit = sum((float(r["coef"]) - 1) if r["won"] else -1 for r in rows)
    fixed_roi = fixed_profit / total_bets * 100

    # Kelly staking
    kelly_profit = sum(
        r["kelly_stake"] * (float(r["coef"]) - 1) if r["won"] else -r["kelly_stake"]
        for r in rows
    )
    kelly_roi = kelly_profit / bankroll * 100

    # Monthly breakdown
    monthly = {}
    for row in rows:
        month_key = row["month_start"].strftime("%Y-%m")
        if month_key not in monthly:
            monthly[month_key] = {
                "bets": 0,
                "wins": 0,
                "fixed_profit": 0.0,
                "kelly_profit": 0.0,
                "coefs": [],
                "probs": [],
            }
        m = monthly[month_key]
        m["bets"] += 1
        m["wins"] += row["won"]
        coef = float(row["coef"])
        m["coefs"].append(coef)
        m["probs"].append(row["prob"])
        if row["won"]:
            m["fixed_profit"] += coef - 1
            m["kelly_profit"] += row["kelly_stake"] * (coef - 1)
        else:
            m["fixed_profit"] -= 1
            m["kelly_profit"] -= row["kelly_stake"]

    monthly_results = []
    for month_key in sorted(monthly.keys()):
        m = monthly[month_key]
        monthly_results.append({
            "month": month_key,
            "bets": m["bets"],
            "wins": m["wins"],
            "win_rate": round(m["wins"] / m["bets"] * 100, 2),
            "avg_coef": round(sum(m["coefs"]) / len(m["coefs"]), 2),
            "avg_prob": round(sum(m["probs"]) / len(m["probs"]), 2),
            "fixed_profit": round(m["fixed_profit"], 2),
            "fixed_roi": round(m["fixed_profit"] / m["bets"] * 100, 2),
            "kelly_profit": round(m["kelly_profit"], 2),
            "kelly_roi": round(m["kelly_profit"] / bankroll * 100, 4),
        })

    # Probability band breakdown
    prob_bands = {
        "70-74": (70, 74),
        "75-79": (75, 79),
        "80-84": (80, 84),
        "85-89": (85, 89),
        "90+": (90, 100),
    }

    prob_band_results = []
    for band_name, (low, high) in prob_bands.items():
        band_rows = [r for r in rows if low <= r["prob"] <= high]
        if not band_rows:
            continue
        band_bets = len(band_rows)
        band_wins = sum(r["won"] for r in band_rows)
        band_fixed_profit = sum(
            (float(r["coef"]) - 1) if r["won"] else -1 for r in band_rows
        )
        prob_band_results.append({
            "band": band_name,
            "bets": band_bets,
            "wins": band_wins,
            "win_rate": round(band_wins / band_bets * 100, 2),
            "avg_coef": round(sum(float(r["coef"]) for r in band_rows) / band_bets, 2),
            "avg_prob": round(sum(r["prob"] for r in band_rows) / band_bets, 2),
            "fixed_profit": round(band_fixed_profit, 2),
            "fixed_roi": round(band_fixed_profit / band_bets * 100, 2),
        })

    # Coefficient band breakdown
    coef_bands = {
        "1.50-1.59": (1.50, 1.59),
        "1.60-1.69": (1.60, 1.69),
        "1.70-1.80": (1.70, 1.80),
    }

    coef_band_results = []
    for band_name, (low, high) in coef_bands.items():
        band_rows = [r for r in rows if low <= float(r["coef"]) <= high]
        if not band_rows:
            continue
        band_bets = len(band_rows)
        band_wins = sum(r["won"] for r in band_rows)
        band_fixed_profit = sum(
            (float(r["coef"]) - 1) if r["won"] else -1 for r in band_rows
        )
        coef_band_results.append({
            "band": band_name,
            "bets": band_bets,
            "wins": band_wins,
            "win_rate": round(band_wins / band_bets * 100, 2),
            "avg_prob": round(sum(r["prob"] for r in band_rows) / band_bets, 2),
            "fixed_profit": round(band_fixed_profit, 2),
            "fixed_roi": round(band_fixed_profit / band_bets * 100, 2),
        })

    # Streak analysis
    current_streak = 0
    max_win_streak = 0
    max_loss_streak = 0
    for row in rows:
        if row["won"]:
            current_streak = max(current_streak + 1, 1)
            max_win_streak = max(max_win_streak, current_streak)
        else:
            current_streak = min(current_streak - 1, -1)
            max_loss_streak = max(max_loss_streak, abs(current_streak))

    # Drawdown analysis (fixed staking)
    running_profit = 0
    peak = 0
    max_drawdown = 0
    for row in rows:
        running_profit += (float(row["coef"]) - 1) if row["won"] else -1
        if running_profit > peak:
            peak = running_profit
        drawdown = peak - running_profit
        max_drawdown = max(max_drawdown, drawdown)

    # Kelly drawdown
    running_kelly = 0
    peak_kelly = 0
    max_kelly_drawdown = 0
    for row in rows:
        running_kelly += (
            row["kelly_stake"] * (float(row["coef"]) - 1) if row["won"] else -row["kelly_stake"]
        )
        if running_kelly > peak_kelly:
            peak_kelly = running_kelly
        drawdown = peak_kelly - running_kelly
        max_kelly_drawdown = max(max_kelly_drawdown, drawdown)

    return {
        "parameters": {
            "min_prob": min_prob,
            "min_coef": min_coef,
            "max_coef": max_coef,
            "bankroll": bankroll,
            "kelly_cap": kelly_cap,
            "kelly_fraction": kelly_fraction,
        },
        "summary": {
            "total_bets": total_bets,
            "wins": wins,
            "losses": total_bets - wins,
            "win_rate": round(win_rate, 2),
            "avg_coef": round(sum(float(r["coef"]) for r in rows) / total_bets, 2),
            "avg_prob": round(sum(r["prob"] for r in rows) / total_bets, 2),
            "date_range": {
                "start": rows[0]["match_date"].isoformat(),
                "end": rows[-1]["match_date"].isoformat(),
            },
        },
        "fixed_staking": {
            "total_profit": round(fixed_profit, 2),
            "roi_pct": round(fixed_roi, 2),
            "max_drawdown": round(max_drawdown, 2),
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
        },
        "kelly_staking": {
            "total_profit": round(kelly_profit, 2),
            "roi_pct": round(kelly_roi, 4),
            "max_drawdown": round(max_kelly_drawdown, 2),
            "avg_stake_pct": round(
                sum(r["kelly_fraction"] for r in rows) / total_bets * 100, 4
            ),
            "max_stake_pct": round(max(r["kelly_fraction"] for r in rows) * 100, 4),
        },
        "monthly": monthly_results,
        "prob_bands": prob_band_results,
        "coef_bands": coef_band_results,
    }


def format_output(results: dict) -> str:
    """Format simulation results as human-readable text."""
    if "error" in results:
        return f"Error: {results['error']}"

    p = results["parameters"]
    s = results["summary"]
    fs = results["fixed_staking"]
    ks = results["kelly_staking"]

    lines = []
    lines.append("=" * 70)
    lines.append("FOREBET TENNIS BETTING SIMULATION")
    lines.append("=" * 70)
    lines.append("")
    lines.append("PARAMETERS:")
    lines.append(f"  Min Probability:    {p['min_prob']}%")
    lines.append(f"  Coefficient Range:  {p['min_coef']} - {p['max_coef']}")
    lines.append(f"  Bankroll:           ₦{p['bankroll']:,.0f}")
    lines.append(f"  Kelly Cap:          {p['kelly_cap']*100:.1f}% (Half-Kelly: {p['kelly_fraction']*100:.0f}%)")
    lines.append("")
    lines.append("OVERALL SUMMARY:")
    lines.append(f"  Date Range:         {s['date_range']['start']} to {s['date_range']['end']}")
    lines.append(f"  Total Bets:         {s['total_bets']}")
    lines.append(f"  Wins / Losses:      {s['wins']} / {s['losses']}")
    lines.append(f"  Win Rate:           {s['win_rate']:.2f}%")
    lines.append(f"  Avg Coefficient:    {s['avg_coef']:.2f}")
    lines.append(f"  Avg Probability:    {s['avg_prob']:.2f}%")
    lines.append("")
    lines.append("FIXED STAKING (1 unit per bet):")
    lines.append(f"  Total Profit:       {fs['total_profit']:+.2f} units")
    lines.append(f"  ROI:                {fs['roi_pct']:+.2f}%")
    lines.append(f"  Max Drawdown:       {fs['max_drawdown']:.2f} units")
    lines.append(f"  Max Win Streak:     {fs['max_win_streak']}")
    lines.append(f"  Max Loss Streak:    {fs['max_loss_streak']}")
    lines.append("")
    lines.append(f"KELLY STAKING (Half-Kelly, {p['kelly_cap']*100:.1f}% cap):")
    lines.append(f"  Total Profit:       ₦{ks['total_profit']:+,.2f}")
    lines.append(f"  ROI:                {ks['roi_pct']:+.4f}%")
    lines.append(f"  Max Drawdown:       ₦{ks['max_drawdown']:,.2f}")
    lines.append(f"  Avg Stake:          {ks['avg_stake_pct']:.4f}% of bankroll")
    lines.append(f"  Max Stake:          {ks['max_stake_pct']:.4f}% of bankroll")
    lines.append("")

    # Monthly table
    lines.append("MONTHLY BREAKDOWN:")
    lines.append(
        f"{'Month':<8} {'Bets':>5} {'Wins':>5} {'WR%':>7} {'AvgC':>6} {'AvgP':>6} "
        f"{'FixedP':>9} {'FixROI%':>8} {'KellyP':>10} {'KelROI%':>8}"
    )
    lines.append("-" * 95)
    for m in results["monthly"]:
        lines.append(
            f"{m['month']:<8} {m['bets']:>5} {m['wins']:>5} {m['win_rate']:>7.2f} "
            f"{m['avg_coef']:>6.2f} {m['avg_prob']:>6.2f} "
            f"{m['fixed_profit']:>+9.2f} {m['fixed_roi']:>+8.2f} "
            f"{m['kelly_profit']:>+10.2f} {m['kelly_roi']:>+8.4f}"
        )
    lines.append("")

    # Probability bands
    lines.append("PROBABILITY BAND BREAKDOWN:")
    lines.append(
        f"{'Band':<10} {'Bets':>5} {'Wins':>5} {'WR%':>7} {'AvgC':>6} {'AvgP':>6} "
        f"{'Profit':>9} {'ROI%':>8}"
    )
    lines.append("-" * 65)
    for b in results["prob_bands"]:
        lines.append(
            f"{b['band']:<10} {b['bets']:>5} {b['wins']:>5} {b['win_rate']:>7.2f} "
            f"{b['avg_coef']:>6.2f} {b['avg_prob']:>6.2f} "
            f"{b['fixed_profit']:>+9.2f} {b['fixed_roi']:>+8.2f}"
        )
    lines.append("")

    # Coefficient bands
    lines.append("COEFFICIENT BAND BREAKDOWN:")
    lines.append(
        f"{'Band':<12} {'Bets':>5} {'Wins':>5} {'WR%':>7} {'AvgP':>6} "
        f"{'Profit':>9} {'ROI%':>8}"
    )
    lines.append("-" * 55)
    for b in results["coef_bands"]:
        lines.append(
            f"{b['band']:<12} {b['bets']:>5} {b['wins']:>5} {b['win_rate']:>7.2f} "
            f"{b['avg_prob']:>6.2f} {b['fixed_profit']:>+9.2f} {b['fixed_roi']:>+8.2f}"
        )
    lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


def cli_main():
    """Command-line entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Simulate betting strategies on Forebet tennis data"
    )
    parser.add_argument(
        "--min-prob", type=int, default=70, help="Minimum probability %% (default: 70)"
    )
    parser.add_argument(
        "--min-coef", type=float, default=1.50, help="Minimum coefficient (default: 1.50)"
    )
    parser.add_argument(
        "--max-coef", type=float, default=1.80, help="Maximum coefficient (default: 1.80)"
    )
    parser.add_argument(
        "--bankroll", type=float, default=100000, help="Starting bankroll (default: 100000)"
    )
    parser.add_argument(
        "--kelly-cap", type=float, default=0.02, help="Kelly cap fraction (default: 0.02)"
    )
    parser.add_argument(
        "--kelly-fraction", type=float, default=0.5, help="Kelly fraction (default: 0.5 = Half-Kelly)"
    )
    parser.add_argument(
        "--output", type=str, help="Output file path (optional)"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON instead of formatted text"
    )

    args = parser.parse_args()

    try:
        results = run_simulation(
            min_prob=args.min_prob,
            min_coef=args.min_coef,
            max_coef=args.max_coef,
            bankroll=args.bankroll,
            kelly_cap=args.kelly_cap,
            kelly_fraction=args.kelly_fraction,
        )

        if args.json:
            import json
            output = json.dumps(results, indent=2, default=str)
        else:
            output = format_output(results)

        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
            print(f"Results saved to {args.output}")
        else:
            print(output)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli_main()