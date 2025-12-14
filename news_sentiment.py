# news_sentiment.py — GDELT-powered news → sentiment → OANDA trades
import os, time, json, math, logging, requests
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

# ---------- OANDA / ENV ----------
HOST  = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC   = os.environ["OANDA_ACCOUNT"]
API   = f"{HOST}/v3"
H     = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Instruments (comma separated ENV). Defaults are safe.
INSTRUMENTS = [s.strip() for s in os.getenv("INSTRUMENTS", "EUR_USD,GBP_USD").split(",") if s.strip()]

# Loop / risk controls
NEWS_INTERVAL       = int(os.getenv("NEWS_INTERVAL_SEC", "180"))    # seconds between loops
COOLDOWN_MIN        = int(os.getenv("NEWS_COOLDOWN_MIN", "30"))     # minutes between trades per instrument
MAX_POS_PER_INST    = int(os.getenv("MAX_POS_PER_INST", "1"))       # 1 = at most one open side per instrument
DAILY_LOSS_CAP_PCT  = float(os.getenv("DAILY_LOSS_CAP_PCT", "2.0")) # pause for day if drawdown exceeds this %
RISK_PER_TRADE_PCT  = float(os.getenv("RISK_PER_TRADE_PCT", "0.25"))# % of NAV risked to SL per trade
TP_PIPS             = int(os.getenv("TP_PIPS", "50"))
SL_PIPS             = int(os.getenv("SL_PIPS", "25"))

# Heartbeat (dashboard reads this)
HEARTBEAT_FN = "runtime/bot_heartbeat.json"

# Lightweight persistent state
STATE_FN = "/tmp/news_state.json"

# Pip sizes (approx)
PIPS = {"EUR_USD":0.0001, "GBP_USD":0.0001, "USD_JPY":0.01, "XAU_USD":0.1}

# Per-instrument keyword bundles for filtering / queries
KEYWORDS = {
    "EUR_USD": ["eurusd", "\"eur usd\"", "euro", "\"european central bank\"", "ecb"],
    "GBP_USD": ["gbpusd", "\"gbp usd\"", "pound", "\"bank of england\"", "boe", "sterling"],
    "USD_JPY": ["usdjpy", "\"usd jpy\"", "yen", "\"bank of japan\"", "boj"],
    "XAU_USD": ["xauusd", "\"xau usd\"", "gold", "bullion"],
}

# ---------- Logging ----------
logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger("gdelt-bot")

# ---------- Small utils ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def load_state():
    try:
        with open(STATE_FN, "r") as f:
            return json.load(f)
    except Exception:
        return {"seen":{}, "last_trade_at":{}, "day_nav_start":None, "day_nav_start_date":None, "paused_today":False}

def save_state(s):
    try:
        with open(STATE_FN, "w") as f:
            json.dump(s, f)
    except Exception as e:
        log.warning("state save failed: %s", e)

def write_heartbeat(**kw):
    hb = {
        "last_run_utc": now_utc().replace(microsecond=0).isoformat().replace("+00:00","Z"),
        "loop_seconds": NEWS_INTERVAL,
        "last_headline": kw.get("headline",""),
        "last_signal": kw.get("signal","idle"),
        "last_action": kw.get("action","idle"),
        "notes": kw.get("notes",""),
    }
    try:
        os.makedirs(os.path.dirname(HEARTBEAT_FN), exist_ok=True)
        with open(HEARTBEAT_FN, "w") as f:
            json.dump(hb, f)
    except Exception as e:
        log.warning("heartbeat write failed: %s", e)

def _retryable(status): return status in (429, 500, 502, 503, 504)

def _get(path, params=None):
    url = f"{API}{path}"
    for i in range(4):
        r = requests.get(url, headers=H, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        if _retryable(r.status_code):
            time.sleep(0.75 * (2**i)); continue
        raise RuntimeError(f"GET {path} -> {r.status_code} {r.text[:240]}")
    raise RuntimeError("GET retries exhausted")

def _post(path, body):
    url = f"{API}{path}"
    for i in range(4):
        r = requests.post(url, headers=H, json=body, timeout=20)
        if r.status_code in (200, 201):
            return r.status_code, r.text
        if _retryable(r.status_code):
            time.sleep(0.75 * (2**i)); continue
        return r.status_code, r.text
    return 599, "POST retries exhausted"

# ---------- OANDA helpers ----------
def account_nav():
    j = _get(f"/accounts/{ACC}/summary")["account"]
    return float(j.get("NAV", j["balance"]))

def get_price(inst):
    j = _get(f"/accounts/{ACC}/pricing", params={"instruments": inst})
    p = j["prices"][0]
    bid = float(p["bids"][0]["price"]); ask = float(p["asks"][0]["price"])
    return bid, ask

def fetch_candles(inst, gran="M5", count=30):
    j = _get(f"/instruments/{inst}/candles", params={"granularity": gran, "count": count, "price": "M"})
    closes = [float(c["mid"]["c"]) for c in j.get("candles", []) if c.get("complete")]
    return closes

def momentum_filter(inst, lookback=10):
    cs = fetch_candles(inst, "M5", lookback + 1)
    if len(cs) < lookback + 1: return 0.0
    return cs[-1] - cs[-lookback]  # + uptrend, - downtrend

def units_for_risk(inst, nav_usd, sl_pips):
    pip = PIPS.get(inst, 0.0001)
    risk_usd = nav_usd * (RISK_PER_TRADE_PCT / 100.0)
    if sl_pips <= 0 or pip <= 0: return 0
    u = risk_usd / (sl_pips * pip)
    return int(max(0, round(u / 10) * 10))

def current_positions(inst):
    j = _get(f"/accounts/{ACC}/openPositions")
    for p in j.get("positions", []):
        if p["instrument"] == inst:
            return int(p["long"]["units"]), int(p["short"]["units"])
    return 0, 0

def place_market(inst, buy: bool, units_abs: int):
    bid, ask = get_price(inst)
    pip = PIPS.get(inst, 0.0001)
    entry = ask if buy else bid
    tp = entry + (TP_PIPS * pip if buy else -TP_PIPS * pip)
    sl = entry - (SL_PIPS * pip if buy else -SL_PIPS * pip)
    fmt = "{:.5f}"
    body = {
        "order": {
            "type": "MARKET",
            "instrument": inst,
            "units": str(units_abs if buy else -units_abs),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": fmt.format(tp)},
            "stopLossOnFill":  {"price": fmt.format(sl)},
        }
    }
    code, txt = _post(f"/accounts/{ACC}/orders", body)
    log.info("[trade] %s %s %s -> %s %s", inst, "BUY" if buy else "SELL", units_abs, code, txt[:180])
    return code, txt

# ---------- Sentiment ----------
def sentiment_score(text: str) -> float:
    # Tiny dictionary approach; fast and transparent
    pos = ["surge","beat","optimism","growth","cooling","hawkish","strong","accelerates","expands","rises","eases","slows inflation"]
    neg = ["plunge","miss","fear","recession","hot","dovish","weak","contracts","slows","falls","spikes inflation"]
    t = (text or "").lower()
    s = sum(w in t for w in pos) - sum(w in t for w in neg)
    return max(-2.0, min(2.0, float(s)))

# ---------- GDELT Doc 2.0 ----------
# Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
# We'll query recent window (e.g., 15min) and compute our own sentiment.
GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

def gdelt_query(q: str, timespan: str = "15min", maxrecords: int = 50) -> list[dict]:
    """Return list of article dicts with 'title','url','seendate','language'."""
    params = {
        "query": q,
        "mode": "ArtList",
        "sort": "datedesc",
        "timespan": timespan,      # e.g., 15min, 1h, 3h
        "maxrecords": str(maxrecords),
        "format": "json",
    }
    r = requests.get(GDELT_DOC_ENDPOINT, params=params, timeout=20)
    if r.status_code != 200:
        log.warning("[gdelt] %s %s", r.status_code, r.text[:200])
        return []
    data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
    arts = data.get("articles", []) if isinstance(data, dict) else []
    out = []
    for a in arts:
        out.append({
            "id": f"{a.get('url','')}|{a.get('seendate','')}",
            "title": a.get("title","") or "",
            "desc": a.get("excerpt","") or "",
            "url": a.get("url","") or "",
            "seendate": a.get("seendate",""),
            "lang": a.get("language",""),
        })
    return out

def build_inst_query(inst: str) -> str:
    # Join the instrument’s keyword list into a single OR query.
    keys = KEYWORDS.get(inst, [inst.replace("_"," ")])
    # GDELT query syntax is simple text; we’ll OR them.
    return " OR ".join(keys)

def best_news_signal(inst: str):
    # One GDELT call per instrument (still light; INSTRUMENTS is short)
    q = build_inst_query(inst)
    arts = gdelt_query(q=q, timespan="1h", maxrecords=75)
    if not arts:
        return None
    best = None
    for a in arts:
        s = sentiment_score(a["title"] + " " + a["desc"])
        if (best is None) or (abs(s) > abs(best[0])):
            best = (s, a)
    return best  # (score, article) or None

# ---------- Main loop ----------
def main():
    state = load_state()

    # Daily reset for loss cap
    today = now_utc().date().isoformat()
    if state.get("day_nav_start_date") != today:
        start_nav = account_nav()
        state["day_nav_start"] = start_nav
        state["day_nav_start_date"] = today
        state["paused_today"] = False
        save_state(state)
        log.info("[init] New trading day. NAV start=%.2f", start_nav)

    while True:
        last_headline = ""
        last_signal = "idle"
        last_action  = "idle"

        try:
            # Loss guard
            nav = account_nav()
            start = float(state.get("day_nav_start") or nav)
            dd = (nav - start) / start * 100.0 if start > 0 else 0.0
            if dd <= -DAILY_LOSS_CAP_PCT:
                if not state.get("paused_today"):
                    state["paused_today"] = True
                    save_state(state)
                    log.warning("[guard] Daily loss cap hit (%.2f%%). Pausing until next day.", dd)
            if state.get("paused_today"):
                write_heartbeat(signal="paused", action="idle", headline="Daily loss cap pause", notes=f"DD={dd:.2f}%")
                time.sleep(NEWS_INTERVAL); continue

            # Per instrument
            for inst in INSTRUMENTS:
                # avoid multiple concurrent positions on same side
                long_u, short_u = current_positions(inst)
                open_slots = (1 if long_u else 0) + (1 if short_u else 0)
                if open_slots >= MAX_POS_PER_INST:
                    continue

                # cooldown check
                last_iso = state.get("last_trade_at", {}).get(inst)
                if last_iso:
                    try:
                        last_dt = datetime.fromisoformat(last_iso)
                    except Exception:
                        last_dt = None
                    if last_dt and now_utc() - last_dt < timedelta(minutes=COOLDOWN_MIN):
                        continue

                sig = best_news_signal(inst)
                if not sig:
                    continue
                score, art = sig
                last_headline = art["title"]

                # momentum agreement
                mom = momentum_filter(inst, lookback=10)

                action = "HOLD"
                if score >= 1.0 and mom > 0:
                    action = "BUY"
                elif score <= -1.0 and mom < 0:
                    action = "SELL"

                log.info("[news] %s score=%+0.2f mom=%+0.5f -> %s | %s",
                         inst, score, mom, action, (art["title"] or "")[:120])
                last_signal = action if action in ("BUY","SELL") else "HOLD"

                if action in ("BUY","SELL"):
                    nav = account_nav()
                    units = units_for_risk(inst, nav, SL_PIPS)
                    if units > 0:
                        code, _ = place_market(inst, buy=(action=="BUY"), units_abs=units)
                        if str(code).startswith("20"):
                            state.setdefault("last_trade_at", {})[inst] = now_utc().isoformat()
                            save_state(state)
                            last_action = f"{action} {units}"
                        else:
                            last_action = f"order_err_{code}"

        except Exception as e:
            log.error("[loop] error: %s", e)
            last_signal = "error"
            last_action = "idle"

        write_heartbeat(signal=last_signal, action=last_action, headline=last_headline, notes="")
        time.sleep(NEWS_INTERVAL)

if __name__ == "__main__":
    log.info("[boot] news_sentiment worker starting (GDELT)… instruments=%s interval=%ss",
             INSTRUMENTS, NEWS_INTERVAL)
    main()
