import os, requests, streamlit as st

HOST=os.environ["OANDA_HOST"]
TOKEN=os.environ["OANDA_TOKEN"]
ACC=os.environ["OANDA_ACCOUNT"]
API=f"{HOST}/v3"
H={"Authorization":f"Bearer {TOKEN}","Content-Type":"application/json"}

def get(path):
    r=requests.get(f"{API}{path}",headers=H,timeout=20)
    r.raise_for_status(); return r.json()

def put(path, body):
    return requests.put(f"{API}{path}",headers=H,json=body,timeout=20)

st.set_page_config(page_title="OANDA Monitor", layout="wide")
st.title("OANDA Monitor (Render)")

acc=get(f"/accounts/{ACC}/summary")["account"]
c1,c2,c3=st.columns(3)
c1.metric("Balance", acc["balance"])
c2.metric("Unrealized P/L", acc.get("unrealizedPL","0"))
c3.metric("NAV", acc.get("NAV", acc["balance"]))
st.subheader("Place Trade")

with st.form("place_trade"):
    c1,c2,c3,c4 = st.columns([2,2,2,2])
    instrument = c1.selectbox("Instrument", ["EUR_USD","GBP_USD","USD_JPY","XAU_USD"], index=0)
    side       = c2.selectbox("Side", ["BUY","SELL"], index=0)
    units_abs  = c3.number_input("Units (abs)", min_value=1, step=100, value=5000)
    riskcol1, riskcol2 = st.columns(2)
    tp_pips = riskcol1.number_input("TP pips", min_value=1, value=50)
    sl_pips = riskcol2.number_input("SL pips", min_value=1, value=25)
    submitted = st.form_submit_button("Submit Order")

if submitted:
    try:
        pip_map={"EUR_USD":0.0001,"GBP_USD":0.0001,"USD_JPY":0.01,"XAU_USD":0.1}
        pip = pip_map.get(instrument,0.0001)

        # fetch quote for TP/SL calc
        pr = requests.get(f"{API}/pricing?instruments={instrument}", headers=H, timeout=15).json()
        bid = float(pr["prices"][0]["bids"][0]["price"])
        ask = float(pr["prices"][0]["asks"][0]["price"])
        is_buy = (side=="BUY")
        entry = ask if is_buy else bid

        tp = entry + (tp_pips*pip if is_buy else -tp_pips*pip)
        sl = entry - (sl_pips*pip if is_buy else -sl_pips*pip)

        units = units_abs if is_buy else -units_abs
        body = {
          "order": {
            "type":"MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce":"FOK",
            "positionFill":"DEFAULT",
            "takeProfitOnFill":{"price": f"{tp:.5f}"},
            "stopLossOnFill": {"price": f"{sl:.5f}"}
          }
        }
        r = requests.post(f"{API}/accounts/{ACC}/orders", headers=H, json=body, timeout=20)
        st.success(f"Order status {r.status_code}")
        st.code(r.text, language="json")
    except Exception as e:
        st.error(f"Failed: {e}")

st.subheader("Open Trades")
trades=get(f"/accounts/{ACC}/trades")["trades"]
pip_map={"EUR_USD":0.0001,"GBP_USD":0.0001,"USD_JPY":0.01,"XAU_USD":0.1}

for t in sorted(trades, key=lambda x: x["openTime"]):
    inst=t["instrument"]; units=t["currentUnits"]; entry=float(t["price"])
    is_long = not str(units).startswith("-")
    pip = pip_map.get(inst,0.0001)

    with st.expander(f"{inst} #{t['id']} units={units} @ {entry}"):
        colA,colB,_ = st.columns(3)
        tp_pips = colA.number_input("TP pips", value=50, key=f"tp_{t['id']}")
        sl_pips = colB.number_input("SL pips", value=25, key=f"sl_{t['id']}")
        tp = entry + (tp_pips*pip if is_long else -tp_pips*pip)
        sl = entry - (sl_pips*pip if is_long else -sl_pips*pip)
        st.write(f"Proposed TP={tp:.5f} SL={sl:.5f}")

        if st.button("Set TP/SL", key=f"set_{t['id']}"):
            r1=put(f"/accounts/{ACC}/trades/{t['id']}", {"takeProfit":{"price":f"{tp:.5f}"}})
            r2=put(f"/accounts/{ACC}/trades/{t['id']}", {"stopLoss":{"price":f"{sl:.5f}"}})
            st.write("TP:", r1.status_code, r1.text[:200])
            st.write("SL:", r2.status_code, r2.text[:200])
