#!/usr/bin/env python3
"""
Diagnostic, not part of the pull pipeline. Fetches one upcoming event and
(if one exists in the lookback window) one finalized event for a league, and
dumps the raw JSON to disk so we can confirm the exact shape of the
`results` object on a completed game -- the one piece of the schema the
public docs didn't show a full example of.

Run once after you have an API key:
    SPORTSGAMEODDS_API_KEY=xxx python3 scripts/inspect_api.py --league NFL

Then share the two printed file paths (or their content) so
sgo_client.extract_final_score() can be locked to the real field names
instead of its current best-effort guesses.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from sgo_client import SportsGameOddsClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default="NFL")
    parser.add_argument("--out-dir", default="/tmp")
    args = parser.parse_args()

    api_key = os.environ.get("SPORTSGAMEODDS_API_KEY", "")
    client = SportsGameOddsClient(api_key)
    now = datetime.now(timezone.utc)

    upcoming = client.get_events(
        league_id=args.league,
        starts_after=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        starts_before=(now + timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        odds_available=True,
        include_open_close_odds=True,
        limit=1,
        max_total=1,
    )
    finalized = client.get_events(
        league_id=args.league,
        starts_after=(now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        starts_before=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        finalized=True,
        include_open_close_odds=True,
        limit=1,
        max_total=1,
    )

    up_path = os.path.join(args.out_dir, f"sgo_sample_upcoming_{args.league}.json")
    fin_path = os.path.join(args.out_dir, f"sgo_sample_finalized_{args.league}.json")
    with open(up_path, "w") as f:
        json.dump(upcoming, f, indent=2)
    with open(fin_path, "w") as f:
        json.dump(finalized, f, indent=2)

    print(f"Upcoming sample ({len(upcoming)} event(s)):  {up_path}")
    print(f"Finalized sample ({len(finalized)} event(s)): {fin_path}")
    if not finalized:
        print(f"No finalized {args.league} game in the last 10 days -- try again "
              f"closer to a game, or pass --league for a sport currently in season.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
