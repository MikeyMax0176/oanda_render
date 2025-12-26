# OANDA News Trading Bot

An automated trading bot that analyzes financial news headlines and executes trades on OANDA based on sentiment analysis.

## Features

- 📰 **News Monitoring**: Fetches headlines from Reuters, BBC, and NYT RSS feeds
- 🧠 **Sentiment Analysis**: Uses VADER sentiment analysis to evaluate market sentiment
- 💹 **Automated Trading**: Places market orders with take-profit and stop-loss
- 📊 **Dashboard**: Real-time Streamlit dashboard for monitoring and manual trading
- ☁️ **Cloud Deployment**: Ready to deploy on Render.com

## Architecture

```
┌─────────────────┐
│  RSS Feeds      │
│  (Reuters, etc) │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  bot.py         │  ← Main trading logic
│  - Sentiment    │
│  - Risk Mgmt    │
│  - Order Place  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  OANDA API      │
│  (Live/Practice)│
└─────────────────┘

         ┌─────────────────┐
         │  dashboard.py   │  ← Streamlit UI
         │  - Monitor      │
         │  - Manual Trade │
         └─────────────────┘
```

## Quick Start

### 1. Get OANDA API Credentials

1. Sign up at [OANDA](https://www.oanda.com/)
2. Get your API token from [Account Settings → Manage API Access](https://www.oanda.com/account/tpa/personal_token)
3. Note your Account ID (found in your account dashboard)

### 2. Local Setup

```bash
# Clone the repository
git clone <your-repo-url>
cd oanda_render

# Install dependencies
pip install -r requirements.txt

# Create environment file
cp .env.example .env

# Edit .env with your credentials
nano .env
```

### 3. Test Locally

```bash
# Test in dry-run mode (no real trades)
DRY_RUN=true python bot.py

# Run the dashboard
streamlit run dashboard.py --server.port 8501
```

### 4. Deploy to Render

1. Push your code to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com/)
3. Click "New +" → "Blueprint"
4. Connect your GitHub repository
5. Render will detect `render.yaml` and set up:
   - **Web Service**: Streamlit dashboard
   - **Worker**: Trading bot

6. Add environment variables in Render:
   - `OANDA_HOST`
   - `OANDA_ACCOUNT`
   - `OANDA_TOKEN`
   - Set `DRY_RUN=false` when ready for live trading

## Configuration

### Trading Parameters

Edit these in `.env` or Render environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `true` | Set to `false` for live trading |
| `BOT_INSTRUMENT` | `EUR_USD` | Trading pair |
| `BOT_TP_PIPS` | `38` | Take profit distance in pips |
| `BOT_SL_PIPS` | `25` | Stop loss distance in pips |
| `BOT_RISK_USD` | `500` | Risk per trade in USD |
| `BOT_TRADE_INTERVAL_MIN` | `3` | Signal check frequency (minutes) |
| `BOT_COOLDOWN_MIN` | `30` | Wait time between trades (minutes) |
| `BOT_MAX_CONCURRENT` | `3` | Max simultaneous trades |
| `BOT_MIN_SPREAD` | `0.0002` | Max spread (2 pips) |
| `BOT_SENT_THRESHOLD` | `0.15` | Min sentiment score to trade |
| `BOT_MAX_DAILY_LOSS` | `1500` | Daily loss limit in USD |

### Risk Management

The bot includes multiple safety features:

1. **Dry-Run Mode**: Test without placing real orders
2. **Spread Check**: Won't trade if spread is too wide
3. **Cooldown Period**: Prevents overtrading
4. **Max Concurrent Trades**: Limits exposure
5. **Take-Profit & Stop-Loss**: Every trade has TP/SL
6. **Daily Loss Limit**: Stops trading after threshold

## Files

- `bot.py` - Main trading bot with sentiment analysis
- `news_sentiment.py` - Standalone news monitoring worker (alternative)
- `news_heartbeat.py` - Monitoring helper for dashboard
- `dashboard.py` - Streamlit web interface
- `render.yaml` - Render deployment configuration
- `Procfile` - Process definitions for Render
- `start.sh` - Dashboard startup script
- `requirements.txt` - Python dependencies

## How It Works

1. **News Fetching**: Bot polls RSS feeds every 3 minutes (configurable)
2. **Sentiment Analysis**: VADER analyzes each headline for sentiment
3. **Signal Generation**: If sentiment exceeds threshold, generates trade signal
4. **Risk Checks**: Validates spread, cooldown, max trades
5. **Order Execution**: Places market order with TP/SL if all checks pass
6. **Monitoring**: Dashboard displays live status and trade history

## Dashboard Features

- 🟢 **Status Light**: Shows if bot is running and API is healthy
- 📊 **Account Overview**: Balance, P/L, NAV
- 📈 **Open Trades**: View and modify TP/SL on active trades
- 🎯 **Manual Trading**: Place orders with custom TP/SL
- 📜 **Recent Activity**: Last 14 days of fills and closes

## Troubleshooting

### Bot Not Trading

1. Check `DRY_RUN` is set to `false`
2. Verify OANDA credentials are correct
3. Check sentiment threshold - news might not be strong enough
4. Look for cooldown or max concurrent trade limits
5. Check spread - might be too wide

### Dashboard Not Showing Data

1. Verify OANDA credentials in environment
2. Check bot is creating files in `runtime/` directory
3. Ensure `RUNTIME_DIR` is set correctly

### API Errors

- **401 Unauthorized**: Check your API token
- **403 Forbidden**: Verify account ID is correct
- **404 Not Found**: Check OANDA_HOST URL
- **429 Rate Limited**: Bot has retry logic, but may need to slow down

## Safety Warnings

⚠️ **Important**: 

- Start with OANDA Practice account (api-fxpractice.oanda.com)
- Test thoroughly in DRY_RUN mode before live trading
- Never risk more than you can afford to lose
- Monitor the bot regularly
- Sentiment analysis is not foolproof
- Past performance doesn't guarantee future results

## Support

For issues:
1. Check the logs in Render dashboard
2. Review `TRADING_FIX.md` for known issues
3. Test locally with verbose logging

## License

MIT License - Use at your own risk. This is educational software.
