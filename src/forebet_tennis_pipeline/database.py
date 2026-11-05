"""
Database module for Forebet Tennis Pipeline.
Handles PostgreSQL connection, schema, and migrations.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from dotenv import load_dotenv
from pathlib import Path

# Load .env from config directory (project root)
config_path = Path(__file__).parent.parent.parent / "config" / ".env"
load_dotenv(config_path)


class Database:
    def __init__(self):
        self.conn_params = {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", 5432)),
            "database": os.getenv("DB_NAME", "forebet_tennis"),
            "user": os.getenv("DB_USER", "postgres"),
            "password": os.getenv("DB_PASSWORD", ""),
        }

    @contextmanager
    def get_connection(self):
        conn = psycopg2.connect(**self.conn_params)
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def get_cursor(self, cursor_factory=RealDictCursor):
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=cursor_factory) as cur:
                yield cur
            conn.commit()

    def test_connection(self):
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    return True
        except Exception as e:
            print(f"Database connection failed: {e}")
            return False


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    id SERIAL PRIMARY KEY,
    match_date DATE NOT NULL,
    match_id VARCHAR(50) NOT NULL,
    player1 VARCHAR(100) NOT NULL,
    player2 VARCHAR(100) NOT NULL,
    datetime TIMESTAMP,
    prediction_code VARCHAR(20),
    pred_winner SMALLINT,
    pred_score VARCHAR(10),
    actual_winner SMALLINT,
    actual_score VARCHAR(10),
    status VARCHAR(20),
    coef DECIMAL(5,2),
    avg_games_per_set DECIMAL(5,2),
    set_scores INTEGER[],
    p1_prob SMALLINT,
    p2_prob SMALLINT,
    p1_favorite BOOLEAN,
    p2_favorite BOOLEAN,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (match_date, match_id, player1, player2)
);

CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_players ON matches(player1, player2);
CREATE INDEX IF NOT EXISTS idx_matches_date_players ON matches(match_date, player1, player2);

-- Value bets view: matches where prob * coef >= 120
CREATE OR REPLACE VIEW value_bets AS
SELECT 
    m.*,
    CASE WHEN pred_winner = 1 THEN p1_prob ELSE p2_prob END as prob,
    coef,
    (CASE WHEN pred_winner = 1 THEN p1_prob ELSE p2_prob END) * coef / 100.0 as pred_value
FROM matches m
WHERE 
    m.p1_prob IS NOT NULL AND m.p2_prob IS NOT NULL
    AND m.coef IS NOT NULL
    AND (CASE WHEN pred_winner = 1 THEN p1_prob ELSE p2_prob END) * m.coef >= 120;

-- Kelly stakes view: Half-Kelly with 2% cap, 100k max
CREATE OR REPLACE VIEW kelly_stakes AS
SELECT 
    v.*,
    CASE 
        WHEN v.coef > 1 AND v.prob > 0 AND v.prob < 100 
        THEN LEAST(
            GREATEST(0, (v.coef - 1) * v.prob / 100.0 - (100 - v.prob) / 100.0) / (v.coef - 1),
            0.02
        )
        ELSE 0
    END as kelly_fraction,
    CASE 
        WHEN v.coef > 1 AND v.prob > 0 AND v.prob < 100 
        THEN LEAST(
            GREATEST(0, (v.coef - 1) * v.prob / 100.0 - (100 - v.prob) / 100.0) / (v.coef - 1),
            0.02
        ) * 100000
        ELSE 0
    END as kelly_stake_capped
FROM value_bets v;
"""


def init_database():
    """Initialize database schema."""
    db = Database()
    with db.get_connection() as conn:
        with conn.cursor() as cur:
            # Drop views and table first to ensure clean schema
            cur.execute("DROP VIEW IF EXISTS kelly_stakes CASCADE;")
            cur.execute("DROP VIEW IF EXISTS value_bets CASCADE;")
            cur.execute("DROP TABLE IF EXISTS matches CASCADE;")
            conn.commit()  # Commit the drops before creating
            # Create table and views
            cur.execute("""
                CREATE TABLE matches (
                    id SERIAL PRIMARY KEY,
                    match_date DATE NOT NULL,
                    match_id VARCHAR(50) NOT NULL,
                    player1 VARCHAR(100) NOT NULL,
                    player2 VARCHAR(100) NOT NULL,
                    datetime TIMESTAMP,
                    prediction_code VARCHAR(20),
                    pred_winner SMALLINT,
                    pred_score VARCHAR(10),
                    actual_winner SMALLINT,
                    actual_score VARCHAR(10),
                    status VARCHAR(20),
                    coef DECIMAL(5,2),
                    avg_games_per_set DECIMAL(5,2),
                    set_scores INTEGER[],
                    p1_prob SMALLINT,
                    p2_prob SMALLINT,
                    p1_favorite BOOLEAN,
                    p2_favorite BOOLEAN,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (match_date, match_id, player1, player2)
                );
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(match_date);
                CREATE INDEX IF NOT EXISTS idx_matches_players ON matches(player1, player2);
                CREATE INDEX IF NOT EXISTS idx_matches_date_players ON matches(match_date, player1, player2);
            """)
            # Create views
            cur.execute("""
                CREATE OR REPLACE VIEW value_bets AS
                SELECT 
                    m.*,
                    CASE WHEN pred_winner = 1 THEN p1_prob ELSE p2_prob END as prob,
                    coef,
                    (CASE WHEN pred_winner = 1 THEN p1_prob ELSE p2_prob END) * coef / 100.0 as pred_value
                FROM matches m
                WHERE 
                    m.p1_prob IS NOT NULL AND m.p2_prob IS NOT NULL
                    AND m.coef IS NOT NULL
                    AND (CASE WHEN pred_winner = 1 THEN p1_prob ELSE p2_prob END) * m.coef >= 120;
            """)
            cur.execute("""
                CREATE OR REPLACE VIEW kelly_stakes AS
                SELECT 
                    v.*,
                    CASE 
                        WHEN v.coef > 1 AND v.prob > 0 AND v.prob < 100 
                        THEN LEAST(
                            GREATEST(0, (v.coef - 1) * v.prob / 100.0 - (100 - v.prob) / 100.0) / (v.coef - 1),
                            0.02
                        )
                        ELSE 0
                    END as kelly_fraction,
                    CASE 
                        WHEN v.coef > 1 AND v.prob > 0 AND v.prob < 100 
                        THEN LEAST(
                            GREATEST(0, (v.coef - 1) * v.prob / 100.0 - (100 - v.prob) / 100.0) / (v.coef - 1),
                            0.02
                        ) * 100000
                        ELSE 0
                    END as kelly_stake_capped
                FROM value_bets v;
            """)
    print("✓ Database schema initialized")


def main():
    """Entry point for console script."""
    import sys
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Initialize database schema")
        print("Usage: setup-db")
        return
    init_database()


if __name__ == "__main__":
    main()