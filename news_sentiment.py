#!/usr/bin/env python3
"""
news_sentiment.py - Long-running news sentiment worker for Render
Continuously monitors news feeds and analyzes sentiment
"""

import os
import sys
import time
import json
import logging
import traceback
from datetime import datetime, timezone

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ========= LOGGING SETUP =========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/opt/render/project/src/runtime/news.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# ========= ENV & CONSTANTS =========
RUNTIME_DIR = os.getenv("RUNTIME_DIR", "/opt/render/project/src/runtime")
SENTIMENT_DATA_PATH = os.getenv("SENTIMENT_DATA_PATH", f"{RUNTIME_DIR}/sentiment_data.json")
RSS_URL = os.getenv("BOT_RSS_URL", "https://feeds.reuters.com/reuters/businessNews")
POLL_INTERVAL_SEC = int(os.getenv("SENTIMENT_POLL_INTERVAL", "300"))  # 5 minutes default
HEADLINES_LIMIT = int(os.getenv("SENTIMENT_HEADLINES_LIMIT", "10"))

os.makedirs(RUNTIME_DIR, exist_ok=True)

analyzer = SentimentIntensityAnalyzer()


# ========= HELPER FUNCTIONS =========
def now_utc() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def write_json_atomic(path: str, obj: dict):
    """Write JSON atomically using temp file."""
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def fetch_headlines(limit=10) -> list[str]:
    """Fetch headlines from RSS feed."""
    try:
        feed = feedparser.parse(RSS_URL)
        titles = [e.get("title", "").strip() for e in feed.entries[:limit]]
        return [t for t in titles if t]
    except Exception as e:
        logger.error(f"Failed to fetch headlines: {e}")
        return []


def analyze_sentiment(text: str) -> dict:
    """Analyze sentiment of text using VADER."""
    try:
        scores = analyzer.polarity_scores(text)
        return {
            "compound": scores["compound"],
            "positive": scores["pos"],
            "neutral": scores["neu"],
            "negative": scores["neg"]
        }
    except Exception as e:
        logger.error(f"Failed to analyze sentiment: {e}")
        return {"compound": 0.0, "positive": 0.0, "neutral": 1.0, "negative": 0.0}


def process_news():
    """Main news processing logic."""
    logger.info("Fetching and analyzing news...")
    
    headlines = fetch_headlines(limit=HEADLINES_LIMIT)
    if not headlines:
        logger.warning("No headlines found")
        return
    
    logger.info(f"Found {len(headlines)} headlines")
    
    # Analyze all headlines
    analyzed = []
    best_positive = None
    best_negative = None
    max_pos_score = -1.0
    max_neg_score = 1.0
    
    for headline in headlines:
        sentiment = analyze_sentiment(headline)
        analyzed.append({
            "headline": headline,
            "sentiment": sentiment,
            "timestamp": now_utc().isoformat()
        })
        
        # Track extremes
        compound = sentiment["compound"]
        if compound > max_pos_score:
            max_pos_score = compound
            best_positive = headline
        if compound < max_neg_score:
            max_neg_score = compound
            best_negative = headline
        
        # Log headline for heartbeat monitoring
        logger.info(f"HEADLINE: {headline[:100]} | Sentiment: {compound:+.3f}")
    
    # Calculate average sentiment
    avg_compound = sum(a["sentiment"]["compound"] for a in analyzed) / len(analyzed) if analyzed else 0.0
    
    # Write results
    result = {
        "last_update": now_utc().isoformat(),
        "source": RSS_URL,
        "headlines_count": len(headlines),
        "average_sentiment": avg_compound,
        "best_positive": {
            "headline": best_positive,
            "score": max_pos_score
        } if best_positive else None,
        "best_negative": {
            "headline": best_negative,
            "score": max_neg_score
        } if best_negative else None,
        "recent_headlines": analyzed[:5]  # Keep top 5 for dashboard
    }
    
    write_json_atomic(SENTIMENT_DATA_PATH, result)
    logger.info(f"Analysis complete. Avg sentiment: {avg_compound:+.3f}")


# ========= MAIN LOOP =========
def main():
    """Main worker loop."""
    logger.info("=" * 60)
    logger.info("News Sentiment Worker Starting")
    logger.info(f"RSS URL: {RSS_URL}")
    logger.info(f"Poll Interval: {POLL_INTERVAL_SEC}s")
    logger.info(f"Runtime Dir: {RUNTIME_DIR}")
    logger.info("=" * 60)
    
    loop_count = 0
    
    while True:
        loop_count += 1
        loop_start = time.time()
        
        try:
            logger.info(f"--- Loop {loop_count} starting ---")
            process_news()
            logger.info(f"--- Loop {loop_count} completed ---")
            
        except KeyboardInterrupt:
            logger.info("Received interrupt signal, shutting down...")
            break
            
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            logger.error(traceback.format_exc())
            # Continue running even after error
            
        # Calculate sleep time
        elapsed = time.time() - loop_start
        sleep_time = max(10.0, POLL_INTERVAL_SEC - elapsed)
        logger.info(f"Sleeping for {sleep_time:.1f} seconds...")
        
        try:
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            logger.info("Received interrupt during sleep, shutting down...")
            break
    
    logger.info("News Sentiment Worker Stopped")


if __name__ == "__main__":
    main()
