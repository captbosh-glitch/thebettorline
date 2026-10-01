#!/usr/bin/env python3
"""
One-time sanity check: confirms the league_id values in data/config.json
are real SportsGameOdds leagueIDs. NFL, NBA, and MLB are already confirmed
against the published docs; NCAAF, NCAAB, and NHL follow the same naming
convention but hadn't been seen in a live response when this was written.

Run once after you have an API key:
    SPORTSGAMEODDS_API_KEY=xxx python3 scripts/verify_leagues.py

If it reports a mismatch, fix the league_id in data/config.json (no other
code changes needed) and re-run.
"""
from __future__ import annotations

import os
import sys

import common
from sgo_client import SportsGameOddsClient


def main() -> int:
    api_key = os.environ.get("SPORTSGAMEODDS_API_KEY", "")
    client = SportsGameOddsClient(api_key)
    config = common.load_config()

    leagues = client.get_leagues()
    live_ids = {lg.get("leagueID") or lg.get("id"): lg for lg in leagues}

    print(f"API returned {len(live_ids)} leagues.\n")
    ok = True
    for configured in config["leagues"]:
        lid = configured["league_id"]
        if lid in live_ids:
            print(f"  OK    {lid}")
        else:
            ok = False
            close = [k for k in live_ids if k and lid.lower() in k.lower()]
            print(f"  WRONG {lid}  -- not found. Did you mean: {close or 'no close match -- see full list below'}")

    if not ok:
        print("\nFull league list from the API:")
        for lid, lg in sorted(live_ids.items(), key=lambda kv: kv[0] or ""):
            print(f"  {lid}  (sportID={lg.get('sportID')})")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
