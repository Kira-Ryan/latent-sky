"""Who visited latent-sky.dev: a day of CloudFront access logs, summarised.

    source infra/gpu/latentsky.env && python infra/web/visitors.py             # yesterday, UTC
    python infra/web/visitors.py --date 2026-09-06 --days 7                    # a week ending that day
    python infra/web/visitors.py --date 2026-09-06 --json                      # machine-readable
    python infra/web/visitors.py --date 2026-09-06 --raw 3                     # the first records, verbatim

Reads the JSON-lines objects that setup-analytics.sh has CloudFront deliver, and
prints what a person actually wants to know: how many humans came, from where,
sent by whom, which pages and which forecasts they opened, and what broke.
Nothing is written anywhere; this is read-only on the log bucket.

What "a visitor" means here: a distinct (address, browser) pair that fetched a
page — the globe, the verification index, or a report — with a non-bot user
agent. Not a person: one person on a phone and a laptop is two visitors, and a
shared office address is one. Bots are excluded by user agent (crawlers, link
previewers, HTTP libraries, uptime monitors). The Playwright checks run from
this machine use a real Chrome user agent and are NOT excluded; on a quiet day
they appear as a visitor from South Africa.

Countries come from CloudFront's c-country field when the delivery carries it;
otherwise the edge location's airport code is shown instead (edge:IAD), which
is where the request was served, not where it came from.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import json
import os
import re
import sys
import urllib.parse

import boto3

FORBIDDEN_ACCOUNT = "093047596153"  # the company account — never, see account_guard.sh
DEFAULT_DISTRIBUTION = "E2F82BMNK777GD"
SITE_HOSTS = ("latent-sky.dev", "www.latent-sky.dev")

BOT_UA = re.compile(
    r"bot|crawl|spider|slurp|preview|headless|monitor|uptime|pingdom|lighthouse|"
    r"facebookexternalhit|linkedin|twitterbot|slack|whatsapp|telegram|discord|"
    r"python-requests|python-urllib|curl/|wget/|go-http-client|java/|okhttp|"
    r"apache-httpclient|node-fetch|axios|undici|scrapy|postman|insomnia|"
    r"gptbot|claudebot|ccbot|bytespider|petalbot|ahrefs|semrush|mj12|dotbot|"
    r"yandex|baidu|duckduck|applebot|ia_archiver|archive\.org|bingpreview",
    re.IGNORECASE,
)


def guard(session: boto3.Session) -> str:
    """The same rule as infra/gpu/account_guard.sh, for Python: refuse the company
    account outright, and require the personal account to be named explicitly."""
    want = os.environ.get("LATENTSKY_AWS_ACCOUNT")
    if not want:
        sys.exit("REFUSING: set LATENTSKY_AWS_ACCOUNT (source infra/gpu/latentsky.env)")
    actual = session.client("sts").get_caller_identity()["Account"]
    if actual == FORBIDDEN_ACCOUNT:
        sys.exit(f"REFUSING: current credentials are the COMPANY account ({FORBIDDEN_ACCOUNT}). "
                 f"Use the personal profile: export AWS_PROFILE=latentsky")
    if actual != want:
        sys.exit(f"REFUSING: current AWS account {actual} != LATENTSKY_AWS_ACCOUNT {want}")
    return actual


def field(rec: dict, *names: str):
    """Log fields arrive under their CloudFront names, which include parentheses
    (cs(Referer)); tolerate the plausible spellings so a format change does not
    silently zero a column."""
    for n in names:
        if n in rec:
            return rec[n]
    return None


def read_day(s3, bucket: str, dist: str, day: dt.date) -> list[dict]:
    prefix = f"cloudfront/{dist}/{day:%Y}/{day:%m}/{day:%d}/"
    records: list[dict] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
            if body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            text = body.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if text[0] == "[":
                records.extend(json.loads(text))
                continue
            for line in text.splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    return records


def summarise(records: list[dict]) -> dict:
    humans = [r for r in records if not BOT_UA.search(str(field(r, "cs(User-Agent)", "cs-user-agent", "user_agent") or ""))]
    bots = len(records) - len(humans)

    def stem(r): return str(field(r, "cs-uri-stem", "uri_stem") or "")
    def status(r):
        try: return int(field(r, "sc-status", "status") or 0)
        except ValueError: return 0
    def is_page(r):
        s = stem(r)
        return status(r) in (200, 304) and (s in ("/", "/index.html") or s.endswith(".html"))

    pages = [r for r in humans if is_page(r)]
    visitors = {(field(r, "c-ip", "ip"), field(r, "cs(User-Agent)", "cs-user-agent")) for r in pages}

    countries = collections.Counter()
    for r in pages:
        c = field(r, "c-country", "country")
        if not c or c == "-":
            loc = str(field(r, "x-edge-location", "edge_location") or "?")
            c = f"edge:{loc[:3]}"
        countries[c] += 1

    referrers = collections.Counter()
    for r in pages:
        ref = str(field(r, "cs(Referer)", "cs-referer", "referer") or "-")
        if ref == "-":
            continue
        host = urllib.parse.urlsplit(ref).hostname or ref
        if host.endswith(SITE_HOSTS):
            continue
        referrers[host] += 1

    page_counts = collections.Counter(stem(r) for r in pages)

    events = collections.Counter()
    for r in humans:
        m = re.match(r"^/data/web/(.+?)/manifest\.json$", stem(r))
        if m and status(r) in (200, 304):
            events[m.group(1)] += 1
        elif stem(r) == "/data/web/manifest.json" and status(r) in (200, 304):
            events["(global ERA5)"] += 1

    linked_events = collections.Counter()
    for r in pages:
        q = str(field(r, "cs-uri-query", "query") or "-")
        m = re.search(r"(?:^|&)event=([^&]+)", q)
        if m:
            linked_events[urllib.parse.unquote(m.group(1))] += 1

    reports = collections.Counter(stem(r) for r in pages if stem(r).startswith("/verification/"))

    origin_errors = collections.Counter()
    for r in humans:
        if str(field(r, "x-edge-result-type", "result_type") or "") == "Error" or status(r) >= 400:
            origin_errors[(status(r), stem(r))] += 1

    mb = sum(int(field(r, "sc-bytes", "bytes") or 0) for r in records) / 1e6

    return {
        "requests": len(records),
        "bot_requests": bots,
        "human_requests": len(humans),
        "page_views": len(pages),
        "visitors": len(visitors),
        "countries": countries.most_common(12),
        "referrers": referrers.most_common(12),
        "pages": page_counts.most_common(12),
        "forecasts_opened": events.most_common(12),
        "arrived_by_event_link": linked_events.most_common(8),
        "reports_read": reports.most_common(8),
        "errors": [[s, p, n] for (s, p), n in origin_errors.most_common(10)],
        "megabytes": round(mb, 1),
    }


def table(title: str, rows, width: int = 44) -> None:
    if not rows:
        return
    print(f"\n  {title}")
    for k, n in rows:
        print(f"    {str(k)[:width]:<{width}} {n:>6}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", type=dt.date.fromisoformat,
                    default=(dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)),
                    help="last day of the range, UTC (default: yesterday)")
    ap.add_argument("--days", type=int, default=1, help="how many days back from --date (default 1)")
    ap.add_argument("--distribution", default=DEFAULT_DISTRIBUTION)
    ap.add_argument("--bucket", default=None, help="default latentsky-logs-<account>")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    ap.add_argument("--raw", type=int, default=0, metavar="N", help="print the first N records verbatim and stop")
    args = ap.parse_args(argv)

    session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "latentsky"))
    account = guard(session)
    bucket = args.bucket or f"latentsky-logs-{account}"
    s3 = session.client("s3", region_name="us-east-1")

    days = [args.date - dt.timedelta(days=i) for i in range(args.days - 1, -1, -1)]
    per_day: dict[str, list[dict]] = {}
    for day in days:
        per_day[day.isoformat()] = read_day(s3, bucket, args.distribution, day)

    everything = [r for recs in per_day.values() for r in recs]
    if args.raw:
        for r in everything[: args.raw]:
            print(json.dumps(r, indent=1))
        print(f"\n{len(everything)} record(s) over {len(days)} day(s)")
        return

    summary = summarise(everything)
    summary["days"] = {d: summarise(recs)["visitors"] for d, recs in per_day.items()}
    summary["range"] = [days[0].isoformat(), days[-1].isoformat()]

    if args.json:
        print(json.dumps(summary, indent=1))
        return

    span = summary["range"][0] if len(days) == 1 else f"{summary['range'][0]} to {summary['range'][1]}"
    print(f"latent-sky.dev — {span} (UTC)")
    if not everything:
        print("  no log objects for this range. Logs arrive within minutes to an hour of a request;"
              " if the range is older than the logging setup, there is nothing to read.")
        return
    print(f"  visitors      {summary['visitors']:>6}   distinct (address, browser) pairs that opened a page")
    print(f"  page views    {summary['page_views']:>6}")
    print(f"  requests      {summary['requests']:>6}   of which bots {summary['bot_requests']}, transferred {summary['megabytes']} MB")
    if len(days) > 1:
        table("visitors by day", list(summary["days"].items()))
    table("countries (page views)", summary["countries"])
    table("sent by (referrer host)", summary["referrers"])
    table("pages", summary["pages"])
    table("forecasts opened (manifest loads)", summary["forecasts_opened"])
    table("arrived by a direct event link", summary["arrived_by_event_link"])
    table("verification reports read", summary["reports_read"])
    table("origin errors seen by humans (status, path)", [(f"{s} {p}", n) for s, p, n in summary["errors"]], width=60)


if __name__ == "__main__":
    main()
