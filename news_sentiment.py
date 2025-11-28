import os, time, json, requests
from datetime import datetime, timedelta

HOST = os.environ["OANDA_HOST"]
TOKEN = os.environ["OANDA_TOKEN"]
ACC = os.environ["OANDA_ACCOUNT"]
API = f"{HOST}/v3"
H  = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY","")   # get one at https://newsapi.org
INSTRUMENTS = [s.strip() for s in os.getenv("INSTRUMENTS","EUR_USD,GBP_USD").split(",") if s.strip()]
INTERVAL_SEC = int(os.getenv("NEWS_INTERVAL_SEC","180"))  # fetch cadence
COOLDOWN_MIN = int(os.getenv("NEWS_COOLDOWN_MIN","30"))   # per instrument trade cooldown
UNITS = int(os.getenv("NEWS_UNITS","5000"))               # +/- via side
TP_PIPS = int(os.getenv("TP_PIPS","50"))
SL_PIPS = int(os.getenv("SL_PIPS","25"))

PIPS = {"EUR_USD":0.0001,"GBP_USD":0.0001,"USD_JPY":0.01,"XAU_USD":0.1}
# crude currency → query map for headlines
QMAP = {
    "EUR_USD": ["euro", "ecb", "europe inflation", "eurusd"],
    "GBP_USD": ["pound", "bank of england", "uk inflation", "gbpusd"],
    "USD_JPY": ["boj", "japan yen", "usdjpy"],
    "XAU_USD": ["gold price", "xauusd"],
}

STATE_FN = "/tmp/news_state.json"  # dedupe + cooldown memory (ephemeral is fine)
def load_state():
    try: return json.load(open(STATE_FN))
    except: return {"seen": {}, "last_trade_at": {}}
def save_state(s): open(STATE_FN,"w").write(json.dumps(s))

def sentiment_vader(text:str)->float:
    # minimal, dependency-free proxy: super-simple lexicon weights.
    # (Swap to nltk.vader later if you prefer.)
    pos_words = ["surge","beat","optimism","growth","cooling inflation","hawkish","strong"]
    neg_words = ["plunge","miss","fear","recession","hot inflation","dovish","weak"]
    t = text.lower()
    score = sum(w in t for w in pos_words) - sum(w in t for w in neg_words)
    return max(-1.0, min(1.0, float(score)))

def get_price(instrument):
    r=requests.get(f"{API}/accounts/{ACC}/pricing?instruments={instrument}", headers=H, timeout=15)
    r.raise_for_status()
    p = r.json()["prices"][0]
    return float(p["bids"][0]["price"]), float(p["asks"][0]["price"])

def place_order(instrument, side_buy:bool, tp_pips:int, sl_pips:int, units_abs:int):
    bid, ask = get_price(instrument)
    pip = PIPS.get(instrument,0.0001)
    entry = ask if side_buy else bid
    tp = entry + (tp_pips*pip if side_buy else -tp_pips*pip)
    sl = entry - (sl_pips*pip if side_buy else -sl_pips*pip)
    units = units_abs if side_buy else -units_abs
    body = {"order":{
        "type":"MARKET","instrument":instrument,"units":str(units),
        "timeInForce":"FOK","positionFill":"DEFAULT",
        "takeProfitOnFill":{"price":f"{tp:.5f}"},
        "stopLossOnFill":{"price":f"{sl:.5f}"},
    }}
    resp = requests.post(f"{API}/accounts/{ACC}/orders", headers=H, json=body, timeout=20)
    print(f"[news] order {instrument} {'BUY' if side_buy else 'SELL'} ->", resp.status_code, resp.text[:200])

def fetch_headlines(q:str):
    if not NEWSAPI_KEY:
        return []
    r = requests.get(
        "https://newsapi.org/v2/everything",
        params={"q": q, "language":"en", "pageSize": 10, "sortBy":"publishedAt"},
        headers={"X-Api-Key": NEWSAPI_KEY}, timeout=20
    )
    if r.status_code != 200:
        print("[news] api error", r.status_code, r.text[:200]); return []
    arts = r.json().get("articles", [])
    # canonical id to dedupe: url + publishedAt
    return [{
        "id": (a.get("url","") + "|" + a.get("publishedAt","")),
        "title": a.get("title",""),
        "desc": a.get("description",""),
        "url": a.get("url",""),
        "time": a.get("publishedAt","")
    } for a in arts]

def decide_trade(inst, score:float):
    # thresholds – tune as desired
    if score >= 1.0:  return "BUY"
    if score <= -1.0: return "SELL"
    return "HOLD"

def main():
    state = load_state()
    while True:
        try:
            now = datetime.utcnow()
            for inst in INSTRUMENTS:
                # cooldown check
                last = state["last_trade_at"].get(inst)
                if last and now - datetime.fromisoformat(last) < timedelta(minutes=COOLDOWN_MIN):
                    continue

                # gather & score multiple queries → take max magnitude
                qs = QMAP.get(inst, [inst.lower()])
                best = None
                for q in qs:
                    for art in fetch_headlines(q):
                        if art["id"] in state["seen"]:
                            continue
                        state["seen"][art["id"]] = True
                        text = f"{art['title']} {art['desc']}"
                        s = sentiment_vader(text)
                        if best is None or abs(s) > abs(best[0]):
                            best = (s, art)

                if best:
                    score, art = best
                    action = decide_trade(inst, score)
                    print(f"[news] {inst} score={score:+.2f} -> {action} | {art['title'][:120]}")
                    if action in ("BUY","SELL"):
                        place_order(inst, action=="BUY", TP_PIPS, SL_PIPS, UNITS)
                        state["last_trade_at"][inst] = now.isoformat()

            save_state(state)
        except Exception as e:
            print("[news] loop error:", e)

        time.sleep(INTERVAL_SEC)

if __name__ == "__main__":
    main()
