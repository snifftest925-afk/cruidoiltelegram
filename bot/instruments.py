"""
Resolves the current-month MCX Crude Oil futures instrument_key by downloading
and filtering Upstox's instrument master file.

Why this exists: Upstox instrument keys for MCX futures are NOT stable strings
you can hardcode (e.g. just "CRUDEOIL") -- they're tied to a specific exchange
token per contract/expiry, and the active contract rolls over every month.
Hardcoding last month's key will silently break your bot. This module always
fetches the *current* file at startup so it stays correct without you having
to edit code every expiry.
"""

import gzip
import json
import logging
from datetime import datetime
from io import BytesIO

import requests

from . import config

logger = logging.getLogger(__name__)


def fetch_mcx_instruments() -> list[dict]:
    """Download and parse the MCX instrument master file."""
    resp = requests.get(config.MCX_INSTRUMENTS_URL, timeout=30)
    resp.raise_for_status()
    with gzip.open(BytesIO(resp.content)) as f:
        data = json.load(f)
    return data


def _looks_like_crudeoil(inst: dict) -> bool:
    """
    Loose match across whatever name-ish fields are present. Upstox's file
    format/casing has shifted between versions (seen: 'CRUDE OIL' with a
    space, lowercase segment values, etc.), so we normalize aggressively
    instead of requiring an exact string.
    """
    haystack = " ".join(
        str(inst.get(field, ""))
        for field in ("name", "trading_symbol", "short_name", "asset_symbol")
    ).upper()
    haystack = haystack.replace(" ", "").replace("_", "").replace("-", "")
    return "CRUDEOIL" in haystack


def _looks_like_mcx_future(inst: dict) -> bool:
    segment = str(inst.get("segment", "")).upper()
    instrument_type = str(inst.get("instrument_type", "")).upper()
    return "MCX" in segment and instrument_type in ("FUT", "FUTURE", "FUTCOM", "FUTCOM")


def find_current_crudeoil_future(instruments: list[dict]) -> dict:
    """
    Filter for CRUDEOIL futures contracts and return the one with the
    nearest (soonest, but not yet expired) expiry -- i.e. the current month
    contract that's actively traded.
    """
    candidates = []
    crudeoil_matches_seen = []  # for diagnostics if nothing qualifies
    now = datetime.now()

    for inst in instruments:
        if not _looks_like_crudeoil(inst):
            continue

        crudeoil_matches_seen.append(inst)

        if not _looks_like_mcx_future(inst):
            continue

        expiry_raw = inst.get("expiry")
        if not expiry_raw:
            continue

        # Upstox expiry is typically epoch millis or an ISO string depending
        # on file version -- handle both defensively.
        try:
            if isinstance(expiry_raw, (int, float)):
                expiry_dt = datetime.fromtimestamp(expiry_raw / 1000)
            else:
                expiry_dt = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue

        if expiry_dt < now:
            continue  # already expired, skip

        candidates.append((expiry_dt, inst))

    if not candidates:
        # Dump whatever CRUDEOIL-ish entries we DID find so the real field
        # values are visible in the logs instead of guessing blind again.
        if crudeoil_matches_seen:
            sample = crudeoil_matches_seen[:5]
            logger.error(
                "Found %d CRUDEOIL-ish entries but none matched the futures/expiry "
                "filter. Sample entries (raw): %s",
                len(crudeoil_matches_seen),
                json.dumps(sample, indent=2, default=str),
            )
        else:
            logger.error(
                "No entries containing 'CRUDEOIL' found at all in the MCX file. "
                "Total instruments in file: %d. Sample of first 3 entries: %s",
                len(instruments),
                json.dumps(instruments[:3], indent=2, default=str),
            )
        raise RuntimeError(
            "No active CRUDEOIL futures contract found in MCX instrument file. "
            "Check the logs just above this error for the raw entries Upstox "
            "actually returned -- the field names/values may differ from what "
            "this code expects. Also check https://community.upstox.com in "
            "case MCX trading/data has been disabled."
        )

    candidates.sort(key=lambda pair: pair[0])
    nearest_expiry, instrument = candidates[0]
    logger.info(
        "Resolved current CRUDEOIL future: %s (expiry %s, instrument_key %s)",
        instrument.get("trading_symbol"),
        nearest_expiry.date(),
        instrument.get("instrument_key"),
    )
    return instrument


def get_current_crudeoil_instrument_key() -> tuple[str, dict]:
    """Convenience wrapper: returns (instrument_key, full_instrument_dict)."""
    instruments = fetch_mcx_instruments()
    inst = find_current_crudeoil_future(instruments)
    return inst["instrument_key"], inst
