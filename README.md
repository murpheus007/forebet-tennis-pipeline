# Forebet Tennis Pipeline

A complete data pipeline for scraping, storing, and analyzing Forebet tennis predictions.

## Features

- **Historical Scraper**: Scrapes past dates with Cloudflare bypass
- **Date Range Support**: Scrape single dates or ranges (e.g., `2025-01-01` to `2025-12-31`)
- **Tiebreak Parsing**: Correctly handles concatenated scores (e.g., `47` → `4-3 TB:7-4`)
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
├── src/
│   ├── __init__.py
│   ├── scraper.py            # Historical scraper (single date)
│   ├── batch_scraper.py      # Parallel batch scraper (date ranges)
│   ├── live_scraper.py       # Live scraper (current dates)
│   ├── database.py           # DB connection, schema, views
│   └── etl.py                # JSON → PostgreSQL loader
├── data/
│   ├── json/                 # Downloaded JSON files
│   └── reference_2026-01-01.json  # Reference format
├── scripts/
├── requirements.txt
├── pyproject.toml            # Package config with CLI entry points
└── README.md
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
DB_PASSWORD=your_s...n
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
# Single historical date
scrape-historical 2026-01-01

# Sequential historical range (safer for Cloudflare)
scrape-batch --start 2025-01-01 --end 2025-12-31

# Parallel historical range (faster, 3 workers)
scrape-batch --start 2025-01-01 --end 2025-12-31 --workers 3

# Full year
scrape-batch --year 2025

# Current/live date
scrape-live
```

### 6. Load to Database

```bash
# Load all JSON files
load-to-db

# Load specific pattern
load-to-db --pattern "2025-01-*_historical.json"

# Load single file
load-to-db --file data/json/2025-01-01_historical.json
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
| `player1`, `player2` | VARCHAR(100) | Player names |
| `prediction_code` | VARCHAR(20) | e.g., `12-0`, `07.81.50` |
| `pred_winner` | SMALLINT | 1 or 2 |
| `pred_score` | VARCHAR(10) | Predicted set score |
| `avg_games_per_set` | DECIMAL(5,2) | |
| `coef` | DECIMAL(5,2) | Decimal odds |
| `actual_winner` | SMALLINT | 1 or 2 |
| `actual_score` | VARCHAR(10) | Actual set score |
| `status` | VARCHAR(20) | FT, Cancl., etc. |
| `set_scores` | INT[] | Flat array: [h1,a1,h2,a2,...] |
| `tiebreak_details` | JSONB | Tiebreak info per set |
| `p1_prob`, `p2_prob` | SMALLINT | Win probabilities |
| `p1_favorite`, `p2_favorite` | BOOLEAN | Favorite indicator |

### Views

**`value_bets`** - Matches where `prob * coef >= 120`:
```sql
SELECT * FROM value_bets;
```

**`kelly_stakes`** - Half-Kelly fraction (2% cap):
```sql
SELECT * FROM kelly_stakes;
```

## Example Queries

```sql
-- Today's value bets with Kelly stakes
SELECT m.player1, m.player2, m.coef, m.p1_prob, m.p2_prob,
       v.pred_value, k.kelly_fraction
FROM matches m
JOIN value_bets v ON m.id = v.id
JOIN kelly_stakes k ON m.id = k.id
WHERE m.match_date = CURRENT_DATE;

-- Backtest: All 2025 value bets P&L
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

### Historical Format (Past Dates)

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

### Football Filtering

Automatically excludes matches with prediction codes:
- `X` (draw)
- Team names (e.g., `Liverpool`)
- Competition codes (e.g., `UCL`, `Us1`)

## Requirements

- Python 3.10+
- PostgreSQL 14+
- Playwright (Chromium)

## License

MIT