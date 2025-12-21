# db.py
"""
Database layer with SQLAlchemy support for Postgres (production) and SQLite (local dev).
Uses DATABASE_URL env var if present, otherwise falls back to local SQLite.
"""
import os
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Text, Boolean,
    CheckConstraint
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import StaticPool

# Get DATABASE_URL (Postgres on Render) or fall back to SQLite
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    DB_PATH = os.getenv("DB_PATH", "/opt/render/project/src/runtime/bot.db")
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    DATABASE_URL = f"sqlite:///{DB_PATH}"
    print(f"[db] Using SQLite: {DB_PATH}")
else:
    print(f"[db] Using DATABASE_URL: {DATABASE_URL[:30]}...")

# Create engine with appropriate settings
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
else:
    # Postgres/production
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ========= Models =========
class BotState(Base):
    """Single-row table to store bot enabled state."""
    __tablename__ = "bot_state"
    
    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    updated_at = Column(String, nullable=False)
    
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row_check"),
    )


class Article(Base):
    """News articles with sentiment scores."""
    __tablename__ = "articles"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    published_at = Column(String, nullable=False, index=True)
    source = Column(String, nullable=False)
    title = Column(Text, nullable=False)
    url = Column(String, nullable=True)
    sentiment = Column(Float, nullable=False)
    instrument = Column(String, nullable=False)
    raw_json = Column(Text, nullable=False)


class Trade(Base):
    """Trade execution records."""
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    ts = Column(String, nullable=False, index=True)
    instrument = Column(String, nullable=False)
    side = Column(String, nullable=False)
    units = Column(Integer, nullable=False)
    notional_usd = Column(Float, nullable=True)
    sentiment = Column(Float, nullable=False)
    headline = Column(Text, nullable=False)
    order_id = Column(String, nullable=True)
    status = Column(String, nullable=False)
    fill_price = Column(Float, nullable=True)
    raw_json = Column(Text, nullable=False)


def get_session() -> Session:
    """Get a new database session."""
    return SessionLocal()


def init_db():
    """
    Initialize database with required tables.
    Safe to call multiple times - creates tables if they don't exist.
    """
    Base.metadata.create_all(bind=engine)
    
    # Ensure bot_state has exactly one row
    session = get_session()
    try:
        state = session.query(BotState).filter_by(id=1).first()
        if not state:
            state = BotState(
                id=1,
                enabled=False,
                updated_at=datetime.now(timezone.utc).isoformat()
            )
            session.add(state)
            session.commit()
    finally:
        session.close()
    
    print(f"[db] initialized")


def set_bot_enabled(enabled: bool):
    """Enable or disable the bot."""
    session = get_session()
    try:
        state = session.query(BotState).filter_by(id=1).first()
        if state:
            state.enabled = enabled
            state.updated_at = datetime.now(timezone.utc).isoformat()
            session.commit()
            print(f"[db] bot_enabled set to {enabled}")
    finally:
        session.close()


def get_bot_enabled() -> bool:
    """Check if bot is enabled."""
    session = get_session()
    try:
        state = session.query(BotState).filter_by(id=1).first()
        return state.enabled if state else False
    finally:
        session.close()


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
    session = get_session()
    try:
        article = Article(
            published_at=published_at,
            source=source,
            title=title,
            url=url,
            sentiment=sentiment,
            instrument=instrument,
            raw_json=json.dumps(raw_data or {})
        )
        session.add(article)
        session.commit()
    finally:
        session.close()


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
    session = get_session()
    try:
        trade = Trade(
            ts=ts,
            instrument=instrument,
            side=side,
            units=units,
            notional_usd=notional_usd,
            sentiment=sentiment,
            headline=headline,
            order_id=order_id,
            status=status,
            fill_price=fill_price,
            raw_json=json.dumps(raw_data or {})
        )
        session.add(trade)
        session.commit()
    finally:
        session.close()


def get_recent_articles(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieve recent articles ordered by published_at descending.
    
    Args:
        limit: Maximum number of articles to return
        
    Returns:
        List of article dicts
    """
    session = get_session()
    try:
        articles = (
            session.query(Article)
            .order_by(Article.published_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": a.id,
                "published_at": a.published_at,
                "source": a.source,
                "title": a.title,
                "url": a.url,
                "sentiment": a.sentiment,
                "instrument": a.instrument,
                "raw_json": a.raw_json
            }
            for a in articles
        ]
    finally:
        session.close()


def get_recent_trades(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Retrieve recent trades ordered by ts descending.
    
    Args:
        limit: Maximum number of trades to return
        
    Returns:
        List of trade dicts
    """
    session = get_session()
    try:
        trades = (
            session.query(Trade)
            .order_by(Trade.ts.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": t.id,
                "ts": t.ts,
                "instrument": t.instrument,
                "side": t.side,
                "units": t.units,
                "notional_usd": t.notional_usd,
                "sentiment": t.sentiment,
                "headline": t.headline,
                "order_id": t.order_id,
                "status": t.status,
                "fill_price": t.fill_price,
                "raw_json": t.raw_json
            }
            for t in trades
        ]
    finally:
        session.close()


# Initialize on import
if __name__ == "__main__":
    init_db()
    print("Database initialized successfully")
