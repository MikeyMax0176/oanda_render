# dashboard.py
import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import streamlit as st

# =========================
# Config / Environment
# =========================
HOST  = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC   = os.environ["OANDA_ACCOUNT"]
API   = f"{HOST}/v3"
H     = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Optional file paths (bot/news workers can write these)
ROOT_DIR            = Path(os.environ.get("APP_ROOT", "/opt/render/project/src"))
RUNTIME_DIR         = ROOT_DIR / "runtime"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

HEARTBEAT_PATH      = Path(os.environ.get("NS_HEARTBEAT_PATH", str(RUNTIME_DIR / "bot_heartbeat.json")))
LAST_TRADE_PATH     = Path(os.environ.get("NS_LAST_TRADE_PATH", str(RUNTIME_DIR / "last_trade.json")))
NEWS_SIGNAL_PATH    = Path(os.environ.get("NS_NEWS_PATH",       str(RUNTIME_DIR / "news_signal.json")))

# Risk/behavior knobs (for display only)
RISK_PCT           = float(os.getenv("NS_RISK_PCT", "0.005"))
SL_PIPS            = float(os.getenv("NS_SL_PIPS", "25"))
TP_PIPS            = float(os.getenv("NS_TP_PIPS", "38"))
SENT_THRESH        = float(os.getenv("NS_SENT_THRESH", "0.15"))
COOLDOWN_MIN       = float(os.getenv("NS_COOLDOWN_MIN", "0"))
TRADE_INTERVAL_MIN = float(os.getenv("NS_TRADE_INTERVAL_MIN", "1"))
MAX_CONCURRENT     = int(os.getenv("NS_MAX_CONCURRENT", "3"))
MIN_SPREAD         = float(os.getenv("NS_MIN_SPREAD", "0.0002"))
MAX_DAILY_LOSS     = float(os.getenv("NS_MAX_DAILY_LOSS", "1500"))
INSTRUMENTS        = [x.strip() for x in os.getenv("NS_INSTRUMENTS", "EUR_USD,GBP_USD,USD_JPY").split(",") if x.strip()]

PIP_MAP = {"EUR_USD":0.0001, "GBP_USD":0.0001, "USD_JPY":0.01, "XAU_USD":0.1}
DIGITS  = {"EUR_USD":5, "GBP_USD":5, "USD_JPY":3, "XAU_USD":2}

# =========================
# Helpers
# =========================
def fmt_price(inst: str, x: float) -> str:
    return f"{x:.{DIGITS.get(inst,5)}f}"

def get(path: str, params: dict | None = None):
    r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def post(path: str, body: dict):
    return requests.post(f"{API}{path}", headers=H, json=body, timeout=20)

def put(path: str, body: dict):
    return requests.put(f"{API}{path}", headers=H, json=body, timeout=20)

def read_json_safe(p: Path):
    try:
        if p.exists():
            return json.loads(p.read_text())
    except Exception:
        pass
    return None

def file_age_seconds(p: Path) -> float | None:
    try:
        if p.exists():
            return max(0.0, time.time() - p.stat().st_mtime)
    except Exception:
        return None
    return None

def account_summary():
    return get(f"/accounts/{ACC}/summary")["account"]

def open_trades():
    return get(f"/accounts/{ACC}/trades").get("trades", [])

def recent_transactions(days: int = 14):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    try:
        j = get(f"/accounts/{ACC}/transactions",
                params={
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "type": "ORDER_FILL,ORDER_CANCEL,TRADE_CLOSE"
                })
        return j.get("transactions", [])
    except requests.HTTPError:
        return []

def today_realized_pl():
    """Sum realized PL (fills & closes) since UTC midnight."""
    now = datetime.now(timezone.utc)
    start = datetime(year=now.year, month=now.month, day=now.day, tzinfo=timezone.utc)
    try:
        j = get(f"/accounts/{ACC}/transactions",
                params={
                    "from": start.isoformat(),
                    "to": now.isoformat(),
                    "type": "ORDER_FILL,TRADE_CLOSE"
                })
        pl = 0.0
        for t in j.get("transactions", []):
            if "pl" in t:
                try:
                    pl += float(t["pl"])
                except Exception:
                    pass
        return pl
    except requests.HTTPError:
        return None

# =========================
# UI
# =========================
st.set_page_config(page_title="OANDA Monitor", layout="wide")
st.title("OANDA Monitor")

# Auto-refresh every 10s
st_autorefresh = st.empty()
st_autorefresh.write(f"Auto-refresh every 10s · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
st.experimental_set_query_params(_=str(int(time.time())))  # make sure Streamlit doesn't cache too hard
st.experimental_rerun if False else None  # no-op; placeholder to appease linters

# =========================
# Status Light + Account header
# =========================
acc = None
api_ok = False
api_err = None
try:
    acc = account_summary()
    api_ok = True
except Exception as e:
    api_err = str(e)

hb_age = file_age_seconds(HEARTBEAT_PATH)
hb_fresh = (hb_age is not None) and (hb_age <= 120)

working = api_ok and hb_fresh

def status_dot(green: bool):
    color = "#29cc6a" if green else "#ff4d4f"
    return f"""
    <div style="display:flex;align-items:center;gap:10px">
      <div style="width:14px;height:14px;border-radius:50%;background:{color};box-shadow:0 0 8px {color};"></div>
      <span style="font-weight:600;color:{color}">{'Working' if green else 'Not working'}</span>
    </div>
    """

c0, c1, c2, c3, c4 = st.columns([1.2, 2, 2, 2, 2])
with c0:
    st.markdown(status_dot(working), unsafe_allow_html=True)
with c1:
    st.metric("Account", acc.get("alias", ACC) if acc else ACC)
with c2:
    st.metric("Balance", acc["balance"] if acc else "—")
with c3:
    st.metric("Unrealized P/L", acc.get("unrealizedPL", "0") if acc else "—")
with c4:
    st.metric("NAV", acc.get("NAV", acc["balance"]) if acc else "—")

if not api_ok:
    st.error(f"OANDA API error: {api_err}")
if hb_age is None:
    st.info("No heartbeat file found yet (expected at: {})".format(HEARTBEAT_PATH))
elif not hb_fresh:
    st.warning(f"Heartbeat stale: last update {int(hb_age)}s ago (threshold 120s).")

st.divider()

# =========================
# Bot Status panel
# =========================
st.subheader("Bot Status")

bs1, bs2, bs3, bs4 = st.columns(4)
bs1.metric("Max Concurrent Trades", f"{MAX_CONCURRENT}")
bs2.metric("Trade Interval (min)", f"{TRADE_INTERVAL_MIN}")
bs3.metric("Cooldown (min)", f"{COOLDOWN_MIN}")
bs4.metric("Min Spread", f"{MIN_SPREAD}")

cs1, cs2, cs3, cs4 = st.columns(4)
cs1.metric("Risk % per Trade", f"{RISK_PCT*100:.2f}%")
cs2.metric("SL (pips)", f"{SL_PIPS}")
cs3.metric("TP (pips)", f"{TP_PIPS}")
cs4.metric("Sentiment Threshold", f"{SENT_THRESH:+.2f}")

# live counts
open_ts = []
try:
    open_ts = open_trades()
except Exception as e:
    st.warning(f"Unable to fetch open trades: {e}")

daily_pl = today_realized_pl()
ds1, ds2, ds3 = st.columns(3)
ds1.metric("Open Trades", f"{len(open_ts)}")
ds2.metric("Realized P/L (UTC today)", "—" if daily_pl is None else f"{daily_pl:.2f}")
ds3.metric("Max Daily Loss (guard)", f"-{MAX_DAILY_LOSS:.0f}")

# =========================
# Headline traded on (and latest signal)
# =========================
st.subheader("Latest Trade Headline")

lt = read_json_safe(LAST_TRADE_PATH) or {}
sig = read_json_safe(NEWS_SIGNAL_PATH) or {}

if lt:
    ln1 = lt.get("headline") or lt.get("title") or "—"
    url = lt.get("url") or lt.get("link")
    inst = lt.get("instrument", "—")
    side = lt.get("side", "—")
    sent = lt.get("sentiment", None)
    ts   = lt.get("time") or lt.get("timestamp")
    st.write(f"**Instrument:** {inst} · **Side:** {side} · **Sentiment:** {sent if sent is not None else '—'} · **Time:** {ts if ts else '—'}")
    if url:
        st.markdown(f"[{ln1}]({url})")
    else:
        st.write(ln1)
else:
    st.info("No recorded trade headline yet.")
    # Fall back to current signal so user sees *something*
    if sig:
        st.caption("Most recent news signal:")
        ln1 = sig.get("headline") or sig.get("title") or "—"
        url = sig.get("url") or sig.get("link")
        sent = sig.get("sentiment", "—")
        st.write(f"**Sentiment:** {sent}")
        if url:
            st.markdown(f"[{ln1}]({url})")
        else:
            st.write(ln1)

st.divider()

# =========================
# Place Market Order (manual)
# =========================
st.subheader("Place Market Order (with TP/SL)")

with st.form("place_trade"):
    c1, c2, c3, c4, c5 = st.columns([2, 1.2, 1.6, 1.2, 1.2])
    instrument = c1.selectbox("Instrument", INSTRUMENTS, index=0)
    side       = c2.selectbox("Side", ["BUY", "SELL"], index=0)
    units_abs  = c3.number_input("Units (absolute)", min_value=1, step=100, value=5000)
    tp_pips_in = c4.number_input("TP (pips)", min_value=1, value=int(TP_PIPS))
    sl_pips_in = c5.number_input("SL (pips)", min_value=1, value=int(SL_PIPS))
    submitted  = st.form_submit_button("Submit Order")

if submitted:
    try:
        pip = PIP_MAP.get(instrument, 0.0001)
        # OANDA pricing endpoint needs ?instruments= and optionally account
        pr = get("/pricing", params={"instruments": instrument, "accountId": ACC})
        bid = float(pr["prices"][0]["bids"][0]["price"])
        ask = float(pr["prices"][0]["asks"][0]["price"])
        is_buy = (side == "BUY")
        entry = ask if is_buy else bid

        tp = entry + (tp_pips_in * pip if is_buy else -tp_pips_in * pip)
        sl = entry - (sl_pips_in * pip if is_buy else -sl_pips_in * pip)

        units = units_abs if is_buy else -units_abs
        body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "takeProfitOnFill": {"price": f"{tp:.5f}", "timeInForce": "GTC"},
                "stopLossOnFill":  {"price": f"{sl:.5f}", "timeInForce": "GTC"}
            }
        }
        r = post(f"/accounts/{ACC}/orders", body)
        if r.status_code in (200, 201):
            st.success(f"Order placed ({r.status_code})")
        else:
            st.error(f"Order failed ({r.status_code}): {r.text[:400]}")
        st.code(r.text, language="json")
    except Exception as e:
        st.error(f"Order failed: {e}")

st.divider()

# =========================
# Open Trades
# =========================
st.subheader("Open Trades")
try:
    trades = open_ts if open_ts else open_trades()
    for t in sorted(trades, key=lambda x: x["openTime"]):
        inst = t["instrument"]
        units = t["currentUnits"]
        entry = float(t["price"])
        is_long = not str(units).startswith("-")
        with st.expander(f"{inst} #{t['id']} units={units} @ {fmt_price(inst, entry)}"):
            colA, colB = st.columns(2)
            tp_pips_set = colA.number_input("TP (pips)", value=int(TP_PIPS), key=f"tp_{t['id']}")
            sl_pips_set = colB.number_input("SL (pips)", value=int(SL_PIPS), key=f"sl_{t['id']}")
            pip = PIP_MAP.get(inst, 0.0001)
            tp = entry + (tp_pips_set * pip if is_long else -tp_pips_set * pip)
            sl = entry - (sl_pips_set * pip if is_long else -sl_pips_set * pip)
            st.write(f"Proposed TP={fmt_price(inst, tp)}  SL={fmt_price(inst, sl)}")
            if st.button("Apply TP/SL", key=f"set_{t['id']}"):
                r1 = put(f"/accounts/{ACC}/trades/{t['id']}", {"takeProfit": {"price": f"{tp:.5f}"}})
                r2 = put(f"/accounts/{ACC}/trades/{t['id']}", {"stopLoss":  {"price": f"{sl:.5f}"}})
                st.write("TP:", r1.status_code, r1.text[:200])
                st.write("SL:", r2.status_code, r2.text[:200])
except Exception as e:
    st.warning(f"Could not list open trades: {e}")

# =========================
# Recent Transactions
# =========================
st.subheader("Recent Transactions (last 14 days)")
tx = recent_transactions(14)
if not tx:
    st.caption("No transactions to show (or endpoint not available).")
else:
    for t in tx[-50:][::-1]:  # show up to the latest 50, newest first
        st.code(json.dumps(t, indent=2), language="json")
