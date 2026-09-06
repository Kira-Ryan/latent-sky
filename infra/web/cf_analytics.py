#!/usr/bin/env python3
"""Cloudflare Web Analytics for latent-sky.dev, through the API. Stdlib only.

    source ../gpu/latentsky.env && python cf_analytics.py status   # what exists (read-only)
    source ../gpu/latentsky.env && python cf_analytics.py ensure   # create the site if absent; print its beacon token

The site is host-based: Cloudflare does not proxy latent-sky.dev (the DNS points
straight at CloudFront), so the "automatic injection" mode does nothing and the
page has to carry the snippet itself. deploy-site.sh injects it from
CF_BEACON_TOKEN in latentsky.env; `ensure` prints the value to put there.

Guard, in the spirit of ephemera's guard_cf.py: the token and account id come
from the environment; the account is fetched from Cloudflare and must be the one
named in the environment and must not be an employer account. Nothing here can
touch a zone or DNS; the token it expects carries Account Settings only, which
is what Cloudflare's API reference lists for the Web Analytics site endpoints.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"
HOST = "latent-sky.dev"
FORBIDDEN_NAME_FRAGMENTS = ("ultisim",)   # employer identity; refused by name, no override


class GuardRefused(RuntimeError):
    pass


def call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path, method=method,
        headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"success": False, "errors": [{"code": e.code, "message": e.reason}]}


def errors(r: dict) -> str:
    return "; ".join(f"{e.get('code')}: {e.get('message')}" for e in (r.get("errors") or [])) or "unknown error"


def enforce() -> str:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    if not token or not account:
        raise GuardRefused("REFUSING: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must both be set "
                           "(source infra/gpu/latentsky.env)")
    r = call("GET", "/user/tokens/verify")
    if not r.get("success") or r["result"].get("status") != "active":
        raise GuardRefused(f"REFUSING: the token is not active ({errors(r)})")
    r = call("GET", f"/accounts/{account}")
    if not r.get("success"):
        raise GuardRefused(f"REFUSING: the token cannot read account {account[:4]}… ({errors(r)}); "
                           f"it is scoped elsewhere, or lacks Account Settings")
    name = r["result"].get("name", "")
    if any(f in name.lower() for f in FORBIDDEN_NAME_FRAGMENTS):
        raise GuardRefused(f"REFUSING: account {account[:4]}… is {name!r}, an employer identity. Never.")
    print(f"guard: Cloudflare account {account[:4]}… is {name!r}; proceeding", file=sys.stderr)
    return account


def sites(account: str) -> list[dict]:
    r = call("GET", f"/accounts/{account}/rum/site_info/list")
    if not r.get("success"):
        sys.exit(f"could not list Web Analytics sites: {errors(r)}")
    return r.get("result") or []


def site_host(s: dict) -> str:
    return s.get("host") or (s.get("ruleset") or {}).get("zone_name") or "?"


def status(account: str) -> None:
    found = sites(account)
    print(f"{len(found)} Web Analytics site(s) on the account:")
    for s in found:
        tok = s.get("site_token") or ""
        print(f"  {site_host(s):24} site_tag={s.get('site_tag')}  auto_install={s.get('auto_install')}  "
              f"token={tok[:4]}…{tok[-4:] if len(tok) > 8 else ''}  created={s.get('created')}")


def ensure(account: str) -> None:
    ours = [s for s in sites(account) if site_host(s) == HOST]
    if ours:
        s = ours[0]
        print(f"site {HOST}: exists (site_tag {s.get('site_tag')}, auto_install={s.get('auto_install')})")
    else:
        r = call("POST", f"/accounts/{account}/rum/site_info", {"host": HOST, "auto_install": False})
        if not r.get("success"):
            sys.exit(f"site create failed: {errors(r)}")
        s = r["result"]
        print(f"site {HOST}: created (site_tag {s.get('site_tag')})")
    tok = s.get("site_token")
    if not tok:
        sys.exit("the site has no site_token in the API response; open it in the dashboard and choose "
                 "'Enable with JS Snippet installation'")
    print(f"beacon token: {tok}")
    print("put it in infra/gpu/latentsky.env as CF_BEACON_TOKEN and run deploy-site.sh")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in ("status", "ensure"):
        sys.exit(__doc__)
    try:
        acct = enforce()
    except GuardRefused as e:
        sys.exit(str(e))
    status(acct) if cmd == "status" else ensure(acct)
