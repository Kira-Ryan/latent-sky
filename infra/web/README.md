# The public site

Everything that puts latent-sky.dev in front of a visitor lives here: the deploy, the domain, and the record of who came. The site is a private S3 bucket behind CloudFront; there is no server and nothing runs at view time.

Every script sources `../gpu/account_guard.sh` first and refuses to run against any AWS account but the personal one named in `../gpu/latentsky.env`. That guard exists because the machine's default AWS profile belongs to a company account, and nothing here may ever touch it.

## Deploy

```
source ../gpu/latentsky.env && ./deploy-site.sh [--dry-run]
```

`deploy-site.sh` stages `data/web` beside the built app, audits what it staged against the licence rules, uploads with cache headers baked into each object, merges the live catalogue and verification record so a deploy never erases a daily run the pipeline published, invalidates only the entry points, and then verifies the live site with real requests. Its header comment is the specification; read it before changing the order of anything.

## Domain

`DOMAIN.md` records how latent-sky.dev is registered and pointed at the distribution, and the certificate that fronts it.

## Who visits

Two independent records, deliberately: one that cannot be blocked and one that is pleasant to read.

**CloudFront access logs** are the ground truth. `setup-analytics.sh` turns them on: every request the edge serves is written, within an hour, to the private bucket `latentsky-logs-<account>` under `cloudfront/<distribution>/<yyyy>/<MM>/<dd>/<HH>/`, as JSON lines carrying the time, the viewer's address, country and network, the path and query, the status, the referrer and the user agent. Nothing runs in the page for this, so nothing can block it. Objects expire after 180 days, because raw logs contain addresses and a portfolio site has no reason to hold them longer.

```
source ../gpu/latentsky.env && ./setup-analytics.sh --status   # what exists, newest objects
python visitors.py                                              # yesterday, summarised
python visitors.py --date 2026-09-06 --days 7                   # a week
```

`visitors.py` reads a day or a range and prints what a person wants to know: how many humans came, from which countries, sent by which sites, which pages and which forecasts they opened, and which requests failed at the origin. A visitor is a distinct address-and-browser pair that opened a page with a non-bot user agent. That is not a person: a phone and a laptop are two, an office is one, and the Playwright checks run from this machine count as one visitor from South Africa. Read the numbers with that in mind.

**Cloudflare Web Analytics** is the dashboard. It is one deferred script on the page, sets no cookie, needs no consent banner, and shows pages, referrers, countries and devices over time. About a third of a technical audience blocks it, which is why the logs exist. It is injected at deploy time from `CF_BEACON_TOKEN` in `latentsky.env`; when the token is empty nothing is injected and the deploy says so. Create the site once in the Cloudflare dashboard under Analytics & Logs, Web Analytics, and paste the token from its snippet.

**What is not done.** Nobody is identified. The logs are never joined to names, the dashboard never sees an address, and neither record leaves the account. If the site ever states a privacy position publicly, it should say exactly this.
