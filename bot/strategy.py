"""
Core signal-generation strategy -- trend + strength + breakout + volume
confirmation. Loosened from an earlier, much stricter version after it
proved too rarely satisfied in practice (good in principle, but firing
almost never isn't useful day to day).

THIS IS NOT A PROVEN PROFITABLE STRATEGY. Loosening filters increases how
often signals fire, which also means more low-conviction/false signals get
through. That trade-off is intentional here, per explicit instruction, not
an oversight -- treat every alert as a starting point to verify, not an
instruction to act on blindly.

Active CE (call) rules:
  EMA9 > EMA21 (5m)                          [REQUIRED -- gates the signal]
  AND ADX(14) > 20                            [REQUIRED, lowered from 25]
  AND Price breaks 10-candle high             [REQUIRED]
  AND Current volume > 1.5 x AvgVolume20       [REQUIRED -- kept as-is per instruction]
  AND close > open (bullish candle)            [REQUIRED -- basic direction check]
  AND body_size > 0.3 x candle_range            [REQUIRED, loosened from 0.5 -- screens dojis only]
  AND Trade time within allowed session         [enforced by main.py, not here]

Active PE (put) rules: mirror image of the above (EMA9<EMA21, breaks
10-candle low, close<open, same ADX/volume/body thresholds).

WHAT WAS REMOVED vs the stricter version:
  - 15m EMA confirmation is no longer REQUIRED to gate the signal. It's
    still computed and shown in the message as context (e.g. "15m trend:
    bullish (agrees)" or "(disagrees)") so you have the information without
    it blocking signals when the higher timeframe lags the 5m move.
  - The wick / close-location check (close must be within X% of the
    candle's high/low) is removed entirely -- this was the single
    strictest, most rarely satisfied condition.

WHAT WAS KEPT AS-IS:
  - Volume confirmation (> 1.5x average) -- explicitly requested to keep.
  - ATR-based SL/TP1/TP2 -- unchanged.
  - "Trade time within allowed session" -- enforced by main.py's
    is_market_open() check before this module is ever called.

The earlier, stricter rule set (with 15m as a hard gate, ADX>25, body>50%,
and symmetric wick checks) is preserved in git history / strategy_basic.py
comments if you want to revert later.
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
    adx_value: float
    higher_tf_agrees: bool  # informational only -- does NOT gate the signal
    timestamp: datetime
    reason: str


def _parse_candles(raw_candles: list[list]) -> list[dict]:
    """
    Convert Upstox's raw candle arrays (newest-first) into oldest-first
    list of dicts. Upstox candle format:
    [timestamp, open, high, low, close, volume, oi]
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


def _candle_body_pct(candle: dict) -> float:
    """Body size as a fraction of the candle's full high-low range.
    Returns 0 for a zero-range candle (avoids division by zero)."""
    full_range = candle["high"] - candle["low"]
    if full_range <= 0:
        return 0.0
    body = abs(candle["close"] - candle["open"])
    return body / full_range


def evaluate(raw_candles_5m: list[list], raw_candles_15m: list[list]) -> Signal | None:
    """
    Main entry point. Takes raw Upstox candle arrays for the trading
    timeframe (5m) and the higher timeframe (15m, informational only).
    Returns a Signal if 5m conditions for either CE or PE are met,
    otherwise None.
    """
    candles_5m = _parse_candles(raw_candles_5m)
    candles_15m = _parse_candles(raw_candles_15m)

    min_required_5m = max(
        config.EMA_SLOW,
        config.ATR_PERIOD * 2,  # ADX needs ~2x period to stabilize
        config.ADX_PERIOD * 2,
        config.BREAKOUT_LOOKBACK,
        config.VOLUME_AVG_PERIOD,
    ) + 2
    min_required_15m = config.EMA_SLOW + 2

    if len(candles_5m) < min_required_5m:
        logger.info(
            "Not enough 5m candles yet (%d/%d) -- skipping this cycle.",
            len(candles_5m), min_required_5m,
        )
        return None

    closes_5m = [c["close"] for c in candles_5m]
    highs_5m = [c["high"] for c in candles_5m]
    lows_5m = [c["low"] for c in candles_5m]
    volumes_5m = [c["volume"] for c in candles_5m]

    ema_fast_5m = indicators.ema(closes_5m, config.EMA_FAST)
    ema_slow_5m = indicators.ema(closes_5m, config.EMA_SLOW)
    atr_values = indicators.atr(candles_5m, config.ATR_PERIOD)
    adx_values = indicators.adx(candles_5m, config.ADX_PERIOD)
    roll_high = indicators.rolling_high(highs_5m, config.BREAKOUT_LOOKBACK)
    roll_low = indicators.rolling_low(lows_5m, config.BREAKOUT_LOOKBACK)
    avg_volume = indicators.rolling_avg(volumes_5m, config.VOLUME_AVG_PERIOD)

    i = len(candles_5m) - 1  # latest closed 5m candle

    # 15m trend is informational only -- if it's not available yet (e.g.
    # early in the session), we don't block the signal on it, we just can't
    # show the context line.
    higher_tf_agrees = None
    if len(candles_15m) >= min_required_15m:
        closes_15m = [c["close"] for c in candles_15m]
        ema_fast_15m = indicators.ema(closes_15m, config.EMA_FAST)
        ema_slow_15m = indicators.ema(closes_15m, config.EMA_SLOW)
        j = len(candles_15m) - 1
        if ema_fast_15m[j] is not None and ema_slow_15m[j] is not None:
            bullish_15m = ema_fast_15m[j] > ema_slow_15m[j]
            bearish_15m = ema_fast_15m[j] < ema_slow_15m[j]
        else:
            bullish_15m = bearish_15m = False
    else:
        bullish_15m = bearish_15m = False

    # Guard: every REQUIRED indicator must have a real value (not None).
    required_values = [
        ema_fast_5m[i], ema_slow_5m[i],
        atr_values[i], adx_values[i],
        roll_high[i], roll_low[i], avg_volume[i],
    ]
    if any(v is None for v in required_values):
        logger.info("Indicators still warming up -- skipping this cycle.")
        return None

    current_candle = candles_5m[i]
    current_price = closes_5m[i]
    current_atr = atr_values[i]
    current_adx = adx_values[i]
    current_volume = volumes_5m[i]

    bullish_5m = ema_fast_5m[i] > ema_slow_5m[i]
    bearish_5m = ema_fast_5m[i] < ema_slow_5m[i]

    strong_trend = current_adx > config.ADX_MIN_THRESHOLD
    broke_up = current_price > roll_high[i]
    broke_down = current_price < roll_low[i]
    volume_confirmed = current_volume > (config.VOLUME_MULTIPLIER * avg_volume[i])
    body_pct = _candle_body_pct(current_candle)
    strong_body = body_pct > config.MIN_CANDLE_BODY_PCT
    bullish_candle_color = current_candle["close"] > current_candle["open"]
    bearish_candle_color = current_candle["close"] < current_candle["open"]

    direction = None
    reason_parts = []
    higher_tf_agrees_result = False

    if (
        bullish_5m and strong_trend and broke_up
        and volume_confirmed and strong_body and bullish_candle_color
    ):
        direction = "CE"
        higher_tf_agrees_result = bullish_15m
        reason_parts = [
            f"EMA{config.EMA_FAST}>EMA{config.EMA_SLOW} (5m)",
            f"ADX={current_adx:.1f}>{config.ADX_MIN_THRESHOLD}",
            f"broke {config.BREAKOUT_LOOKBACK}-candle high",
            f"vol={current_volume:.0f}>{config.VOLUME_MULTIPLIER}x avg({avg_volume[i]:.0f})",
            f"bullish body={body_pct:.0%}>{config.MIN_CANDLE_BODY_PCT:.0%}",
            f"15m trend {'agrees' if bullish_15m else 'does NOT confirm'}",
        ]
    elif (
        bearish_5m and strong_trend and broke_down
        and volume_confirmed and strong_body and bearish_candle_color
    ):
        direction = "PE"
        higher_tf_agrees_result = bearish_15m
        reason_parts = [
            f"EMA{config.EMA_FAST}<EMA{config.EMA_SLOW} (5m)",
            f"ADX={current_adx:.1f}>{config.ADX_MIN_THRESHOLD}",
            f"broke {config.BREAKOUT_LOOKBACK}-candle low",
            f"vol={current_volume:.0f}>{config.VOLUME_MULTIPLIER}x avg({avg_volume[i]:.0f})",
            f"bearish body={body_pct:.0%}>{config.MIN_CANDLE_BODY_PCT:.0%}",
            f"15m trend {'agrees' if bearish_15m else 'does NOT confirm'}",
        ]
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
        adx_value=round(current_adx, 2),
        higher_tf_agrees=higher_tf_agrees_result,
        timestamp=datetime.now(),
        reason=" | ".join(reason_parts),
    )
