cat > dashboard.py <<'PY'
import os, requests, streamlit as st, pandas as pd
from datetime import datetime, timedelta, timezone

HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]
API = f"{HOST}/v3"
H   = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

PIP = {"EUR_USD":0.0001, "GBP_USD":0.0001, "USD_JPY":0.01, "XAU_USD":0.1}

def get(path, params=None):
    r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
    r.raise_for_status(); return r.json()

def put(path, body):
    return requests.put(f"{API}{path}", headers=H, json=body, timeout=20)

def post(path, body):
    return requests.post(f"{API}{path}", headers=H, json=body, timeout=20)

def account_summary():
    return get(f"/accounts/{ACC}/summary")["account"]

def current_pricing(inst):
    j = get(f"/accounts/{ACC}/pricing", params={"instruments": inst})
    p = j["prices"][0]; bid=float(p["bids"][0]["price"]); ask=float(p["asks"][0]["price"])
    return bid, ask

def recent_transactions(days=14):
    end = datetime.now(timezone.utc); start = end - timedelta(days=days)
    j = get(f"/accounts/{ACC}/transactions",
            params={"from": start.isoformat(), "to": end.isoformat(),
                    "type":"ORDER_FILL,ORDER_CANCEL,TRADE_CLOSE"})
    return j.get("transactions", [])

st.set_page_config(page_title="OANDA Monitor", layout="wide")
st.title("OANDA Monitor (Render)")

# Top metrics
acc = account_summary()
c1,c2,c3 = st.columns(3)
c1.metric("Balance", acc["balance"])
c2.metric("Unrealized P/L", acc.get("unrealizedPL","0"))
c3.metric("NAV", acc.get("NAV", acc["balance"]))

# ---- Place Trade ----
st.subheader("Place Trade")
with st.form("place_trade"):
    a,b,c = st.columns(3)
    inst  = a.selectbox("Instrument", ["EUR_USD","GBP_USD","USD_JPY","XAU_USD"], index=0)
    side  = b.selectbox("Side", ["BUY","SELL"], index=0)
    units_abs = c.number_input("Units (abs)", min_value=1, step=100, value=5000)
    r1,r2 = st.columns(2)
    tp_pips = r1.number_input("TP pips", min_value=1, value=50)
    sl_pips = r2.number_input("SL pips", min_value=1, value=25)
    submitted = st.form_submit_button("Submit Order")

if submitted:
    try:
        pip = PIP.get(inst, 0.0001)
        bid, ask = current_pricing(inst)
        is_buy = (side=="BUY")
        entry = ask if is_buy else bid
        tp = entry + (tp_pips * pip if is_buy else -tp_pips * pip)
        sl = entry - (sl_pips * pip if is_buy else -sl_pips * pip)
        units = units_abs if is_buy else -units_abs
        body = {
            "order": {
                "type":"MARKET","instrument":inst,"units":str(units),
                "timeInForce":"FOK","positionFill":"DEFAULT",
                "takeProfitOnFill":{"price":f"{tp:.5f}"},
                "stopLossOnFill":{"price":f"{sl:.5f}"}
            }
        }
        r = post(f"/accounts/{ACC}/orders", body)
        st.success(f"Order status {r.status_code}")
        st.code(r.text, language="json")
    except Exception as e:
        st.error(f"Failed: {e}")

# ---- Performance (last 14 days) ----
st.subheader("Performance (last 14 days)")
rows=[]
for t in recent_transactions(14):
    if t.get("type") in ("ORDER_FILL","TRADE_CLOSE"):
        rows.append({
            "time": t["time"],
            "type": t["type"],
            "instrument": t.get("instrument"),
            "units": float(t.get("units","0")),
            "price": float(t.get("price","0")),
            "pl": float(t.get("pl","0")),
        })
df = pd.DataFrame(rows)
if not df.empty:
    df["time"] = pd.to_datetime(df["time"])
    df["date"] = df["time"].dt.date
    daily = df.groupby("date", as_index=False)["pl"].sum()
    cum = daily["pl"].cumsum().iloc[-1]
    m1,m2 = st.columns(2)
    m1.metric("Realized P&L", f"{df['pl'].sum():.2f}")
    m2.metric("Cumulative", f"{cum:.2f}")
    st.line_chart(daily.set_index("date"))
    st.dataframe(df.sort_values("time", ascending=False), use_container_width=True)
else:
    st.caption("No recent fills yet.")

# ---- Open Trades ----
st.subheader("Open Trades")
trades = get(f"/accounts/{ACC}/trades").get("trades", [])
for t in sorted(trades, key=lambda x: x["openTime"]):
    inst = t["instrument"]; units=t["currentUnits"]; entry=float(t["price"])
    is_long = not str(units).startswith("-"); pip = PIP.get(inst,0.0001)
    with st.expander(f"{inst} #{t['id']} units={units} @ {entry}"):
        ca,cb = st.columns(2)
        tp_p = ca.number_input("TP pips", value=50, key=f"tp_{t['id']}")
        sl_p = cb.number_input("SL pips", value=25, key=f"sl_{t['id']}")
        tp = entry + (tp_p * pip if is_long else -tp_p * pip)
        sl = entry - (sl_p * pip if is_long else -sl_p * pip)
        st.write(f"Proposed TP = {tp:.5f}   SL = {sl:.5f}")
        if st.button("Set TP/SL", key=f"set_{t['id']}"):
            r1 = put(f"/accounts/{ACC}/trades/{t['id']}", {"takeProfit":{"price":f"{tp:.5f}"}})
            r2 = put(f"/accounts/{ACC}/trades/{t['id']}", {"stopLoss":{"price":f"{sl:.5f}"}})
            st.write("TP:", r1.status_code, r1.text[:200])
            st.write("SL:", r2.status_code, r2.text[:200])
PY
