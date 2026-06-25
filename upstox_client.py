"""
Thin client around the Upstox V3 historical/intraday candle APIs.
"""

import logging
from datetime import datetime, timedelta

import requests

from . import config

logger = logging.getLogger(__name__)


class UpstoxClient:
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
        })

    def get_intraday_candles(self, instrument_key: str, interval_minutes: int) -> list[list]:
        """
        Fetch today's intraday candles at the given minute interval using the
        V3 API. Returns a list of [timestamp, open, high, low, close, volume, oi],
        most recent first (this is how Upstox returns it).
        """
        url = (
            f"{config.UPSTOX_BASE_URL}/v3/historical-candle/intraday/"
            f"{instrument_key}/minutes/{interval_minutes}"
        )
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("data", {}).get("candles", [])

    def get_recent_daily_candles(self, instrument_key: str, days_back: int = 5) -> list[list]:
        """Fallback / supplementary daily candles, e.g. for context."""
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        url = (
            f"{config.UPSTOX_BASE_URL}/v3/historical-candle/"
            f"{instrument_key}/days/1/{to_date}/{from_date}"
        )
        resp = self.session.get(url, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
        return payload.get("data", {}).get("candles", [])
