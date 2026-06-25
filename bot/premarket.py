"""
Pre-market analysis: computes classic pivot-point support/resistance levels
from the most recent completed daily candle, and formats a short Telegram
message. Sent once each morning before market open.

This also doubles as a "the bot is alive" heartbeat -- if you get this
message every morning, you know the scheduler, Upstox auth, and Telegram
connection are all working, without needing an actual trade signal to fire.

Formula used (classic/standard pivot points -- the most widely referenced
version): https://www.tradingview.com/support/solutions/43000521824-pivot-points-standard/
    P  = (High + Low + Close) / 3
    R1 = 2*P - Low        S1 = 2*P - High
    R2 = P + (High - Low) S2 = P - (High - Low)
    R3 = High + 2*(P - Low)   S3 = Low - 2*(High - P)
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PivotLevels:
    pivot: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float
    prev_high: float
    prev_low: float
    prev_close: float


def compute_pivot_levels(prev_high: float, prev_low: float, prev_close: float) -> PivotLevels:
    pivot = (prev_high + prev_low + prev_close) / 3
    r1 = 2 * pivot - prev_low
    s1 = 2 * pivot - prev_high
    r2 = pivot + (prev_high - prev_low)
    s2 = pivot - (prev_high - prev_low)
    r3 = prev_high + 2 * (pivot - prev_low)
    s3 = prev_low - 2 * (prev_high - pivot)

    return PivotLevels(
        pivot=round(pivot, 2),
        r1=round(r1, 2),
        r2=round(r2, 2),
        r3=round(r3, 2),
        s1=round(s1, 2),
        s2=round(s2, 2),
        s3=round(s3, 2),
        prev_high=prev_high,
        prev_low=prev_low,
        prev_close=prev_close,
    )


def pivot_levels_from_daily_candles(raw_daily_candles: list[list]) -> PivotLevels | None:
    """
    raw_daily_candles: Upstox's raw candle arrays, newest-first.
    [timestamp, open, high, low, close, volume, oi]
    Uses the most recent COMPLETED day (index 0, since today's candle won't
    exist yet before market open).
    """
    if not raw_daily_candles:
        logger.warning("No daily candles available for pre-market pivot calc.")
        return None

    latest = raw_daily_candles[0]
    _, _open, high, low, close = latest[0], latest[1], latest[2], latest[3], latest[4]
    return compute_pivot_levels(high, low, close)


def format_premarket_message(levels: PivotLevels, expiry_display: str) -> str:
    bias = "Bullish above pivot" if levels.prev_close > levels.pivot else "Bearish below pivot"

    lines = [
        "🌅 CRUDEOIL Pre-Market Levels",
        f"(based on prior session: H {levels.prev_high} / L {levels.prev_low} / C {levels.prev_close})",
        "",
        f"R3 = {levels.r3}",
        f"R2 = {levels.r2}",
        f"R1 = {levels.r1}",
        f"Pivot = {levels.pivot}",
        f"S1 = {levels.s1}",
        f"S2 = {levels.s2}",
        f"S3 = {levels.s3}",
        "",
        f"Bias: {bias}",
        "",
        f"Bot is live and tracking {expiry_display} expiry. Intraday signals begin after market open once enough candles build up.",
    ]
    return "\n".join(lines)
