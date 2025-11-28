import os, time, requests

HOST   = os.environ["OANDA_HOST"]
TOKEN  = os.environ["OANDA_TOKEN"]
ACC    = os.environ["OANDA_ACCOUNT"]
API    = f"{HOST}/v3"
H      = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

PIPS = {"EUR_USD":0.0001,"GBP_USD":0.0001,"USD_JPY":0.01,"XAU_USD":0.1}
TP_PIPS = int(os.getenv("TP_PIPS", "50"))
SL_PIPS = int(os.getenv("SL_PIPS", "25"))
INTERVAL = int(os.getenv("BOT_INTERVAL_SEC", "60"))

def pipsize(inst): return PIPS.get(inst, 0.0001)

def get(path):
    for _ in range(3):
        r = requests.get(f"{API}{path}", headers=H, timeout=20)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429,500,502,503,504):
            time.sleep(1.0); continue
        raise RuntimeError(f"GET {path} -> {r.status_code} {r.text[:200]}")
    raise RuntimeError(f"GET {path} retries exhausted")

def put(path, body):
    r = requests.put(f"{API}{path}", headers=H, json=body, timeout=20)
    return r.status_code, r.text[:200]

while True:
    try:
        trades = get(f"/accounts/{ACC}/trades").get("trades", [])
        trades.sort(key=lambda t: t["openTime"])
        print(f"[bot] open trades: {len(trades)}")

        for t in trades:
            inst = t["instrument"]; entry = float(t["price"])
            is_long = not str(t["currentUnits"]).startswith("-")
            pip = pipsize(inst)
            tp = entry + (TP_PIPS*pip if is_long else -TP_PIPS*pip)
            sl = entry - (SL_PIPS*pip if is_long else -SL_PIPS*pip)

            c1,_ = put(f"/accounts/{ACC}/trades/{t['id']}/orders", {"takeProfit":{"price":f"{tp:.5f}"}})
            c2,_ = put(f"/accounts/{ACC}/trades/{t['id']}/orders", {"stopLoss":{"price":f"{sl:.5f}"}})
            print(f"  {inst}#{t['id']} TP->{c1} SL->{c2}")
    except Exception as e:
        print("[bot] error:", e)
    time.sleep(INTERVAL)
