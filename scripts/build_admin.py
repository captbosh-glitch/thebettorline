#!/usr/bin/env python3
"""
Renders admin/index.html from data/usage.json, data/status.json,
data/pull_log.json, and data/config.json. Run after every pull (the
workflow does this automatically) so the admin page always reflects the
latest run.
"""
from __future__ import annotations

import html
import sys
from datetime import datetime, timezone

import common

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>thebettorline admin</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 920px; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }}
  h1 {{ font-size: 1.4rem; }}
  h2 {{ font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid #8884; padding-bottom: .25rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: .5rem; }}
  th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #8883; font-size: .92rem; }}
  .ok {{ color: #1a7f37; }}
  .err {{ color: #c0392b; }}
  .banner {{ background: #c0392b; color: white; padding: .75rem 1rem; border-radius: 6px; margin: 1rem 0; }}
  .muted {{ color: #888; font-size: .85rem; }}
  .pull-now {{ padding: .5rem 1rem; font-size: 1rem; cursor: pointer; }}
  code {{ font-size: .85em; }}
</style>
</head>
<body>
<h1>thebettorline &mdash; data layer admin</h1>
<p class="muted">Generated {generated_at} UTC</p>

{warning_banner}

<h2>API usage</h2>
<table>
  <tr><th>Today</th><th>This week</th><th>This month</th><th>Monthly limit</th><th>% of limit</th></tr>
  <tr><td>{today}</td><td>{this_week}</td><td>{this_month}</td><td>{monthly_limit}</td><td>{pct_of_limit}%</td></tr>
</table>

<h2>League status</h2>
<table>
  <tr><th>League</th><th>Last successful pull</th><th>Last error</th></tr>
  {status_rows}
</table>

<h2>Pull now</h2>
<p>
  <button class="pull-now" onclick="pullNow()">Pull now</button>
  <span id="pull-now-result" class="muted"></span>
</p>
<p class="muted">
  Needs <code>worker/pull-now-worker.js</code> deployed and its URL set below.
  See the README for setup. Until then, trigger a manual pull from the
  repo's Actions tab (workflow_dispatch) instead.
</p>
<script>
  const WORKER_URL = ""; // e.g. "https://thebettorline-pull-now.yourname.workers.dev"
  async function pullNow() {{
    const resultEl = document.getElementById('pull-now-result');
    if (!WORKER_URL) {{
      resultEl.textContent = "Worker URL not configured -- see README.";
      return;
    }}
    const secret = sessionStorage.getItem('tbl_admin_secret') || prompt("Admin secret:");
    if (!secret) return;
    sessionStorage.setItem('tbl_admin_secret', secret);
    resultEl.textContent = "Triggering...";
    try {{
      const resp = await fetch(WORKER_URL, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', 'X-Admin-Secret': secret }},
        body: JSON.stringify({{ note: 'manual pull from admin page' }})
      }});
      resultEl.textContent = resp.ok ? "Triggered -- check back in a minute." : `Failed (${{resp.status}})`;
    }} catch (e) {{
      resultEl.textContent = "Failed to reach worker.";
    }}
  }}
</script>

<h2>Recent pulls</h2>
<table>
  <tr><th>Time (UTC)</th><th>Trigger</th><th>Leagues</th><th>Snapshot rows</th><th>API calls</th><th>Errors</th></tr>
  {pull_rows}
</table>

</body>
</html>
"""


def fmt_status_row(league_id: str, status: dict) -> str:
    success = status.get("last_success_at", "never")
    error = status.get("last_error")
    error_cell = f'<span class="err">{html.escape(error)}</span>' if error else '<span class="ok">none</span>'
    return f"<tr><td>{html.escape(league_id)}</td><td>{html.escape(success)}</td><td>{error_cell}</td></tr>"


def fmt_pull_row(entry: dict) -> str:
    leagues = ", ".join(r["league_id"] for r in entry.get("leagues", []))
    rows = sum(r.get("snapshot_rows", 0) for r in entry.get("leagues", []))
    errors = len(entry.get("errors", []))
    err_cell = f'<span class="err">{errors}</span>' if errors else "0"
    return (f"<tr><td>{html.escape(entry.get('ts',''))}</td>"
            f"<td>{html.escape(entry.get('trigger',''))}</td>"
            f"<td>{html.escape(leagues)}</td><td>{rows}</td>"
            f"<td>{entry.get('api_calls_used', 0)}</td><td>{err_cell}</td></tr>")


def main() -> int:
    config = common.load_config()
    usage = common.usage_summary(config)
    status = common.read_json(common.STATUS_PATH, {})
    pulls = common.read_json(common.PULL_LOG_PATH, [])

    warning = ""
    if usage["over_80_pct"]:
        warning = (f'<div class="banner">Warning: {usage["pct_of_limit"]}% of this month\'s '
                   f'{usage["monthly_limit"]}-call budget used.</div>')

    status_rows = "\n  ".join(fmt_status_row(lg["league_id"], status.get(lg["league_id"], {}))
                               for lg in config["leagues"])
    pull_rows = "\n  ".join(fmt_pull_row(e) for e in reversed(pulls[-30:])) or "<tr><td colspan=6>No pulls logged yet.</td></tr>"

    out = TEMPLATE.format(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        warning_banner=warning,
        today=usage["today"], this_week=usage["this_week"], this_month=usage["this_month"],
        monthly_limit=usage["monthly_limit"], pct_of_limit=usage["pct_of_limit"],
        status_rows=status_rows, pull_rows=pull_rows,
    )

    out_path = common.REPO_ROOT / "admin" / "index.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out, encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
