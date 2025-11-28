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
