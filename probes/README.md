# Probes

Cheap experiments that retire the project's largest unknowns before any production code.
Ordered by value per pound. See `DOCS/Architecture.md` §14 for the full table and rationale.

| # | Probe | Cost | Status |
|---|---|---|---|
| 0 | File the EC2 GPU quota increase (`L-DB2E81BA`, 8 vCPUs, us-east-1) | $0 | **outstanding — do this first** |
| 1 | Run NVIDIA's `03_ensemble_downscaling.py` verbatim on Doksuri | ~$1 | outstanding (needs GPU) |
| 2 | [`probe2_corrdiff_grid.py`](probe2_corrdiff_grid.py) — measure the CorrDiff grid | $0 | ✅ done 31 Jul 2026 |
| 3 | [`probe3-colour-identity/`](probe3-colour-identity/) — Cesium colour contamination | $0 | ✅ done 31 Jul 2026 |
| 4 | [`probe4-payload/`](probe4-payload/) — real-field compression at final geometry | $0 | ✅ done 31 Jul 2026 |
| 5 | `t3.micro` deadman-switch rehearsal | $0.01 | outstanding |
| 6 | WebGL2 `TEXTURE_2D_ARRAY` allocation on the target laptop | $0 | outstanding |
| 7 | Confirm CloudFront flat-rate plan eligibility in the console | $0 | outstanding |

## Probe 2 — CorrDiff grid

```bash
pip install zarr numpy
python probes/probe2_corrdiff_grid.py --download    # 717 MB, unauthenticated, no NGC key
```

Settled: **2.0684 km** cell size (2 km confirmed, not 3 km); output grid is **448 × 448**;
extent 19.5187–27.9282° N, 116.1372–125.5459° E; **not** equirectangular — up to **37 km** of
longitude drift down a column, so the regrid is mandatory; checkpoints are Apache-2.0 while the
bundled dataset is CC BY-NC-ND 4.0; and the downscaled peak wind is **2.21×** the coarse peak.

## Probe 3 — colour identity

```bash
cd probes/probe3-colour-identity && npm install && node run.mjs
```

Drives real Chrome via `playwright-core` (no browser download) and samples rendered pixels from a
live CesiumJS globe. Proves that Cesium's defaults shift an authored colour by up to **+77/255** on
dark values between globe framing and hero framing, and that four lines null it exactly.

Exits non-zero if any fixed config is contaminated — suitable for CI as a permanent regression test.

## Probe 4 — payload, measured on real fields

```bash
python -m pip install scipy cmcrameri cmocean cmweather gcsfs xarray zarr brotli
python probes/probe4-payload/probe4.py     # ~83 s cold (fetches one ERA5 frame from ARCO), ~69 s cached
```

Encodes the five real CWB timesteps at final geometry (579×566 and 463×453) through the real LUTs,
plus one real global ERA5 frame — 2024-07-24T00Z, the Gaemi hero init date, fetched anonymously.
Result: the hero byte estimates hold; the global ones were 2.3× low (real coastlines compress worse
than synthetic spectra), which dropped the global cadence to 12-hourly. Full table in
[`probe4-payload/results.md`](probe4-payload/results.md); `DOCS/Architecture.md` §8 now carries the
measured figures.

