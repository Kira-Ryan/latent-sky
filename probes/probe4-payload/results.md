# Probe 4 — payload measured on real fields

> Run: 2026-08-01T18:06:03+00:00 · Python 3.12.10 · Pillow 12.3.0 · encoder `lossless=True, exact=True, method=6` · MB = 10^6 bytes (matches §8 `du -sb`)
> Hero source: the 5 real CWB sample timesteps (2 are Typhoon Chanthu 2021-09-12),
> regridded per §5.4 — cKDTree nearest on the unit sphere; median NN distance 0.842 km at 1.25x, with 3.68% of target pixels beyond the rotated WRF hull (bbox corners, §3.4 curvature — max 37.2 km, edge-clamped).
> Global source: ARCO `gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3` @ 2024-07-24T00:00Z (anonymous, no credentials) — served from local cache era5_cache.npz.

**Round-trip identity: PROVEN.** Every encoded frame was decoded and compared — all 39 frames bit-identical. The built-in adversarial test (random RGB under alpha=0) confirms Pillow 12.3.0 honours `exact=True` (and that omitting it corrupts: default round-trip identical = False). §5.5's `exact=True` is mandatory, as stated.

## Per-frame measurements — hero (5 real timesteps; t3, t4 are the typhoon)

| Layer | px | lossless B/frame (mean of 5) | typhoon mean (t3,t4) | B/px mean | B/px typhoon | q90 mean | brotli-q11 index mean |
|---|---|---|---|---|---|---|---|
| fine_wind10m_1.25x | 327,714 | 93,621 | 107,728 | 0.286 | 0.329 | 46,839 | 92,752 |
| fine_wind10m_1.00x | 209,739 | 77,915 | 91,257 | 0.371 | 0.435 | 35,205 | 74,709 |
| fine_mrr_1.25x | 327,714 | 58,946 | 108,763 | 0.180 | 0.332 | 43,280 | 57,231 |
| fine_mrr_1.00x | 209,739 | 49,194 | 90,662 | 0.235 | 0.432 | 32,042 | 44,876 |
| fine_t2m_1.25x | 327,714 | 42,840 | 44,192 | 0.131 | 0.135 | 8,413 | 43,738 |
| fine_t2m_1.00x | 209,739 | 35,666 | 36,795 | 0.170 | 0.175 | 6,067 | 35,743 |
| coarse_wind10m_36x40 | 1,440 | 1,016 | 1,166 | 0.706 | 0.810 | 670 | 864 |

## Per-frame measurements — global (one real timestep)

| Layer | px | lossless B | B/px | q90 B | brotli-q11 index B |
|---|---|---|---|---|---|
| global_t2m_1440x721 | 1,038,240 | 278,074 | 0.268 | 53,092 | 259,603 |
| global_t2m_720x361 | 259,920 | 102,116 | 0.393 | 19,992 | 93,289 |
| global_wind10m_1440x721 | 1,038,240 | 371,524 | 0.358 | 213,798 | 343,601 |
| global_wind10m_720x361 | 259,920 | 129,274 | 0.497 | 85,536 | 119,928 |

## §8 rows, re-measured

Central = mean-of-5-timesteps x steps (3 of the 5 sample times are quiet weather); typhoon = mean of the two Chanthu frames x steps — the honest upper bound, since a hero event's 12 steps are mostly active weather. Global rows extrapolate the single measured frame (a real typhoon-day analysis).

| §8 row | §8 est. MB | implied §8 B/px | measured B/px (central / typhoon) | measured central MB | measured typhoon MB | verdict |
|---|---|---|---|---|---|---|
| 6 — hero coarse wind, 36x40 | 0.02 | 1.157 | 0.706 / 0.810 | 0.01 | 0.01 | beats §8 |
| 7 — hero fine wind, 579x566 | 0.79 | 0.201 | 0.286 / 0.329 | 1.12 | 1.29 | breaks §8 |
| 8 — hero fine reflectivity, 579x566 | 1.38 | 0.351 | 0.180 / 0.332 | 0.71 | 1.31 | beats §8 |
| 13 — hero fine t2m, 579x566 (lazy) | 0.59 | 0.150 | 0.131 / 0.135 | 0.51 | 0.53 | beats §8 |
| 9 — ensemble panel, 4 x fine wind frame (proxy) | 0.49 | — | — | 0.37 | 0.43 | beats §8 |
| 10 — global t2m, 720x361 | 0.95 | 0.174 | 0.393 | 2.14 | 2.14 | breaks §8 |
| 11 — global wind, 720x361 | 1.16 | 0.213 | 0.497 | 2.71 | 2.71 | breaks §8 |
| **Hero + global subtotal (eager rows 6-11)** | **4.79** | | | **7.08** | **7.90** | |
| **Projected eager total (+2.42 core, §8)** | **7.21** | | | **9.50** | **10.32** | vs 12.00 ceiling |

## Payload levers, re-measured

| Lever | §8 claimed | measured (typhoon-weighted) |
|---|---|---|
| regrid 1.25x -> 1.00x, all three fine layers | -0.90 MB | **-0.50 MB** |
| global steps 21 -> 11 | -1.05 MB | **-2.31 MB** |

## Where reality lands against §8's 0.15-0.35 B/px assumption

**Hero fine layers: the assumption HOLDS.** Measured typhoon-frame B/px at 1.25x spans
**0.135-0.332** — inside the band, at its upper half for wind and reflectivity.
Where §8's hero rows break, they break because the row's *implied* B/px sat near the bottom
of the band (see the implied column above), not because real fields blew past 0.35.

**Global layers: the assumption BREAKS.** Real 0.5-degree ERA5 encodes at
**0.393 / 0.497 B/px** (t2m / wind) — the coastline-and-gradient content a
k^-4 synthetic field does not have. §8's global rows implied ~0.17-0.21 B/px and are
**~2.3x under-estimated**; this is where the eager budget actually moves.

**Bottom line: eager lands at 9.50 MB central /
10.32 MB typhoon-weighted** against §8's
**7.21 MB** estimate — over the estimate but still under the 12.00 MB
ceiling, with the measured global-steps lever (-2.31 MB alone)
big enough to restore the whole margin without touching the hero.

Alternative datapoints (not shipped, measured for the record):
- **WebP q90 lossy** is ~2x smaller on hero wind and ~5x smaller on
  global t2m. That is the price of colour identity being a property of the format; known, and
  declined deliberately.
- **brotli -q 11 of the raw uint8 index plane** (the "packed blob" design, LUT applied
  client-side) lands within +/-8% of lossless WebP on every full-size field — slightly
  smaller on wind/reflectivity/global, slightly larger on smooth t2m. Only the ~1 KB 36x40
  coarse frames favour brotli (~15%), where WebP container overhead dominates. No meaningful
  win, and it forfeits native image decode. Not worth pursuing.

Findings that belong in Architecture.md:
- **§7.1 t2m range clips the real global field.** ERA5 2 m temperature at 2024-07-24T00:00Z spans
  210.41-318.85 K; 5.53% of the globe (the
  Antarctic winter plateau) sits below the LUT's 233.15 K vmin and saturates at index 0. Fine
  for the Taiwan hero; the GLOBAL t2m layer needs either a wider range (e.g. 203.15 K floor)
  or an explicit "clipped at -40 C" caption.
- **The §5.4 bbox corners are outside the rotated WRF hull.** 3.68%
  of 1.25x target pixels have no source cell within one native cell (max 37.2 km
  at the corners — the same §3.4 curvature, seen from the other side). Nearest-neighbour
  edge-clamps them, smearing edge values into the corners. The production encoder should
  alpha-mask beyond-hull pixels instead; interior median NN distance is
  0.842 km, i.e. the regrid itself is sound.

Caveats, stated plainly:
- The 5 hero timesteps are monthly analysis snapshots, not 12 consecutive forecast steps of
  one event; the typhoon-weighted column is the honest planning figure for a Gaemi run.
- The ensemble-panel row reuses the fine-wind per-frame cost as a proxy for 4 CorrDiff
  samples — same variable, same geometry, different noise draw.
- The native 36x40 grid extends slightly past the 448x448 output hull (lat 19.25 vs 19.52,
  lon 125.75 vs 125.55); nearest-neighbour clamps to the edge cells there, which is what the
  shipped "before" layer would also do.
- Global rows extrapolate one real frame x 21 steps; frame-to-frame variance is unmeasured,
  but analysis fields at 6 h spacing vary far less than the hero's convective field, and the
  measured frame is a real typhoon-day analysis, not a quiet day.
