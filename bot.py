# bot.py
import os
import json
import time
import math
from datetime import datetime, timezone, timedelta

import requests
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Import database layer
import db

# ========= ENV & CONSTANTS =========
HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]

API = f"{HOST}/v3"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Runtime files the dashboard reads
RUNTIME_DIR = os.getenv("RUNTIME_DIR", "/opt/render/project/src/runtime")
HEARTBEAT_PATH = os.getenv("HEARTBEAT_PATH", f"{RUNTIME_DIR}/bot_heartbeat.json")
NEWS_LAST_TRADE_PATH = os.getenv("NEWS_LAST_TRADE_PATH", f"{RUNTIME_DIR}/news_last_trade.json")

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

RSS_URL = os.getenv("BOT_RSS_URL", "https://feeds.reuters.com/reuters/businessNews")

# Pip sizes and price formatting
PIP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}.get(INSTRUMENT, 0.0001)
DIGITS = {"EUR_USD": 5, "GBP_USD": 5, "USD_JPY": 3, "XAU_USD": 2}.get(INSTRUMENT, 5)

# Retry policy
RETRY_STATUSES = {429, 500, 502, 503, 504}

analyzer = SentimentIntensityAnalyzer()

os.makedirs(RUNTIME_DIR, exist_ok=True)


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


# ========= News & sentiment =========
def fetch_headlines(limit=8) -> list[str]:
    feed = feedparser.parse(RSS_URL)
    titles = [e.get("title", "").strip() for e in feed.entries[:limit]]
    return [t for t in titles if t]


def best_headline_with_sentiment(titles: list[str]) -> tuple[str, float] | None:
    best = None
    best_abs = 0.0
    for t in titles:
        s = analyzer.polarity_scores(t)["compound"]
        if abs(s) > best_abs:
            best_abs = abs(s)
            best = (t, s)
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


def record_last_trade_headline(headline: str, sentiment: float, side: str):
    payload = {
        "headline": headline,
        "sentiment": sentiment,
        "side": side,
        "instrument": INSTRUMENT,
        "time": now_utc().isoformat(),
        "source": "Reuters RSS",
    }
    write_json_atomic(NEWS_LAST_TRADE_PATH, payload)


# ========= Main loop =========
def main():
    print("[bot] starting…")
    
    # Initialize database
    db.init_db()
    
    last_trade_time = datetime(1970, 1, 1, tzinfo=timezone.utc)

    while True:
        loop_started = now_utc()
        try:
            # Check if bot is enabled
            if not db.get_bot_enabled():
                print("[bot] disabled, sleeping...")
                time.sleep(10)
                continue
            
            # update heartbeat up-front so the light is green soon after start
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
            all_titles = []
            try:
                all_titles = fetch_headlines(limit=10)
                chosen = best_headline_with_sentiment(all_titles)
            except Exception as e:
                print(f"[bot] news error: {e}")

            # Log all fetched articles to database
            for title in all_titles:
                try:
                    sent = analyzer.polarity_scores(title)["compound"]
                    db.log_article(
                        published_at=now_utc().isoformat(),
                        source="Reuters RSS",
                        title=title,
                        sentiment=sent,
                        instrument=INSTRUMENT,
                        url=None,
                        raw_data={"title": title, "score": sent}
                    )
                except Exception as e:
                    print(f"[bot] error logging article: {e}")

            # decide trade
            should_trade = False
            side = None
            sentiment = 0.0
            headline = ""

            if chosen and spread is not None and len(trades) < MAX_CONCURRENT:
                headline, sentiment = chosen
                if abs(sentiment) >= SENT_THRESHOLD:
                    if spread <= MIN_SPREAD:
                        # cooldown check
                        minutes_since_trade = (now_utc() - last_trade_time).total_seconds() / 60.0
                        if minutes_since_trade >= COOLDOWN_MIN:
                            side = "BUY" if sentiment > 0 else "SELL"
                            should_trade = True
                        else:
                            print(f"[bot] cooldown active: {minutes_since_trade:.1f} < {COOLDOWN_MIN} min")
                    else:
                        print(f"[bot] spread too wide: {spread:.5f} > {MIN_SPREAD:.5f}")
                else:
                    print(f"[bot] sentiment below threshold: {sentiment:+.2f} (th={SENT_THRESHOLD:+.2f})")

            if should_trade and bid is not None and ask is not None:
                entry = ask if side == "BUY" else bid
                units = units_for_risk_usd(RISK_USD, SL_PIPS, PIP)
                tp = entry + (TP_PIPS * PIP if side == "BUY" else -TP_PIPS * PIP)
                sl = entry - (SL_PIPS * PIP if side == "BUY" else -SL_PIPS * PIP)
                units_signed = units if side == "BUY" else -units

                print(f"[bot] placing {side} {INSTRUMENT} units={units_signed} @ {entry:.{DIGITS}f} "
                      f"TP={tp:.{DIGITS}f} SL={sl:.{DIGITS}f} headline='{headline[:80]}' sent={sentiment:+.2f}")

                r = place_market(units_signed, tp, sl)
                trade_status = "FILLED" if r.status_code in (200, 201) else "REJECTED"
                
                # Extract order ID and fill price from response
                order_id = None
                fill_price = None
                raw_response = {}
                
                try:
                    raw_response = r.json()
                    if "orderFillTransaction" in raw_response:
                        fill_tx = raw_response["orderFillTransaction"]
                        order_id = fill_tx.get("orderID")
                        fill_price = float(fill_tx.get("price", 0))
                    elif "orderCreateTransaction" in raw_response:
                        order_id = raw_response["orderCreateTransaction"].get("id")
                except Exception as e:
                    print(f"[bot] error parsing response: {e}")
                
                # Log trade to database
                notional = abs(units_signed) * (fill_price if fill_price else entry) / 10000  # approximate USD
                db.log_trade(
                    ts=now_utc().isoformat(),
                    instrument=INSTRUMENT,
                    side=side,
                    units=units_signed,
                    notional_usd=notional,
                    sentiment=sentiment,
                    headline=headline,
                    order_id=order_id,
                    status=trade_status,
                    fill_price=fill_price,
                    raw_data=raw_response
                )
                
                if r.status_code in (200, 201):
                    print("[bot] order OK", r.status_code)
                    last_trade_time = now_utc()
                    record_last_trade_headline(headline, sentiment, side)
                else:
                    print("[bot] order FAILED", r.status_code, r.text[:400])

            # refresh heartbeat with live extras
            extra = {
                "last_headline": headline,
                "last_sentiment": sentiment,
                "spread": spread,
                "open_trades": len(trades),
                "last_side": side,
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
