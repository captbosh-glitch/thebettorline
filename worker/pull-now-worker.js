/**
 * Cloudflare Worker: the only thing standing between the admin page's
 * "Pull now" button and your GitHub token. The token and the admin secret
 * both live as Worker secrets (set with `wrangler secret put`, never in
 * this file, never in front-end code) -- the static admin page never sees
 * either one.
 *
 * Flow: admin page POSTs here with an X-Admin-Secret header -> this Worker
 * checks the secret -> fires a `repository_dispatch` event of type
 * `pull_now` against your repo -> GitHub Actions' pull.yml (which listens
 * for that event type) runs scripts/fetch_odds.py --trigger manual.
 *
 * Deploy:
 *   npm install -g wrangler
 *   wrangler secret put GITHUB_TOKEN      # a fine-grained PAT, "Contents: write" only, scoped to this one repo
 *   wrangler secret put ADMIN_SECRET      # whatever password you want the admin page to ask for
 *   wrangler deploy
 * Then put the deployed URL into admin/index.html's WORKER_URL constant
 * (scripts/build_admin.py writes that template; edit the template there,
 * not the generated file, so it survives the next rebuild).
 */

const REPO_OWNER = "captbosh-glitch"; // TODO confirm this is the right GitHub org/user for this repo
const REPO_NAME = "thebettorline";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const providedSecret = request.headers.get("X-Admin-Secret") || "";
    if (!env.ADMIN_SECRET || providedSecret !== env.ADMIN_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    let note = "manual pull from admin page";
    try {
      const body = await request.json();
      if (body && typeof body.note === "string" && body.note.trim()) {
        note = body.note.trim().slice(0, 200);
      }
    } catch {
      // no/invalid body is fine -- fall back to the default note
    }

    const ghResp = await fetch(
      `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
          "Accept": "application/vnd.github+json",
          "User-Agent": "thebettorline-pull-now-worker",
          "X-GitHub-Api-Version": "2022-11-28",
        },
        body: JSON.stringify({
          event_type: "pull_now",
          client_payload: { note },
        }),
      }
    );

    if (ghResp.status === 204) {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }

    const text = await ghResp.text();
    return new Response(
      JSON.stringify({ ok: false, status: ghResp.status, detail: text.slice(0, 300) }),
      { status: 502, headers: { "Content-Type": "application/json" } }
    );
  },
};
