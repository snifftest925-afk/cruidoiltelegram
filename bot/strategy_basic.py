"""
ARCHIVED / NOT USED BY main.py.

This is the original simpler strategy (EMA9/21 crossover + ATR + single-
timeframe breakout), kept here for reference and easy rollback. The active
strategy is now strategy.py, which implements the stricter multi-timeframe
+ ADX + volume + candle-quality rules.

----
Core signal-generation strategy.

THIS IS NOT A PROVEN PROFITABLE STRATEGY. It is a transparent, rule-based
combination of common technical tools (EMA trend filter, ATR volatility
filter, N-candle breakout) so that every signal the bot sends is fully
explainable: you can always trace exactly why it fired. Treat it as a
starting framework to test, tune, and replace with your own rules -- not
as financial advice or a guarantee of edge.

Logic, plainly:
  1. Trend bias: EMA(fast) vs EMA(slow). Fast > slow = bullish bias, else bearish.
  2. Volatility filter: only act if ATR is above a minimum -- skip dead/choppy markets.
  3. Trigger: price breaks above the recent N-candle high (in a bullish bias)
     or below the recent N-candle low (in a bearish bias).
  4. When triggered: suggest buying the corresponding option side (CE for
     bullish breakout, PE for bearish breakdown), strike = nearest ATM
     rounded to STRIKE_STEP.
  5. SL/TP computed as ATR multiples from the current futures price.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from . import config, indicators

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    direction: str  # "CE" or "PE"
    futures_price: float
    strike: int
    sl: float
    tp1: float
    tp2: float
    atr_value: float
    timestamp: datetime
    reason: str


def _parse_candles(raw_candles: list[list]) -> list[dict]:
    """
    Convert Upstox's raw candle arrays (newest-first) into oldest-first
    list of dicts, which is what indicators.py expects.

    Upstox candle format: [timestamp, open, high, low, close, volume, oi]
    """
    parsed = []
    for c in raw_candles:
        parsed.append({
            "timestamp": c[0],
            "open": c[1],
            "high": c[2],
            "low": c[3],
            "close": c[4],
            "volume": c[5] if len(c) > 5 else 0,
        })
    parsed.reverse()  # now oldest-first
    return parsed


def evaluate(raw_candles: list[list]) -> Signal | None:
    """
    Main entry point: takes raw Upstox candle data, returns a Signal if
    conditions are met, otherwise None.
    """
    candles = _parse_candles(raw_candles)

    min_required = max(config.EMA_SLOW, config.ATR_PERIOD, config.BREAKOUT_LOOKBACK) + 2
    if len(candles) < min_required:
        logger.info(
            "Not enough candles yet (%d/%d) -- skipping this cycle.",
            len(candles), min_required,
        )
        return None

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    ema_fast = indicators.ema(closes, config.EMA_FAST)
    ema_slow = indicators.ema(closes, config.EMA_SLOW)
    atr_values = indicators.atr(candles, config.ATR_PERIOD)
    roll_high = indicators.rolling_high(highs, config.BREAKOUT_LOOKBACK)
    roll_low = indicators.rolling_low(lows, config.BREAKOUT_LOOKBACK)

    i = len(candles) - 1  # latest closed candle
    if ema_fast[i] is None or ema_slow[i] is None or atr_values[i] is None:
        return None
    if roll_high[i] is None or roll_low[i] is None:
        return None

    current_price = closes[i]
    current_atr = atr_values[i]

    bullish_bias = ema_fast[i] > ema_slow[i]
    bearish_bias = ema_fast[i] < ema_slow[i]

    broke_up = current_price > roll_high[i]
    broke_down = current_price < roll_low[i]

    direction = None
    reason = ""

    if bullish_bias and broke_up:
        direction = "CE"
        reason = (
            f"EMA{config.EMA_FAST} > EMA{config.EMA_SLOW} (uptrend bias) and price "
            f"broke {config.BREAKOUT_LOOKBACK}-candle high"
        )
    elif bearish_bias and broke_down:
        direction = "PE"
        reason = (
            f"EMA{config.EMA_FAST} < EMA{config.EMA_SLOW} (downtrend bias) and price "
            f"broke {config.BREAKOUT_LOOKBACK}-candle low"
        )
    else:
        return None

    strike = round(current_price / config.STRIKE_STEP) * config.STRIKE_STEP

    if direction == "CE":
        sl = current_price - (config.SL_ATR_MULT * current_atr)
        tp1 = current_price + (config.TP1_ATR_MULT * current_atr)
        tp2 = current_price + (config.TP2_ATR_MULT * current_atr)
    else:
        sl = current_price + (config.SL_ATR_MULT * current_atr)
        tp1 = current_price - (config.TP1_ATR_MULT * current_atr)
        tp2 = current_price - (config.TP2_ATR_MULT * current_atr)

    return Signal(
        direction=direction,
        futures_price=round(current_price, 2),
        strike=int(strike),
        sl=round(sl, 2),
        tp1=round(tp1, 2),
        tp2=round(tp2, 2),
        atr_value=round(current_atr, 2),
        timestamp=datetime.now(),
        reason=reason,
    )
