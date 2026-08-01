"""Latent Sky encode pipeline — CPU-side, infinitely repeatable.

Modules:
    ramps     bake 256x1 RGBA LUT PNGs from configs/ramps.yaml (once; committed)
    regrid    curvilinear 2-D lat/lon -> equirectangular nearest-neighbour
    encode    field -> uint8 index -> LUT -> WebP lossless (exact alpha) + identity
    manifest  emit manifest.json validated against schema/manifest.schema.json
    budget    payload ceiling gate
"""

__version__ = "0.1.0"
