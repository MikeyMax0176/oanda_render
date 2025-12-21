# OANDA News Trading Bot - V2 Architecture

## Overview

Sentiment-driven trading bot that monitors Reuters RSS feed and executes OANDA trades based on news sentiment analysis.

## Architecture

### Two-Service Design

**Web Service (dashboard.py)**
- Streamlit UI dashboard
- Displays account info, open trades, recent activity
- Shows recent articles and trades from database
- Start/Stop controls for worker via `bot_state.enabled` flag
- UI-only - no trading logic

**Worker Service (worker.py)**
- Background process running trading loop
- Fetches news headlines from Reuters RSS
- Analyzes sentiment with VADER
- Places OANDA market orders with TP/SL
- Logs all articles and trades to database
- Controlled by `bot_state.enabled` flag

### Database Layer (db.py)

**SQLAlchemy ORM** with support for:
- **Production**: Postgres via `DATABASE_URL` env var
- **Local Dev**: SQLite fallback

**Tables:**
- `bot_state`: Single-row table with `enabled` flag
- `articles`: News articles with sentiment scores
- `trades`: Trade execution records

## Deployment (Render)

Defined in `render.yaml`:

1. **Postgres Database**: `oanda-bot-db`
   - Shared by web and worker services
   - Connection via `DATABASE_URL` env var

2. **Web Service**: `oanda-dashboard`
   - Runs Streamlit on port 10000
   - Public-facing dashboard

3. **Worker Service**: `oanda-trading-worker`
   - Runs `worker.py` in background
   - No public endpoint
   - Polls news and trades continuously

## Environment Variables

**Required:**
- `OANDA_HOST`: API endpoint (e.g., `https://api-fxpractice.oanda.com`)
- `OANDA_ACCOUNT`: Account ID
- `OANDA_TOKEN`: API token
- `DATABASE_URL`: Postgres connection string (auto-injected by Render)

**Optional (Trading Strategy):**
- `BOT_INSTRUMENT`: Default `EUR_USD`
- `BOT_TP_PIPS`: Take-profit distance (default `38`)
- `BOT_SL_PIPS`: Stop-loss distance (default `25`)
- `BOT_RISK_USD`: Risk per trade (default `500`)
- `BOT_TRADE_INTERVAL_MIN`: Poll frequency (default `1`)
- `BOT_COOLDOWN_MIN`: Wait after trade (default `0`)
- `BOT_MAX_CONCURRENT`: Max open trades (default `3`)
- `BOT_MIN_SPREAD`: Max spread to trade (default `0.0002`)
- `BOT_SENT_THRESHOLD`: Min sentiment to trade (default `0.15`)

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run dashboard (in one terminal)
streamlit run dashboard.py

# Run worker (in another terminal)
python worker.py
```

Or use Procfile with honcho/foreman:
```bash
honcho start
```

## Database Schema

### bot_state
- `id`: Always 1 (enforced)
- `enabled`: Boolean
- `updated_at`: ISO timestamp

### articles
- `id`: Auto-increment
- `published_at`: ISO timestamp
- `source`: e.g., "Reuters RSS"
- `title`: Headline text
- `url`: Optional link
- `sentiment`: Float (-1.0 to 1.0)
- `instrument`: Trading pair
- `raw_json`: Original data

### trades
- `id`: Auto-increment
- `ts`: ISO timestamp
- `instrument`: Trading pair
- `side`: "BUY" or "SELL"
- `units`: Signed integer
- `notional_usd`: Approximate USD value
- `sentiment`: Score that triggered trade
- `headline`: News headline
- `order_id`: OANDA order ID
- `status`: "FILLED", "REJECTED", etc.
- `fill_price`: Execution price
- `raw_json`: OANDA response

## Workflow

1. User opens dashboard and clicks "START BOT"
2. Dashboard sets `bot_state.enabled = True` in database
3. Worker loop checks flag every iteration
4. If enabled:
   - Fetch RSS headlines
   - Compute sentiment scores
   - Log all articles to database
   - If sentiment threshold met → place OANDA order
   - Log trade to database
5. Dashboard displays live articles and trades from DB

## Safety Features

- **Spread Check**: Won't trade if spread > `BOT_MIN_SPREAD`
- **Cooldown**: Enforces minimum time between trades
- **Concurrent Limit**: Max `BOT_MAX_CONCURRENT` open positions
- **Sentiment Threshold**: Only trades if abs(sentiment) > `BOT_SENT_THRESHOLD`
- **Database-Driven**: Worker respects `bot_state.enabled` flag

## Monitoring

- Dashboard shows green/red status light for API health
- Recent articles table shows sentiment analysis
- Recent trades table shows execution history
- Worker logs print to stdout (visible in Render logs)
