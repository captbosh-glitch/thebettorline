"""
Local smoke test (not part of the shipped pipeline) -- exercises
fetch_odds.pull_league() and grade_yesterdays_games() against a mocked
SportsGameOddsClient so we can sanity check the whole pull/consensus/
snapshot/grading flow without a real API key or network access.

Run: python3 scripts/test_pull_smoke.py
"""
import shutil
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

import common
import fetch_odds
from sgo_client import odd_id

TEST_DATA_DIR = Path("/tmp/tbl_smoke_data")


class FakeClient:
    def __init__(self, upcoming_events, finalized_events):
        self._upcoming = upcoming_events
        self._finalized = finalized_events
        self.calls = 0

    def get_events(self, *, league_id, starts_after=None, starts_before=None,
                    finalized=None, odds_available=None, include_open_close_odds=False, limit=100):
        self.calls += 1
        return self._finalized if finalized else self._upcoming


def make_upcoming_event():
    def book(home_spread, home_price, away_spread, away_price):
        return {home_spread: {}}

    return {
        "eventID": "evt1",
        "teams": {"home": "Jets", "away": "Bills"},
        "status": {"startsAt": "2026-10-01T17:00:00Z"},
        "odds": {
            odd_id("home", "sp", "home"): {
                "byBookmaker": {
                    "draftkings": {"spread": "-3.5", "odds": "-110", "available": True},
                    "fanduel": {"spread": "-3.0", "odds": "-108", "available": True},
                    "caesars": {"spread": "-3.5", "odds": "-112", "available": True},
                }
            },
            odd_id("away", "sp", "away"): {
                "byBookmaker": {
                    "draftkings": {"spread": "+3.5", "odds": "-110", "available": True},
                    "fanduel": {"spread": "+3.0", "odds": "-112", "available": True},
                    "caesars": {"spread": "+3.5", "odds": "-108", "available": True},
                }
            },
            odd_id("all", "ou", "over"): {
                "byBookmaker": {
                    "draftkings": {"overUnder": "44.5", "odds": "-110", "available": True},
                    "fanduel": {"overUnder": "45.0", "odds": "-110", "available": True},
                    "caesars": {"overUnder": "44.5", "odds": "-105", "available": True},
                }
            },
            odd_id("home", "ml", "home"): {
                "byBookmaker": {
                    "draftkings": {"odds": "-180", "available": True},
                    "fanduel": {"odds": "-175", "available": True},
                    "caesars": {"odds": "-185", "available": True},
                }
            },
            odd_id("away", "ml", "away"): {
                "byBookmaker": {
                    "draftkings": {"odds": "+155", "available": True},
                    "fanduel": {"odds": "+150", "available": True},
                    "caesars": {"odds": "+160", "available": True},
                }
            },
        },
    }


def make_finalized_event():
    return {
        "eventID": "evt0",
        "teams": {"home": "Jets", "away": "Bills"},
        "results": {"game": {"home": {"points": 24}, "away": {"points": 20}}},
        "odds": {
            odd_id("home", "sp", "home"): {"closeFairSpread": "-2.5"},
            odd_id("all", "ou", "over"): {"closeFairOverUnder": "43.5"},
            odd_id("home", "ml", "home"): {"closeFairOdds": "-150"},
            odd_id("away", "ml", "away"): {"closeFairOdds": "+130"},
        },
    }


def main():
    if TEST_DATA_DIR.exists():
        shutil.rmtree(TEST_DATA_DIR)
    common.DATA_DIR = TEST_DATA_DIR
    common.GAMES_DIR = TEST_DATA_DIR / "games"
    common.SNAPSHOTS_DIR = TEST_DATA_DIR / "snapshots"
    common.KEYNUMBERS_DIR = TEST_DATA_DIR / "keynumbers"
    common.RESULTS_DIR = TEST_DATA_DIR / "results"
    common.USAGE_PATH = TEST_DATA_DIR / "usage.json"
    common.PULL_LOG_PATH = TEST_DATA_DIR / "pull_log.json"
    common.STATUS_PATH = TEST_DATA_DIR / "status.json"
    for d in (common.GAMES_DIR, common.SNAPSHOTS_DIR, common.KEYNUMBERS_DIR, common.RESULTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    config = {
        "leagues": [{"league_id": "NFL", "sport_id": "FOOTBALL", "enabled": True}],
        "markets": ["sp", "ou", "ml"],
        "opener_min_books": 3,
        "pull_window_hours": 36,
    }
    ts = "2026-10-01T13:00:00Z"

    client = FakeClient([make_upcoming_event()], [make_finalized_event()])
    result = fetch_odds.pull_league(client, config, config["leagues"][0], ts, is_morning=True)
    print("pull_league result:", result)

    kn = common.read_json(common.KEYNUMBERS_DIR / "NFL.json", {})
    sp = kn["evt1"]["sp"]
    ou = kn["evt1"]["ou"]
    ml = kn["evt1"]["ml"]
    print("sp opener/current:", sp["opener"], sp["current"])
    print("ou opener/current:", ou["opener"], ou["current"])
    print("ml opener/current:", ml["opener"], ml["current"])

    assert sp["opener"]["value"] == -3.5, "expected median spread -3.5"
    assert ou["opener"]["value"] == 44.5, "expected median total 44.5"
    assert 0.0 < ml["opener"]["value"] < 1.0, "expected a no-vig probability"

    snap_file = common.SNAPSHOTS_DIR / "NFL.jsonl"
    lines = snap_file.read_text().strip().splitlines()
    print(f"snapshot rows written: {len(lines)} (expect 9 = 3 books x 3 markets)")
    assert len(lines) == 9

    # Second pull with identical odds should produce zero new snapshot rows.
    result2 = fetch_odds.pull_league(client, config, config["leagues"][0], "2026-10-01T13:10:00Z", is_morning=False)
    print("second pull result (should be 0 new rows):", result2)
    assert result2["snapshot_rows"] == 0

    results = common.read_json(common.RESULTS_DIR / "NFL.json", {})
    print("graded results:", results.get("evt0"))
    assert results["evt0"]["grading"]["sp"] == "home"  # Jets won by 4, closing spread -2.5 -> covered
    assert results["evt0"]["grading"]["ou"] == "over"  # 44 total > 43.5
    assert results["evt0"]["grading"]["ml"] == "home"

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
