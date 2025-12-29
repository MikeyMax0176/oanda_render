# bot.py
import os
import json
import time
import math
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ========= ENV & CONSTANTS =========
HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]

API = f"{HOST}/v3"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Trading control
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("true", "1", "yes")

# Runtime files the dashboard reads
RUNTIME_DIR = os.getenv("RUNTIME_DIR", "/opt/render/project/src/runtime")
HEARTBEAT_PATH = os.getenv("HEARTBEAT_PATH", f"{RUNTIME_DIR}/bot_heartbeat.json")
NEWS_LAST_TRADE_PATH = os.getenv("NEWS_LAST_TRADE_PATH", f"{RUNTIME_DIR}/news_last_trade.json")

# Data directory for persistent stores (fallback to /tmp if not writable)
DATA_DIR = os.getenv("DATA_DIR", "/opt/render/project/src/data")
SEEN_HEADLINES_PATH = os.path.join(DATA_DIR, "seen_headlines.json")
MAX_SEEN_HEADLINES = 500

# Relevance filtering
REQUIRED_KEYWORDS = os.getenv(
    "REQUIRED_KEYWORDS",
    "EUR,USD,ECB,FED,INFLATION,RATES,CPI,GDP,PMI,NFP"
).upper().split(",")
REQUIRED_KEYWORDS = [k.strip() for k in REQUIRED_KEYWORDS if k.strip()]

# Strategy knobs (override with env vars if you wish)
INSTRUMENT = os.getenv("BOT_INSTRUMENT", "EUR_USD")
TP_PIPS = float(os.getenv("BOT_TP_PIPS", "38"))         # take-profit distance
SL_PIPS = float(os.getenv("BOT_SL_PIPS", "25"))         # stop-loss distance
RISK_USD = float(os.getenv("BOT_RISK_USD", "500"))      # ~$ risk per trade at SL
TRADE_INTERVAL_MIN = float(os.getenv("BOT_TRADE_INTERVAL_MIN", "1"))   # poll cadence
COOLDOWN_MIN = float(os.getenv("BOT_COOLDOWN_MIN", "0"))               # wait after a fill
MAX_CONCURRENT = int(os.getenv("BOT_MAX_CONCURRENT", "3"))
MIN_SPREAD = float(os.getenv("BOT_MIN_SPREAD", "0.0002"))  # won’t trade if spread wider
SENT_THRESHOLD = float(os.getenv("BOT_SENT_THRESHOLD", "0.15"))

RSS_URLS = [
    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://feeds.reuters.com/reuters/businessNews",
]

# Pip sizes and price formatting
PIP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}.get(INSTRUMENT, 0.0001)
DIGITS = {"EUR_USD": 5, "GBP_USD": 5, "USD_JPY": 3, "XAU_USD": 2}.get(INSTRUMENT, 5)

# Retry policy
RETRY_STATUSES = {429, 500, 502, 503, 504}

analyzer = SentimentIntensityAnalyzer()

os.makedirs(RUNTIME_DIR, exist_ok=True)

# Ensure data directory exists with fallback to /tmp
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    # Test write access
    test_file = os.path.join(DATA_DIR, ".write_test")
    with open(test_file, "w") as f:
        f.write("test")
    os.remove(test_file)
except (OSError, PermissionError) as e:
    print(f"[bot] WARNING: {DATA_DIR} not writable ({e}), falling back to /tmp")
    DATA_DIR = "/tmp"
    SEEN_HEADLINES_PATH = os.path.join(DATA_DIR, "seen_headlines.json")


# ========= HTTP helpers with backoff =========
def _sleep(i: int):  # 0.5,1,2,4,8...
    time.sleep(0.5 * (2 ** i))


def _request(method: str, path: str, *, params=None, json_body=None, retries=5):
    last = None
    url = f"{API}{path}"
    for i in range(retries):
        try:
            r = requests.request(method, url, headers=H, params=params, json=json_body, timeout=20)
        except requests.RequestException as e:
            last = e
            _sleep(i)
            continue
        if r.status_code not in RETRY_STATUSES:
            return r
        last = r
        _sleep(i)
    if isinstance(last, requests.Response):
        raise requests.HTTPError(f"{method} {path} -> {last.status_code}: {last.text[:400]}")
    raise last


def get_json(path: str, *, params=None):
    r = _request("GET", path, params=params)
    r.raise_for_status()
    return r.json()


def post_json(path: str, body: dict):
    r = _request("POST", path, json_body=body)
    return r


# ========= OANDA helpers =========
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def fmt_price(x: float) -> str:
    return f"{x:.{DIGITS}f}"


def account_summary() -> dict:
    return get_json(f"/accounts/{ACC}/summary")["account"]


def open_trades() -> list[dict]:
    return get_json(f"/accounts/{ACC}/trades").get("trades", [])


def has_open_position(instrument: str) -> bool:
    """Check if there's any open position for the given instrument."""
    try:
        trades = open_trades()
        for trade in trades:
            if trade.get("instrument") == instrument:
                return True
        
        # Also check positions endpoint for any non-zero positions
        positions = get_json(f"/accounts/{ACC}/positions").get("positions", [])
        for pos in positions:
            if pos.get("instrument") == instrument:
                long_units = float(pos.get("long", {}).get("units", "0"))
                short_units = float(pos.get("short", {}).get("units", "0"))
                if long_units != 0 or short_units != 0:
                    return True
        return False
    except Exception as e:
        print(f"[bot] WARNING: has_open_position error: {e}")
        return True  # Fail-safe: assume position exists if we can't check


def pricing() -> tuple[float, float, float]:
    """Return (bid, ask, spread)."""
    j = get_json(f"/accounts/{ACC}/pricing", params={"instruments": INSTRUMENT})
    p = j["prices"][0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return bid, ask, ask - bid


def place_market(units: int, tp: float, sl: float) -> requests.Response:
    body = {
        "order": {
            "type": "MARKET",
            "instrument": INSTRUMENT,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": fmt_price(tp), "timeInForce": "GTC"},
            "stopLossOnFill": {"price": fmt_price(sl), "timeInForce": "GTC"},
        }
    }
    return post_json(f"/accounts/{ACC}/orders", body)


# ========= Sizing =========
def units_for_risk_usd(risk_usd: float, sl_pips: float, pip: float) -> int:
    """
    Approximation for USD quote pairs (e.g., EUR/USD):
      risk_usd ≈ units * pip * sl_pips  =>  units ≈ risk_usd / (pip * sl_pips)
    """
    u = risk_usd / (pip * sl_pips)
    # round to nearest 10 to avoid tiny units
    return max(1000, int(round(u / 10.0) * 10))


# ========= Headline deduplication =========
def compute_headline_id(source: str, guid: str, title: str) -> str:
    """Compute a unique ID for a headline using SHA256."""
    combined = f"{source}|{guid}|{title}"
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()


def load_seen_headlines() -> set:
    """Load the set of seen headline IDs from disk."""
    try:
        if os.path.exists(SEEN_HEADLINES_PATH):
            with open(SEEN_HEADLINES_PATH, 'r') as f:
                data = json.load(f)
                return set(data.get("headline_ids", []))
    except Exception as e:
        print(f"[bot] WARNING: failed to load seen headlines: {e}")
    return set()


def save_seen_headlines(seen: set):
    """Save the set of seen headline IDs to disk, keeping only the last MAX_SEEN_HEADLINES."""
    try:
        # Keep only the most recent entries (limit size)
        seen_list = list(seen)[-MAX_SEEN_HEADLINES:]
        data = {
            "headline_ids": seen_list,
            "count": len(seen_list),
            "last_updated": now_utc().isoformat()
        }
        write_json_atomic(SEEN_HEADLINES_PATH, data)
    except Exception as e:
        print(f"[bot] WARNING: failed to save seen headlines: {e}")


def is_headline_seen(headline_id: str, seen: set) -> bool:
    """Check if headline has already been traded."""
    return headline_id in seen


def mark_headline_seen(headline_id: str, seen: set) -> set:
    """Mark headline as seen and return updated set."""
    seen.add(headline_id)
    return seen


# ========= News & sentiment =========
def fetch_headlines(limit=8) -> list[dict]:
    """Fetch headlines from multiple RSS feeds with fallback.
    Returns list of dicts with keys: title, source, guid, link
    """
    for rss_url in RSS_URLS:
        try:
            feed = feedparser.parse(rss_url)
            source = rss_url.split('/')[2]  # Extract domain
            entries = []
            for e in feed.entries[:limit]:
                title = e.get("title", "").strip()
                if not title:
                    continue
                entries.append({
                    "title": title,
                    "source": source,
                    "guid": e.get("id", e.get("link", "")),
                    "link": e.get("link", "")
                })
            if len(entries) >= 3:  # Need at least 3 headlines
                print(f"[bot] fetched {len(entries)} headlines from {source}")
                return entries
        except Exception as e:
            print(f"[bot] feed error {rss_url.split('/')[2]}: {e}")
            continue
    print("[bot] WARNING: all feeds failed, returning empty list")
    return []


def is_headline_relevant(title: str) -> bool:
    """Check if headline contains at least one required keyword."""
    if not REQUIRED_KEYWORDS:
        return True  # No filtering if no keywords configured
    title_upper = title.upper()
    for keyword in REQUIRED_KEYWORDS:
        if keyword in title_upper:
            return True
    return False


def best_headline_with_sentiment(entries: list[dict]) -> tuple[dict, float] | None:
    """Find the entry with the strongest sentiment that passes relevance filter.
    Returns (entry_dict, sentiment) or None.
    """
    best = None
    best_abs = 0.0
    
    for entry in entries:
        title = entry["title"]
        
        # Check relevance first
        if not is_headline_relevant(title):
            continue
        
        s = analyzer.polarity_scores(title)["compound"]
        if abs(s) > best_abs:
            best_abs = abs(s)
            best = (entry, s)
    
    return best


# ========= Files the dashboard reads =========
def write_json_atomic(path: str, obj: dict):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def write_heartbeat(extra: dict | None = None):
    acc_alias = ""
    try:
        acc_alias = account_summary().get("alias", ACC)
    except Exception:
        pass

    hb = {
        "last_beat": now_utc().isoformat(),
        "account": acc_alias,
        "instrument": INSTRUMENT,
        "risk_pct": float(os.getenv("BOT_RISK_PCT", "0")),  # optional display
        "risk_usd": RISK_USD,
        "sl_pips": SL_PIPS,
        "tp_pips": TP_PIPS,
        "trade_interval_min": TRADE_INTERVAL_MIN,
        "cooldown_min": COOLDOWN_MIN,
        "min_spread": MIN_SPREAD,
        "sentiment_threshold": SENT_THRESHOLD,
        "max_concurrent_trades": MAX_CONCURRENT,
        "max_daily_loss": float(os.getenv("BOT_MAX_DAILY_LOSS", "1500")),
    }
    if extra:
        hb.update(extra)
    write_json_atomic(HEARTBEAT_PATH, hb)


def record_last_trade_headline(headline: str, sentiment: float, side: str, source: str = "RSS"):
    payload = {
        "headline": headline,
        "sentiment": sentiment,
        "side": side,
        "instrument": INSTRUMENT,
        "time": now_utc().isoformat(),
        "source": source,
    }
    write_json_atomic(NEWS_LAST_TRADE_PATH, payload)


# ========= Main loop =========
def main():
    print(f"[bot] starting… DRY_RUN={'ENABLED (no orders will be placed)' if DRY_RUN else 'DISABLED (live trading)'}")
    print(f"[bot] config: instrument={INSTRUMENT} tp={TP_PIPS} sl={SL_PIPS} threshold={SENT_THRESHOLD}")
    print(f"[bot] safety: headline_dedupe={SEEN_HEADLINES_PATH}, keywords={','.join(REQUIRED_KEYWORDS[:5])}...")
    last_trade_time = datetime(1970, 1, 1, tzinfo=timezone.utc)
    
    # Load seen headlines at startup
    seen_headlines = load_seen_headlines()
    print(f"[bot] loaded {len(seen_headlines)} seen headlines from disk")

    while True:
        loop_started = now_utc()
        print(f"[bot] loop starting at {loop_started.strftime('%H:%M:%S')}")
        try:
            # update heartbeat up-front so the light is green soon after start
            print("[bot] writing heartbeat...")
            write_heartbeat()

            # guardrails
            try:
                trades = open_trades()
            except Exception as e:
                print(f"[bot] open_trades error: {e}")
                trades = []

            if len(trades) >= MAX_CONCURRENT:
                print(f"[bot] max concurrent trades reached: {len(trades)} ≥ {MAX_CONCURRENT}")

            # fetch pricing/spread
            try:
                bid, ask, spread = pricing()
            except Exception as e:
                print(f"[bot] pricing error: {e}")
                bid, ask, spread = None, None, None

            # news sentiment
            chosen = None
            try:
                entries = fetch_headlines(limit=10)
                chosen = best_headline_with_sentiment(entries)
                if chosen is None and entries:
                    print(f"[bot] no relevant headlines found (checked {len(entries)} entries, keywords: {','.join(REQUIRED_KEYWORDS[:3])}...)")
            except Exception as e:
                print(f"[bot] news error: {e}")

            # decide trade
            should_trade = False
            side = None
            sentiment = 0.0
            headline = ""
            headline_id = None
            source = ""

            if chosen and spread is not None and len(trades) < MAX_CONCURRENT:
                entry, sentiment = chosen
                headline = entry["title"]
                source = entry["source"]
                headline_id = compute_headline_id(entry["source"], entry["guid"], entry["title"])
                
                # Check if we've already traded this headline
                if is_headline_seen(headline_id, seen_headlines):
                    print(f"[bot] already traded headline_id={headline_id[:16]}... ('{headline[:60]}')")
                elif abs(sentiment) >= SENT_THRESHOLD:
                    if spread <= MIN_SPREAD:
                        # cooldown check
                        minutes_since_trade = (now_utc() - last_trade_time).total_seconds() / 60.0
                        if minutes_since_trade >= COOLDOWN_MIN:
                            # Position gating: check if we already have an open position
                            if has_open_position(INSTRUMENT):
                                print(f"[bot] position already open for {INSTRUMENT}, skipping entry")
                            else:
                                side = "BUY" if sentiment > 0 else "SELL"
                                should_trade = True
                        else:
                            print(f"[bot] cooldown active: minutes_since_last_trade={minutes_since_trade:.1f} < COOLDOWN_MIN={COOLDOWN_MIN}")
                    else:
                        print(f"[bot] spread too wide: {spread:.5f} > {MIN_SPREAD:.5f}")
                else:
                    print(f"[bot] sentiment below threshold: {sentiment:+.2f} (th={SENT_THRESHOLD:+.2f})")

            if should_trade and bid is not None and ask is not None and headline_id is not None:
                entry_price = ask if side == "BUY" else bid
                units = units_for_risk_usd(RISK_USD, SL_PIPS, PIP)
                tp = entry_price + (TP_PIPS * PIP if side == "BUY" else -TP_PIPS * PIP)
                sl = entry_price - (SL_PIPS * PIP if side == "BUY" else -SL_PIPS * PIP)
                units_signed = units if side == "BUY" else -units

                print(f"[bot] {'DRY-RUN: would place' if DRY_RUN else 'placing'} {side} {INSTRUMENT} units={units_signed} @ {entry_price:.{DIGITS}f} "
                      f"TP={tp:.{DIGITS}f} SL={sl:.{DIGITS}f} headline='{headline[:80]}' sent={sentiment:+.2f}")

                if DRY_RUN:
                    print("[bot] DRY-RUN mode enabled - no actual order placed")
                    record_last_trade_headline(headline, sentiment, f"{side} (DRY-RUN)", source)
                    # Still mark as seen in dry-run to test deduplication
                    seen_headlines = mark_headline_seen(headline_id, seen_headlines)
                    save_seen_headlines(seen_headlines)
                else:
                    r = place_market(units_signed, tp, sl)
                    if r.status_code in (200, 201):
                        print(f"[bot] order OK {r.status_code}")
                        last_trade_time = now_utc()
                        record_last_trade_headline(headline, sentiment, side, source)
                        # Mark headline as seen and save
                        seen_headlines = mark_headline_seen(headline_id, seen_headlines)
                        save_seen_headlines(seen_headlines)
                        print(f"[bot] marked headline as seen: {headline_id[:16]}...")
                    else:
                        print("[bot] order FAILED", r.status_code, r.text[:400])

            # refresh heartbeat with live extras
            extra = {
                "last_headline": headline,
                "last_sentiment": sentiment,
                "spread": spread,
                "open_trades": len(trades),
                "last_side": side,
                "seen_headlines_count": len(seen_headlines),
            }
            write_heartbeat(extra)

        except Exception as e:
            print("[bot] loop error:", e)

        # sleep until next interval
        elapsed = (now_utc() - loop_started).total_seconds()
        wait_s = max(5.0, TRADE_INTERVAL_MIN * 60.0 - elapsed)
        time.sleep(wait_s)


if __name__ == "__main__":
    main()
