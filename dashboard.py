# dashboard.py
import os
import time
from datetime import datetime, timezone
import requests
import streamlit as st

# ---- ENV / API ----
HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]
API = f"{HOST}/v3"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

# Instrument pip/tick precision
PIP_MAP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}
DIGITS = {"EUR_USD": 5, "GBP_USD": 5, "USD_JPY": 3, "XAU_USD": 2}  # for price formatting

def fmt_price(inst: str, x: float) -> str:
    return f"{x:.{DIGITS.get(inst, 5)}f}"

def get(path: str, params: dict | None = None):
    r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def post(path: str, body: dict):
    r = requests.post(f"{API}{path}", headers=H, json=body, timeout=20)
    return r

def put(path: str, body: dict):
    r = requests.put(f"{API}{path}", headers=H, json=body, timeout=20)
    return r

# ---- UI SETUP ----
st.set_page_config(page_title="OANDA Monitor", layout="wide")
st.title("OANDA Monitor")

# ---- HEADER / ACCOUNT SUMMARY ----
err_box = st.empty()
try:
    acc = get(f"/accounts/{ACC}/summary")["account"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Account", acc.get("alias", ACC))
    c2.metric("Balance", acc["balance"])
    c3.metric("Unrealized P/L", acc.get("unrealizedPL", "0"))
    c4.metric("NAV", acc.get("NAV", acc["balance"]))
except Exception as e:
    err_box.error(f"Failed to fetch account summary: {e}")

st.divider()

# ---- PLACE TRADE FORM ----
st.subheader("Place Market Order (with TP/SL)")
with st.form("place_trade"):
    c1, c2, c3, c4, c5 = st.columns([2, 1.2, 1.8, 1.2, 1.2])
    instrument = c1.selectbox("Instrument", ["EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD"], index=0)
    side = c2.selectbox("Side", ["BUY", "SELL"], index=0)
    units_abs = c3.number_input("Units (absolute)", min_value=1, step=100, value=5000)
    tp_pips = c4.number_input("TP (pips)", min_value=1, value=50)
    sl_pips = c5.number_input("SL (pips)", min_value=1, value=25)
    submitted = st.form_submit_button("Submit Order")

if submitted:
    try:
        # Pricing is account-scoped
        q = get(f"/accounts/{ACC}/pricing", params={"instruments": instrument})
        px = q["prices"][0]
        bid = float(px["bids"][0]["price"])
        ask = float(px["asks"][0]["price"])
        is_buy = (side == "BUY")
        entry = ask if is_buy else bid

        pip = PIP_MAP.get(instrument, 0.0001)
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
                "takeProfitOnFill": {"price": fmt_price(instrument, tp)},
                "stopLossOnFill": {"price": fmt_price(instrument, sl)},
            }
        }
        r = post(f"/accounts/{ACC}/orders", body)
        if r.status_code in (200, 201):
            st.success(f"Order accepted ({r.status_code}).")
        else:
            st.error(f"Order failed: {r.status_code}\n{r.text[:800]}")
        with st.expander("Order response"):
            st.code(r.text, language="json")
    except Exception as e:
        st.error(f"Order error: {e}")

st.divider()

# ---- OPEN TRADES & PER-TRADE TP/SL ----
st.subheader("Open Trades (adjust TP/SL)")

def load_trades():
    try:
        j = get(f"/accounts/{ACC}/trades")
        return sorted(j.get("trades", []), key=lambda t: t["openTime"])
    except Exception as e:
        st.error(f"Failed to fetch trades: {e}")
        return []

trades = load_trades()
if not trades:
    st.info("No open trades.")
else:
    for t in trades:
        inst = t["instrument"]
        units = t["currentUnits"]
        entry = float(t["price"])
        is_long = not str(units).startswith("-")
        pip = PIP_MAP.get(inst, 0.0001)

        with st.expander(f"{inst} #{t['id']}  units={units}  @ {fmt_price(inst, entry)}  (opened {t['openTime']})"):
            # Try to show current dependent orders
            try:
                d = get(f"/accounts/{ACC}/trades/{t['id']}")
                detail = d.get("trade", {})
                cur_tp = (detail.get("takeProfitOrder") or {}).get("price")
                cur_sl = (detail.get("stopLossOrder") or {}).get("price")
                st.write(f"Current TP: {cur_tp} | Current SL: {cur_sl}")
            except Exception:
                st.write("Current TP/SL: (unavailable)")

            colA, colB, colC = st.columns([1, 1, 1])
            tp_in = colA.number_input("TP pips", value=50, key=f"tp_{t['id']}")
            sl_in = colB.number_input("SL pips", value=25, key=f"sl_{t['id']}")
            tp_new = entry + (tp_in * pip if is_long else -tp_in * pip)
            sl_new = entry - (sl_in * pip if is_long else -sl_in * pip)
            colC.write(f"Proposed TP={fmt_price(inst, tp_new)}  SL={fmt_price(inst, sl_new)}")

            if st.button("Set TP/SL", key=f"set_{t['id']}"):
                try:
                    body = {
                        "takeProfit": {"price": fmt_price(inst, tp_new)},
                        "stopLoss": {"price": fmt_price(inst, sl_new)},
                    }
                    r = put(f"/accounts/{ACC}/trades/{t['id']}/orders", body)
                    if r.status_code == 200:
                        st.success("TP/SL set.")
                    else:
                        st.error(f"Set failed: {r.status_code}\n{r.text[:800]}")
                    with st.expander("Response"):
                        st.code(r.text, language="json")
                except Exception as e:
                    st.error(f"Error setting TP/SL: {e}")

st.divider()

# ---- ACTIVITY (last N transactions) ----
st.subheader("Recent Activity")
with st.spinner("Loading recent transactions…"):
    try:
        # Pull a small window using lastTransactionID as a safer cursor
        acc_now = get(f"/accounts/{ACC}/summary")["account"]
        last_id = int(acc_now.get("lastTransactionID", "0"))
        # Fetch the last ~50 transactions by ID range (avoid time parsing issues)
        lo = max(0, last_id - 200)
        j = get(f"/accounts/{ACC}/transactions/idrange",
                params={"from": str(lo), "to": str(last_id)})
        txs = j.get("transactions", [])
        if not txs:
            st.info("No recent transactions found.")
        else:
            for tx in txs[-50:]:
                with st.expander(f"{tx['time']}  {tx['type']}  (id {tx['id']})"):
                    st.code(tx, language="json")
    except Exception as e:
        st.warning(f"Could not load transactions (showing none). Details: {e}")

# ---- FOOTER ----
st.caption(
    f"UTC now: {datetime.now(timezone.utc).isoformat()} • "
    f"Service Host: {os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'local')}"
)
