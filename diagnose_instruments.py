"""
Standalone diagnostic: downloads the MCX instrument file and prints out
everything that looks CRUDEOIL-related, with its raw field values, so we
can see exactly what Upstox is actually sending (field names, casing,
segment/instrument_type values, expiry format) instead of guessing.

Run this directly wherever the bot runs (Railway shell, or locally if your
network allows reaching assets.upstox.com):

    python -m bot.diagnose_instruments
"""

import gzip
import json
from io import BytesIO

import requests

from . import config


def main():
    print(f"Fetching {config.MCX_INSTRUMENTS_URL} ...")
    resp = requests.get(config.MCX_INSTRUMENTS_URL, timeout=30)
    resp.raise_for_status()
    with gzip.open(BytesIO(resp.content)) as f:
        data = json.load(f)

    print(f"Total MCX instruments in file: {len(data)}")
    print(f"Sample of first instrument's keys: {list(data[0].keys())}")
    print()

    matches = []
    for inst in data:
        haystack = " ".join(
            str(inst.get(field, ""))
            for field in ("name", "trading_symbol", "short_name", "asset_symbol")
        ).upper().replace(" ", "")
        if "CRUDEOIL" in haystack:
            matches.append(inst)

    print(f"Entries matching 'CRUDEOIL' (loosely): {len(matches)}")
    print()

    if not matches:
        print("No CRUDEOIL matches at all. Printing 5 random sample instruments")
        print("from the file instead, so we can see the real field structure:")
        for inst in data[:5]:
            print(json.dumps(inst, indent=2, default=str))
            print("---")
        return

    # Show distinct segment/instrument_type combos seen among matches, plus
    # full raw dump of each -- this is the part that tells us what filter
    # to actually use in instruments.py
    seen_combos = set()
    for inst in matches:
        combo = (inst.get("segment"), inst.get("instrument_type"))
        seen_combos.add(combo)

    print(f"Distinct (segment, instrument_type) combos among CRUDEOIL matches: {seen_combos}")
    print()
    print("Full raw entries:")
    for inst in matches:
        print(json.dumps(inst, indent=2, default=str))
        print("---")


if __name__ == "__main__":
    main()
