# Forebet Tennis & Football Pipeline

A complete data pipeline for scraping, storing, and analyzing Forebet tennis and football predictions.

## Features

- **Historical Scraper (Tennis)**: Scrapes past dates with Cloudflare bypass
- **Historical Scraper (Football 1x2)**: Scrapes football 1x2 predictions for past dates
- **Date Range Support**: Scrape single dates or ranges (e.g., `2025-01-01` to `2025-12-31`)
- **Parallel Scraping**: 3 workers for fast batch scraping
- **Tiebreak Parsing** (Tennis): Correctly handles concatenated scores (e.g., `47` → `4-3 TB:7-4`)
- **Football 1x2 Parsing**: Handles prediction codes (X1-1, 12-1, 21-2, 1X-1), 1x2 odds, avg goals, weather
- **Football Filtering**: Automatically excludes non-tennis matches (UCL, Us1, etc.)
- **PostgreSQL Storage**: Normalized schema with indexes
- **Value Bets View**: Pre-computed `prob * coef >= 120` filter
- **Kelly Stakes View**: Half-Kelly with 2% cap, 100k max
- **Config via .env**: No hardcoded credentials

## Project Structure

```
forebet-tennis-pipeline/
├── config/
│   └── .env.example          # Configuration template
├── data/
│   └── json/
│       ├── *.json                    # Tennis historical data
│       ├── custom/                   # Football 1x2 data (gitignored)
│       ├── reference_2026-01-01.json # Reference format
│       └── .gitkeep
├── src/
│   └── forebet_tennis_pipeline/
│       ├── __init__.py
│       ├── scraper.py            # Tennis historical scraper (single date)
│       ├── batch_scraper.py      # Tennis parallel batch scraper (date ranges)
│       ├── football_scraper.py   # Football 1x2 scraper (date ranges)
│       ├── live_scraper.py       # Tennis live scraper (current dates)
│       ├── database.py           # DB connection, schema, views
│       └── etl.py                # JSON → PostgreSQL loader
├── pyproject.toml                # Package config with CLI entry points
├── README.md
└── requirements.txt
```

## Quick Start

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Playwright (Chromium)

### 1. Clone & Setup

```bash
git clone https://github.com/yourusername/forebet-tennis-pipeline.git
cd forebet-tennis-pipeline

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Install Playwright Chromium
playwright install chromium

# Install package in editable mode (makes CLI commands available)
pip install -e .
```

### 2. PostgreSQL Setup (one-time)

```bash
# Install PostgreSQL (Ubuntu/Debian)
sudo apt update && sudo apt install postgresql postgresql-contrib
sudo service postgresql start

# Create database and user
sudo -u postgres psql -c "CREATE DATABASE forebet_tennis;"
sudo -u postgres psql -c "CREATE USER postgres WITH PASSWORD 'your_secure_password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE forebet_tennis TO postgres;"
```

### 3. Configure Environment

```bash
cp config/.env.example config/.env
nano config/.env
```

**Required settings in `config/.env`:**
```bash
# Database (MUST SET)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=forebet_tennis
DB_USER=postgres
DB_PASSWORD=your_secure_password

# Scraping
SCRAPE_START_DATE=2025-01-01
SCRAPE_END_DATE=2025-12-31
SCRAPE_DELAY_SECONDS=3
SCRAPE_HEADLESS=false
SCRAPE_BROWSER_COUNT=1
```

### 4. Initialize Database

```bash
setup-db
```

This creates tables: `matches`, views: `value_bets`, `kelly_stakes`

### 5. Scrape Data

```bash
# Tennis - Single historical date
scrape-historical 2026-01-01

# Tennis - Sequential historical range (safer for Cloudflare)
scrape-batch --start 2025-01-01 --end 2025-12-31

# Tennis - Parallel historical range (faster, 3 workers)
scrape-batch --start 2025-01-01 --end 2025-12-31 --workers 3

# Tennis - Full year
scrape-batch --year 2025

# Football 1x2 - Single historical date
scrape-football 2026-11-01

# Football 1x2 - Parallel historical range (faster, 3 workers)
scrape-football --start 2026-02-01 --end 2026-10-31 --workers 3

# Tennis - Current/live date
scrape-live
```

### 6. Load to Database

```bash
# Load all JSON files
load-to-db

# Load specific pattern
load-to-db --pattern "custom/*.json"

# Load specific file
load-to-db --file data/json/2026-01-01_historical.json
```

### 7. Query Results

```bash
psql -U postgres -d forebet_tennis -c "SELECT * FROM value_bets LIMIT 10;"
```

## Database Schema

### `matches` table

| Column | Type | Description |
|--------|------|-------------|
| `match_id` | VARCHAR(50) | Forebet match ID |
| `match_date` | DATE | Match date |
| `match_time` | TIME | Match time |
| `player1`, `player2` | VARCHAR(100) | Player/team names |
| `prediction_code` | VARCHAR(20) | e.g., `12-0`, `07.81.50`, `X1-1` |
| `pred_winner` | SMALLINT | 1=home, 0=draw, 2=away |
| `pred_score` | VARCHAR(10) | Predicted score |
| `avg_games_per_set` | DECIMAL(5,2) | Tennis: avg games/set |
| `avg_goals` | DECIMAL(5,2) | Football: avg goals |
| `coef` | DECIMAL(5,2) | Decimal odds |
| `actual_winner` | SMALLINT | 1=home, 0=draw, 2=away |
| `actual_score` | VARCHAR(10) | Actual score |
| `status` | VARCHAR(20) | FT, Cancl., etc. |
| `set_scores` | INT[] | Tennis: flat array [h1,a1,h2,a2,...] |
| `tiebreak_details` | JSONB | Tiebreak info per set |
| `p1_prob`, `p2_prob`, `p_draw_prob` | SMALLINT | Win probabilities |
| `p1_favorite`, `p2_favorite`, `p_draw_favorite` | BOOLEAN | Favorite indicator |

### Views

**`value_bets`** - Matches where `prob * coef >= 120`:
```sql
SELECT * FROM value_bets;
```

**`kelly_stakes`** - Half-Kelly with 2% cap, 100k max:
```sql
SELECT * FROM kelly_stakes;
```

## Example Queries

```sql
-- Today's value bets with Kelly stakes
SELECT m.player1, m.player2, m.coef, m.p1_prob, m.p2_prob, m.p_draw_prob,
       v.pred_value, k.kelly_fraction
FROM matches m
JOIN value_bets v ON m.id = v.id
JOIN kelly_stakes k ON m.id = k.id
WHERE m.match_date = CURRENT_DATE;

-- Backtest: All value bets P&L
SELECT 
    match_date,
    COUNT(*) as bets,
    SUM(CASE WHEN pred_winner = actual_winner THEN coef - 1 ELSE -1 END) as pnl
FROM value_bets
WHERE match_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY match_date
ORDER BY match_date;
```

## Scraper Details

### Tennis Historical Format (Past Dates)

The historical page uses CONCATENATED format:

```
[0] "L. TienA. Blockx 21/12/2025 18:10"  - players + datetime
[1] "5644"                               - match_id
[2] "12-0"                               - prediction_code
[3] "2"                                  - pred_sets_p1
[4] "0"                                  - pred_sets_p2
[5] "7.8"                                - avg_games_per_set
[6] "1.50"                               - coef
[7] "47"                                 - set1 (tiebreak: 4-3 TB:7-4)
[8] "34"                                 - set2 (tiebreak: 3-4 TB:3-7)
[9] "4"                                  - set3
[10] "2"                                 - set3
[11] "4"                                 - set4
[12] "1"                                 - set4
[13] "FT"                                - status
[14] "3"                                 - actual_sets_p1
[15] "0"                                 - actual_sets_p2
```

### Tiebreak Parsing

| Raw | Parsed |
|-----|--------|
| `47`, `34` | `4-3 TB:7-4` (home won 4-3, tiebreak 7-4) |
| `4`, `2` | `4-2` (no tiebreak) |

### Football 1x2 Format

Football 1x2 predictions use `.rcnt` elements with text format:

```
Line 0: League code (e.g., "Mx1")
Line 1: "Team1 Team2 DD/MM/YYYY HH:MM"
Line 2: Match ID
Line 3: Prediction code (e.g., "X1-1", "12-1", "21-2", "1X-1")
Line 4: Predicted score
Line 5: Coef/odds
Line 6: Temperature
Line 7: Avg goals
Line 8: 1x2 odds (home draw away)
Line 9: Flags
Line 9: Status (FT, HT, etc.)
Line 10: Final score with half-time
```

**Prediction codes:**
- `X1-1`: Draw FT, Home HT
- `12-1`: Home/Draw FT, Home HT
- `21-2`: Away/Draw FT, Away HT
- `1X-1`: Home/Draw FT, Home HT

### Football Filtering

Automatically excludes matches with prediction codes:
- `X` (draw only)
- Team names (e.g., `Liverpool`)
- Competition codes (e.g., `UCL`, `Us1`)

## Requirements

- Python 3.10+
- PostgreSQL 14+
- Playwright (Chromium)

## License

MIT