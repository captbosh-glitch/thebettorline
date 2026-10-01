"""
Shared helpers: config loading, paths, timezone, atomic JSON I/O, and the
usage/pull/error logs that back the admin page.

Nothing in here talks to the SportsGameOdds API -- see sgo_client.py for that.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"

GAMES_DIR = DATA_DIR / "games"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
KEYNUMBERS_DIR = DATA_DIR / "keynumbers"
RESULTS_DIR = DATA_DIR / "results"
USAGE_PATH = DATA_DIR / "usage.json"
PULL_LOG_PATH = DATA_DIR / "pull_log.json"
STATUS_PATH = DATA_DIR / "status.json"

for d in (GAMES_DIR, SNAPSHOTS_DIR, KEYNUMBERS_DIR, RESULTS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def tz(config: dict) -> ZoneInfo:
    return ZoneInfo(config.get("timezone", "America/New_York"))


def now_local(config: dict) -> datetime:
    return datetime.now(tz(config))


def enabled_leagues(config: dict) -> list[dict]:
    return [lg for lg in config["leagues"] if lg.get("enabled", True)]


# ---------------------------------------------------------------------------
# Atomic JSON read/write -- the GitHub Actions workflow commits these files,
# so a half-written file from a crashed run would be a real problem. Write to
# a temp file in the same directory, then os.replace (atomic on the same
# filesystem) over the real path.
# ---------------------------------------------------------------------------

def read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            # Shouldn't happen given atomic writes, but don't let a corrupt
            # file take down an entire pull.
            return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True))
            f.write("\n")


# ---------------------------------------------------------------------------
# Usage tracking.
#
# IMPORTANT: confirmed live against a real account (2026-09-30) -- the
# SportsGameOdds "amateur" tier's real monthly quota is on *entities*
# (roughly: events/odds-rows actually returned), not raw HTTP requests --
# per-month max-requests is reported as "unlimited". So rather than counting
# our own requests (which was measuring the wrong thing, and would
# under/over-estimate how close we are to the real cap), record_account_usage
# stores what GET /account/usage itself reports each pull, and usage_summary
# reads that back. Call record_account_usage(client.get_account_usage())
# once per pull run.
# ---------------------------------------------------------------------------

def record_account_usage(usage_payload: dict, when: datetime | None = None) -> None:
    """usage_payload is the dict returned by SportsGameOddsClient.get_account_usage()."""
    rate_limits = (usage_payload or {}).get("rateLimits", {})
    per_day = rate_limits.get("per-day", {})
    per_month = rate_limits.get("per-month", {})

    usage = read_json(USAGE_PATH, {"daily_entities": {}})
    usage.setdefault("daily_entities", {})
    day_key = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    # per-day current-entities is the provider's own running total for
    # today, not an increment -- store it as-is (overwrite, don't add).
    day_entities = per_day.get("current-entities")
    if day_entities is not None:
        usage["daily_entities"][day_key] = day_entities

    usage["month_current_entities"] = per_month.get("current-entities")
    usage["month_max_entities"] = per_month.get("max-entities")
    usage["last_checked_at"] = iso_now()
    write_json(USAGE_PATH, usage)


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def usage_summary(config: dict, as_of: datetime | None = None) -> dict:
    usage = read_json(USAGE_PATH, {"daily_entities": {}})
    daily = usage.get("daily_entities", {})
    local_now = as_of or now_local(config)
    today_key = local_now.strftime("%Y-%m-%d")
    week_start = local_now - timedelta(days=local_now.weekday())  # Monday

    today = daily.get(today_key, 0)
    this_week = sum(
        v for k, v in daily.items()
        if week_start.strftime("%Y-%m-%d") <= k <= local_now.strftime("%Y-%m-%d")
    )
    # The provider's own current-month total is authoritative (covers days
    # we might not have a local snapshot for); our config limit is still an
    # admin-settable budget, which may be lower than the plan's hard cap.
    this_month = usage.get("month_current_entities") or 0
    limit = config.get("monthly_api_call_limit") or usage.get("month_max_entities") or 0
    pct = (this_month / limit * 100) if limit else 0
    return {
        "today": today,
        "this_week": this_week,
        "this_month": this_month,
        "monthly_limit": limit,
        "pct_of_limit": round(pct, 1),
        "over_80_pct": pct >= 80,
        "last_checked_at": usage.get("last_checked_at"),
    }


# ---------------------------------------------------------------------------
# Pull log -- one entry per workflow run that actually pulled something.
# Used by the admin page's history and by the "manual pull" log requirement.
# ---------------------------------------------------------------------------

def log_pull(entry: dict, keep_last: int = 500) -> None:
    log = read_json(PULL_LOG_PATH, [])
    log.append(entry)
    log = log[-keep_last:]
    write_json(PULL_LOG_PATH, log)


# ---------------------------------------------------------------------------
# Status -- last successful pull per league, and the most recent error per
# league, surfaced directly on the admin page.
# ---------------------------------------------------------------------------

def update_status(league_id: str, *, success: bool, timestamp: str, error: str | None = None) -> None:
    status = read_json(STATUS_PATH, {})
    status.setdefault(league_id, {})
    if success:
        status[league_id]["last_success_at"] = timestamp
        status[league_id]["last_error"] = None
        status[league_id]["last_error_at"] = None
    else:
        status[league_id]["last_error"] = error
        status[league_id]["last_error_at"] = timestamp
    write_json(STATUS_PATH, status)
