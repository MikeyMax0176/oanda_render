# news_sentiment.py  (full replacement)
import os, time, json, math, requests, logging, sys, pathlib
from datetime import datetime, timedelta, timezone

# ----- runtime dirs / logging / heartbeat -----
RUNTIME_DIR = pathlib.Path("runtime")
RUNTIME_DIR.mkdir(exist_ok=True)
LOG_PATH = RUNTIME_DIR / "news.log"
HB_PATH  = RUNTIME_DIR / "bot_heartbeat.json"
STATE_FN = RUNTIME_DIR / "news_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, encoding="utf-8")]
)
log = logging.getLogger("newsbot")

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def write_heartbeat(**kwargs):
    hb = {
        "last_run_utc": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        **kwargs,
    }
    HB_PATH.write_text(json.dumps(hb, ensure_ascii=False))

# ====== ENV ======
HOST  = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC   = os.environ["OANDA_ACCOUNT"]
API   = f"{HOST}/v3"
H     = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

NEWSAPI_KEY      = os.getenv("NEWSAPI_KEY", "")
INSTRUMENTS      = [s.strip() for s in os.getenv("INSTRUMENTS", "EUR_USD,GBP_USD").split(",") if s.strip()]
NEWS_INTERVAL    = int(os.getenv("NEWS_INTERVAL_SEC", "180"))
COOLDOWN_MIN     = int(os.getenv("NEWS_COOLDOWN_MIN", "30"))
MAX_POS_PER_INST = int(os.getenv("MAX_POS_PER_INST", "1"))
DAILY_LOSS_CAP_PCT   = float(os.getenv("DAILY_LOSS_CAP_PCT", "2.0"))   # pause day if drawdown exceeds this %
RISK_PER_TRADE_PCT   = float(os.getenv("RISK_PER_TRADE_PCT", "0.25"))  # risk % of NAV to SL per trade
TP_PIPS = int(os.getenv("TP_PIPS", "50"))
SL_PIPS = int(os.getenv("SL_PIPS", "25"))

# pip sizes for quick TP/SL math (approx for USD-quoted)
PIPS = {"EUR_USD":0.0001, "GBP_USD":0.0001, "USD_JPY":0.01, "XAU_USD":0.1}

# query terms per instrument for NewsAPI
QMAP = {
    "EUR_USD": ["eurusd","euro","ecb","eurozone","europe inflation"],
    "GBP_USD": ["gbpusd","pound","bank of england","uk inflation"],
    "USD_JPY": ["usdjpy","yen","boj","japan inflation"],
    "XAU_USD": ["xauusd","gold price","gold"],
}

# ====== HELPERS ======
def load_state():
    try:
        return json.loads(STATE_FN.read_text())
    except Exception:
        return {"seen":{}, "last_trade_at":{}, "day_nav_start":None, "day_nav_start_date":None, "paused_today":False}

def save_state(s):
    STATE_FN.write_text(json.dumps(s))

def _retryable(status):
    return status in (429, 500, 502, 503, 504)

def _get(path, params=None):
    url = f"{API}{path}"
    for i in range(4):
        r = requests.get(url, headers=H, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
        if _retryable(r.status_code):
            time.sleep(0.5 * (2**i)); continue
        raise RuntimeError(f"GET {path} -> {r.status_code} {r.text[:240]}")
    raise RuntimeError(f"GET {path} retries exhausted")

def _post(path, body):
    url = f"{API}{path}"
    for i in range(4):
        r = requests.post(url, headers=H, json=body, timeout=20)
        if r.status_code in (200, 201): return r.status_code, r.text
        if _retryable(r.status_code):
            time.sleep(0.5 * (2**i)); continue
        return r.status_code, r.text
    return 599, "POST retries exhausted"

def account_nav():
    j = _get(f"/accounts/{ACC}/summary")["account"]
    return float(j.get("NAV", j["balance"]))

def get_price(inst):
    j = _get(f"/accounts/{ACC}/pricing", params={"instruments": inst})
    p = j["prices"][0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return bid, ask

def fetch_candles(inst, gran="M5", count=30):
    j = _get(f"/instruments/{inst}/candles", params={"granularity": gran, "count": count, "price": "M"})
    closes = []
    for c in j.get("candles", []):
        if c.get("complete"): closes.append(float(c["mid"]["c"]))
    return closes

def momentum_filter(inst, lookback=10):
    cs = fetch_candles(inst, "M5", lookback + 1)
    if len(cs) < lookback + 1: return 0.0
    return cs[-1] - cs[-lookback]

def sentiment_score(text: str) -> float:
    pos = ["surge","beat","optimism","growth","cooling","hawkish","strong","accelerates","expands"]
    neg = ["plunge","miss","fear","recession","hot","dovish","weak","contracts","slows"]
    t = (text or "").lower()
    s = sum(w in t for w in pos) - sum(w in t for w in neg)
    return max(-2.0, min(2.0, float(s)))

def fetch_headlines(q: str):
    if not NEWSAPI_KEY:
        return []
    r = requests.get(
        "https://newsapi.org/v2/everything",
        params={"q": q, "language": "en", "pageSize": 10, "sortBy": "publishedAt"},
        headers={"X-Api-Key": NEWSAPI_KEY},
        timeout=20
    )
    if r.status_code != 200:
        log.warning("[news] error %s %s", r.status_code, r.text[:200])
        return []
    arts = r.json().get("articles", [])
    out = []
    for a in arts:
        aid = (a.get("url", "") + "|" + a.get("publishedAt", ""))
        out.append({"id":aid, "title":a.get("title",""), "desc":a.get("description") or ""})
    return out

def best_news_signal(inst):
    qs = QMAP.get(inst, [inst.lower()])
    best = None
    for q in qs:
        for a in fetch_headlines(q):
            s = sentiment_score(a["title"] + " " + a["desc"])
            if best is None or abs(s) > abs(best[0]):
                best = (s, a)
    return best  # (score, article) or None

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
            long_u = int(p["long"]["units"])
            short_u = int(p["short"]["units"])
            return long_u, short_u
    return 0, 0

def place_market(inst, buy: bool, units_abs: int, headline: str | None):
    bid, ask = get_price(inst)
    pip = PIPS.get(inst, 0.0001)
    entry = ask if buy else bid
    tp = entry + (TP_PIPS * pip if buy else -TP_PIPS * pip)
    sl = entry - (SL_PIPS * pip if buy else -SL_PIPS * pip)
    body = {
        "order": {
            "type": "MARKET",
            "instrument": inst,
            "units": str(units_abs if buy else -units_abs),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": f"{tp:.5f}"},
            "stopLossOnFill":  {"price": f"{sl:.5f}"},
        }
    }
    code, txt = _post(f"/accounts/{ACC}/orders", body)
    msg = f"[trade] {inst} {'BUY' if buy else 'SELL'} {units_abs} -> {code}"
    if headline: msg += f" | headline={headline[:140]}"
    log.info(msg)
    # reflect in heartbeat immediately
    write_heartbeat(last_headline=headline, last_action=f"order {code}", last_signal=("BUY" if buy else "SELL"))

# ====== MAIN LOOP ======
def main():
    log.info("[boot] news_sentiment worker starting… instruments=%s interval=%ss", INSTRUMENTS, NEWS_INTERVAL)
    state = load_state()

    # daily reset for loss cap
    today = now_utc().date().isoformat()
    if state.get("day_nav_start_date") != today:
        start_nav = account_nav()
        state["day_nav_start"] = start_nav
        state["day_nav_start_date"] = today
        state["paused_today"] = False
        save_state(state)
        log.info("[init] New trading day. NAV start=%.2f", start_nav)

    while True:
        loop_note = ""
        try:
            nav = account_nav()
            start = float(state.get("day_nav_start") or nav)
            dd = (nav - start) / start * 100.0 if start > 0 else 0.0
            if dd <= -DAILY_LOSS_CAP_PCT:
                if not state.get("paused_today"):
                    state["paused_today"] = True
                    save_state(state)
                    log.warning("[guard] Daily loss cap hit (%.2f%%). Pausing until next day.", dd)
            if state.get("paused_today"):
                write_heartbeat(loop_seconds=NEWS_INTERVAL, last_headline=None, last_signal="PAUSED", last_action="paused", notes=f"drawdown {dd:.2f}%")
                time.sleep(NEWS_INTERVAL)
                continue

            last_headline = None
            last_signal   = "HOLD"
            last_action   = "idle"

            for inst in INSTRUMENTS:
                # limit concurrent positions
                long_u, short_u = current_positions(inst)
                open_slots = (1 if long_u else 0) + (1 if short_u else 0)
                if open_slots >= MAX_POS_PER_INST:
                    continue

                # cooldown
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
                headline = (art.get("title") or "").strip()
                desc = (art.get("desc") or "").strip()
                last_headline = headline or desc or None

                # momentum agreement
                mom = momentum_filter(inst, lookback=10)

                action = "HOLD"
                if score >= 1.0 and mom > 0: action = "BUY"
                elif score <= -1.0 and mom < 0: action = "SELL"

                log.info("[news] %s score=%+.2f mom=%+.5f -> %s | %s", inst, score, mom, action, (headline or "")[:120])

                if action in ("BUY", "SELL"):
                    nav = account_nav()
                    units = units_for_risk(inst, nav, SL_PIPS)
                    if units > 0:
                        place_market(inst, buy=(action == "BUY"), units_abs=units, headline=last_headline)
                        state.setdefault("last_trade_at", {})[inst] = now_utc().isoformat()
                        save_state(state)
                        last_signal = action
                        last_action = "placed order"
                        break  # one trade per loop is enough

            write_heartbeat(loop_seconds=NEWS_INTERVAL, last_headline=last_headline, last_signal=last_signal, last_action=last_action, notes=loop_note)

        except Exception as e:
            log.exception("[loop] error: %s", e)
            write_heartbeat(loop_seconds=NEWS_INTERVAL, last_headline=None, last_signal="ERROR", last_action="exception", notes=str(e))

        time.sleep(NEWS_INTERVAL)

if __name__ == "__main__":
    main()
