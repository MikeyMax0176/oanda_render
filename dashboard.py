# dashboard.py
import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
import streamlit as st

# =========================
# ENV / OANDA API WIRES
# =========================
HOST = os.environ["OANDA_HOST"]               # e.g. https://api-fxpractice.oanda.com
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]
API = f"{HOST}/v3"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Price formatting / pip maps
PIP_MAP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}
DIGITS  = {"EUR_USD": 5,      "GBP_USD": 5,      "USD_JPY": 3,     "XAU_USD": 2}

def fmt_price(inst: str, x: float) -> str:
    return f"{x:.{DIGITS.get(inst, 5)}f}"

def get(path: str, params: dict | None = None):
    r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def post(path: str, body: dict):
    return requests.post(f"{API}{path}", headers=H, json=body, timeout=25)

def put(path: str, body: dict):
    return requests.put(f"{API}{path}", headers=H, json=body, timeout=25)

# Correct pricing endpoint (avoid 404s):
def get_pricing(instruments: list[str]):
    inst_csv = ",".join(instruments)
    # Use account-scoped pricing endpoint
    r = requests.get(
        f"{API}/accounts/{ACC}/pricing",
        headers=H,
        params={"instruments": inst_csv},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

# =========================
# BOT “STATUS” / LAST TRADE
# =========================
def read_last_trade_json() -> dict | None:
    """
    If your bot writes a small JSON file with the last decision, read it.
    Expected shape (example):
    {
      "ts": "2025-11-28T21:52:44Z",
      "headline": "ECB hints at rate cut",
      "instrument": "EUR_USD",
      "side": "BUY",
      "units": 1000,
      "tp": 1.16309,
      "sl": 1.15859
    }
    """
    for candidate in (
        Path(__file__).with_name("last_trade.json"),
        Path("/opt/render/project/src/last_trade.json"),
        Path.cwd() / "last_trade.json",
    ):
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None

def recent_transactions(days: int = 14) -> list[dict]:
    # OANDA rejects 'type=' when mis-specified; fetch a window, then filter locally.
    start = datetime.now(timezone.utc) - timedelta(days=days)
    end   = datetime.now(timezone.utc)
    j = get(
        f"/accounts/{ACC}/transactions",
        params={"from": start.isoformat(), "to": end.isoformat()},
    )
    tx = j.get("transactions", [])
    wanted = {"ORDER_FILL", "ORDER_CANCEL", "TRADE_CLOSE"}
    return [t for t in tx if t.get("type") in wanted][-50:]

# =========================
# STREAMLIT UI
# =========================
st.set_page_config(page_title="OANDA News Bot Dashboard", layout="wide")
st.title("OANDA News Bot Dashboard")

err_box = st.empty()

# ---------- Header KPIs ----------
try:
    acc = get(f"/accounts/{ACC}/summary")["account"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Account", acc.get("alias", ACC))
    c2.metric("Balance", acc.get("balance", "—"))
    c3.metric("Unrealized P/L", acc.get("unrealizedPL", "0"))
    c4.metric("NAV", acc.get("NAV", acc.get("balance", "—")))
except Exception as e:
    err_box.error(f"Account summary error: {e}")

st.divider()

# ---------- Status Light + Bot Panel ----------
status_col, bot_col = st.columns([1, 3])

# Status light is green if pricing call succeeds; red otherwise
with status_col:
    light = "🔴"
    try:
        pr = get_pricing(["EUR_USD"])
        if pr.get("prices"):
            light = "🟢"
    except Exception:
        light = "🔴"
    st.subheader("Status")
    st.markdown(f"# {light}")
    st.caption("Green = API healthy / dashboard running")

with bot_col:
    st.subheader("Bot Status")
    last = read_last_trade_json()
    if last:
        st.markdown(
            f"**Last decision:** {last.get('side','?')} {last.get('instrument','?')} "
            f"{last.get('units','?')} @ TP {last.get('tp','?')} / SL {last.get('sl','?')}  "
            f" — *{last.get('ts','(time unknown)')}*"
        )
        headline = last.get("headline") or os.getenv("LATEST_HEADLINE")
        if headline:
            st.info(f"**Headline traded:** {headline}")
        else:
            st.caption("No headline recorded yet.")
    else:
        st.caption("No last-trade file found. When the bot trades, it should write `last_trade.json` next to this file.")

st.divider()

# ---------- Place Order ----------
st.subheader("Place Market Order (with TP/SL)")

with st.form("place_trade"):
    c1, c2, c3, c4, c5 = st.columns([2, 1.2, 1.6, 1.2, 1.2])
    instrument = c1.selectbox("Instrument", ["EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD"], index=0)
    side       = c2.selectbox("Side", ["BUY", "SELL"], index=0)
    units_abs  = c3.number_input("Units (absolute)", min_value=1, step=100, value=1000)
    tp_pips    = c4.number_input("TP (pips)", min_value=1, value=50)
    sl_pips    = c5.number_input("SL (pips)", min_value=1, value=25)
    submitted  = st.form_submit_button("Submit Order")

if submitted:
    try:
        # Quote for TP/SL calc
        price_j = get_pricing([instrument])
        p0 = price_j["prices"][0]
        bid = float(p0["bids"][0]["price"])
        ask = float(p0["asks"][0]["price"])
        is_buy = (side == "BUY")
        entry  = ask if is_buy else bid

        pip = PIP_MAP.get(instrument, 0.0001)
        tp  = entry + (tp_pips * pip if is_buy else -tp_pips * pip)
        sl  = entry - (sl_pips * pip if is_buy else -sl_pips * pip)
        units = units_abs if is_buy else -units_abs

        body = {
            "order": {
                "type": "MARKET",
                "instrument": instrument,
                "units": str(units),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "takeProfitOnFill": {"price": fmt_price(instrument, tp), "timeInForce": "GTC"},
                "stopLossOnFill":  {"price": fmt_price(instrument, sl), "timeInForce": "GTC"},
            }
        }
        r = post(f"/accounts/{ACC}/orders", body)
        if r.status_code in (200, 201):
            st.success(f"Order OK ({r.status_code})")
        else:
            st.error(f"Order failed ({r.status_code}): {r.text[:500]}")
        st.code(r.text, language="json")
    except Exception as e:
        st.error(f"Order failed: {e}")

st.divider()

# ---------- Open Trades with inline TP/SL set ----------
st.subheader("Open Trades")
try:
    trades = get(f"/accounts/{ACC}/trades").get("trades", [])
    if not trades:
        st.caption("No open trades.")
    for t in sorted(trades, key=lambda z: z["openTime"]):
        inst   = t["instrument"]
        entry  = float(t["price"])
        units  = int(t["currentUnits"])
        is_long = units > 0
        pip    = PIP_MAP.get(inst, 0.0001)

        with st.expander(f"{inst} #{t['id']} — units={units} @ {fmt_price(inst, entry)}"):
            cA, cB = st.columns(2)
            tp_p = cA.number_input("New TP (pips)", value=50, key=f"tp_p_{t['id']}")
            sl_p = cB.number_input("New SL (pips)", value=25, key=f"sl_p_{t['id']}")

            tp = entry + (tp_p * pip if is_long else -tp_p * pip)
            sl = entry - (sl_p * pip if is_long else -sl_p * pip)
            st.write(f"Proposed TP={fmt_price(inst, tp)}  SL={fmt_price(inst, sl)}")

            if st.button("Apply TP/SL", key=f"apply_{t['id']}"):
                # OANDA recommends /trades/{id}/orders for TP/SL updates
                payload = {
                    "takeProfit": {"price": fmt_price(inst, tp)},
                    "stopLoss":   {"price": fmt_price(inst, sl)},
                }
                r = put(f"/accounts/{ACC}/trades/{t['id']}/orders", payload)
                if r.status_code in (200, 201):
                    st.success(f"Updated ({r.status_code})")
                else:
                    st.error(f"Update failed ({r.status_code}): {r.text[:400]}")
                st.code(r.text, language="json")
except Exception as e:
    st.error(f"Failed to fetch trades: {e}")

st.divider()

# ---------- Recent Activity ----------
st.subheader("Recent Fills/Closes (last 14 days)")
try:
    tx = recent_transactions(14)
    if not tx:
        st.caption("No recent fills/cancels/closes found in the window.")
    else:
        for t in tx[::-1]:
            ts = t.get("time", "")
            ttype = t.get("type", "")
            line = f"**{ttype}** — {ts}"
            details = []
            for k in ("instrument", "price", "units", "orderID", "tradeID", "reason", "pl", "commission"):
                if k in t:
                    details.append(f"{k}={t[k]}")
            st.markdown(line)
            if details:
                st.caption(", ".join(details))
except Exception as e:
    st.error(f"Recent transactions error: {e}")
