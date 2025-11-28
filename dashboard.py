# dashboard.py
import os
import json
import math
import time
from datetime import datetime, timezone, timedelta

import requests
import streamlit as st

# =========================
# ENV / OANDA BASICS
# =========================
HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]

API = f"{HOST}/v3"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Optional paths written by your background bot/news worker
HEARTBEAT_PATH = os.getenv("HEARTBEAT_PATH", "/opt/render/project/src/runtime/bot_heartbeat.json")
NEWS_LAST_TRADE_PATH = os.getenv("NEWS_LAST_TRADE_PATH", "/opt/render/project/src/runtime/news_last_trade.json")

# PIP sizes and formatting digits for common instruments
PIP_MAP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}
DIGITS = {"EUR_USD": 5, "GBP_USD": 5, "USD_JPY": 3, "XAU_USD": 2}


# =========================
# Robust HTTP helpers
# =========================
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5


def _sleep(i: int) -> None:
    time.sleep(0.5 * (2 ** i))  # 0.5,1,2,4,8


def _raise_http(path: str, resp: requests.Response, method="GET"):
    raise requests.HTTPError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")


def get(path: str, params: dict | None = None, retries: int = MAX_RETRIES):
    last = None
    for i in range(retries):
        try:
            r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
        except requests.RequestException as e:
            last = e
            _sleep(i)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in RETRY_STATUSES:
            last = r
            _sleep(i)
            continue
        _raise_http(path, r, "GET")
    if isinstance(last, requests.Response):
        _raise_http(path, last, "GET")
    raise requests.HTTPError(f"GET {path} failed after retries: {last}")


def post(path: str, body: dict, retries: int = MAX_RETRIES) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            r = requests.post(f"{API}{path}", headers=H, json=body, timeout=20)
        except requests.RequestException as e:
            last = e
            _sleep(i)
            continue
        if r.status_code not in RETRY_STATUSES:
            return r
        last = r
        _sleep(i)
    return last if isinstance(last, requests.Response) else r


def put(path: str, body: dict | None = None, retries: int = MAX_RETRIES) -> requests.Response:
    last = None
    for i in range(retries):
        try:
            r = requests.put(f"{API}{path}", headers=H, json=body or {}, timeout=20)
        except requests.RequestException as e:
            last = e
            _sleep(i)
            continue
        if r.status_code not in RETRY_STATUSES:
            return r
        last = r
        _sleep(i)
    return last if isinstance(last, requests.Response) else r


# =========================
# Utility helpers
# =========================
def fmt(inst: str, x: float) -> str:
    return f"{x:.{DIGITS.get(inst, 5)}f}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(ts: str) -> datetime:
    # OANDA timestamps are usually Zulu; handle both with/without Z
    if ts.endswith("Z"):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromisoformat(ts)


def heartbeat_info() -> dict | None:
    try:
        with open(HEARTBEAT_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return None


def last_trade_news() -> dict | None:
    try:
        with open(NEWS_LAST_TRADE_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return None


def account_summary() -> dict:
    return get(f"/accounts/{ACC}/summary")["account"]


def open_trades() -> list[dict]:
    return get(f"/accounts/{ACC}/trades").get("trades", [])


def pricing(instrument: str) -> dict:
    # IMPORTANT: correct endpoint requires account in the path
    return get(f"/accounts/{ACC}/pricing", params={"instruments": instrument})


def today_realized_pl() -> float:
    # Sum 'pl' for transactions since UTC midnight
    start = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    end = now_utc() + timedelta(seconds=1)
    j = get(
        f"/accounts/{ACC}/transactions",
        params={
            "from": start.isoformat(),
            "to": end.isoformat(),
            "type": "ORDER_FILL,ORDER_CANCEL,TRADE_CLOSE",
        },
    )
    total = 0.0
    for t in j.get("transactions", []):
        try:
            total += float(t.get("pl", "0"))
        except Exception:
            pass
    return total


# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="OANDA Monitor", layout="wide")
st.title("OANDA Monitor")

# small refresh button to avoid auto-refresh warnings
c_refresh, _, _ = st.columns([1, 6, 1])
if c_refresh.button("↻ Refresh"):
    st.rerun()

# -------------------------
# HEADER: Status + Account
# -------------------------
status_col, a1, a2, a3 = st.columns([1.1, 3, 3, 3])

# status light (red/green) based on heartbeat freshness + API reachability
light = "red"
status_msg = "Not working"
hb = heartbeat_info()
hb_text = "None"
if hb and "last_beat" in hb:
    hb_time = parse_ts(hb["last_beat"])
    age = (now_utc() - hb_time).total_seconds()
    hb_text = hb["last_beat"]
    # consider 'fresh' if within 180 seconds
    if age <= 180:
        light = "green"
        status_msg = "Working"

status_col.markdown(
    f"""
    <div style="display:inline-flex;align-items:center;gap:8px;">
        <div style="width:14px;height:14px;border-radius:50%;background:{light};border:1px solid #555;"></div>
        <span><b>{status_msg}</b></span>
    </div><div style="font-size:12px;color:#999;">{hb_text}</div>
    """,
    unsafe_allow_html=True,
)

acc_err = st.empty()
try:
    acc = account_summary()
    a1.metric("Account", acc.get("alias", ACC))
    a2.metric("Unrealized P/L", acc.get("unrealizedPL", "0"))
    a3.metric("NAV", acc.get("NAV", acc.get("balance", "—")))
except Exception as e:
    acc_err.error(f"OANDA API error: {e}")

# If the heartbeat file is missing, show a gentle note
if hb is None:
    st.info(
        f"No heartbeat file found yet (expected at: {HEARTBEAT_PATH})",
        icon="ℹ️",
    )

st.divider()

# -------------------------
# BOT STATUS PANEL
# (reads whatever your bot writes to heartbeat JSON)
# -------------------------
st.subheader("Bot Status")
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)

if hb:
    c1.metric("Max Concurrent Trades", hb.get("max_concurrent_trades", "—"))
    c2.metric("Trade Interval (min)", hb.get("trade_interval_min", "—"))
    c3.metric("Cooldown (min)", hb.get("cooldown_min", "—"))
    c4.metric("Min Spread", hb.get("min_spread", "—"))
    c5.metric("Risk % per Trade", f"{hb.get('risk_pct', 0):.2%}" if isinstance(hb.get("risk_pct"), (int, float)) else "—")
    c6.metric("SL (pips)", hb.get("sl_pips", "—"))
    c7.metric("TP (pips)", hb.get("tp_pips", "—"))
else:
    st.caption("No bot heartbeat details to display yet.")

# live counts & guardrails
c1b, c2b, c3b = st.columns(3)
try:
    ot = open_trades()
    c1b.metric("Open Trades", len(ot))
except Exception as e:
    c1b.error(f"Unable to fetch open trades: {e}")

try:
    day_pl = today_realized_pl()
    c2b.metric("Realized P/L (UTC today)", f"{day_pl:+.2f}")
except Exception as e:
    c2b.error(f"Realized P/L fetch failed: {e}")

c3b.metric("Max Daily Loss (guard)", hb.get("max_daily_loss", "—") if hb else "—")

st.divider()

# -------------------------
# LATEST TRADE HEADLINE
# -------------------------
st.subheader("Latest Trade Headline")
news = last_trade_news()
if news:
    headline = news.get("headline", "—")
    src = news.get("source", "—")
    sent = news.get("sentiment", None)
    sent_txt = f"{sent:+.2f}" if isinstance(sent, (int, float)) else "—"
    tstamp = news.get("time", "—")

    st.markdown(f"**{headline}**")
    cN1, cN2, cN3 = st.columns(3)
    cN1.write(f"**Source:** {src}")
    cN2.write(f"**Sentiment:** {sent_txt}")
    cN3.write(f"**Time (UTC):** {tstamp}")
else:
    st.caption("No recorded trade headline yet.")

st.divider()

# -------------------------
# PLACE MARKET ORDER
# -------------------------
st.subheader("Place Market Order (with TP/SL)")

with st.form("place_trade"):
    cc1, cc2, cc3, cc4, cc5 = st.columns([2, 1.2, 1.6, 1.2, 1.2])
    instrument = cc1.selectbox("Instrument", ["EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD"], index=0)
    side = cc2.selectbox("Side", ["BUY", "SELL"], index=0)
    units_abs = cc3.number_input("Units (absolute)", min_value=1, step=100, value=1000)
    tp_pips = cc4.number_input("TP (pips)", min_value=1, value=38)
    sl_pips = cc5.number_input("SL (pips)", min_value=1, value=25)
    submitted = st.form_submit_button("Submit Order")

if submitted:
    try:
        pip = PIP_MAP.get(instrument, 0.0001)

        # Correct pricing endpoint for OANDA v20:
        pr = pricing(instrument)
        # Pull best bid/ask
        p0 = pr["prices"][0]
        bid = float(p0["bids"][0]["price"])
        ask = float(p0["asks"][0]["price"])
        is_buy = (side == "BUY")
        entry = ask if is_buy else bid

        tp = entry + (tp_pips * pip if is_buy else -tp_pips * pip)
        sl = entry - (sl_pips * pip if is_buy else -sl_pips * pip)

        units = units_abs if is_buy else -units_abs
        body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "takeProfitOnFill": {"price": fmt(instrument, tp), "timeInForce": "GTC"},
                "stopLossOnFill": {"price": fmt(instrument, sl), "timeInForce": "GTC"},
            }
        }
        r = post(f"/accounts/{ACC}/orders", body)
        if r is None:
            st.error("Order failed (no response).")
        elif r.status_code not in (200, 201):
            st.error(f"Order failed: {r.status_code}\n\n{r.text[:500]}")
        else:
            st.success(f"Order OK ({r.status_code})")
            st.code(r.text, language="json")
            st.balloons()
            st.rerun()
    except Exception as e:
        st.error(f"Order failed: {e}")

st.divider()

# -------------------------
# OPEN TRADES (with quick TP/SL set)
# -------------------------
st.subheader("Open Trades")
try:
    trades = open_trades()
    if not trades:
        st.caption("No open trades.")
    else:
        for t in sorted(trades, key=lambda x: x["openTime"]):
            inst = t["instrument"]
            units = t["currentUnits"]
            entry = float(t["price"])
            is_long = not str(units).startswith("-")
            pip = PIP_MAP.get(inst, 0.0001)

            with st.expander(f"{inst} #{t['id']}   units={units}   entry={fmt(inst, entry)}"):
                cA, cB, cC = st.columns(3)
                tp_p = cA.number_input("TP (pips)", value=38, key=f"tp_{t['id']}")
                sl_p = cB.number_input("SL (pips)", value=25, key=f"sl_{t['id']}")
                tp_val = entry + (tp_p * pip if is_long else -tp_p * pip)
                sl_val = entry - (sl_p * pip if is_long else -sl_p * pip)
                cC.write(f"Proposed TP={fmt(inst, tp_val)}  SL={fmt(inst, sl_val)}")

                if st.button("Set TP/SL", key=f"set_{t['id']}"):
                    r1 = put(f"/accounts/{ACC}/trades/{t['id']}/orders", {"takeProfit": {"price": fmt(inst, tp_val)}})
                    r2 = put(f"/accounts/{ACC}/trades/{t['id']}/orders", {"stopLoss": {"price": fmt(inst, sl_val)}})
                    st.write("TP:", r1.status_code, r1.text[:200])
                    st.write("SL:", r2.status_code, r2.text[:200])
except Exception as e:
    st.error(f"Open trades error: {e}")
