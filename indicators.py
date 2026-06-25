"""
Plain-Python technical indicator calculations (no pandas/numpy dependency,
keeps the bot lightweight to deploy on Railway's free tier).

All functions expect candles as a list of dicts with keys:
    timestamp, open, high, low, close, volume
ordered OLDEST FIRST (chronological). Upstox returns newest-first, so the
caller is responsible for reversing before passing in here -- see
strategy.py where this conversion happens once, in one place.
"""


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average. Returns a list same length as input;
    first (period - 1) entries are None since EMA needs a seed."""
    if len(values) < period:
        return [None] * len(values)

    result: list[float | None] = [None] * (period - 1)
    multiplier = 2 / (period + 1)

    seed = sum(values[:period]) / period
    result.append(seed)

    prev = seed
    for price in values[period:]:
        current = (price - prev) * multiplier + prev
        result.append(current)
        prev = current

    return result


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(
        high - low,
        abs(high - prev_close),
        abs(low - prev_close),
    )


def atr(candles: list[dict], period: int) -> list[float | None]:
    """Average True Range (Wilder's smoothing). candles must be oldest-first."""
    if len(candles) < period + 1:
        return [None] * len(candles)

    trs: list[float] = [None]  # first candle has no prev_close
    for i in range(1, len(candles)):
        tr = true_range(candles[i]["high"], candles[i]["low"], candles[i - 1]["close"])
        trs.append(tr)

    result: list[float | None] = [None] * period
    seed = sum(trs[1:period + 1]) / period
    result.append(seed)

    prev_atr = seed
    for i in range(period + 1, len(trs)):
        current_atr = (prev_atr * (period - 1) + trs[i]) / period
        result.append(current_atr)
        prev_atr = current_atr

    return result


def rolling_high(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    for i in range(len(values)):
        if i < window:
            result.append(None)
        else:
            result.append(max(values[i - window:i]))
    return result


def rolling_low(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    for i in range(len(values)):
        if i < window:
            result.append(None)
        else:
            result.append(min(values[i - window:i]))
    return result


def rolling_avg(values: list[float], window: int) -> list[float | None]:
    """Simple moving average over the trailing `window` candles (excludes
    the current candle -- i.e. average of the PRIOR window candles, so
    'current volume vs AvgVolume20' compares against a window that doesn't
    include the breakout candle itself)."""
    result: list[float | None] = []
    for i in range(len(values)):
        if i < window:
            result.append(None)
        else:
            result.append(sum(values[i - window:i]) / window)
    return result


def _wilder_smooth(values: list[float], period: int) -> list[float | None]:
    """
    Generic Wilder smoothing: first value is a simple average of the first
    `period` values, then each subsequent value is
    (prev * (period - 1) + current) / period.
    Used for smoothing +DM, -DM, and TR in the ADX calculation.
    """
    if len(values) < period:
        return [None] * len(values)

    result: list[float | None] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    result.append(seed)

    prev = seed
    for v in values[period:]:
        current = (prev * (period - 1) + v) / period
        result.append(current)
        prev = current

    return result


def adx(candles: list[dict], period: int = 14) -> list[float | None]:
    """
    Average Directional Index (Wilder). candles must be oldest-first.

    Standard formula:
      +DM = current high - previous high (if positive AND > -DM, else 0)
      -DM = previous low - current low (if positive AND > +DM, else 0)
      TR  = true range (see true_range())
      Smooth +DM, -DM, TR over `period` using Wilder's smoothing.
      +DI = 100 * smoothed(+DM) / smoothed(TR)
      -DI = 100 * smoothed(-DM) / smoothed(TR)
      DX  = 100 * |+DI - -DI| / (+DI + -DI)
      ADX = Wilder-smoothed DX over `period`

    Returns a list the same length as `candles`; entries are None until
    enough data has accumulated to produce a real value (this needs roughly
    2x the period due to the double smoothing step, same as any ADX impl).
    """
    n = len(candles)
    if n < period * 2:
        return [None] * n

    plus_dm = [0.0]
    minus_dm = [0.0]
    trs = [0.0]

    for i in range(1, n):
        up_move = candles[i]["high"] - candles[i - 1]["high"]
        down_move = candles[i - 1]["low"] - candles[i]["low"]

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)

        trs.append(true_range(candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]))

    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)
    smoothed_tr = _wilder_smooth(trs, period)

    dx: list[float | None] = [None] * n
    for i in range(n):
        if smoothed_tr[i] is None or smoothed_tr[i] == 0:
            continue
        plus_di = 100 * smoothed_plus_dm[i] / smoothed_tr[i]
        minus_di = 100 * smoothed_minus_dm[i] / smoothed_tr[i]
        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx[i] = 0.0
        else:
            dx[i] = 100 * abs(plus_di - minus_di) / di_sum

    # Second smoothing pass: ADX is the Wilder-smoothed DX. We can't reuse
    # _wilder_smooth directly since dx has leading Nones -- smooth only the
    # non-None tail, then pad the front to keep list length aligned.
    first_valid = next((i for i, v in enumerate(dx) if v is not None), None)
    if first_valid is None:
        return [None] * n

    dx_tail = dx[first_valid:]
    adx_tail = _wilder_smooth(dx_tail, period)
    return [None] * first_valid + adx_tail
