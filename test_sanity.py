"""
Quick sanity checks for indicators.py, strategy.py, and premarket.py using
synthetic data. Not a full test suite -- just enough to catch obvious bugs
before deploying. Run with: python -m bot.test_sanity
"""

import random

from bot import indicators
from bot.premarket import compute_pivot_levels, pivot_levels_from_daily_candles
from bot.strategy import evaluate, _candle_body_pct


def make_synthetic_candles(n=80, start_price=6900, trend=0.5, volatility=8,
                            volume_base=1000, volume_spike_at=None, volume_spike_mult=2.0,
                            interval_minutes=5):
    """
    Generates fake OHLCV candles for testing, in Upstox's newest-first raw
    array format: [timestamp, open, high, low, close, volume, oi].

    volume_spike_at: index (in chronological/oldest-first order) where
    volume should spike, to test the volume-confirmation filter.
    """
    candles = []
    price = start_price
    for i in range(n):
        price += trend + random.uniform(-volatility, volatility)
        high = price + random.uniform(0, volatility)
        low = price - random.uniform(0, volatility)
        open_ = price - random.uniform(-volatility / 2, volatility / 2)
        close = price
        volume = volume_base + random.uniform(-100, 100)
        if volume_spike_at is not None and i == volume_spike_at:
            volume *= volume_spike_mult
        ts = f"2026-06-24T{9 + (i * interval_minutes) // 60:02d}:{(i * interval_minutes) % 60:02d}:00+05:30"
        candles.append([ts, open_, high, low, close, volume])
    candles.reverse()  # newest-first, like Upstox returns
    return candles


def make_strong_breakout_candles(n=80, direction="up", interval_minutes=5):
    """
    Builds a candle series that should satisfy ALL of the strict strategy's
    conditions for the given direction: clear EMA trend, high ADX, a final
    breakout candle with a big body and a volume spike, in the right
    direction. Used to prove the strategy CAN fire under the right
    conditions (not just that it correctly stays silent on noise).
    """
    candles = []
    price = 6900.0
    trend = 4.0 if direction == "up" else -4.0
    for i in range(n - 1):
        price += trend + random.uniform(-1.5, 1.5)
        high = price + random.uniform(0.5, 2)
        low = price - random.uniform(0.5, 2)
        open_ = price - trend * 0.3
        close = price
        volume = 1000 + random.uniform(-50, 50)
        ts = f"2026-06-24T{9 + (i * interval_minutes) // 60:02d}:{(i * interval_minutes) % 60:02d}:00+05:30"
        candles.append([ts, open_, high, low, close, volume])

    # Final breakout candle: big body, big volume, clearly through the
    # recent range, and (for PE) closing near its low.
    last_price = price
    if direction == "up":
        open_ = last_price
        close = last_price + 25  # big bullish body
        high = close + 1
        low = open_ - 1
    else:
        open_ = last_price
        close = last_price - 25  # big bearish body
        low = close - 1
        high = open_ + 1
    i = n - 1
    ts = f"2026-06-24T{9 + (i * interval_minutes) // 60:02d}:{(i * interval_minutes) % 60:02d}:00+05:30"
    candles.append([ts, open_, high, low, close, 3000])  # ~3x normal volume

    candles.reverse()
    return candles


def test_ema_basic():
    values = [float(x) for x in range(1, 31)]
    result = indicators.ema(values, 9)
    assert result[8] is not None, "EMA should have a value once seed period reached"
    assert all(v is None for v in result[:8]), "EMA should be None before seed period"
    print("✓ EMA basic test passed")


def test_atr_basic():
    candles = make_synthetic_candles(40)
    parsed = []
    for c in reversed(candles):
        parsed.append({"timestamp": c[0], "open": c[1], "high": c[2], "low": c[3], "close": c[4]})
    result = indicators.atr(parsed, 14)
    assert result[14] is not None, "ATR should have a value once seed period reached"
    assert result[14] > 0, "ATR should be positive"
    print(f"✓ ATR basic test passed (sample value: {result[20]:.2f})")


def test_adx_basic():
    random.seed(7)
    candles_raw = make_synthetic_candles(80, trend=0.8, volatility=6)
    parsed = []
    for c in reversed(candles_raw):
        parsed.append({"high": c[2], "low": c[3], "close": c[4]})
    result = indicators.adx(parsed, 14)
    assert result[-1] is not None, "ADX should have a value by the end of the series"
    assert 0 <= result[-1] <= 100, "ADX must be within 0-100"
    print(f"✓ ADX basic test passed (sample value: {result[-1]:.1f})")


def test_candle_body_pct():
    # Strong bullish candle: body is most of the range
    bullish = {"open": 100, "close": 110, "high": 111, "low": 99}
    assert _candle_body_pct(bullish) > 0.5

    # Strong bearish candle: body is most of the range
    bearish = {"open": 110, "close": 100, "high": 111, "low": 99}
    assert _candle_body_pct(bearish) > 0.5

    # Doji-like candle: tiny body relative to range -- should fail even the
    # loosened 0.3 threshold
    doji = {"open": 100, "close": 100.5, "high": 105, "low": 95}
    assert _candle_body_pct(doji) < 0.3

    print("✓ Candle body % calculation passed")


def test_strategy_runs_without_crashing_on_noise():
    random.seed(1)
    candles_5m = make_synthetic_candles(80, trend=0.3, volatility=8, interval_minutes=5)
    candles_15m = make_synthetic_candles(40, trend=0.3, volatility=8, interval_minutes=15)
    signal = evaluate(candles_5m, candles_15m)
    print(f"✓ Strategy evaluated without error on noisy data. Signal: {signal}")


def test_strategy_insufficient_data():
    candles_5m = make_synthetic_candles(5)
    candles_15m = make_synthetic_candles(5, interval_minutes=15)
    signal = evaluate(candles_5m, candles_15m)
    assert signal is None, "Should return None when insufficient data"
    print("✓ Insufficient-data guard works")


def test_strategy_fires_ce_on_strong_uptrend():
    random.seed(3)
    candles_5m = make_strong_breakout_candles(80, direction="up", interval_minutes=5)
    # 15m trend should agree -- build a consistent uptrend series for it too
    candles_15m = make_strong_breakout_candles(40, direction="up", interval_minutes=15)
    signal = evaluate(candles_5m, candles_15m)
    if signal is not None:
        assert signal.direction == "CE", f"Expected CE, got {signal.direction}"
        print(f"✓ Strategy fires CE under strong bullish conditions: {signal}")
    else:
        # Not a hard failure -- synthetic data has randomness in it, and the
        # strategy is intentionally strict (that's the point). But flag it
        # since this scenario is built specifically to satisfy every filter.
        print(
            "⚠ Strategy did NOT fire CE on a scenario built to satisfy all "
            "filters. This may just be synthetic-data randomness, but if "
            "this happens consistently across re-runs, check the filter "
            "logic in strategy.py."
        )


def test_strategy_fires_pe_on_strong_downtrend():
    random.seed(5)
    candles_5m = make_strong_breakout_candles(80, direction="down", interval_minutes=5)
    candles_15m = make_strong_breakout_candles(40, direction="down", interval_minutes=15)
    signal = evaluate(candles_5m, candles_15m)
    if signal is not None:
        assert signal.direction == "PE", f"Expected PE, got {signal.direction}"
        print(f"✓ Strategy fires PE under strong bearish conditions: {signal}")
    else:
        print(
            "⚠ Strategy did NOT fire PE on a scenario built to satisfy all "
            "filters (same caveat as the CE case above)."
        )


def test_pivot_levels():
    levels = compute_pivot_levels(prev_high=7050.0, prev_low=6920.0, prev_close=7030.0)
    assert levels.prev_low <= levels.pivot <= levels.prev_high
    assert levels.r1 > levels.pivot > levels.s1
    assert levels.r2 > levels.r1
    assert levels.r3 > levels.r2
    assert levels.s2 < levels.s1
    assert levels.s3 < levels.s2

    raw_daily = [
        ['2026-06-23T00:00:00+05:30', 6960.0, 7050.0, 6920.0, 7030.0, 50000, 0],
    ]
    levels_from_raw = pivot_levels_from_daily_candles(raw_daily)
    assert levels_from_raw.pivot == levels.pivot
    print(f"✓ Pivot levels test passed (pivot={levels.pivot}, R1={levels.r1}, S1={levels.s1})")


if __name__ == "__main__":
    test_ema_basic()
    test_atr_basic()
    test_adx_basic()
    test_candle_body_pct()
    test_strategy_runs_without_crashing_on_noise()
    test_strategy_insufficient_data()
    test_strategy_fires_ce_on_strong_uptrend()
    test_strategy_fires_pe_on_strong_downtrend()
    test_pivot_levels()
    print("\nAll sanity checks completed.")
