# bot.py  (full replacement or merge carefully if you have custom logic)
import os, time, math, requests
from datetime import datetime, timezone, timedelta

HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]
API  = f"{HOST}/v3"
H    = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# === Tunables from ENV ===
RISK_PCT        = float(os.getenv("NS_RISK_PCT", "0.005"))        # 0.5% ≈ $500 on 100k
SL_PIPS          = float(os.getenv("NS_SL_PIPS", "25"))
TP_PIPS          = float(os.getenv("NS_TP_PIPS", "38"))
SENT_THRESH      = float(os.getenv("NS_SENT_THRESH", "0.15"))
COOLDOWN_MIN     = float(os.getenv("NS_COOLDOWN_MIN", "0"))
TRADE_INTERVAL   = float(os.getenv("NS_TRADE_INTERVAL_MIN", "1"))  # global throttle minutes
POLL_SEC         = int(os.getenv("NS_POLL_SEC", "20"))
MAX_CONCURRENT   = int(os.getenv("NS_MAX_CONCURRENT", "3"))
MIN_SPREAD       = float(os.getenv("NS_MIN_SPREAD", "0.0002"))
MAX_DAILY_LOSS   = float(os.getenv("NS_MAX_DAILY_LOSS", "1500"))
INSTRUMENTS      = [x.strip() for x in os.getenv("NS_INSTRUMENTS", "EUR_USD").split(",") if x.strip()]

PIP = {"EUR_USD":0.0001, "GBP_USD":0.0001, "USD_JPY":0.01, "XAU_USD":0.1}

last_trade_time  = None
daily_start_date = None
daily_pl         = 0.0

def get(p, params=None):
    r = requests.get(f"{API}{p}", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def post(p, body):
    return requests.post(f"{API}{p}", headers=H, json=body, timeout=20)

def now_utc():
    return datetime.now(timezone.utc)

def reset_daily_if_needed():
    global daily_start_date, daily_pl
    today = now_utc().date()
    if daily_start_date != today:
        daily_start_date = today
        daily_pl = 0.0

def account_summary():
    return get(f"/accounts/{ACC}/summary")["account"]

def open_trades():
    return get(f"/accounts/{ACC}/trades").get("trades", [])

def recent_pl_increment(minutes=2):
    """Approx PL delta from most recent fills in the last N minutes."""
    end = now_utc()
    start = end - timedelta(minutes=minutes)
    j = get(f"/accounts/{ACC}/transactions",
            params={"from": start.isoformat(), "to": end.isoformat(),
                    "type":"ORDER_FILL,TRADE_CLOSE"})
    inc = 0.0
    for t in j.get("transactions", []):
        # realized PL on close; ignore tiny floats
        if t.get("type") == "ORDER_FILL" and "pl" in t:
            inc += float(t["pl"])
        if t.get("type") == "TRADE_CLOSE" and "pl" in t:
            inc += float(t["pl"])
    return inc

def latest_sentiment():
    """
    Pull the latest sentiment snapshot produced by news_sentiment.py.
    Return list of tuples: (instrument, sentiment, timestamp).
    Positive => buy; negative => sell.
    """
    try:
        j = get("/accounts/{}/summary".format(ACC))  # quick ping to confirm auth
    except:
        pass
    # Replace this with your actual sentiment source.
    # For now, assume news_sentiment.py stores a file/endpoint you already wired.
    # Example (very simple stub): always returns neutral except every few polls.
    t = now_utc().second
    fake = []
    for inst in INSTRUMENTS:
        s = 0.0
        if t % 4 == 0: s = 0.2     # triggers BUY
        if t % 6 == 0: s = -0.2    # triggers SELL sometimes
        fake.append((inst, s, now_utc()))
    return fake

def pricing(inst):
    j = get("/pricing", params={"instruments": inst, "accountId": ACC})
    p = j["prices"][0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return bid, ask, p

def enough_time_since_last_trade():
    global last_trade_time
    if last_trade_time is None:
        return True
    return (now_utc() - last_trade_time) >= timedelta(minutes=TRADE_INTERVAL)

def can_open_more():
    return len(open_trades()) < MAX_CONCURRENT

def compute_units(inst, risk_usd, sl_pips):
    pip = PIP.get(inst, 0.0001)
    # For majors: 1 unit ≈ pip value of pip
    # risk per unit = sl_pips * pip value
    risk_per_unit = sl_pips * pip
    if risk_per_unit <= 0: return 0
    units = int(max(1, risk_usd / risk_per_unit))
    return units

def place(inst, side, tp_pips, sl_pips):
    bid, ask, _ = pricing(inst)
    spread = ask - bid
    if spread > MIN_SPREAD:
        return False, f"Spread too wide ({spread:.5f} > {MIN_SPREAD})"

    acc = account_summary()
    bal = float(acc["balance"])
    risk_usd = bal * RISK_PCT

    pip = PIP.get(inst, 0.0001)
    entry = ask if side == "BUY" else bid
    tp = entry + (tp_pips * pip if side == "BUY" else -tp_pips * pip)
    sl = entry - (sl_pips * pip if side == "BUY" else -sl_pips * pip)

    units = compute_units(inst, risk_usd, sl_pips)
    if units <= 0:
        return False, "Units computed as 0"

    units = units if side == "BUY" else -units
    body = {
        "order": {
            "type": "MARKET",
            "instrument": inst,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "takeProfitOnFill": {"price": f"{tp:.5f}", "timeInForce": "GTC"},
            "stopLossOnFill":  {"price": f"{sl:.5f}", "timeInForce": "GTC"}
        }
    }
    r = post(f"/accounts/{ACC}/orders", body)
    ok = r.status_code in (200, 201)
    return ok, r.text[:400]

def loop():
    global last_trade_time, daily_pl
    while True:
        try:
            reset_daily_if_needed()

            # apply daily stop
            inc = recent_pl_increment(2)
            daily_pl += inc
            if daily_pl <= -abs(MAX_DAILY_LOSS):
                print(f"[halt] daily loss {daily_pl:.2f} <= -{MAX_DAILY_LOSS}, pausing until UTC midnight")
                time.sleep(60)
                continue

            # throttle
            if not enough_time_since_last_trade():
                time.sleep(POLL_SEC)
                continue

            # check open concurrency
            if not can_open_more():
                time.sleep(POLL_SEC)
                continue

            # get latest sentiment
            snaps = latest_sentiment()
            fired = False
            for inst, s, ts in snaps:
                if inst not in INSTRUMENTS:
                    continue
                if s >= SENT_THRESH:
                    ok, msg = place(inst, "BUY", TP_PIPS, SL_PIPS)
                    print(f"[trade BUY {inst}] {ok} {msg}")
                    fired = fired or ok
                elif s <= -SENT_THRESH:
                    ok, msg = place(inst, "SELL", TP_PIPS, SL_PIPS)
                    print(f"[trade SELL {inst}] {ok} {msg}")
                    fired = fired or ok

                # If one trade was placed, mark time and optionally respect COOLDOWN
                if fired:
                    last_trade_time = now_utc()
                    # COOLDOWN_MIN can be zero (constant churn). TRADE_INTERVAL is the main global throttle.
                    if COOLDOWN_MIN > 0:
                        time.sleep(int(COOLDOWN_MIN * 60))
                    break

        except Exception as e:
            print("[error loop]", e)

        time.sleep(POLL_SEC)

if __name__ == "__main__":
    loop()
