"""Emit /verification/index.html — every scored run, newest first, with its result.

    python tools/verification_index.py --results data/verification --out data/verification/pages/index.html

A list of links would be a filing cabinet. What a reader wants, and what a
meteorologist wants most, is whether the skill is stable across days, so each row
carries the run's own headline figure and the table reads as a scoreboard.

The page is generated from the same results files the reports are, so a row can
never claim a score its report does not contain. A results file with no
`headline` block is listed with its figures blank rather than omitted: a run that
was scored is part of the record even if this tool cannot summarise it.

Reports published by the daily pipeline live only in the site bucket, so the
`--extra` flag takes JSON rows for runs this checkout does not hold.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verifications &middot; Latent Sky</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {
    color-scheme: light;
    --bg: #f2f4f7; --panel: #ffffff;
    --ink: #121a2c; --ink-2: #4a566e; --ink-3: #7b869a;
    --line: #d5dbe5; --grid: #e6eaf0; --accent: #0e8386;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --bg: #0d1420; --panel: #141d2c;
      --ink: #e9eef6; --ink-2: #b1bccd; --ink-3: #7f8ba0;
      --line: #253349; --grid: #1d2a3d; --accent: #45c5c7;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #0d1420; --panel: #141d2c;
    --ink: #e9eef6; --ink-2: #b1bccd; --ink-3: #7f8ba0;
    --line: #253349; --grid: #1d2a3d; --accent: #45c5c7;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink); font-family: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif; font-size: 15.5px; line-height: 1.6; }
  .wrap { max-width: 900px; margin: 0 auto; padding: 40px 24px 64px; }
  a { color: var(--accent); text-decoration: none; }
  .topnav { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; margin: 0 0 18px; padding-bottom: 12px; border-bottom: 1px solid var(--line); font-size: 13px; }
  .topnav .home { font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-weight: 600; font-size: 17px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--ink); }
  .topnav .home:hover { color: var(--accent); }
  .topnav .sep { color: var(--ink-3); }
  .topnav .globe { margin-left: auto; border-bottom: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); }
  .eyebrow { font-family: "Barlow Condensed", sans-serif; font-weight: 600; font-size: 13px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); }
  h1 { font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-weight: 600; font-size: 42px; line-height: 1.05; margin: 6px 0 14px; }
  .dek { font-size: 17px; color: var(--ink-2); max-width: 66ch; margin: 0 0 26px; text-wrap: pretty; }
  .tablewrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; background: var(--panel); }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th, td { padding: 10px 14px; text-align: left; border-bottom: 1px solid var(--grid); white-space: nowrap; }
  th { font-family: "Barlow Condensed", sans-serif; font-weight: 600; font-size: 12.5px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink-3); border-bottom: 1px solid var(--line); }
  tr:last-child td { border-bottom: 0; }
  td.num { font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; }
  td.result { white-space: normal; color: var(--ink-2); min-width: 22ch; }
  .none { color: var(--ink-3); }
  .empty { padding: 26px; color: var(--ink-2); }
  footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 13px; color: var(--ink-3); display: flex; flex-wrap: wrap; gap: 6px 24px; }
</style>
</head>
<body>
<div class="wrap">
  <nav class="topnav">
    <a class="home" href="/">Latent Sky</a>
    <span class="sep" aria-hidden="true">&middot;</span>
    <span>Verifications</span>
    <a class="globe" href="/">Back to the globe</a>
  </nav>
  <div class="eyebrow">Latent Sky</div>
  <h1>Every run, scored against what happened</h1>
  <p class="dek">Each forecast here was published before the weather it describes, then measured
  against observed radar afterwards with the Fractions Skill Score and republished with the
  observations beside it. The results are what they are.</p>

  <div class="tablewrap" id="table">
    <p class="empty">Loading the record&hellip;</p>
  </div>

  <footer>
    <span>Kira Ryan &middot; <a href="/">latent-sky.dev</a></span>
    <span id="count"></span>
  </footer>
</div>
<script>
// The rows live in a JSON file the daily pipeline appends to, so a new scored run
// needs no HTML regeneration and no image rebuild. A failed fetch says so rather
// than rendering an empty table, which would read as "nothing has been scored".
const esc = (v) => String(v).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const summarise = (h) => {
  if (!h) return '<span class="none">not summarised</span>';
  if (h.usefulScaleKm === null || h.usefulScaleKm === undefined) {
    return `No useful skill at ${h.thresholdDbz} dBZ at any scale to ${Math.round(h.largestScaleKm)} km`;
  }
  return `Useful at ${Math.round(h.usefulScaleKm)} km, ${h.usefulHours} of ${h.scoredHours} h &middot; ${h.thresholdDbz} dBZ`;
};
fetch("__INDEX_URL__", { cache: "no-store" })
  .then((r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.json(); })
  .then((doc) => {
    const runs = (doc.runs || []).slice().sort((a, b) => String(b.init).localeCompare(String(a.init)));
    const scored = runs.filter((r) => r.scored);
    document.getElementById("count").textContent =
      `${scored.length} run${scored.length === 1 ? "" : "s"} scored of ${runs.length} published`;
    if (!runs.length) {
      document.getElementById("table").innerHTML =
        '<p class="empty">No runs have been published yet.</p>';
      return;
    }
    document.getElementById("table").innerHTML =
      '<table><thead><tr><th>Run</th><th>Initialised</th><th>Result</th><th>Ensemble</th><th></th></tr></thead><tbody>' +
      runs.map((r) => {
        const when = r.init ? esc(String(r.init).slice(0, 16).replace("T", " ")) + "Z" : "&mdash;";
        const name = r.liveUrl ? `<a href="${esc(r.liveUrl)}">${esc(r.title || r.id)}</a>` : esc(r.title || r.id);
        const members = r.members > 1 ? `${r.members} members` : '<span class="none">single run</span>';
        // Only a run whose report actually exists offers a link. An unscored run
        // is listed, because it is part of the record, and says so plainly.
        const read = r.scored && r.reportUrl
          ? `<a href="${esc(r.reportUrl)}">Read</a>`
          : '<span class="none">&mdash;</span>';
        const result = r.scored ? summarise(r.headline) : '<span class="none">not yet scored</span>';
        return `<tr><td>${name}</td><td class="num">${when}</td><td class="result">${result}</td><td>${members}</td><td>${read}</td></tr>`;
      }).join("") + "</tbody></table>";
  })
  .catch((err) => {
    console.error("verification index unavailable:", err);
    document.getElementById("table").innerHTML =
      '<p class="empty">The record could not be loaded just now. Every report is still at /verification/, and the run pages link their own.</p>';
  });
</script>
</body>
</html>
"""

EMPTY = '    <p class="empty">No runs have been scored yet. The daily forecast is scored against radar the day after it is published, so the first entry appears then.</p>'


def summarise(headline: dict | None) -> str:
    """The run's result in a phrase, or an honest blank."""
    if not headline:
        return '<span class="none">not summarised</span>'
    thr = headline.get("thresholdDbz")
    km = headline.get("usefulScaleKm")
    if km is None:
        return f'No useful skill at {thr} dBZ at any scale to {round(headline.get("largestScaleKm", 0))} km'
    return (f'Useful at {round(km)} km, {headline.get("usefulHours")} of '
            f'{headline.get("scoredHours")} h &middot; {thr} dBZ')


def rows_from(results_dir: pathlib.Path, pages_dir: pathlib.Path) -> list[dict]:
    rows = []
    for path in sorted(results_dir.glob("*.fss.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        ev = data.get("event") or {}
        init = ev.get("init") or data.get("init") or ""
        # The page beside it, by the name fss_report.py --site-out writes.
        stem = path.name[: -len(".fss.json")]
        candidates = [p.name for p in pages_dir.glob("*.html") if p.name != "index.html"]
        page = next((c for c in candidates if stem.split("_")[0] in c or (ev.get("id") or "") in c), None)
        rows.append({
            "init": init,
            "id": ev.get("id") or stem,
            "page": page,
            "live": ev.get("live_url"),
            "members": data.get("members"),
            "headline": data.get("headline"),
        })
    return rows


def render(rows: list[dict]) -> str:
    if not rows:
        return EMPTY
    rows = sorted(rows, key=lambda r: r["init"], reverse=True)
    out = ['    <table>', '      <thead><tr><th>Run</th><th>Initialised</th><th>Result</th><th>Ensemble</th><th></th></tr></thead>', '      <tbody>']
    for r in rows:
        when = r["init"][:16].replace("T", " ") + "Z" if r["init"] else "&mdash;"
        name = html.escape(r["id"])
        link = f'<a href="/verification/{html.escape(r["page"])}">Read</a>' if r["page"] else '<span class="none">&mdash;</span>'
        run_link = f'<a href="{html.escape(r["live"])}">{name}</a>' if r.get("live") else name
        members = f'{r["members"]} members' if r.get("members") else '<span class="none">single run</span>'
        out.append(f'        <tr><td>{run_link}</td><td class="num">{when}</td>'
                   f'<td class="result">{summarise(r["headline"])}</td><td>{members}</td><td>{link}</td></tr>')
    out += ['      </tbody>', '    </table>']
    return "\n".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=pathlib.Path, default=pathlib.Path("data/verification"))
    ap.add_argument("--pages", type=pathlib.Path, default=pathlib.Path("data/verification/pages"))
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("data/verification/pages/index.html"))
    ap.add_argument("--json-out", type=pathlib.Path, default=pathlib.Path("data/verification/pages/index.json"),
                    help="the seed record: the curated runs this checkout holds. The daily "
                         "publisher appends its own rows to the copy on the site bucket, so "
                         "this file is a starting point and never the whole truth.")
    ap.add_argument("--index-url", default="/verification/index.json",
                    help="where the page fetches the record from at view time")
    args = ap.parse_args(argv)

    runs = [
        {
            "id": r["id"],
            "title": r["id"],
            "init": r["init"],
            "scored": bool(r["page"]),
            "liveUrl": r.get("live"),
            "members": r.get("members") or 1,
            "reportUrl": f"/verification/{r['page']}" if r["page"] else None,
            "headline": r.get("headline"),
        }
        for r in rows_from(args.results, args.pages)
    ]
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps({"schemaVersion": 1, "runs": runs}, indent=1) + chr(10), encoding="utf-8")
    args.out.write_text(PAGE.replace("__INDEX_URL__", args.index_url), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes, shell)")
    print(f"wrote {args.json_out} ({len(runs)} curated run(s))")
    for r in sorted(runs, key=lambda r: r["init"], reverse=True):
        print(f"  {r['init'][:16]:18} {r['id']:22} scored={r['scored']} -> {r['reportUrl']}")


if __name__ == "__main__":
    main()
