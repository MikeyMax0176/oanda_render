# src/dashboard.py
import os
import requests
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone

# ==== ENV & API ====
HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]
API = f"{HOST}/v3"
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

PIP_MAP = {"EUR_USD": 0.0001, "GBP_USD": 0.0001, "USD_JPY": 0.01, "XAU_USD": 0.1}

# ==== HTTP helpers ====
def get(path, params=None):
    r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def put(path, body):
    return requests.put(f"{API}{path}", headers=H, json=body, timeout=20)

def post(path, body):
    return requests.post(f"{API}{path}", headers=H, json=body, timeout=20)

def account_summary():
    return get(f"/accounts/{ACC}/summary")["account"]

def current_pricing(instrument: str):
    j = get(f"/accounts/{ACC}/pricing", params={"instruments": instrument})
    p = j["prices"][0]
    bid = float(p["bids"][0]["price"])
    ask = float(p["asks"][0]["price"])
    return bid, ask

def recent_transactions(days=14):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "type": "ORDER_FILL,ORDER_CANCEL,TRADE_CLOSE",
    }
    j = get(f"/accounts/{ACC}/transactions", params=params)
    return j.get("transactions", [])

# ==== UI ====
st.set_page_config(page_title="OANDA Monitor", layout="wide")
st.title("OANDA Monitor (Render)")

# Top metrics
acc = account_summary()
col1, col2, col3 = st.columns(3)
col1.metric("Balance", acc["balance"])
col2.metric("Unrealized P/L", acc.get("unrealizedPL", "0"))
col3.metric("NAV", acc.get("NAV", acc["balance"]))

# ----- Place Trade -----
st.subheader("Place Trade")

with st.form("place_trade"):
    c1, c2, c3 = st.columns(3)
    instrument = c1.selectbox("Instrument", ["EUR_USD", "GBP_USD", "USD_JPY", "XAU_USD"], index=0)
    side       = c2.selectbox("Side", ["BUY", "SELL"], index=0)
    units_abs  = c3.number_input("Units (abs)", min_value=1, step=100, value=5000)

    r1, r2 = st.columns(2)
    tp_pips = r1.number_input("TP pips", min_value=1, value=50)
    sl_pips = r2.number_input("SL pips", min_value=1, value=25)

    submitted = st.form_submit_button("Submit Order")

if submitted:
    try:
        pip = PIP_MAP.get(instrument, 0.0001)
        bid, ask = current_pricing(instrument)
        is_buy = (side == "BUY")
        entry = ask if is_buy else bid

        tp =
