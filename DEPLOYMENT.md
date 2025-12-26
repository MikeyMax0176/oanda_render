# 🚀 OANDA Trading Bot - Deployment Checklist

## Pre-Deployment Setup

### 1. Get OANDA Credentials ✅

- [ ] Sign up at [OANDA](https://www.oanda.com/)
- [ ] Create a **Practice Account** (recommended for testing)
- [ ] Generate API token: [Account → Manage API Access](https://www.oanda.com/account/tpa/personal_token)
- [ ] Note down:
  - Account ID: `____________`
  - API Token: `____________`
  - Host URL: `https://api-fxpractice.oanda.com` (practice) or `https://api-fxtrade.oanda.com` (live)

### 2. Test Locally ✅

```bash
# Run validation tests
python3 test_bot.py

# Create environment file
cp .env.example .env
nano .env  # Add your OANDA credentials

# Test bot in dry-run mode
DRY_RUN=true python3 bot.py
# Press Ctrl+C after 1-2 minutes to stop

# Test dashboard (in another terminal)
streamlit run dashboard.py --server.port 8501
# Open http://localhost:8501 in browser
```

### 3. GitHub Setup ✅

```bash
# Initialize git (if not already)
git init
git add .
git commit -m "Initial commit: OANDA trading bot"

# Push to GitHub
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git branch -M main
git push -u origin main
```

## Render.com Deployment

### Step 1: Connect Repository

1. Go to [Render Dashboard](https://dashboard.render.com/)
2. Click **"New +"** → **"Blueprint"**
3. Connect your GitHub account (if not connected)
4. Select your repository: `MikeyMax0176/oanda_render`
5. Click **"Connect"**

### Step 2: Configure Environment Variables

Render will detect `render.yaml` and create:
- **Web Service**: `oanda-dashboard` (Streamlit UI)
- **Worker Service**: `oanda-news-worker` (Trading bot)

Add these environment variables to **BOTH services**:

#### Required Variables (Add to Both Services)

| Variable | Value | Description |
|----------|-------|-------------|
| `OANDA_HOST` | `https://api-fxpractice.oanda.com` | Practice account URL |
| `OANDA_ACCOUNT` | `YOUR_ACCOUNT_ID` | From OANDA dashboard |
| `OANDA_TOKEN` | `YOUR_API_TOKEN` | From OANDA API settings |
| `OPENAI_API_KEY` | *(optional)* | Only if using OpenAI features |

#### Worker-Specific Variables (Already in render.yaml)

These are pre-configured in render.yaml but can be overridden:

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | **IMPORTANT**: Set to `false` only when ready for live trading |
| `BOT_SENT_THRESHOLD` | `0.15` | Minimum sentiment (-1 to 1) to trigger trade |
| `BOT_MIN_SPREAD` | `0.0002` | Max spread (0.0002 = 2 pips) |
| `BOT_MAX_CONCURRENT` | `3` | Max simultaneous trades |
| `BOT_COOLDOWN_MIN` | `30` | Minutes to wait between trades |
| `BOT_TRADE_INTERVAL_MIN` | `3` | How often to check for signals |

### Step 3: Deploy

1. Click **"Apply"** or **"Create Blueprint"**
2. Wait for deployment (5-10 minutes)
3. Monitor logs in Render dashboard

### Step 4: Verify Deployment

#### Check Worker Logs

Look for these log messages:

```
[bot] starting… DRY_RUN=ENABLED (no orders will be placed)
[bot] pricing error: ...  # Expected initially (no credentials yet)
[bot] news error: ...     # Expected initially
```

Add environment variables, then restart the worker:

```
[bot] max concurrent trades reached: 0 ≥ 3
[bot] spread too wide: 0.00025 > 0.00020
[bot] sentiment below threshold: +0.12 (th=+0.15)
```

Good signs:
- No Python errors
- Bot loops every 3 minutes
- Attempts to fetch news

#### Check Dashboard

1. Open the web service URL (e.g., `https://oanda-dashboard.onrender.com`)
2. Should see:
   - 🔴 Red status light initially (expected - need OANDA credentials)
   - After adding credentials and restarting: 🟢 Green light
   - Account info (balance, NAV)
   - "No open trades" message

## Go-Live Checklist

### Before Enabling Live Trading

- [ ] **Tested in dry-run mode for at least 24 hours**
- [ ] Verified sentiment analysis is working correctly
- [ ] Confirmed spread checks are functioning
- [ ] Reviewed all environment variables
- [ ] Set appropriate risk limits (`BOT_RISK_USD`, `BOT_MAX_DAILY_LOSS`)
- [ ] Understood the trading strategy completely
- [ ] Have emergency stop plan (know how to pause worker)
- [ ] Started with **PRACTICE ACCOUNT** first

### Enable Live Trading

**PRACTICE ACCOUNT FIRST:**

1. In Render Worker environment variables:
   - Set `DRY_RUN=false`
   - Verify `OANDA_HOST=https://api-fxpractice.oanda.com`
2. Restart the worker service
3. Monitor for 1-2 weeks

**LIVE ACCOUNT (Only after successful practice period):**

1. Create live OANDA account
2. Get NEW API credentials for live account
3. Update Render environment variables:
   - `OANDA_HOST=https://api-fxtrade.oanda.com`
   - `OANDA_ACCOUNT=<live-account-id>`
   - `OANDA_TOKEN=<live-api-token>`
4. Consider more conservative settings:
   - `BOT_SENT_THRESHOLD=0.25` (higher = fewer trades)
   - `BOT_COOLDOWN_MIN=60` (longer cooldown)
   - `BOT_MAX_CONCURRENT=2` (fewer trades)
   - `BOT_RISK_USD=100` (lower risk per trade)
5. Monitor constantly for first 48 hours

## Monitoring & Maintenance

### Daily Checks

- [ ] View dashboard for account status
- [ ] Check worker logs for errors
- [ ] Review recent trades in dashboard
- [ ] Monitor P/L and balance

### Weekly Tasks

- [ ] Review sentiment threshold effectiveness
- [ ] Analyze win/loss ratio
- [ ] Adjust parameters if needed
- [ ] Check for news feed issues

### Emergency Stop

If you need to stop trading immediately:

**Option 1: Pause Worker**
1. Go to Render Dashboard → Worker Service
2. Click "Suspend" or "Delete"

**Option 2: Enable Dry-Run**
1. Set `DRY_RUN=true` in environment variables
2. Restart worker

**Option 3: Close All Positions**
1. Open dashboard
2. Manually close all open trades
3. Then use Option 1 or 2

## Troubleshooting

### Worker Not Starting

```
Check Render logs for:
- Python import errors → pip install -r requirements.txt issue
- Missing environment variables → Add required vars
- Permission errors → Check runtime directory creation
```

### Bot Not Trading

```
- Verify DRY_RUN=false (if you want live trades)
- Check OANDA credentials are correct
- Look for "spread too wide" or "sentiment below threshold" messages
- Verify news feeds are accessible
- Check cooldown period hasn't been triggered
```

### API Errors

```
401 Unauthorized → Check OANDA_TOKEN
403 Forbidden → Check OANDA_ACCOUNT ID
404 Not Found → Check OANDA_HOST URL
429 Rate Limited → Bot has built-in retry logic
```

### Dashboard Shows Red Light

```
- Verify all 3 OANDA environment variables are set
- Check OANDA credentials are valid
- Restart web service after adding variables
- Check web service logs for specific errors
```

## Support & Resources

- **OANDA API Docs**: https://developer.oanda.com/rest-live-v20/introduction/
- **Render Docs**: https://render.com/docs
- **VADER Sentiment**: https://github.com/cjhutto/vaderSentiment
- **Project README**: See README.md for architecture details

## Safety Reminders

⚠️ **CRITICAL SAFETY POINTS**

1. **Always start with PRACTICE account**
2. **Test in DRY_RUN mode first**
3. **Never risk more than you can afford to lose**
4. **Monitor the bot regularly** - don't set and forget
5. **Sentiment analysis is probabilistic** - not guaranteed
6. **Markets can be unpredictable** - use stop losses
7. **Have an emergency stop plan**
8. **Start small and scale up gradually**

---

## Deployment Status Tracking

- [ ] Local testing completed
- [ ] GitHub repository created and pushed
- [ ] Render blueprint connected
- [ ] Environment variables added
- [ ] Services deployed successfully
- [ ] Worker logs show no critical errors
- [ ] Dashboard accessible and functional
- [ ] 24h dry-run testing completed
- [ ] Ready for practice account testing
- [ ] (Optional) Live account enabled

**Current Status**: _______________

**Date Deployed**: _______________

**Notes**: _______________
