#!/usr/bin/env python3
"""
Main entry point for a pull. Run on a schedule (every 10 min) by the GitHub
Actions workflow, which lets this script -- not the cron table -- decide
whether "now" matches one of the admin-configured pull_times. That's what
makes pull_times, pull_window_hours, and the league list admin settings
instead of code changes: edit data/config.json and push, nothing in the
workflow or this script needs to change.

What a pull does, per enabled league:
  1. Ask the API for events in the next pull_window_hours. If a league has
     none, we're done with it -- that single call is also the "does this
     league have any games" check, so an off-season/off-day league costs
     nothing extra beyond the one query that comes back empty.
  2. For each event, pull spread/total/moneyline odds, compute the
     consensus (median) line, and update that game's opener/current/
     consensus-history key numbers.
  3. Diff against the last known per-book line/price and append a snapshot
     row only when something actually changed.

On the admin-configured morning pull only, also:
  4. Fetch yesterday's finalized games with the API's own closing odds
     (includeOpenCloseOdds) and final scores, lock in "Close" from the
     API's closing line, and grade each side (win/cover/over-under)
     against the closing consensus.

Usage:
  python3 scripts/fetch_odds.py --trigger schedule
  python3 scripts/fetch_odds.py --trigger manual --note "testing new book coverage"
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

import common
import odds_math
from sgo_client import ApiCallStats, SportsGameOddsClient, extract_final_score, odd_id

ISO = "%Y-%m-%dT%H:%M:%S%z"


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def matches_a_pull_time(config: dict, local_now: datetime) -> str | None:
    """Returns the configured pull_time string ("09:00" etc) that local_now
    falls within pull_window_minutes of, or None if it doesn't match any."""
    window = timedelta(minutes=config.get("pull_window_minutes", 20))
    for pt in config["pull_times"]:
        hh, mm = (int(x) for x in pt.split(":"))
        candidate = local_now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if abs(local_now - candidate) <= window:
            return pt
    return None


def best_consensus_line(line_rows: list[dict]) -> float | None:
    return odds_math.consensus_spread_or_total(
        [r["line"] for r in line_rows if r["available"] and r["line"] is not None]
    )


def rows_for_odd(event: dict, odd_key: str, line_field: str | None) -> list[dict]:
    """byBookmaker entries for one oddID -> [{book, line, price, available}]."""
    odd = (event.get("odds") or {}).get(odd_key) or {}
    out = []
    for book_id, book in (odd.get("byBookmaker") or {}).items():
        out.append({
            "book": book_id,
            "line": book.get(line_field) if line_field else None,
            "price": book.get("odds"),
            "available": bool(book.get("available", True)),
        })
    return out


def to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def process_market_sp(event: dict) -> dict:
    home_rows = rows_for_odd(event, odd_id("home", "sp", "home"), "spread")
    for r in home_rows:
        r["line"] = to_float(r["line"])
        r["price"] = to_float(r["price"])
    consensus = best_consensus_line(home_rows)
    return {"rows": home_rows, "consensus": consensus}


def process_market_ou(event: dict) -> dict:
    over_rows = rows_for_odd(event, odd_id("all", "ou", "over"), "overUnder")
    for r in over_rows:
        r["line"] = to_float(r["line"])
        r["price"] = to_float(r["price"])
    consensus = best_consensus_line(over_rows)
    return {"rows": over_rows, "consensus": consensus}


def process_market_ml(event: dict) -> dict:
    home_rows = rows_for_odd(event, odd_id("home", "ml", "home"), None)
    away_rows = rows_for_odd(event, odd_id("away", "ml", "away"), None)
    for r in home_rows + away_rows:
        r["price"] = to_float(r["price"])
    away_by_book = {r["book"]: r for r in away_rows}
    pairs = []
    for r in home_rows:
        away = away_by_book.get(r["book"])
        if r["available"] and away and away["available"] and r["price"] is not None and away["price"] is not None:
            pairs.append((r["price"], away["price"]))
    consensus_prob = odds_math.consensus_moneyline_prob(pairs)
    # For snapshotting we store each book's home-side price as "price" with
    # no "line" (moneylines don't have a point value).
    return {"rows": home_rows, "consensus": consensus_prob}


MARKET_PROCESSORS = {"sp": process_market_sp, "ou": process_market_ou, "ml": process_market_ml}


def update_keynumbers(kn: dict, event_id: str, market: str, consensus, ts: str, min_books: int, books_available: int) -> None:
    kn.setdefault(event_id, {})
    mkt = kn[event_id].setdefault(market, {
        "opener": None, "current": None, "close": None, "consensus_history": [],
    })
    if consensus is None:
        return
    if mkt["opener"] is None and books_available >= min_books:
        mkt["opener"] = {"value": consensus, "timestamp": ts, "books_count": books_available}
    mkt["current"] = {"value": consensus, "timestamp": ts}
    if mkt["close"] is None or mkt["close"].get("source") != "api_closing":
        mkt["close"] = {"value": consensus, "timestamp": ts, "source": "last_pulled"}
    mkt["consensus_history"].append({"timestamp": ts, "value": consensus})
    mkt["consensus_history"] = mkt["consensus_history"][-200:]
    opener_val = mkt["opener"]["value"] if mkt["opener"] else None
    if market == "ml":
        mkt["movement_vs_opener_pp"] = odds_math.prob_movement_pct_points(consensus, opener_val)
    else:
        mkt["movement_vs_opener_pts"] = odds_math.points_movement(consensus, opener_val)


def diff_and_snapshot(last_seen: dict, event_id: str, market: str, rows: list[dict], ts: str) -> list[dict]:
    """Returns new snapshot rows (only where line/price changed since the
    last pull) and mutates last_seen in place to the new values."""
    out = []
    bucket = last_seen.setdefault(event_id, {}).setdefault(market, {})
    for r in rows:
        key = r["book"]
        prev = bucket.get(key)
        cur = {"line": r["line"], "price": r["price"]}
        if prev != cur:
            out.append({
                "ts": ts, "event_id": event_id, "market": market,
                "book": key, "line": r["line"], "price": r["price"],
            })
            bucket[key] = cur
    return out


def pull_league(client: SportsGameOddsClient, config: dict, league: dict, ts: str, is_morning: bool) -> dict:
    league_id = league["league_id"]
    now = datetime.now(timezone.utc)
    window_hours = config.get("pull_window_hours", 36)
    events = client.get_events(
        league_id=league_id,
        starts_after=iso(now),
        starts_before=iso(now + timedelta(hours=window_hours)),
        odds_available=True,
    )

    if not events:
        return {"league_id": league_id, "games_found": 0, "snapshot_rows": 0}

    games_path = common.GAMES_DIR / f"{league_id}.json"
    kn_path = common.KEYNUMBERS_DIR / f"{league_id}.json"
    last_seen_path = common.SNAPSHOTS_DIR / f"{league_id}_last.json"
    snap_path = common.SNAPSHOTS_DIR / f"{league_id}.jsonl"

    games = common.read_json(games_path, {})
    kn = common.read_json(kn_path, {})
    last_seen = common.read_json(last_seen_path, {})
    new_rows: list[dict] = []
    min_books = config.get("opener_min_books", 3)

    for event in events:
        event_id = event.get("eventID") or event.get("id")
        if not event_id:
            continue
        games[event_id] = {
            "teams": event.get("teams"),
            "starts_at": event.get("status", {}).get("startsAt") or event.get("startsAt"),
            "status": event.get("status"),
            "league_id": league_id,
        }
        for market in config.get("markets", ["sp", "ml", "ou"]):
            processor = MARKET_PROCESSORS.get(market)
            if not processor:
                continue
            result = processor(event)
            books_available = sum(1 for r in result["rows"] if r["available"])
            update_keynumbers(kn, event_id, market, result["consensus"], ts, min_books, books_available)
            new_rows.extend(diff_and_snapshot(last_seen, event_id, market, result["rows"], ts))

    common.write_json(games_path, games)
    common.write_json(kn_path, kn)
    common.write_json(last_seen_path, last_seen)
    common.append_jsonl(snap_path, new_rows)

    result = {"league_id": league_id, "games_found": len(events), "snapshot_rows": len(new_rows)}

    if is_morning:
        result["grading"] = grade_yesterdays_games(client, config, league_id, ts)

    return result


def grade_yesterdays_games(client: SportsGameOddsClient, config: dict, league_id: str, ts: str) -> dict:
    now_utc = datetime.now(timezone.utc)
    starts_after = iso(now_utc - timedelta(hours=36))
    starts_before = iso(now_utc)
    finalized = client.get_events(
        league_id=league_id,
        starts_after=starts_after,
        starts_before=starts_before,
        finalized=True,
        include_open_close_odds=True,
    )

    kn_path = common.KEYNUMBERS_DIR / f"{league_id}.json"
    results_path = common.RESULTS_DIR / f"{league_id}.json"
    kn = common.read_json(kn_path, {})
    results = common.read_json(results_path, {})

    graded, missing_score, missing_line = 0, 0, 0

    for event in finalized:
        event_id = event.get("eventID") or event.get("id")
        if not event_id or event_id in results:
            continue

        score = extract_final_score(event)
        if score is None:
            missing_score += 1
            continue
        home_score, away_score = score

        closing = {}
        for market, side, line_field in (
            ("sp", "home", "spread"), ("ou", "all", "overUnder"),
        ):
            key = odd_id(side, market, "home" if market == "sp" else "over")
            odd = (event.get("odds") or {}).get(key) or {}
            value = odd.get("closeFairSpread") if market == "sp" else odd.get("closeFairOverUnder")
            if value is None:
                value = odd.get("closeBookSpread" if market == "sp" else "closeBookOverUnder")
            closing[market] = to_float(value)

        home_ml_close = (event.get("odds") or {}).get(odd_id("home", "ml", "home"), {}).get("closeFairOdds")
        away_ml_close = (event.get("odds") or {}).get(odd_id("away", "ml", "away"), {}).get("closeFairOdds")

        kn.setdefault(event_id, {})
        for market, value in closing.items():
            if value is not None:
                mkt = kn[event_id].setdefault(market, {"opener": None, "current": None, "close": None, "consensus_history": []})
                mkt["close"] = {"value": value, "timestamp": ts, "source": "api_closing"}

        grading = {}
        sp_close = closing.get("sp")
        if sp_close is not None:
            margin = home_score - away_score
            covered = margin + sp_close
            grading["sp"] = "home" if covered > 0 else ("away" if covered < 0 else "push")
        else:
            missing_line += 1

        ou_close = closing.get("ou")
        if ou_close is not None:
            total = home_score + away_score
            grading["ou"] = "over" if total > ou_close else ("under" if total < ou_close else "push")

        grading["ml"] = "home" if home_score > away_score else ("away" if away_score > home_score else "push")

        results[event_id] = {
            "home_score": home_score,
            "away_score": away_score,
            "closing_lines": closing,
            "closing_ml_fair_odds": {"home": home_ml_close, "away": away_ml_close},
            "grading": grading,
            "graded_at": ts,
        }
        graded += 1

    common.write_json(kn_path, kn)
    common.write_json(results_path, results)
    return {"graded": graded, "missing_score": missing_score, "missing_closing_line": missing_line}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger", choices=["schedule", "manual"], default="schedule")
    parser.add_argument("--note", default=None, help="Optional note for a manual pull (shown in the admin pull log).")
    args = parser.parse_args()

    config = common.load_config()
    local_now = common.now_local(config)
    ts = iso(datetime.now(timezone.utc))

    pull_time_label = matches_a_pull_time(config, local_now)
    if args.trigger == "schedule" and pull_time_label is None:
        print(f"[{ts}] No configured pull_time matches {local_now.isoformat()} -- skipping.")
        return 0

    is_morning = args.trigger == "manual" or pull_time_label == config.get("morning_pull_time")

    api_key = os.environ.get("SPORTSGAMEODDS_API_KEY", "")
    stats = ApiCallStats()
    client = SportsGameOddsClient(api_key, stats=stats)

    per_league_results = []
    errors = []
    for league in common.enabled_leagues(config):
        league_id = league["league_id"]
        try:
            result = pull_league(client, config, league, ts, is_morning)
            per_league_results.append(result)
            common.update_status(league_id, success=True, timestamp=ts)
        except Exception as exc:  # noqa: BLE001 -- one league's failure shouldn't sink the run
            err_text = f"{exc}"
            errors.append({"league_id": league_id, "error": err_text})
            common.update_status(league_id, success=False, timestamp=ts, error=err_text)
            traceback.print_exc()

    try:
        common.record_account_usage(client.get_account_usage())
    except Exception as exc:  # noqa: BLE001 -- usage reporting must never sink a pull
        print(f"[{ts}] Warning: couldn't refresh account usage: {exc}")

    common.log_pull({
        "ts": ts,
        "trigger": args.trigger,
        "pull_time_label": pull_time_label,
        "is_morning": is_morning,
        "note": args.note,
        "leagues": per_league_results,
        "api_calls_used": stats.calls,
        "errors": errors,
    })

    print(f"[{ts}] trigger={args.trigger} morning={is_morning} calls={stats.calls} "
          f"leagues={[r['league_id'] for r in per_league_results]} errors={len(errors)}")

    return 1 if errors and not per_league_results else 0


if __name__ == "__main__":
    sys.exit(main())
