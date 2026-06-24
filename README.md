# Crude Oil Signal Bot (MCX, Upstox + Telegram)

An intraday signal bot for MCX Crude Oil. It pulls futures candle data from
Upstox, runs a simple rule-based strategy (EMA trend + ATR volatility filter
+ breakout trigger), and sends CE/PE buy alerts to a Telegram chat.

## ⚠️ Read this before you rely on it

- **This is not a profitable trading system.** It's a transparent rules
  framework so every alert is explainable. Backtest and paper-trade before
  risking real money. Past performance of any strategy (including this one)
  does not predict future results.
- **No live option premium.** Upstox's API does **not** provide a live
  options chain for the MCX exchange. The bot can only compute signals from
  the **futures** price and tell you the suggested strike/direction — you
  must check the actual CE/PE LTP on your broker app yourself before placing
  any order. The "BUY @ ___" line is intentionally left blank.
- **MCX API trading status changes.** Upstox has, in the past, temporarily
  disabled MCX algo/API trading during regulatory transitions (SEBI's
  "Safer participation of retail investors in Algorithmic trading"
  framework). This bot only *reads* data and sends Telegram messages — it
  never places orders — so it's unaffected by order-placement restrictions,
  but if Upstox disables MCX data access entirely, the bot will fail to
  fetch candles. Check https://community.upstox.com for current status if
  it stops working.
- **You place all trades manually.** This bot does not connect to your
  demat/trading account for order execution. It only sends you a Telegram
  message; you decide whether to act on it.

## How it works

1. On startup, downloads Upstox's MCX instrument master file and finds the
   current (nearest unexpired) CRUDEOIL futures contract automatically —
   so it doesn't break when the contract rolls over each month.
2. **Pre-market message (once daily, default 8:30 AM IST):** fetches the
   prior session's daily candle and computes classic pivot-point
   support/resistance levels (Pivot, R1-R3, S1-S3), sending a short summary
   to Telegram. This also works as a daily "the bot is alive" check — if
   you get this message every morning, the scheduler, Upstox auth, and
   Telegram connection are all confirmed working, with no need to wait for
   an actual trade signal.
3. Every `CHECK_INTERVAL_SECONDS` (default 5 min) during market hours,
   fetches the latest 5-minute AND 15-minute candles for the contract.
4. Computes EMA(9)/EMA(21) on both timeframes, ADX(14), a rolling 20-period
   average volume, and a rolling N-candle high/low.
5. **CE (call) fires when ALL of these hold on the 5-minute timeframe:**
   - EMA9 > EMA21 (5m trend)
   - ADX(14) > 20 (trend has some real strength, not pure chop)
   - Price breaks the last 10-candle high (breakout trigger)
   - Current candle's volume > 1.5x the 20-period average (real
     participation -- this filter is intentionally kept strict)
   - The breakout candle is bullish (close > open) with body > 30% of its
     high-low range (screens out dojis/indecision candles only)
6. **PE (put) fires on the mirrored conditions:** EMA9 < EMA21 (5m), ADX >
   20, breaks the 10-candle low, same volume confirmation, bearish candle
   (close < open) with body > 30%.
7. The 15-minute EMA trend is also checked and shown in the message
   ("15m trend confirms: Yes/No") but does **not** block the signal --
   it's context, not a gate. This was loosened from an earlier version
   where it was a hard requirement, because requiring both timeframes (on
   top of ADX, volume, and candle-quality filters) made signals fire too
   rarely to be useful day to day.
8. SL/TP1/TP2 are ATR-based multiples of the current futures price (adapts
   to volatility instead of using fixed point distances).
9. Sends a formatted message to your Telegram chat, listing exactly which
   conditions fired. Repeated signals in the same direction are suppressed
   for `MIN_MINUTES_BETWEEN_SAME_SIGNAL` to avoid spam while a trend
   persists.

**A note on the trade-off here:** looser filters mean more signals, but
also more lower-conviction ones. The volume filter (1.5x average) is kept
strict by design since it's a genuine real-participation check, but the
others (15m confirmation, ADX threshold, candle body) were deliberately
relaxed after the stricter combination proved to fire too rarely in
practice. If you find it's now firing on weak setups, the first knob to
tighten back up is usually `ADX_MIN_THRESHOLD` (try 25 again) rather than
re-adding the wick/close-location check, which was the single biggest
bottleneck before.
   conditions fired. Repeated signals in the same direction are suppressed
   for `MIN_MINUTES_BETWEEN_SAME_SIGNAL` to avoid spam while a trend
   persists.

**Note on trailing stops:** this bot does not track your live broker
position, so it cannot automatically "move SL to cost after TP1" — it
shows TP1/TP2/SL once at signal time, and trailing-stop management is on
you from there.

## Project structure

```
bot/
  config.py        # all settings, loaded from environment variables
  instruments.py    # finds current CRUDEOIL futures instrument_key
  upstox_client.py  # Upstox API wrapper (candle data)
  indicators.py     # EMA, ATR, rolling high/low (no external deps)
  strategy.py        # signal generation logic (active strategy)
  strategy_basic.py   # archived simpler strategy, not used -- kept for reference/rollback
  premarket.py       # pivot-point support/resistance + daily heartbeat message
  notifier.py        # Telegram message formatting + sending
  main.py             # scheduling loop, entry point
  diagnose_instruments.py  # standalone debug script for instrument lookup issues
  test_sanity.py      # quick logic checks with synthetic data
requirements.txt
railway.toml
.env.example
```

## Setup

### 1. Get a Telegram bot token

1. Message [@BotFather](https://t.me/BotFather) on Telegram, send `/newbot`,
   follow the prompts. You'll get a token like `123456:ABC-DEF...`.
2. Send any message to your new bot from your personal account (or add it
   to a group/channel).
3. Find your chat ID by visiting:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   and looking for `"chat":{"id": ...}` in the response.

### 2. Get an Upstox access token (do this every trading day)

Upstox access tokens are valid only for the current day and must be
regenerated each morning. The general flow:

1. Go to https://account.upstox.com/developer/apps and create an app if you
   haven't (you'll get an `api_key` / `api_secret` and set a redirect URI).
2. Each morning, open this URL in a browser (replace `YOUR_API_KEY` and
   `YOUR_REDIRECT_URI`):
   ```
   https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id=YOUR_API_KEY&redirect_uri=YOUR_REDIRECT_URI
   ```
3. Log in, approve access. You'll be redirected with a `code` query param.
4. Exchange that code for an access token:
   ```bash
   curl -X POST https://api.upstox.com/v2/login/authorization/token \
     -H 'Content-Type: application/x-www-form-urlencoded' \
     -d 'code=YOUR_CODE' \
     -d 'client_id=YOUR_API_KEY' \
     -d 'client_secret=YOUR_API_SECRET' \
     -d 'redirect_uri=YOUR_REDIRECT_URI' \
     -d 'grant_type=authorization_code'
   ```
5. Copy the `access_token` from the response and set it as the
   `UPSTOX_ACCESS_TOKEN` environment variable on Railway (under your
   project's **Variables** tab). The bot will keep using it until it
   expires; you'll need to repeat this each trading day.

   *(This manual step is the one part of the pipeline that can't be fully
   automated without storing your Upstox login credentials, which this
   project intentionally avoids for security reasons. Some people script
   steps 2-4 with a headless browser; that's outside this bot's scope.)*

### 3. Deploy to Railway

1. Push this folder to a new GitHub repository.
2. On [Railway](https://railway.app), create a new project → **Deploy from
   GitHub repo** → select your repo.
3. Railway will detect `railway.toml` and use Nixpacks automatically.
4. Under **Variables**, add:
   - `UPSTOX_ACCESS_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - (optionally) any of the strategy/scheduling overrides from
     `.env.example`
5. Deploy. Check the **Logs** tab to confirm it starts and resolves the
   current CRUDEOIL contract without errors.
6. Each trading morning, update `UPSTOX_ACCESS_TOKEN` in Railway's Variables
   tab with the fresh token (step 2 above), which will trigger a redeploy.

### 4. Local testing (optional, before deploying)

```bash
cp .env.example .env
# edit .env with real values
pip install -r requirements.txt
export $(grep -v '^#' .env | xargs)  # load env vars into shell
python -m bot.test_sanity   # sanity checks with synthetic data
python -m bot.main          # run the real loop (only sends real Telegram messages during market hours)
```

## Troubleshooting

### "No active CRUDEOIL futures contract found"

This means the bot downloaded Upstox's MCX instrument file but its filter
couldn't find a matching, unexpired CRUDEOIL futures entry. Possible causes:

- Upstox changed field names/values in their instrument file since this
  code was written (most likely).
- MCX data/trading has been disabled on Upstox's side (check
  https://community.upstox.com for announcements).
- A genuine gap between contract expiries (rare, but the file might briefly
  list only an already-expired contract).

**To debug it yourself**, run the diagnostic script wherever the bot runs
(Railway's shell, or locally):

```bash
python -m bot.diagnose_instruments
```

This prints every CRUDEOIL-related entry it finds in Upstox's raw file,
including the exact `segment`, `instrument_type`, and `expiry` field values.
Compare that against the filter logic in `bot/instruments.py`
(`_looks_like_crudeoil` and `_looks_like_mcx_future`) and adjust if Upstox's
actual values differ from what's expected there.

The bot also now logs a sample of raw entries directly in its own error
message when this happens (check Railway's Logs tab), so you may not even
need to run the diagnostic separately.

### "Market closed" shown all day, signals never fire

Fixed in this version. The bug: Railway runs containers in UTC, but MCX
market hours are in IST (UTC+5:30). The bot now explicitly converts to
`Asia/Kolkata` time for every market-hours check, regardless of the
container's local timezone — look for `"Market closed (current IST time:
...)"` in the logs to confirm it's reading the correct time.

### Crash-loop on Railway (restarting every second)

Fixed in this version — instrument resolution now retries with backoff
*inside* the running process instead of crashing and relying on Railway to
restart it. If you still see rapid restarts, check the logs for what's
actually failing; it's likely a different issue (e.g. invalid Telegram
token format) than the original instrument lookup bug.

## Tuning the strategy

All of these are environment variables (see `.env.example` for defaults):

| Variable | Meaning |
|---|---|
| `EMA_FAST` / `EMA_SLOW` | Trend filter periods |
| `ATR_PERIOD` | Volatility lookback |
| `BREAKOUT_LOOKBACK` | N-candle high/low for breakout trigger |
| `CANDLE_INTERVAL_MINUTES` | Candle timeframe used for analysis |
| `SL_ATR_MULT` / `TP1_ATR_MULT` / `TP2_ATR_MULT` | Stop/target distance as ATR multiples |
| `STRIKE_STEP` | Strike rounding (50 for standard MCX crude oil cycles) |
| `HIGHER_TIMEFRAME_MINUTES` | Confirmation timeframe (default 15m, informational only -- shown in message, doesn't gate the signal) |
| `ADX_PERIOD` / `ADX_MIN_THRESHOLD` | Trend-strength filter (default 20) |
| `VOLUME_AVG_PERIOD` / `VOLUME_MULTIPLIER` | Volume confirmation -- kept strict (1.5x avg) by design |
| `MIN_CANDLE_BODY_PCT` | Minimum breakout candle body as % of its range (default 0.30, just screens dojis) |
| `CHECK_INTERVAL_SECONDS` | How often the bot polls for a new signal |
| `MIN_MINUTES_BETWEEN_SAME_SIGNAL` | Cooldown to avoid repeat alerts |
| `PREMARKET_ENABLED` | Turn the daily pre-market message on/off |
| `PREMARKET_HOUR` / `PREMARKET_MINUTE` | When the pre-market message sends (IST) |
| `PREMARKET_LOOKBACK_DAYS` | How many days of daily candles to fetch for the prior-session pivot calc |

Edit `bot/strategy.py` directly if you want to replace the logic entirely —
it's intentionally written as one self-contained function (`evaluate()`) so
swapping in your own rules doesn't require touching the rest of the bot.

## License

Use, modify, and deploy freely for your own personal use.
