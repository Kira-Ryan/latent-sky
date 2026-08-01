# Latent Sky

**AI weather forecasting, rendered as a living globe — with no GPU, no server, and no runtime inference.**

Latent Sky runs NVIDIA's open Earth-2 models end to end — a global SFNO forecast, downscaled to
~2 km over Taiwan by the CorrDiff generative diffusion model — then bakes the result into a folder
of static files that any laptop can render in CesiumJS on integrated graphics.

NVIDIA's own interactive Earth-2 visualisations pixel-stream from cloud GPUs. This one is a folder
of files.

> **Status: pre-release.** The architecture is decided and measured (`DOCS/Architecture.md`), the
> renderer is being built against real sample data, and the first full forecast run is pending GPU
> quota. Nothing here is a live link yet.

## The one number

Over the same domain and timesteps, the coarse model's peak 10 m wind is **23.6 m/s**. The
downscaled field reaches **52.2 m/s** — a resolved typhoon eyewall the 25 km grid cannot represent
at all. 1,440 grid cells in, 200,704 out. Measured, not claimed:
`python probes/probe2_corrdiff_grid.py --download`.

## Repository

| Path | What |
|---|---|
| `DOCS/` | Concept and architecture documents |
| `probes/` | Cheap experiments that retire the big unknowns — each reproducible, two already run |
| `schema/` | The manifest contract between the Python pipeline and the web app |
| `pipeline/` | Python: forecast (rented GPU, once) and encode (laptop, repeatable) |
| `web/` | TypeScript/Svelte/CesiumJS — the static site |
| `data/` | Committed encoded assets; GPU output is published as a Release asset, never committed |
| `licences/` | Per-asset licence audit manifest |

## Licence

MIT for this repository's code. Models, data sources and colour maps carry their own licences —
audited per asset in `licences/MANIFEST.yaml`. The bundled CorrDiff *sample dataset* is
CC BY-NC-ND 4.0 and is used for local development only; nothing derived from it is published or
committed. Deployed imagery comes exclusively from our own model runs.
