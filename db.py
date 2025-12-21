# db.py
import sqlite3
import json
import os
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

DB_PATH = os.getenv("DB_PATH", "/opt/render/project/src/runtime/bot.db")

# Ensure directory exists
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


@contextmanager
def get_conn():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """
    Initialize database with required tables.
    Safe to call multiple times (uses IF NOT EXISTS).
    """
    with get_conn() as conn:
        # bot_state: single-row table for bot enabled flag
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        
        # Ensure one row exists
        conn.execute("""
            INSERT OR IGNORE INTO bot_state (id, enabled, updated_at)
            VALUES (1, 0, ?)
        """, (datetime.now(timezone.utc).isoformat(),))
        
        # articles: news articles with sentiment
        conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                published_at TEXT NOT NULL,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT,
                sentiment REAL NOT NULL,
                instrument TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_articles_published 
            ON articles(published_at DESC)
        """)
        
        # trades: record of all trades placed
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                instrument TEXT NOT NULL,
                side TEXT NOT NULL,
                units INTEGER NOT NULL,
                notional_usd REAL,
                sentiment REAL NOT NULL,
                headline TEXT NOT NULL,
                order_id TEXT,
                status TEXT NOT NULL,
                fill_price REAL,
                raw_json TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_ts 
            ON trades(ts DESC)
        """)
    
    print(f"[db] initialized at {DB_PATH}")


def set_bot_enabled(enabled: bool):
    """Enable or disable the bot."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE bot_state 
            SET enabled = ?, updated_at = ?
            WHERE id = 1
        """, (1 if enabled else 0, datetime.now(timezone.utc).isoformat()))
    print(f"[db] bot_enabled set to {enabled}")


def get_bot_enabled() -> bool:
    """Check if bot is enabled."""
    with get_conn() as conn:
        row = conn.execute("SELECT enabled FROM bot_state WHERE id = 1").fetchone()
        return bool(row["enabled"]) if row else False


def log_article(
    published_at: str,
    source: str,
    title: str,
    sentiment: float,
    instrument: str,
    url: Optional[str] = None,
    raw_data: Optional[Dict[str, Any]] = None
):
    """
    Log a news article with sentiment.
    
    Args:
        published_at: ISO timestamp of article publication
        source: Source name (e.g., "Reuters RSS")
        title: Article headline
        sentiment: Sentiment score (-1.0 to 1.0)
        instrument: Trading instrument (e.g., "EUR_USD")
        url: Optional article URL
        raw_data: Optional dict of raw article data
    """
    raw_json = json.dumps(raw_data or {})
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO articles (published_at, source, title, url, sentiment, instrument, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (published_at, source, title, url, sentiment, instrument, raw_json))


def log_trade(
    ts: str,
    instrument: str,
    side: str,
    units: int,
    sentiment: float,
    headline: str,
    status: str,
    notional_usd: Optional[float] = None,
    order_id: Optional[str] = None,
    fill_price: Optional[float] = None,
    raw_data: Optional[Dict[str, Any]] = None
):
    """
    Log a trade execution.
    
    Args:
        ts: ISO timestamp of trade
        instrument: Trading instrument (e.g., "EUR_USD")
        side: "BUY" or "SELL"
        units: Number of units (signed)
        sentiment: Sentiment that triggered the trade
        headline: News headline that triggered the trade
        status: Status (e.g., "FILLED", "REJECTED", "PENDING")
        notional_usd: Optional notional value in USD
        order_id: Optional OANDA order ID
        fill_price: Optional fill price
        raw_data: Optional dict of raw response data
    """
    raw_json = json.dumps(raw_data or {})
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO trades (ts, instrument, side, units, notional_usd, sentiment, 
                              headline, order_id, status, fill_price, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ts, instrument, side, units, notional_usd, sentiment, headline, 
              order_id, status, fill_price, raw_json))


def get_recent_articles(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieve recent articles ordered by published_at descending.
    
    Args:
        limit: Maximum number of articles to return
        
    Returns:
        List of article dicts
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, published_at, source, title, url, sentiment, instrument, raw_json
            FROM articles
            ORDER BY published_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        return [dict(row) for row in rows]


def get_recent_trades(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieve recent trades ordered by ts descending.
    
    Args:
        limit: Maximum number of trades to return
        
    Returns:
        List of trade dicts
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, ts, instrument, side, units, notional_usd, sentiment,
                   headline, order_id, status, fill_price, raw_json
            FROM trades
            ORDER BY ts DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        return [dict(row) for row in rows]


# Initialize on import
if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
