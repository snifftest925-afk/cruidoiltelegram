"""
Sends formatted signal messages to a Telegram chat via the Bot API.
"""

import logging

import requests

from . import config
from .strategy import Signal

logger = logging.getLogger(__name__)


def format_expiry_for_display(expiry_raw) -> str:
    """
    Convert an Upstox expiry field (epoch millis OR ISO date string,
    depending on file version) into a 'DD MON' display string, e.g. '25 JUN'.
    Falls back to the raw value if parsing fails, rather than crashing --
    a wrong-looking date in the message is better than a dead bot.
    """
    from datetime import datetime
    try:
        if isinstance(expiry_raw, (int, float)):
            dt = datetime.fromtimestamp(expiry_raw / 1000)
        else:
            dt = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00"))
        return dt.strftime("%d %b").upper()
    except (ValueError, TypeError):
        return str(expiry_raw)


def format_signal_message(signal: Signal, expiry_display: str) -> str:
    """
    Produces a message in the requested style, e.g.:

        CRUDEOIL 7000 CE 25 JUN
        BUY @ ___   (fill from live option LTP -- not available via API for MCX)
        Futures ref: 6985.00
        TP1 = 7050
        TP2 = 7110
        SL  = 6940
        ADX = 31.2

    NOTE: the option premium ("@384" style price) is intentionally left
    blank. Upstox's API does not provide a live MCX options chain, so this
    bot cannot fetch that number -- check your broker app for the live
    CE/PE LTP at the suggested strike before placing the trade.
    """
    lines = [
        f"🛢️ CRUDEOIL {signal.strike} {signal.direction} {expiry_display}",
        "BUY @ ___  (check live option LTP on your broker app)",
        "",
        f"Futures ref price: {signal.futures_price}",
        f"TP1 = {signal.tp1}",
        f"TP2 = {signal.tp2}",
        f"SL  = {signal.sl}",
        f"ADX = {signal.adx_value}",
        f"15m trend confirms: {'Yes' if signal.higher_tf_agrees else 'No (5m-only signal)'}",
        "",
        f"Conditions met: {signal.reason}",
        "",
        "Manage SL manually (e.g. move to cost once TP1 is hit) -- this bot",
        "does not track your open position.",
        "",
        "⚠️ Auto-generated signal. Not financial advice. Verify before trading.",
    ]
    return "\n".join(lines)


def send_telegram_message(text: str) -> bool:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram message sent successfully.")
        return True
    except requests.RequestException as e:
        logger.error("Failed to send Telegram message: %s", e)
        return False
