"""
Minimal client for the SportsGameOdds API (https://sportsgameodds.com/docs).

Confirmed against the published docs:
  - base URL:  https://api.sportsgameodds.com/v2
  - auth:      header "X-Api-Key: <key>"
  - GET /events supports startsAfter/startsBefore, leagueID, sportID,
    finalized/started/ended/live/cancelled, oddsAvailable, oddID, bookmakerID,
    includeOpenCloseOdds, includeAltLines, limit, cursor.
  - oddID format: "{statID}-{statEntityID}-{periodID}-{betTypeID}-{sideID}",
    e.g. "points-home-game-sp-home". betTypeID is sp/ml/ou for full-game
    spread/moneyline/total; statEntityID is home/away/all.
  - each event's odds.<oddID> has fairOdds/bookOdds (current),
    openFairOdds/openBookOdds (opening), and -- with includeOpenCloseOdds --
    closeFairOdds/closeBookOdds, plus a byBookmaker dict keyed by bookmakerID
    with odds/spread/overUnder/lastUpdatedAt/available per book.

NOT yet confirmed against a live response (the docs excerpts didn't show a
full example): the exact shape of a finalized event's `results` object
(final score fields). See scripts/inspect_api.py -- run it once you have a
key, and extract_final_score() below is the one place to fix once we've seen
real output.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

BASE_URL = "https://api.sportsgameodds.com/v2"

FULL_GAME_PERIOD = "game"
STAT_ID = "points"


@dataclass
class ApiCallStats:
    """Tracks how many HTTP requests this process has made, so the caller
    can record it against the daily usage total regardless of how many
    leagues/pages were involved."""
    calls: int = 0


class SportsGameOddsClient:
    def __init__(self, api_key: str, stats: ApiCallStats | None = None, session: requests.Session | None = None):
        if not api_key:
            raise RuntimeError(
                "SPORTSGAMEODDS_API_KEY is not set. Keep it in GitHub Actions "
                "repo secrets -- never commit it or put it in front-end code."
            )
        self.api_key = api_key
        self.stats = stats or ApiCallStats()
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict) -> dict:
        url = f"{BASE_URL}{path}"
        headers = {"X-Api-Key": self.api_key}
        # Keep real values out of exceptions/logs -- the only thing errors
        # should ever reveal is which league/window failed, not the key.
        for attempt in range(1, 4):
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=30)
            except requests.RequestException as exc:
                if attempt == 3:
                    raise RuntimeError(f"Network error calling {path}: {exc}") from None
                time.sleep(2 * attempt)
                continue
            self.stats.calls += 1
            if resp.status_code == 429:
                if attempt == 3:
                    raise RuntimeError(f"Rate limited on {path} after retries")
                time.sleep(5 * attempt)
                continue
            if not resp.ok:
                # Truncate -- error bodies have occasionally echoed query
                # params back, and we'd rather under-share than leak a key.
                raise RuntimeError(f"{path} returned HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json()
        raise RuntimeError(f"Failed to call {path} after retries")

    def get_events(
        self,
        *,
        league_id: str,
        starts_after: str | None = None,
        starts_before: str | None = None,
        finalized: bool | None = None,
        odds_available: bool | None = None,
        include_open_close_odds: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Returns all events for one league matching the filters, paging
        through `cursor` automatically. One underlying HTTP call per page."""
        events: list[dict] = []
        cursor = None
        while True:
            params = {"leagueID": league_id, "limit": limit}
            if starts_after:
                params["startsAfter"] = starts_after
            if starts_before:
                params["startsBefore"] = starts_before
            if finalized is not None:
                params["finalized"] = str(finalized).lower()
            if odds_available is not None:
                params["oddsAvailable"] = str(odds_available).lower()
            if include_open_close_odds:
                params["includeOpenCloseOdds"] = "true"
            if cursor:
                params["cursor"] = cursor

            payload = self._get("/events", params)
            batch = payload.get("data", payload if isinstance(payload, list) else [])
            events.extend(batch)

            cursor = payload.get("nextCursor") if isinstance(payload, dict) else None
            if not cursor or not batch:
                break
        return events

    def get_leagues(self) -> list[dict]:
        payload = self._get("/leagues", {})
        return payload.get("data", payload if isinstance(payload, list) else [])


def odd_id(stat_entity: str, bet_type: str, side: str, period: str = FULL_GAME_PERIOD) -> str:
    return f"{STAT_ID}-{stat_entity}-{period}-{bet_type}-{side}"


def extract_final_score(event: dict) -> tuple[float, float] | None:
    """Best-effort extraction of (home_score, away_score) from a finalized
    event. UNCONFIRMED against a live response -- this is the one function
    to fix once scripts/inspect_api.py shows us real output for a completed
    game. Tries a few plausible shapes rather than assuming one, and returns
    None (never raises) if nothing matches, so a schema surprise shows up as
    a missing/ungraded game in the admin error log instead of a crashed run.
    """
    results = event.get("results") or {}

    # Shape A: results keyed like oddIDs, e.g. results["game"]["home"]["points"]
    try:
        game = results.get(FULL_GAME_PERIOD, {})
        home = game.get("home", {}).get(STAT_ID)
        away = game.get("away", {}).get(STAT_ID)
        if home is not None and away is not None:
            return float(home), float(away)
    except AttributeError:
        pass

    # Shape B: flat results dict, e.g. results["homePoints"] / results["awayPoints"]
    for home_key, away_key in (("homePoints", "awayPoints"), ("homeScore", "awayScore")):
        if home_key in results and away_key in results:
            return float(results[home_key]), float(results[away_key])

    # Shape C: scores live on the event itself, not under "results"
    for home_key, away_key in (("homeScore", "awayScore"), ("homePoints", "awayPoints")):
        if home_key in event and away_key in event:
            return float(event[home_key]), float(event[away_key])

    return None
