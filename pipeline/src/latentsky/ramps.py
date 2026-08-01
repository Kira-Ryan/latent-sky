"""Bake 256x1 RGBA LUT PNGs from configs/ramps.yaml — Architecture.md §7.2(a).

The ramp is baked ONCE, committed to pipeline/luts/, and the encoder only ever
indexes the PNG. Encode time never touches a colormap library, which insulates
the pipeline against colormap-package drift (cmweather's last PyPI release is
0.3.2, January 2024) and makes colour identity a property of a vendored file.

Determinism: the same ramps.yaml must produce byte-identical PNGs. The bake
samples each named colormap at 256 evenly spaced points (all five ramps are
native 256-entry tables, so sampling is exact), applies the alpha policy in
physical-value space, and writes the PNG through a fixed Pillow parameter set.
`bake()` re-encodes every LUT in memory and raises if the second encode differs
from the first, so every run proves its own determinism.

CLI:
    python -m latentsky.ramps [--config configs/ramps.yaml] [--out luts/]
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import pathlib

import numpy as np
import yaml
from PIL import Image

PIPELINE_DIR = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PIPELINE_DIR / "configs" / "ramps.yaml"
DEFAULT_LUT_DIR = PIPELINE_DIR / "luts"

_ALLOWED_CMAP_SOURCES = ("cmcrameri", "cmocean", "cmweather")


@dataclasses.dataclass(frozen=True)
class RampSpec:
    """One §7.1 table row. vmin/vmax are GLOBAL for the variable — never overridden."""

    variable: str
    label: str
    units: str
    cmap: str
    vmin: float
    vmax: float
    alpha: dict  # canonical alpha policy, see alpha_policy()
    midpoint: float | None = None

    @property
    def lut_filename(self) -> str:
        return f"{self.variable}.lut.png"


def alpha_policy(raw: dict) -> dict:
    """Canonicalise an alpha policy dict. This exact form feeds the §7.2 identity hash."""
    policy = raw.get("policy")
    if policy == "opaque":
        return {"policy": "opaque"}
    if policy == "ramp":
        zero_below = float(raw["zero_below"])
        one_by = float(raw["one_by"])
        if not one_by > zero_below:
            raise ValueError(f"alpha ramp requires one_by > zero_below, got {raw}")
        return {"policy": "ramp", "zeroBelow": zero_below, "oneBy": one_by}
    raise ValueError(f"unknown alpha policy: {raw!r}")


def load_ramps(config_path: pathlib.Path = DEFAULT_CONFIG) -> dict[str, RampSpec]:
    """Load and validate ramps.yaml into RampSpec objects, keyed by variable name."""
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{config_path} did not parse to a non-empty mapping")

    specs: dict[str, RampSpec] = {}
    for variable, entry in raw.items():
        vmin, vmax = float(entry["vmin"]), float(entry["vmax"])
        if not vmax > vmin:
            raise ValueError(f"{variable}: vmax must exceed vmin, got {vmin}..{vmax}")
        source = str(entry["cmap"]).split(":", 1)[0]
        if source not in _ALLOWED_CMAP_SOURCES:
            raise ValueError(f"{variable}: cmap source {source!r} not one of {_ALLOWED_CMAP_SOURCES}")
        specs[variable] = RampSpec(
            variable=variable,
            label=str(entry["label"]),
            units=str(entry["units"]),
            cmap=str(entry["cmap"]),
            vmin=vmin,
            vmax=vmax,
            alpha=alpha_policy(entry["alpha"]),
            midpoint=float(entry["midpoint"]) if "midpoint" in entry else None,
        )
    return specs


def _resolve_cmap(qualified: str):
    """'package:name' -> matplotlib colormap object. Only called at BAKE time."""
    source, name = qualified.split(":", 1)
    if source == "cmcrameri":
        import cmcrameri.cm as cmc

        return getattr(cmc, name)
    if source == "cmocean":
        import cmocean.cm as cmo

        return getattr(cmo, name)
    if source == "cmweather":
        import cmweather  # noqa: F401  (import registers its colormaps with matplotlib)
        import matplotlib

        return matplotlib.colormaps[name]
    raise ValueError(f"unknown colormap source in {qualified!r}")


def lut_rgba(spec: RampSpec) -> np.ndarray:
    """(256, 4) uint8 RGBA table: colormap RGB + alpha policy applied in value space."""
    cmap = _resolve_cmap(spec.cmap)
    rgba = np.asarray(cmap(np.linspace(0.0, 1.0, 256)), dtype=np.float64)  # (256, 4) in 0..1
    out = np.empty((256, 4), dtype=np.uint8)
    out[:, :3] = (rgba[:, :3] * 255.0 + 0.5).astype(np.uint8)

    values = spec.vmin + np.arange(256, dtype=np.float64) * (spec.vmax - spec.vmin) / 255.0
    if spec.alpha["policy"] == "opaque":
        out[:, 3] = 255
    else:
        a = np.clip(
            (values - spec.alpha["zeroBelow"]) / (spec.alpha["oneBy"] - spec.alpha["zeroBelow"]),
            0.0,
            1.0,
        )
        out[:, 3] = (a * 255.0 + 0.5).astype(np.uint8)
    return out


def lut_png_bytes(spec: RampSpec) -> bytes:
    """Encode the 256x1 LUT as PNG bytes with a fixed, deterministic parameter set."""
    img = Image.fromarray(lut_rgba(spec)[np.newaxis, :, :], mode="RGBA")  # 1 row, 256 cols
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=9)
    return buf.getvalue()


def bake(
    config_path: pathlib.Path = DEFAULT_CONFIG,
    out_dir: pathlib.Path = DEFAULT_LUT_DIR,
) -> dict[str, pathlib.Path]:
    """Bake every ramp to out_dir/<var>.lut.png. Returns {variable: path}.

    Every run double-encodes in memory and fails loudly on any byte difference,
    so non-determinism can never land silently.
    """
    specs = load_ramps(config_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, pathlib.Path] = {}
    for variable, spec in specs.items():
        first = lut_png_bytes(spec)
        second = lut_png_bytes(spec)
        if first != second:
            raise RuntimeError(
                f"LUT bake for {variable!r} is non-deterministic: two in-memory encodes differ"
            )
        path = out_dir / spec.lut_filename
        path.write_bytes(first)
        paths[variable] = path
    return paths


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_LUT_DIR)
    args = ap.parse_args(argv)

    paths = bake(args.config, args.out)
    for variable, path in paths.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"  {variable:10s} {path.name:20s} {path.stat().st_size:5d} B  sha256 {digest}")
    print(f"\nbaked {len(paths)} LUTs -> {args.out} (double-encode determinism check passed)")


if __name__ == "__main__":
    main()
