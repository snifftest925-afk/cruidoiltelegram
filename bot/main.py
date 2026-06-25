"""
Main loop: every CHECK_INTERVAL_SECONDS during market hours, fetch fresh
candles, evaluate the strategy, and send a Telegram message if a new signal
fires. Designed to run as a long-lived process on Railway (worker service).
"""

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config
from .instruments import get_current_crudeoil_instrument_key
from .notifier import format_signal_message, send_telegram_message, format_expiry_for_display
from .premarket import pivot_levels_from_daily_candles, format_premarket_message
from .strategy import evaluate
from .upstox_client import UpstoxClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

IST = ZoneInfo("Asia/Kolkata")


def now_ist() -> datetime:
    """
    Always use this instead of datetime.now() anywhere in this module.
    Railway (and most hosts) run containers in UTC, not IST -- MCX trading
    hours are defined in IST, so every comparison against MARKET_OPEN_HOUR
    etc. must be done in IST regardless of the server's local timezone.
    """
    return datetime.now(IST)


def is_market_open(now: datetime | None = None) -> bool:
    now = now or now_ist()
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    open_t = now.replace(
        hour=config.MARKET_OPEN_HOUR, minute=config.MARKET_OPEN_MINUTE, second=0, microsecond=0
    )
    close_t = now.replace(
        hour=config.MARKET_CLOSE_HOUR, minute=config.MARKET_CLOSE_MINUTE, second=0, microsecond=0
    )
    return open_t <= now <= close_t


def is_premarket_window(now: datetime | None = None) -> bool:
    """
    True during the window where the daily pre-market message should fire:
    from PREMARKET_HOUR:MINUTE up until market open. Checking a window
    (not an exact minute) makes this robust to the loop's polling cadence --
    if CHECK_INTERVAL_SECONDS is 5 min, an exact-minute check could miss
    the trigger entirely depending on when the loop happens to wake up.
    """
    now = now or now_ist()
    if now.weekday() >= 5:
        return False
    premarket_start = now.replace(
        hour=config.PREMARKET_HOUR, minute=config.PREMARKET_MINUTE, second=0, microsecond=0
    )
    market_open = now.replace(
        hour=config.MARKET_OPEN_HOUR, minute=config.MARKET_OPEN_MINUTE, second=0, microsecond=0
    )
    return premarket_start <= now < market_open


def resolve_instrument_with_retry(max_wait_seconds: int = 600):
    """
    Retries instrument resolution with exponential backoff (capped) instead
    of letting a transient failure (network hiccup, Upstox file momentarily
    unavailable) kill the whole process and trigger a Railway restart loop.
    """
    delay = 10
    elapsed = 0
    while True:
        try:
            return get_current_crudeoil_instrument_key()
        except Exception as e:
            logger.error(
                "Failed to resolve CRUDEOIL instrument (will retry in %ds): %s",
                delay, e,
            )
            time.sleep(delay)
            elapsed += delay
            delay = min(delay * 2, 120)
            if elapsed >= max_wait_seconds:
                # Give up retrying tightly and fall back to the outer
                # process-level restart, but at least we tried for a while
                # first instead of failing instantly.
                raise


def run():
    logger.info("Starting Crude Oil Signal Bot...")
    client = UpstoxClient(config.UPSTOX_ACCESS_TOKEN)

    instrument_key, instrument_info = resolve_instrument_with_retry()
    expiry_display = format_expiry_for_display(instrument_info.get("expiry"))
    logger.info("Tracking instrument: %s", instrument_key)

    last_signal_direction = None
    last_signal_time = None
    last_premarket_sent_date = None  # tracks the date (YYYY-MM-DD) already sent today

    while True:
        try:
            now = now_ist()
            today_str = now.strftime("%Y-%m-%d")

            # --- Pre-market analysis message (once per day) ---
            if (
                config.PREMARKET_ENABLED
                and is_premarket_window(now)
                and last_premarket_sent_date != today_str
            ):
                try:
                    raw_daily = client.get_recent_daily_candles(
                        instrument_key, days_back=config.PREMARKET_LOOKBACK_DAYS
                    )
                    levels = pivot_levels_from_daily_candles(raw_daily)
                    if levels is not None:
                        premarket_message = format_premarket_message(levels, expiry_display)
                        send_telegram_message(premarket_message)
                        logger.info("Pre-market analysis sent for %s.", today_str)
                    else:
                        logger.warning(
                            "Could not compute pre-market levels (no daily candle data yet)."
                        )
                    # Mark as sent for today regardless of whether levels came
                    # back -- avoids retrying every 5 min and spamming errors
                    # if Upstox's daily data genuinely isn't available today
                    # (e.g. a holiday with a thin file).
                    last_premarket_sent_date = today_str
                except Exception as e:
                    logger.exception("Failed to send pre-market analysis: %s", e)
                    # Don't mark as sent -- let it retry next cycle within
                    # the pre-market window in case it was a transient error.

            if not is_market_open(now):
                logger.info(
                    "Market closed (current IST time: %s). Sleeping...",
                    now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                )
                time.sleep(config.CHECK_INTERVAL_SECONDS)
                continue

            raw_candles_5m = client.get_intraday_candles(
                instrument_key, config.CANDLE_INTERVAL_MINUTES
            )
            raw_candles_15m = client.get_intraday_candles(
                instrument_key, config.HIGHER_TIMEFRAME_MINUTES
            )
            signal = evaluate(raw_candles_5m, raw_candles_15m)

            if signal is not None:
                same_direction_recent = (
                    last_signal_direction == signal.direction
                    and last_signal_time is not None
                    and (now - last_signal_time).total_seconds()
                    < config.MIN_MINUTES_BETWEEN_SAME_SIGNAL * 60
                )

                if same_direction_recent:
                    logger.info(
                        "Same-direction signal (%s) suppressed -- last one was %.1f min ago.",
                        signal.direction,
                        (now - last_signal_time).total_seconds() / 60,
                    )
                else:
                    message = format_signal_message(signal, expiry_display)
                    send_telegram_message(message)
                    last_signal_direction = signal.direction
                    last_signal_time = now
            else:
                logger.info("No signal this cycle.")

        except Exception as e:
            logger.exception("Error during signal check cycle: %s", e)
            # Don't crash the whole process on a transient API hiccup --
            # log it and try again next cycle.

        time.sleep(config.CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
