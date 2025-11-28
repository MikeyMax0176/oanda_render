# bot.py  — minimal test worker for Render dashboard wiring
# - Heartbeat every 10s -> runtime/bot_heartbeat.json
# - Fake last-trade snapshot every 30s -> runtime/last_trade.json
# - Optional REAL trade if ENABLE_TRADING=1

import os, json, time, math
from datetime import datetime, timezone
from pathlib import Path
import requests

# ---------- CONFIG / ENV ----------
HOST  = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC   = os.environ["OANDA_ACCOUNT"]
API   = f"{HOST}/v3"
H     = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

ENABLE_TRADING = os.getenv("ENABLE_TRADING", "0") == "1"   # set in Render env to actually trade
INSTRUMENT     = os.getenv("BOT_INSTRUMENT", "EUR_USD")
UNITS_DEFAULT  = int(os.getenv("BOT_UNITS", "1000"))
TP_PIPS        = int(os.getenv("BOT_TP_PIPS", "50"))
SL_PIPS        = int(os.getenv("BOT_SL_PIPS", "25"))

PIP_MAP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}
DIGITS  = {"EUR_USD": 5, "GBP_USD": 5, "USD_JPY": 3, "XAU_USD": 2}

# ---------- RUNTIME FILES ----------
ROOT_DIR    = Path(os.environ.get("APP_ROOT", "/opt/render/project/src"))
RUNTIME_DIR = ROOT_DIR / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

HEARTBEAT_PATH  = Path(os.environ.get("NS_HEARTBEAT_PATH", str(RUNTIME_DIR / "bot_heartbeat.json")))
LAST_TRADE_PATH = Path(os.environ.get("NS_LAST_TRADE_PATH",  str(RUNTIME_DIR / "last_trade.json")))
NEWS_PATH       = Path(os.environ.get("NS_NEWS_PATH",        str(RUNTIME_DIR / "news_signal.json")))

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

def fmt_price(inst: str, x: float) -> str:
    return f"{x:.{DIGITS.get(inst, 5)}f}"

def get(path: str, params: dict | None = None):
    r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def post(path: str, body: dict):
    return requests.post(f"{API}{path}", headers=H, json=body, timeout=20)

def fetch_quote(inst: str):
    j = get(f"/pricing", params={"instruments": inst})
    bid = float(j["prices"][0]["bids"][0]["price"])
    ask = float(j["prices"][0]["asks"][0]["price"])
    return bid, ask

def write_heartbeat():
    HEARTBEAT_PATH.write_text(json.dumps({"ts": now_utc()}))

def write_last_trade_snapshot(data: dict):
    LAST_TRADE_PATH.write_text(json.dumps(data))

def latest_news():
    try:
        if NEWS_PATH.exists():
            return json.loads(NEWS_PATH.read_text())
    except Exception:
        pass
    return {}

def place_real_trade(inst: str, units: int, tp_pips: int, sl_pips: int):
    """Places a REAL market order with TP/SL. Returns (ok, payload_dict)."""
    try:
        pip = PIP_MAP.get(inst, 0.0001)
        bid, ask = fetch_quote(inst)
        is_buy = units > 0
        entry  = ask if is_buy else bid
        tp     = entry + (tp_pips * pip if is_buy else -tp_pips * pip)
        sl     = entry - (sl_pips * pip if is_buy else -sl_pips * pip)

        body = {
            "order": {
                "type": "MARKET",
                "instrument": inst,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "takeProfitOnFill": {"price": fmt_price(inst, tp)},
                "stopLossOnFill":  {"price": fmt_price(inst, sl), "timeInForce": "GTC"},
            }
        }
        r = post(f"/accounts/{ACC}/orders", body)
        ok = r.status_code in (200, 201)
        payload = {}
        try:
            payload = r.json()
        except Exception:
            payload = {"raw": r.text}
        return ok, payload, entry, tp, sl
    except Exception as e:
        return False, {"error": str(e)}, None, None, None

def build_last_trade_payload(inst: str, side: str, units_abs: int, entry: float, tp: float, sl: float):
    sig = latest_news()
    return {
        "instrument": inst,
        "side": side,
        "units": units_abs,
        "price": float(entry),
        "tp": float(tp),
        "sl": float(sl),
        "sentiment": sig.get("sentiment"),
        "headline": sig.get("headline") or sig.get("title"),
        "url": sig.get("url") or sig.get("link"),
        "time": now_utc(),
    }

def main_loop():
    print("[bot] starting test worker; ENABLE_TRADING =", ENABLE_TRADING, flush=True)
    last_fake_at = 0.0
    fake_interval = 30.0  # seconds
    hb_interval   = 10.0  # seconds
    last_hb_at    = 0.0

    while True:
        t = time.time()

        # Heartbeat every ~10s
        if t - last_hb_at >= hb_interval:
            write_heartbeat()
            last_hb_at = t

        # Every ~30s: either do a real tiny trade (if enabled) or write a fake last-trade snapshot
        if t - last_fake_at >= fake_interval:
            last_fake_at = t

            if ENABLE_TRADING:
                # REAL tiny trade
                units = UNITS_DEFAULT  # positive=BUY, negative=SELL if desired
                ok, payload, entry, tp, sl = place_real_trade(INSTRUMENT, units, TP_PIPS, SL_PIPS)
                side = "BUY" if units > 0 else "SELL"

                if ok and entry is not None:
                    snap = build_last_trade_payload(INSTRUMENT, side, abs(units), entry, tp, sl)
                    write_last_trade_snapshot(snap)
                    print(f"[bot] placed real trade: {snap}", flush=True)
                else:
                    # write a failure snapshot for visibility
                    fail = {
                        "instrument": INSTRUMENT, "side": side, "units": abs(units),
                        "price": None, "tp": None, "sl": None,
                        "headline": "Order failed",
                        "url": None,
                        "sentiment": None,
                        "error": payload,
                        "time": now_utc(),
                    }
                    write_last_trade_snapshot(fail)
                    print(f"[bot] order failed: {payload}", flush=True)
            else:
                # FAKE snapshot to prove dashboard piping works
                pip = PIP_MAP.get(INSTRUMENT, 0.0001)
                fake_entry = 1.16000
                fake_tp    = fake_entry + TP_PIPS * pip
                fake_sl    = fake_entry - SL_PIPS * pip
                snap = {
                    "instrument": INSTRUMENT,
                    "side": "BUY",
                    "units": UNITS_DEFAULT,
                    "price": fake_entry,
                    "tp": fake_tp,
                    "sl": fake_sl,
                    "sentiment": 0.2,
                    "headline": "Synthetic test trade (no real order placed)",
                    "url": "https://example.com/test",
                    "time": now_utc(),
                }
                write_last_trade_snapshot(snap)
                print("[bot] wrote fake last_trade snapshot", flush=True)

        time.sleep(1.0)

if __name__ == "__main__":
    try:
        main_loop()
    except KeyboardInterrupt:
        print("[bot] stopping...", flush=True)
