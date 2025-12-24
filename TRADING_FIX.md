# Trading Bot Fix - Complete Implementation Guide

## Problem Summary

The Render worker was only running `news_sentiment.py`, which:
- ❌ Only analyzes news sentiment
- ❌ Does NOT place any OANDA orders
- ❌ Does NOT contain trading logic

The actual trading bot code exists in `bot.py` but was never being executed.

## Root Cause

**Procfile** and **render.yaml** were configured to run:
```
python -u news_sentiment.py
```

But the trading logic is in:
```
python -u bot.py
```

---

## Solution Implemented

### ✅ Files Changed

1. **Procfile** - Changed worker to run `bot.py`
2. **render.yaml** - Changed worker startCommand to run `bot.py`
3. **bot.py** - Added `DRY_RUN` mode support

### 📝 Changes Summary

#### 1. Procfile
```diff
  web: bash start.sh
- worker: python -u news_sentiment.py
+ worker: python -u bot.py
```

#### 2. render.yaml
```diff
  startCommand: |
-   python -u news_sentiment.py
+   python -u bot.py
  envVars:
    - key: OANDA_HOST
      sync: false
    - key: OANDA_ACCOUNT
      sync: false
    - key: OANDA_TOKEN
      sync: false
    - key: OPENAI_API_KEY
      sync: false
+   - key: DRY_RUN
+     value: "true"
+   - key: BOT_SENT_THRESHOLD
+     value: "0.15"
+   - key: BOT_MIN_SPREAD
+     value: "0.0002"
+   - key: BOT_MAX_CONCURRENT
+     value: "3"
+   - key: BOT_COOLDOWN_MIN
+     value: "30"
+   - key: BOT_TRADE_INTERVAL_MIN
+     value: "3"
```

#### 3. bot.py - Added DRY_RUN support
- Added `DRY_RUN` environment variable parsing
- Modified trade execution to check `DRY_RUN` flag
- Logs "would trade" decisions when in dry-run mode
- Only places actual orders when `DRY_RUN=false`

---

## Environment Variables Checklist

### Required (Already Set)
- ✅ `OANDA_HOST` - OANDA API endpoint
- ✅ `OANDA_ACCOUNT` - Your OANDA account ID
- ✅ `OANDA_TOKEN` - Your OANDA API token
- ✅ `OPENAI_API_KEY` - OpenAI API key (for dashboard)

### New Trading Controls (Configured in render.yaml)
- ✅ `DRY_RUN` = "true" - **Set to "false" to enable live trading**
- ✅ `BOT_SENT_THRESHOLD` = "0.15" - Minimum sentiment score to trade
- ✅ `BOT_MIN_SPREAD` = "0.0002" - Maximum spread (20 pips)
- ✅ `BOT_MAX_CONCURRENT` = "3" - Max simultaneous trades
- ✅ `BOT_COOLDOWN_MIN` = "30" - Minutes between trades
- ✅ `BOT_TRADE_INTERVAL_MIN` = "3" - How often to check for trade signals

### Optional (Already in bot.py with defaults)
- `BOT_INSTRUMENT` = "EUR_USD" - Trading pair
- `BOT_TP_PIPS` = "38" - Take profit in pips
- `BOT_SL_PIPS` = "25" - Stop loss in pips
- `BOT_RISK_USD` = "500" - Risk per trade in USD
- `BOT_MAX_DAILY_LOSS` = "1500" - Daily loss limit

---

## Deployment Steps

### Option A: DRY-RUN Mode (Safe Testing - Default)
The configuration is already set to `DRY_RUN=true`. Deploy as-is to test:

1. Commit and push changes:
   ```bash
   git add Procfile render.yaml bot.py
   git commit -m "Fix: Enable bot.py trading with DRY_RUN mode"
   git push origin main
   ```

2. Render will auto-deploy the worker

3. Check logs in Render dashboard for:
   ```
   [bot] DRY-RUN: would place BUY EUR_USD units=50000 @ 1.09234 ...
   [bot] DRY-RUN mode enabled - no actual order placed
   ```

### Option B: LIVE Trading (After Testing)
Once you've verified dry-run works:

1. In Render dashboard, go to `oanda-news-worker` service

2. Edit environment variable:
   ```
   DRY_RUN = false
   ```

3. Save - Render will restart the worker

4. Monitor logs for actual order placement:
   ```
   [bot] placing BUY EUR_USD units=50000 @ 1.09234 ...
   [bot] order OK 201
   ```

---

## What the Bot Now Does

1. **Every 3 minutes** (configurable via `BOT_TRADE_INTERVAL_MIN`):
   - Fetches latest news headlines from Reuters RSS
   - Analyzes sentiment using VADER
   - Checks current OANDA pricing and spread

2. **Trading Decision Logic**:
   - If sentiment > 0.15 → considers BUY
   - If sentiment < -0.15 → considers SELL
   - Only trades if:
     - Spread ≤ 0.0002 (20 pips)
     - < 3 concurrent trades open
     - 30+ minutes since last trade (cooldown)

3. **Order Placement**:
   - **DRY_RUN=true**: Logs decision, no actual order
   - **DRY_RUN=false**: Places market order with TP/SL

4. **Risk Management**:
   - Automatically calculates position size based on $500 risk
   - Sets take profit at +38 pips
   - Sets stop loss at -25 pips

---

## Verification

### Check if bot is running:
```bash
# In Render logs, you should see:
[bot] starting… DRY_RUN=ENABLED (no orders will be placed)
[bot] max concurrent trades reached: 0 ≥ 3
[bot] cooldown active: 0.0 < 30 min
[bot] DRY-RUN: would place BUY EUR_USD ...
```

### Check trading activity:
- Dashboard will show last trade headline and sentiment
- `runtime/bot_heartbeat.json` updated every loop
- `runtime/news_last_trade.json` written when trade signal occurs

---

## Safety Notes

⚠️ **IMPORTANT**: The bot is configured with `DRY_RUN=true` by default. This means:
- It will analyze sentiment and log trade decisions
- **NO actual orders will be placed on OANDA**
- Safe for testing and verification

✅ **To enable live trading**:
1. Verify dry-run logs look correct
2. Set `DRY_RUN=false` in Render environment
3. Monitor closely for first few trades
4. Adjust `BOT_SENT_THRESHOLD`, `BOT_MIN_SPREAD`, etc. as needed

---

## Quick Reference: Key Bot Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| DRY_RUN | true | Safe mode - logs only, no orders |
| BOT_SENT_THRESHOLD | 0.15 | Min sentiment to trade |
| BOT_MIN_SPREAD | 0.0002 | Max spread (20 pips) |
| BOT_MAX_CONCURRENT | 3 | Max open positions |
| BOT_COOLDOWN_MIN | 30 | Minutes between trades |
| BOT_TRADE_INTERVAL_MIN | 3 | Check frequency (minutes) |
| BOT_RISK_USD | 500 | Risk per trade ($) |
| BOT_TP_PIPS | 38 | Take profit (pips) |
| BOT_SL_PIPS | 25 | Stop loss (pips) |
