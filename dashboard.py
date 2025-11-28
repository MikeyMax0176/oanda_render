import os, requests, streamlit as st

HOST  = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC   = os.environ["OANDA_ACCOUNT"]
API   = f"{HOST}/v3"
H     = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def get(path, params=None):
    r = requests.get(f"{API}{path}", headers=H, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def post(path, body):
    return requests.post(f"{API}{path}", headers=H, json=body, timeout=20)

st.set_page_config(page_title="OANDA Monitor", layout="wide")
st.title("OANDA Monitor (Render)")

# Account panel
try:
    acc = get(f"/accounts/{ACC}/summary")["account"]
    c1, c2, c3 = st.columns(3)
    c1.metric("Balance",        acc.get("balance","0"))
    c2.metric("Unrealized P/L", acc.get("unrealizedPL","0"))
    c3.metric("NAV",            acc.get("NAV", acc.get("balance","0")))
except Exception as e:
    st.error(f"Account fetch failed: {e}")

st.divider()
st.subheader("Place Trade")

with st.form("place_trade"):
    c1, c2, c3, c4 = st.columns([2,2,2,2])
    instrument = c1.selectbox("Instrument", ["EUR_USD","GBP_USD","USD_JPY","XAU_USD"], index=0)
    side       = c2.selectbox("Side", ["BUY","SELL"], index=0)
    units_abs  = c3.number_input("Units (abs)", min_value=1, step=100, value=5000)
    tp_pips    = c4.number_input("TP pips", min_value=1, value=50)
    sl_pips    = st.number_input("SL pips", min_value=1, value=25, key="sl_pips_form")
    submitted  = st.form_submit_button("Submit Order")

if submitted:
    try:
        pip_map = {"EUR_USD":0.0001,"GBP_USD":0.0001,"USD_JPY":0.01,"XAU_USD":0.1}
        pip = pip_map.get(instrument, 0.0001)

        q = get("/pricing", params={"instruments": instrument, "accounts": ACC})
        px = q["prices"][0]
        bid = float(px["bids"][0]["price"])
        ask = float(px["asks"][0]["price"])
        is_buy = (side == "BUY")
        entry  = ask if is_buy else bid

        tp = entry + (tp_pips * pip if is_buy else -tp_pips * pip)
        sl = entry - (sl_pips * pip if is_buy else -sl_pips * pip)
        units =  units_abs if is_buy else -units_abs

        body = {"order":{
            "type":"MARKET","instrument":instrument,"units":str(units),
            "timeInForce":"FOK","positionFill":"DEFAULT",
            "takeProfitOnFill":{"price":f"{tp:.5f}","timeInForce":"GTC"},
            "stopLossOnFill": {"price":f"{sl:.5f}","timeInForce":"GTC"}
        }}
        r = post(f"/accounts/{ACC}/orders", body)
        st.success(f"Order status {r.status_code}")
        st.code(r.text[:1200], language="json")
    except Exception as e:
        st.error(f"Order failed: {e}")

st.divider()
st.subheader("Open Trades")

try:
    trades = get(f"/accounts/{ACC}/trades").get("trades", [])
    if not trades:
        st.info("No open trades.")
    else:
        pip_map = {"EUR_USD":0.0001,"GBP_USD":0.0001,"USD_JPY":0.01,"XAU_USD":0.1}
        for t in sorted(trades, key=lambda x: x["openTime"]):
            inst = t["instrument"]
            units = t["currentUnits"]
            entry = float(t["price"])
            is_long = not str(units).startswith("-")
            pip = pip_map.get(inst, 0.0001)

            with st.expander(f"{inst}  #{t['id']}  units={units}  @ {entry}"):
                tp_p = st.number_input(f"TP pips for {t['id']}", value=50, key=f"tp_{t['id']}")
                sl_p = st.number_input(f"SL pips for {t['id']}", value=25, key=f"sl_{t['id']}")
                tp   = entry + (tp_p * pip if is_long else -tp_p * pip)
                sl   = entry - (sl_p * pip if is_long else -sl_p * pip)
                st.write(f"Proposed TP={tp:.5f}  SL={sl:.5f}")

                if st.button(f"Set TP/SL for {t['id']}", key=f"set_{t['id']}"):
                    r1 = requests.put(f"{API}/accounts/{ACC}/trades/{t['id']}/orders",
                        headers=H, json={"takeProfit":{"price":f"{tp:.5f}"}}, timeout=20)
                    r2 = requests.put(f"{API}/accounts/{ACC}/trades/{t['id']}/orders",
                        headers=H, json={"stopLoss": {"price":f"{sl:.5f}"}}, timeout=20)
                    st.write("TP:", r1.status_code, r1.text[:300])
                    st.write("SL:", r2.status_code, r2.text[:300])
except Exception as e:
    st.error(f"Trades fetch failed: {e}")
