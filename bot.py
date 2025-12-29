# bot.py
import os
import json
import time
import math
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import email.utils

import requests
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# ========= ENV & CONSTANTS =========
HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]

API = f"{HOST}/v3"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Trading control - SAFETY: Default to DRY_RUN=1 (safe mode)
# Only enable live trading by explicitly setting DRY_RUN=0
DRY_RUN = os.getenv("DRY_RUN", "1") != "0"

# Runtime files the dashboard reads
RUNTIME_DIR = os.getenv("RUNTIME_DIR", "/opt/render/project/src/runtime")
HEARTBEAT_PATH = os.getenv("HEARTBEAT_PATH", f"{RUNTIME_DIR}/bot_heartbeat.json")
NEWS_LAST_TRADE_PATH = os.getenv("NEWS_LAST_TRADE_PATH", f"{RUNTIME_DIR}/news_last_trade.json")

# Persistent dedupe storage on Render disk
SEEN_HEADLINES_PATH = os.getenv("SEEN_HEADLINES_PATH", "/var/data/seen_headlines.json")
MAX_SEEN_HEADLINES = 500

# Relevance filtering
REQUIRED_KEYWORDS = os.getenv(
    "REQUIRED_KEYWORDS",
    "EUR,USD,ECB,FED,INFLATION,RATES,CPI,GDP,PMI,NFP"
).upper().split(",")
REQUIRED_KEYWORDS = [k.strip() for k in REQUIRED_KEYWORDS if k.strip()]

# Strategy knobs (override with env vars if you wish)
DEFAULT_INSTRUMENT = os.getenv("BOT_INSTRUMENT", "EUR_USD")
INSTRUMENT = DEFAULT_INSTRUMENT  # Will be overridden dynamically based on headline
TP_PIPS = float(os.getenv("BOT_TP_PIPS", "38"))         # take-profit distance
SL_PIPS = float(os.getenv("BOT_SL_PIPS", "25"))         # stop-loss distance
RISK_USD = float(os.getenv("BOT_RISK_USD", "500"))      # ~$ risk per trade at SL
TRADE_INTERVAL_MIN = float(os.getenv("BOT_TRADE_INTERVAL_MIN", "1"))   # poll cadence
COOLDOWN_MIN = float(os.getenv("BOT_COOLDOWN_MIN", "0"))               # wait after a fill
MAX_CONCURRENT = int(os.getenv("BOT_MAX_CONCURRENT", "3"))
MIN_SPREAD = float(os.getenv("BOT_MIN_SPREAD", "0.0002"))  # won’t trade if spread wider
SENT_THRESHOLD = float(os.getenv("BOT_SENT_THRESHOLD", "0.15"))

# FX/Macro-focused RSS feeds (configurable via NEWS_FEEDS env var)
# Using only stable RSS endpoints - no HTML scraping
DEFAULT_NEWS_FEEDS = [
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.fxstreet.com/feeds/news",
    "https://www.investing.com/rss/news.rss",
    "https://www.marketwatch.com/rss/realtimeheadlines",
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",  # CNBC World Economy RSS
]
NEWS_FEEDS = os.getenv("NEWS_FEEDS", ",".join(DEFAULT_NEWS_FEEDS)).split(",")
NEWS_FEEDS = [f.strip() for f in NEWS_FEEDS if f.strip()]

# Relevance scoring thresholds
MIN_RELEVANCE_SCORE = int(os.getenv("MIN_RELEVANCE_SCORE", "4"))

# News debugging and age filtering
NEWS_DEBUG = int(os.getenv("NEWS_DEBUG", "0"))
MAX_HEADLINE_AGE_HOURS = int(os.getenv("MAX_HEADLINE_AGE_HOURS", "24"))

# Pip sizes and price formatting (dynamic based on instrument)
PIP_MAP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}
DIGITS_MAP = {"EUR_USD": 5, "GBP_USD": 5, "USD_JPY": 3, "XAU_USD": 2}

def get_pip(instrument: str) -> float:
    return PIP_MAP.get(instrument, 0.0001)

def get_digits(instrument: str) -> int:
    return DIGITS_MAP.get(instrument, 5)

# Retry policy
RETRY_STATUSES = {429, 500, 502, 503, 504}

analyzer = SentimentIntensityAnalyzer()

os.makedirs(RUNTIME_DIR, exist_ok=True)

# Ensure dedupe directory exists (mkdir -p equivalent) with fallback to /tmp
try:
    # Get directory from SEEN_HEADLINES_PATH and create it (mkdir -p)
    seen_dir = os.path.dirname(SEEN_HEADLINES_PATH)
    if seen_dir:  # Only create if path has a directory component
        os.makedirs(seen_dir, exist_ok=True)
    # Test write access
    test_file = os.path.join(seen_dir if seen_dir else ".", ".write_test")
    with open(test_file, "w") as f:
        f.write("test")
    os.remove(test_file)
except (OSError, PermissionError) as e:
    print(f"[bot] WARNING: {SEEN_HEADLINES_PATH} directory not writable ({e}), falling back to /tmp")
    SEEN_HEADLINES_PATH = "/tmp/seen_headlines.json"
    os.makedirs("/tmp", exist_ok=True)  # Ensure /tmp exists


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


def fmt_price(x: float, instrument: str) -> str:
    digits = get_digits(instrument)
    return f"{x:.{digits}f}"


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


def pricing(instrument: str) -> tuple[float, float, float]:
    """Return (bid, ask, spread) for given instrument."""
    j = get_json(f"/accounts/{ACC}/pricing", params={"instruments": instrument})
    p = j["prices"][0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return bid, ask, ask - bid


def place_market(instrument: str, units: int, tp: float, sl: float) -> requests.Response:
    body = {
        "order": {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": fmt_price(tp, instrument), "timeInForce": "GTC"},
            "stopLossOnFill": {"price": fmt_price(sl, instrument), "timeInForce": "GTC"},
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


# ========= URL canonicalization =========
def canonicalize_url(url: str) -> str:
    """Strip tracking parameters and fragments from URL for deduplication.
    Removes common tracking parameters like utm_*, fbclid, gclid, etc.
    """
    if not url:
        return ""
    
    try:
        parsed = urlparse(url)
        
        # Parse query parameters
        params = parse_qs(parsed.query)
        
        # Remove common tracking parameters
        tracking_params = {
            'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
            'fbclid', 'gclid', 'msclkid', '_ga', 'mc_cid', 'mc_eid',
            'ref', 'source', 'campaign'
        }
        
        # Keep only non-tracking parameters
        cleaned_params = {k: v for k, v in params.items() if k.lower() not in tracking_params}
        
        # Rebuild query string
        new_query = urlencode(cleaned_params, doseq=True) if cleaned_params else ''
        
        # Rebuild URL without fragment and with cleaned query
        canonical = urlunparse((parsed.scheme, parsed.netloc, parsed.path, 
                               parsed.params, new_query, ''))
        
        return canonical.lower()  # Normalize to lowercase for consistent comparison
    except Exception:
        return url.lower()


# ========= Headline deduplication =========
def compute_headline_id(source: str, guid: str, title: str) -> str:
    """Compute a unique ID for a headline using SHA256."""
    combined = f"{source}|{guid}|{title}"
    return hashlib.sha256(combined.encode('utf-8')).hexdigest()


def load_seen_headlines() -> set:
    """Load the set of seen headline IDs from disk. Handles missing/corrupt files gracefully."""
    try:
        if os.path.exists(SEEN_HEADLINES_PATH):
            with open(SEEN_HEADLINES_PATH, 'r') as f:
                data = json.load(f)
                seen = set(data.get("headline_ids", []))
                print(f"[bot] dedupe path={SEEN_HEADLINES_PATH}, loaded {len(seen)} seen headlines")
                return seen
        else:
            print(f"[bot] dedupe path={SEEN_HEADLINES_PATH}, loaded 0 seen headlines (file does not exist)")
    except Exception as e:
        print(f"[bot] WARNING: failed to load seen headlines from {SEEN_HEADLINES_PATH}: {e}")
        print(f"[bot] starting with empty dedupe set")
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


# ========= FX Relevance Scoring =========
def calculate_fx_relevance_score(title: str) -> int:
    """Calculate FX relevance score for a headline.
    Higher score = more relevant to FX/macro trading.
    """
    title_upper = title.upper()
    score = 0
    
    # Central banks (+3)
    central_banks = ["ECB", "FED", "FEDERAL RESERVE", "BOE", "BOJ", "SNB", "RBA", "RBNZ", "PBOC"]
    if any(cb in title_upper for cb in central_banks):
        score += 3
    
    # Key economic indicators and monetary policy (+3)
    monetary_terms = ["CPI", "INFLATION", "RATE", "HIKE", "CUT", "YIELD", "BOND", "TREASURY", "MONETARY"]
    if any(term in title_upper for term in monetary_terms):
        score += 3
    
    # Economic data (+2)
    economic_data = ["GDP", "PMI", "NFP", "JOB", "UNEMPLOYMENT", "RETAIL SALES", "PAYROLL", "MANUFACTURING"]
    if any(term in title_upper for term in economic_data):
        score += 2
    
    # High-impact data releases (+1 bonus)
    high_impact = ["NFP", "NON-FARM", "PAYROLL", "FOMC", "CPI", "INFLATION"]
    if any(term in title_upper for term in high_impact):
        score += 1
    
    # Currency mentions (+2)
    currencies = ["EUR", "USD", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "DOLLAR", "EURO", "POUND", "YEN"]
    currency_pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CHF", "NZD/USD", "USD/CAD"]
    if any(curr in title_upper for curr in currencies + currency_pairs):
        score += 2
    
    # Negative filters (-5 each)
    non_market_terms = [
        "COIN", "ROYAL", "CELEBRITY", "SPORT", "MURDER", "MUSEUM", "ART", "CAT", "DOG",
        "SAVED FOR THE NATION", "900 YEARS", "WEDDING", "DIVORCE", "ACTOR", "ACTRESS",
        "FILM", "MOVIE", "MUSIC", "SINGER", "FOOTBALL", "SOCCER", "BASKETBALL", "CRICKET"
    ]
    for term in non_market_terms:
        if term in title_upper:
            score -= 5
    
    return score


def detect_instrument_from_headline(title: str, default: str = "EUR_USD") -> str:
    """Detect trading instrument from headline content.
    Returns appropriate instrument based on currency/central bank mentions.
    """
    title_upper = title.upper()
    
    # Check for specific currency pairs first
    if "GBP/USD" in title_upper or "CABLE" in title_upper:
        return "GBP_USD"
    if "EUR/USD" in title_upper:
        return "EUR_USD"
    if "USD/JPY" in title_upper:
        return "USD_JPY"
    
    # Check for currency/central bank mentions
    if "GBP" in title_upper or "BOE" in title_upper or "BANK OF ENGLAND" in title_upper or "POUND" in title_upper:
        return "GBP_USD"
    if "EUR" in title_upper or "ECB" in title_upper or "EUROPEAN CENTRAL BANK" in title_upper or "EURO" in title_upper:
        return "EUR_USD"
    if "JPY" in title_upper or "BOJ" in title_upper or "BANK OF JAPAN" in title_upper or "YEN" in title_upper:
        return "USD_JPY"
    
    # Default to EUR_USD for general USD/Fed news
    if "USD" in title_upper or "FED" in title_upper or "FEDERAL RESERVE" in title_upper or "DOLLAR" in title_upper:
        return default
    
    return default


# ========= News & sentiment =========
def fetch_headlines(limit=15) -> list[dict]:
    """Fetch headlines from multiple RSS feeds with fallback.
    Returns list of dicts with keys: title, source, guid, link, score, instrument, published_utc
    Includes DEBUG logging and age filtering.
    
    STRICT REQUIREMENTS:
    - Every item MUST have a parseable publish timestamp
    - Items without timestamps are DISCARDED
    - Items older than MAX_HEADLINE_AGE_HOURS are DISCARDED
    """
    all_entries = []
    seen_canonical_urls = set()  # For cross-source deduplication
    now = now_utc()
    
    # Track discard reasons for summary
    discard_stats = {
        'total_parsed': 0,
        'no_timestamp': 0,
        'parse_failed': 0,
        'too_old': 0,
        'low_relevance': 0,
        'duplicate_url': 0,
        'accepted': 0
    }
    
    if NEWS_DEBUG:
        print(f"[bot][DEBUG] NEWS_DEBUG={NEWS_DEBUG}, MAX_HEADLINE_AGE_HOURS={MAX_HEADLINE_AGE_HOURS}")
    
    for rss_url in NEWS_FEEDS:
        try:
            # Fetch the feed with explicit request to capture status
            if NEWS_DEBUG:
                print(f"[bot][DEBUG] Fetching: {rss_url}")
            
            response = requests.get(rss_url, timeout=10)
            status_code = response.status_code
            content_length = len(response.content)
            
            if NEWS_DEBUG:
                print(f"[bot][DEBUG]   HTTP Status: {status_code}")
                print(f"[bot][DEBUG]   Response Size: {content_length} bytes")
            
            # Parse with feedparser
            feed = feedparser.parse(response.content)
            source = rss_url.split('/')[2]  # Extract domain
            
            parsed_count = len(feed.entries[:limit])
            if NEWS_DEBUG:
                print(f"[bot][DEBUG]   Parsed Headlines: {parsed_count}")
            
            # Track items for this source
            source_relevant = 0
            source_items = []
            
            for e in feed.entries[:limit]:
                title = e.get("title", "").strip()
                if not title:
                    continue
                
                discard_stats['total_parsed'] += 1
                
                link = e.get("link", "")
                guid = e.get("id", link)
                
                # Parse published time
                published_utc = None
                pub_date_str = None
                
                # Try multiple date fields
                for date_field in ['published', 'pubDate', 'updated', 'created']:
                    if date_field in e:
                        pub_date_str = e[date_field]
                        break
                
                # Parse the date
                if pub_date_str:
                    try:
                        # feedparser often provides published_parsed
                        if hasattr(e, 'published_parsed') and e.published_parsed:
                            published_utc = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                        elif hasattr(e, 'updated_parsed') and e.updated_parsed:
                            published_utc = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc)
                        else:
                            # Try parsing with email.utils for RFC 2822 format
                            parsed_time = email.utils.parsedate_to_datetime(pub_date_str)
                            if parsed_time:
                                # Convert to UTC if it has timezone info
                                if parsed_time.tzinfo is None:
                                    published_utc = parsed_time.replace(tzinfo=timezone.utc)
                                else:
                                    published_utc = parsed_time.astimezone(timezone.utc)
                    except Exception as parse_err:
                        discard_stats['parse_failed'] += 1
                        if NEWS_DEBUG:
                            print(f"[bot][DEBUG]   DISCARDED (parse failed): {title[:50]}... | error: {parse_err}")
                        continue
                
                # STRICT: Discard if no published time
                if not published_utc:
                    discard_stats['no_timestamp'] += 1
                    if NEWS_DEBUG:
                        print(f"[bot][DEBUG]   DISCARDED (no timestamp): {title[:50]}...")
                    continue
                
                # Check age - STRICT age gate
                age_hours = (now - published_utc).total_seconds() / 3600
                if age_hours > MAX_HEADLINE_AGE_HOURS:
                    discard_stats['too_old'] += 1
                    if NEWS_DEBUG:
                        print(f"[bot][DEBUG]   DISCARDED (age={age_hours:.1f}h > {MAX_HEADLINE_AGE_HOURS}h): {title[:50]}...")
                    continue
                
                # Store for DEBUG printing (first 10 per source)
                if NEWS_DEBUG and len(source_items) < 10:
                    source_items.append({
                        'published_utc': published_utc,
                        'title': title,
                        'link': link
                    })
                
                # Calculate relevance score
                score = calculate_fx_relevance_score(title)
                
                # Skip if below minimum threshold
                if score < MIN_RELEVANCE_SCORE:
                    discard_stats['low_relevance'] += 1
                    continue
                
                # Deduplicate by canonical URL
                canonical_url = canonicalize_url(link)
                if canonical_url in seen_canonical_urls:
                    discard_stats['duplicate_url'] += 1
                    if NEWS_DEBUG:
                        print(f"[bot][DEBUG]   DISCARDED (duplicate URL): {title[:50]}...")
                    continue
                seen_canonical_urls.add(canonical_url)
                
                # Detect appropriate instrument
                instrument = detect_instrument_from_headline(title, DEFAULT_INSTRUMENT)
                
                # ACCEPTED - this item passed all gates
                discard_stats['accepted'] += 1
                
                all_entries.append({
                    "title": title,
                    "source": source,
                    "guid": guid,
                    "link": link,
                    "published_utc": published_utc,
                    "age_hours": age_hours,
                    "score": score,
                    "instrument": instrument
                })
                source_relevant += 1
            
            # DEBUG: Print first 10 parsed items
            if NEWS_DEBUG and source_items:
                print(f"[bot][DEBUG] First {len(source_items)} parsed items from {source}:")
                for item in source_items:
                    timestamp = item['published_utc'].strftime('%Y-%m-%d %H:%M:%S UTC')
                    print(f"[bot][DEBUG]   {timestamp} | {item['title'][:60]}... | {item['link'][:80]}")
            
            print(f"[bot] fetched {source_relevant} relevant headlines from {source} (status={status_code}, size={content_length}B, parsed={parsed_count})")
        
        except Exception as e:
            print(f"[bot] feed error {rss_url.split('/')[2] if '/' in rss_url else rss_url}: {e}")
            continue
    
    # Sort by score (highest first)
    all_entries.sort(key=lambda x: x['score'], reverse=True)
    
    # Log summary with discard statistics
    print(f"[bot] total relevant headlines: {len(all_entries)}")
    
    if len(all_entries) == 0 and discard_stats['total_parsed'] > 0:
        print(f"[bot] WARNING: All {discard_stats['total_parsed']} items discarded - breakdown:")
        print(f"[bot]   - no_timestamp: {discard_stats['no_timestamp']}")
        print(f"[bot]   - parse_failed: {discard_stats['parse_failed']}")
        print(f"[bot]   - too_old (>{MAX_HEADLINE_AGE_HOURS}h): {discard_stats['too_old']}")
        print(f"[bot]   - low_relevance (<{MIN_RELEVANCE_SCORE}): {discard_stats['low_relevance']}")
        print(f"[bot]   - duplicate_url: {discard_stats['duplicate_url']}")
    elif len(all_entries) > 0:
        # Show age range of accepted candidates
        ages = [e['age_hours'] for e in all_entries]
        print(f"[bot] age range: {min(ages):.1f}h - {max(ages):.1f}h (max allowed: {MAX_HEADLINE_AGE_HOURS}h)")
        
        # Log first few candidates with their details
        if NEWS_DEBUG:
            print(f"[bot][DEBUG] Top {min(5, len(all_entries))} candidates:")
            for i, e in enumerate(all_entries[:5], 1):
                pub_str = e['published_utc'].strftime('%Y-%m-%d %H:%M UTC')
                print(f"[bot][DEBUG]   {i}. age={e['age_hours']:.1f}h, pub={pub_str}, score={e['score']}")
                print(f"[bot][DEBUG]      {e['title'][:80]}...")
    
    return all_entries


def best_headline_with_sentiment(entries: list[dict], seen_headlines: set) -> tuple[dict, float, list[dict]] | None:
    """Find the highest-scoring entry with strong sentiment that hasn't been traded.
    Returns (entry_dict, sentiment, top_5_candidates) or None.
    """
    candidates = []
    
    # Evaluate all entries
    for entry in entries:
        title = entry["title"]
        headline_id = compute_headline_id(entry["source"], entry["guid"], entry["title"])
        
        # Skip if already traded
        if is_headline_seen(headline_id, seen_headlines):
            continue
        
        # Calculate sentiment
        sentiment = analyzer.polarity_scores(title)["compound"]
        
        # Only consider if sentiment is strong enough
        if abs(sentiment) >= SENT_THRESHOLD:
            candidates.append({
                "entry": entry,
                "sentiment": sentiment,
                "headline_id": headline_id,
                "combined_score": entry["score"] + abs(sentiment) * 10  # Weight sentiment heavily
            })
    
    if not candidates:
        return None
    
    # Sort by combined score (relevance + sentiment)
    candidates.sort(key=lambda x: x["combined_score"], reverse=True)
    
    # Get top 5 for logging
    top_5 = candidates[:5]
    
    # DEBUG: Print final filtered candidates
    if NEWS_DEBUG:
        print(f"[bot][DEBUG] Final {len(candidates)} candidates after sentiment filtering:")
        for i, cand in enumerate(candidates[:10], 1):  # Show top 10
            e = cand["entry"]
            timestamp = e['published_utc'].strftime('%Y-%m-%d %H:%M:%S UTC') if 'published_utc' in e else 'N/A'
            print(f"[bot][DEBUG]   {i}. {timestamp} | {e['title'][:60]}... | {e['link'][:80]}")
    
    # Return the best one
    best = candidates[0]
    return (best["entry"], best["sentiment"], top_5)


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
        "default_instrument": DEFAULT_INSTRUMENT,
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
        "min_relevance_score": MIN_RELEVANCE_SCORE,
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
    print(f"[bot] config: default_instrument={DEFAULT_INSTRUMENT} tp={TP_PIPS} sl={SL_PIPS} threshold={SENT_THRESHOLD}")
    print(f"[bot] safety: headline_dedupe={SEEN_HEADLINES_PATH}, min_relevance_score={MIN_RELEVANCE_SCORE}")
    print(f"[bot] feeds: {len(NEWS_FEEDS)} RSS sources configured")
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

            # news sentiment
            chosen = None
            top_candidates = []
            try:
                entries = fetch_headlines(limit=15)
                result = best_headline_with_sentiment(entries, seen_headlines)
                if result:
                    chosen_entry, sentiment, top_candidates = result
                    chosen = (chosen_entry, sentiment)
                    
                    # Log top 5 candidates
                    print(f"[bot] Top 5 candidates:")
                    for i, cand in enumerate(top_candidates, 1):
                        e = cand["entry"]
                        print(f"  {i}. [score={e['score']}, sent={cand['sentiment']:+.2f}, {e['instrument']}] {e['title'][:70]}...")
                elif entries:
                    print(f"[bot] no tradeable headlines (checked {len(entries)} entries, all filtered or already traded)")
            except Exception as e:
                print(f"[bot] news error: {e}")

            # decide trade
            should_trade = False
            side = None
            sentiment = 0.0
            headline = ""
            headline_id = None
            source = ""
            instrument = DEFAULT_INSTRUMENT
            relevance_score = 0
            bid, ask, spread = None, None, None

            if chosen and len(trades) < MAX_CONCURRENT:
                entry, sentiment = chosen
                headline = entry["title"]
                source = entry["source"]
                instrument = entry["instrument"]
                relevance_score = entry["score"]
                headline_id = compute_headline_id(entry["source"], entry["guid"], entry["title"])
                
                # Fetch pricing for the detected instrument
                try:
                    bid, ask, spread = pricing(instrument)
                except Exception as e:
                    print(f"[bot] pricing error for {instrument}: {e}")
                    bid, ask, spread = None, None, None
                
                # Check if we've already traded this headline (redundant check)
                if is_headline_seen(headline_id, seen_headlines):
                    print(f"[bot] already traded headline_id={headline_id[:16]}... ('{headline[:60]}')")
                elif spread is not None and spread <= MIN_SPREAD:
                    # cooldown check
                    minutes_since_trade = (now_utc() - last_trade_time).total_seconds() / 60.0
                    if minutes_since_trade >= COOLDOWN_MIN:
                        # Position gating: check if we already have an open position
                        if has_open_position(instrument):
                            print(f"[bot] position already open for {instrument}, skipping entry")
                        else:
                            side = "BUY" if sentiment > 0 else "SELL"
                            should_trade = True
                    else:
                        print(f"[bot] cooldown active: minutes_since_last_trade={minutes_since_trade:.1f} < COOLDOWN_MIN={COOLDOWN_MIN}")
                elif spread is not None:
                    print(f"[bot] spread too wide: {spread:.5f} > {MIN_SPREAD:.5f}")

            if should_trade and bid is not None and ask is not None and headline_id is not None:
                entry_price = ask if side == "BUY" else bid
                pip = get_pip(instrument)
                digits = get_digits(instrument)
                units = units_for_risk_usd(RISK_USD, SL_PIPS, pip)
                tp = entry_price + (TP_PIPS * pip if side == "BUY" else -TP_PIPS * pip)
                sl = entry_price - (SL_PIPS * pip if side == "BUY" else -SL_PIPS * pip)
                units_signed = units if side == "BUY" else -units

                print(f"[bot] {'DRY-RUN: would place' if DRY_RUN else 'placing'} {side} {instrument} units={units_signed} @ {entry_price:.{digits}f} "
                      f"TP={tp:.{digits}f} SL={sl:.{digits}f} score={relevance_score} sent={sentiment:+.2f}")
                print(f"[bot]   headline: '{headline[:100]}{'...' if len(headline) > 100 else ''}' [{source}]")

                if DRY_RUN:
                    print("[bot] DRY-RUN mode enabled - no actual order placed")
                    record_last_trade_headline(headline, sentiment, f"{side} (DRY-RUN)", source)
                    # Still mark as seen in dry-run to test deduplication
                    seen_headlines = mark_headline_seen(headline_id, seen_headlines)
                    save_seen_headlines(seen_headlines)
                else:
                    r = place_market(instrument, units_signed, tp, sl)
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
                "last_instrument": instrument,
                "last_relevance_score": relevance_score,
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
