"""Render DOCS/Verification-Protocol.md as /verification/protocol.html.

    python tools/protocol_page.py [--src DOCS/Verification-Protocol.md] [--out data/verification/pages/protocol.html]

The Markdown file is the source of truth and is what a reviewer reads in the
repository; this page is the same words in the site's own dress, with the
navigation every verification page carries. Nothing is added or reworded here.
"""

from __future__ import annotations

import argparse
import pathlib

import markdown

ROOT = pathlib.Path(__file__).resolve().parents[2]

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verification protocol &middot; Latent Sky</title>
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
  .wrap { max-width: 820px; margin: 0 auto; padding: 40px 24px 64px; }
  a { color: var(--accent); text-decoration: none; border-bottom: 1px solid color-mix(in srgb, var(--accent) 40%, transparent); }
  a:hover, a:focus-visible { border-bottom-color: var(--accent); outline: none; }
  .topnav { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 12px; margin: 0 0 18px; padding-bottom: 12px; border-bottom: 1px solid var(--line); font-size: 13px; }
  .topnav a { border-bottom: none; }
  .topnav .home { font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-weight: 600; font-size: 17px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--ink); }
  .topnav .home:hover { color: var(--accent); }
  .topnav .sep { color: var(--ink-3); }
  .topnav .index, .topnav .globe { color: var(--accent); border-bottom: 1px solid color-mix(in srgb, var(--accent) 35%, transparent); }
  .topnav .globe { margin-left: auto; }
  .eyebrow { font-family: "Barlow Condensed", sans-serif; font-weight: 600; font-size: 13px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--accent); }
  h1 { font-family: "Barlow Condensed", "Arial Narrow", sans-serif; font-weight: 600; font-size: 42px; line-height: 1.05; margin: 6px 0 14px; text-wrap: balance; }
  h2 { font-family: "Barlow Condensed", sans-serif; font-weight: 600; font-size: 24px; letter-spacing: 0.01em; margin: 36px 0 6px; }
  p, li { max-width: 68ch; }
  p { margin: 0 0 14px; }
  ul { padding-left: 22px; margin: 0 0 14px; }
  li { margin: 0 0 6px; }
  code { font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 0.92em; color: var(--ink); }
  strong { font-weight: 600; }
  footer { margin-top: 40px; padding-top: 14px; border-top: 1px solid var(--line); font-size: 13px; color: var(--ink-3); display: flex; flex-wrap: wrap; gap: 6px 24px; }
</style>
</head>
<body>
<div class="wrap">
  <nav class="topnav">
    <a class="home" href="/">Latent Sky</a>
    <span class="sep" aria-hidden="true">&middot;</span>
    <a class="index" href="/verification/index.html">All verifications</a>
    <a class="globe" href="/">Back to the globe</a>
  </nav>
  <div class="eyebrow">Latent Sky</div>
__BODY__
  <footer>
    <span>Kira Ryan &middot; <a href="/">latent-sky.dev</a> &middot; <a href="https://github.com/Kira-Ryan/latent-sky">Open source on GitHub</a></span>
    <span>Source: <a href="https://github.com/Kira-Ryan/latent-sky/blob/main/DOCS/Verification-Protocol.md">DOCS/Verification-Protocol.md</a></span>
  </footer>
</div>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=pathlib.Path, default=ROOT / "DOCS" / "Verification-Protocol.md")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "data" / "verification" / "pages" / "protocol.html")
    args = ap.parse_args(argv)
    text = args.src.read_text(encoding="utf-8")
    body = markdown.markdown(text, extensions=["smarty"], output_format="html")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(PAGE.replace("__BODY__", body), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes) from {args.src.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
