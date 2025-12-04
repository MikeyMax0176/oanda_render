cat > news_heartbeat.py <<'PY'
import os, time, json, subprocess, pathlib

RUNTIME = pathlib.Path("runtime")
HBFILE  = RUNTIME / "bot_heartbeat.json"
RUNTIME.mkdir(exist_ok=True)

def worker_alive() -> bool:
    try:
        out = subprocess.check_output(
            ["bash","-lc","ps -ef | grep -E 'news_sentiment.py' | grep -v grep || true"],
            text=True, timeout=5
        ).strip()
        return bool(out)
    except Exception:
        return False

def latest_headline() -> str:
    # Try to scrape a headline from your bot logs if present.
    # Adjust the grep pattern if your bot logs a different marker.
    try:
        out = subprocess.check_output(
            ["bash","-lc","tail -n 200 runtime/news.log 2>/dev/null | grep -E 'HEADLINE:' | tail -n 1 || true"],
            text=True, timeout=5
        ).strip()
        if out:
            # Expect lines like: "[trade] HEADLINE: <text>"
            return out.split("HEADLINE:",1)[1].strip()
    except Exception:
        pass
    return ""

def write_heartbeat():
    hb = {
        "last_run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "loop_seconds": 60,
        "worker_alive": worker_alive(),
        "last_headline": latest_headline() or "(unknown)",
        "last_signal": "(sidecar)",
        "last_action": "monitor",
        "notes": "sidecar heartbeat writer"
    }
    HBFILE.write_text(json.dumps(hb, indent=2))

def main():
    while True:
        write_heartbeat()
        time.sleep(60)

if __name__ == "__main__":
    main()
PY
