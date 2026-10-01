# thebettorline.com -- odds data layer

Prompt 1 of the launch sequence: pulls full-game spread/total/moneyline
odds from the [SportsGameOdds API](https://sportsgameodds.com/docs) for
NFL, NCAAF, NBA, NCAAB, NHL, and MLB, tracks opener/current/close/consensus
per game and market, grades completed games, and gives you an admin page
for usage and pull health. No front page / Top 10 / Who Was Right yet --
that's Prompts 2+, once this has been collecting data for a week or two.

Same architecture as RTYB: a static site on GitHub Pages, a GitHub Actions
workflow on a schedule that pulls data, computes everything, and commits
the results. The API key never leaves GitHub Actions -- it's a repo secret,
read only by the workflow, never shipped to the browser.

## One-time setup

1. **Create the GitHub repo** (e.g. `captbosh-glitch/thebettorline`), push
   everything in this folder to it, and turn on **Pages: Deploy from a
   branch** (`main` / root) in repo Settings, same as RTYB.

2. **Add the repo secret** `SPORTSGAMEODDS_API_KEY` (Settings -> Secrets and
   variables -> Actions). Get a key at sportsgameodds.com -- the free
   "Amateur" tier is fine to start.

3. **League IDs and the final-score schema are confirmed.** `NFL`, `NCAAF`,
   `NBA`, `NCAAB`, `NHL`, and `MLB` have all been checked against a live
   `/leagues` response, and `extract_final_score()` has been verified
   against a real finalized game (Rams 28, Ravens 6, Week 3 2026) -- final
   scores live at `results.game.<home|away>.points`, and the closing-line
   fields (`closeFairSpread`, `closeFairOverUnder`, `closeFairOdds`, etc.)
   match exactly. Nothing to do here; this is just a record of what was
   verified and when, in case SportsGameOdds changes their schema later.
   If you ever do want to re-check it (a new sport, a schema change),
   `scripts/verify_leagues.py` and `scripts/inspect_api.py --league X` are
   still there for it.

   One thing *to* watch: a response on the amateur tier can come back with
   a `"notice"` field saying bookmaker odds are missing ("Upgrade your API
   key to access all data from this query") -- real games had 5-6 books
   per market in testing, comfortably above the `opener_min_books: 3`
   threshold, but it's worth keeping an eye on the admin page for games
   that never lock in an Opener.

4. **Mind the real rate limit.** The amateur tier allows **10 requests per
   minute** and bills monthly on **entities returned** (events/odds rows),
   not request count -- confirmed at 2,500 entities/month, with requests
   themselves unlimited. `data/config.json`'s `monthly_api_call_limit` is
   compared against your plan's real usage (pulled from
   `GET /account/usage` every run), not a self-counted tally. The
   production schedule (a few calls, 3x/day) is nowhere near the per-minute
   cap; it's really only hand-testing with `inspect_api.py`/
   `verify_leagues.py` back-to-back that can trip it -- space those out by
   a minute or so if you're poking at the API directly.

5. **(Optional, for the "Pull now" button) Deploy the Cloudflare Worker.**
   The admin page can't call the SportsGameOdds API or trigger a GitHub
   Actions run directly without exposing a credential, so a tiny Worker
   sits in between:

   ```
   cd worker
   npm install -g wrangler      # if you don't have it
   wrangler secret put GITHUB_TOKEN     # fine-grained PAT, "Contents: write" only, scoped to this repo
   wrangler secret put ADMIN_SECRET     # whatever password you want the button to ask for
   wrangler deploy
   ```

   Then edit the `WORKER_URL` line in `scripts/build_admin.py`'s template
   (search for `const WORKER_URL = ""`) to the deployed URL, and double
   check `REPO_OWNER`/`REPO_NAME` at the top of
   `worker/pull-now-worker.js` match your actual repo. Until this is set
   up, trigger a manual pull from the repo's **Actions** tab instead
   (`workflow_dispatch`) -- that still logs normally.

## How a pull works

- The workflow runs every 10 minutes. `scripts/fetch_odds.py` checks
  whether "now" (America/New_York, or whatever `data/config.json` says)
  is within `pull_window_minutes` of one of `pull_times` -- if not, it
  exits immediately and nothing is committed. This is what makes the pull
  schedule an admin setting instead of a cron edit.
- For each enabled league, it asks for events in the next
  `pull_window_hours`. A league with nothing in that window (off-season,
  bye week) comes back empty and nothing further is fetched for it -- the
  single "any games?" query doubles as the actual odds fetch when there
  are games, so an idle league costs nothing extra.
- `data/config.json` is the only file you should need to touch to change
  pull times, the lookahead window, which leagues are active, which
  markets are pulled, how many books define an opener, or the monthly API
  budget.
- The pull configured as `morning_pull_time` additionally fetches
  yesterday's finalized games with the API's own closing odds, locks in
  "Close" from that, and grades each side.

## Data layout

- `data/config.json` -- admin settings (see above).
- `data/games/<LEAGUE>.json` -- current/upcoming games for that league.
- `data/keynumbers/<LEAGUE>.json` -- per game/market: opener, current,
  close, consensus history, and movement.
- `data/snapshots/<LEAGUE>.jsonl` -- append-only log, one row per
  (game, market, sportsbook) whenever its line or price actually changed.
  `<LEAGUE>_last.json` next to it is just the diffing cache, not meant to
  be read directly.
- `data/results/<LEAGUE>.json` -- final scores and grading for completed
  games.
- `data/usage.json`, `data/pull_log.json`, `data/status.json` -- feed the
  admin page.
- `admin/index.html` -- rebuilt after every pull by `scripts/build_admin.py`.

## Checking it before moving to Prompt 2

- Trigger a manual pull (`workflow_dispatch` from the Actions tab) and
  confirm `data/games/<LEAGUE>.json` and `data/keynumbers/<LEAGUE>.json`
  show up for whichever leagues are in season.
- Open `/admin/` on the published site and confirm usage numbers and
  per-league status look right.
- After the next morning pull following a game, check
  `data/results/<LEAGUE>.json` for that game and confirm `grading` is
  populated with real win/cover/over-under values.
