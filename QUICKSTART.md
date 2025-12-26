## 🎯 Quick Start Guide

Your OANDA trading bot is ready! Here's what you have:

### ✅ What's Working

1. **Trading Bot** ([bot.py](bot.py))
   - Fetches Reuters business news headlines
   - Analyzes sentiment using VADER
   - Places OANDA trades based on sentiment signals
   - Includes risk management (TP/SL, spread checks, cooldown)
   - **Currently in DRY_RUN mode** (won't place real trades)

2. **Dashboard** ([dashboard.py](dashboard.py))
   - Real-time account monitoring
   - Manual trade placement with TP/SL
   - View open trades and modify them
   - Recent transaction history

3. **Deployment Ready** ([render.yaml](render.yaml))
   - Pre-configured for Render.com
   - Two services: Web (dashboard) + Worker (bot)
   - Environment variables ready

### 🚀 Get Started in 3 Steps

#### Step 1: Install & Test Locally

```bash
# Run automated setup
./setup.sh

# Or manually:
pip3 install -r requirements.txt
python3 test_bot.py
```

#### Step 2: Add Your OANDA Credentials

```bash
# Copy template and edit
cp .env.example .env
nano .env

# Add these values:
# OANDA_HOST=https://api-fxpractice.oanda.com
# OANDA_ACCOUNT=your-account-id
# OANDA_TOKEN=your-api-token
```

Get credentials:
- Sign up: https://www.oanda.com/
- API Token: https://www.oanda.com/account/tpa/personal_token

#### Step 3: Test Run

```bash
# Terminal 1: Run the bot (dry-run mode)
python3 bot.py
# Watch for: sentiment analysis, trade signals
# Press Ctrl+C to stop

# Terminal 2: Run the dashboard
streamlit run dashboard.py
# Open http://localhost:8501 in browser
```

### 📊 How It Works

```
1. Bot fetches headlines every 3 min
         ↓
2. VADER analyzes sentiment
         ↓
3. Strong sentiment detected?
         ↓
4. Check spread, cooldown, max trades
         ↓
5. Place market order with TP/SL
         ↓
6. Dashboard shows live status
```

### ⚙️ Configuration

Key settings (edit in `.env` or Render):

```bash
DRY_RUN=true                    # false = live trading
BOT_INSTRUMENT=EUR_USD          # Trading pair
BOT_SENT_THRESHOLD=0.15         # Min sentiment (-1 to 1)
BOT_MIN_SPREAD=0.0002           # Max spread (2 pips)
BOT_MAX_CONCURRENT=3            # Max trades
BOT_COOLDOWN_MIN=30             # Wait between trades
BOT_TP_PIPS=38                  # Take profit
BOT_SL_PIPS=25                  # Stop loss
BOT_RISK_USD=500                # Risk per trade
```

### 🌐 Deploy to Render.com

```bash
# 1. Push to GitHub
git add .
git commit -m "Ready to deploy"
git push

# 2. Render Dashboard
# - New > Blueprint
# - Connect your repo
# - Add OANDA credentials as environment variables
# - Deploy!

# See DEPLOYMENT.md for detailed instructions
```

### 📁 Project Files

```
oanda_render/
├── bot.py                    # Main trading bot ⭐
├── dashboard.py              # Streamlit UI ⭐
├── news_sentiment.py         # Alternative news worker
├── news_heartbeat.py         # Monitoring helper
├── render.yaml              # Render deployment config ⭐
├── Procfile                  # Process definitions
├── start.sh                  # Dashboard startup script
├── setup.sh                  # Quick setup script
├── test_bot.py              # Validation tests
├── requirements.txt          # Python dependencies
├── .env.example             # Environment template
├── README.md                 # Full documentation
├── DEPLOYMENT.md             # Deployment checklist
├── TRADING_FIX.md           # Known issues & fixes
└── runtime/                  # Bot runtime data
```

### 🛡️ Safety Features

- ✅ **Dry-run mode** - Test without risking money
- ✅ **Spread check** - Won't trade if spread too wide
- ✅ **Cooldown period** - Prevents overtrading
- ✅ **Max concurrent trades** - Limits exposure
- ✅ **TP/SL on every trade** - Automatic risk management
- ✅ **Practice account support** - Test with fake money

### ⚠️ Important Warnings

1. **Start with PRACTICE account** (`api-fxpractice.oanda.com`)
2. **Test in DRY_RUN=true mode first** (at least 24 hours)
3. **Never risk more than you can afford to lose**
4. **Monitor regularly** - don't set and forget
5. **Sentiment analysis is not foolproof** - markets are unpredictable
6. **Past performance ≠ future results**

### 🐛 Troubleshooting

**Bot not trading?**
- Check `DRY_RUN=false` (if you want live trades)
- Verify OANDA credentials
- Look for log messages: "spread too wide", "sentiment below threshold"

**Dashboard shows red light?**
- Add OANDA environment variables
- Verify credentials are correct
- Check API endpoint URL

**API errors?**
- 401: Wrong API token
- 403: Wrong account ID
- 404: Wrong host URL

### 📚 Resources

- **Full Documentation**: [README.md](README.md)
- **Deployment Guide**: [DEPLOYMENT.md](DEPLOYMENT.md)
- **Known Issues**: [TRADING_FIX.md](TRADING_FIX.md)
- **OANDA API Docs**: https://developer.oanda.com/

### 🆘 Need Help?

1. Run validation: `python3 test_bot.py`
2. Check logs in `runtime/` directory
3. Review error messages in terminal
4. Consult DEPLOYMENT.md for detailed troubleshooting

---

**Ready to trade?** Follow the 3 steps above to get started! 🚀
