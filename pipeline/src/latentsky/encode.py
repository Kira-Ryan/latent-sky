"""Field -> uint8 index -> LUT lookup -> RGBA WebP lossless — §5.5, §7.2, §8.

Exact-alpha finding (tests/test_encode.py PROVES both halves on the installed
stack — Pillow 12.3.0, libwebp via its bundled binaries):

  * `Image.save(..., format="WEBP", lossless=True, exact=True, method=6)`
    round-trips RGBA arrays BIT-IDENTICALLY, including arbitrary RGB values
    under alpha=0.
  * Omitting `exact=True` measurably corrupts RGB under alpha=0 (libwebp's
    documented "cleanup" default), which would silently destroy the transparent
    tail of every presence-field ramp. So `exact=True` is mandatory, no
    fallback path is needed on this stack, and `save_webp()` additionally
    re-decodes every frame it writes and raises on any byte difference — the
    encoder proves its own round-trip on every single frame it emits.

Identity mechanism (§7.2b): every layer carries
    identity = sha256(canonical JSON of {variable, lutSha256, vmin, vmax, alphaPolicy})
computed by `identity_checksum()`. `verify_identity()` fails if two layers of
the same variable disagree; `verify_global_ranges()` fails if any layer used
anything but the GLOBAL vmin/vmax from ramps.yaml. manifest.py runs both gates
before a manifest can exist — per-layer normalisation is impossible to commit,
not merely discouraged.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import pathlib

import numpy as np
from PIL import Image

from .ramps import RampSpec


class EncodeError(RuntimeError):
    """A frame failed to encode losslessly. Never continue past this."""


class RampIdentityError(RuntimeError):
    """Two layers of the same variable carry different ramp identities (§7.2b)."""


class GlobalRangeError(RuntimeError):
    """A layer used non-global vmin/vmax — the per-layer-autoscale failure (§7.1)."""


def load_lut(path: pathlib.Path) -> tuple[np.ndarray, str]:
    """Read a 256x1 RGBA LUT PNG -> ((256, 4) uint8, sha256 of the file bytes)."""
    data = path.read_bytes()
    img = Image.open(pathlib.Path(path))
    if img.mode != "RGBA" or img.size != (256, 1):
        raise ValueError(f"{path} is not a 256x1 RGBA LUT (mode={img.mode}, size={img.size})")
    lut = np.asarray(img, dtype=np.uint8).reshape(256, 4)
    return lut, hashlib.sha256(data).hexdigest()


def identity_checksum(
    variable: str, lut_sha256: str, vmin: float, vmax: float, alpha_policy: dict
) -> str:
    """sha256 over the canonical (variable, lut sha256, vmin, vmax, alpha policy) tuple."""
    canonical = json.dumps(
        {
            "alphaPolicy": alpha_policy,
            "lutSha256": lut_sha256,
            "variable": variable,
            "vmax": float(vmax),
            "vmin": float(vmin),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def quantise(field: np.ndarray, vmin: float, vmax: float) -> tuple[np.ndarray, np.ndarray]:
    """clip/affine-scale to uint8 LUT index — the §5.5 arithmetic, identical both sides.

    Returns (index, finite_mask). NaN samples (regrid pixels outside the source
    footprint) index 0 and are reported invalid so the caller makes them
    transparent rather than painting vmin's colour.
    """
    f = np.asarray(field, dtype=np.float64)
    finite = np.isfinite(f)
    scaled = np.clip((f - vmin) / (vmax - vmin), 0.0, 1.0)
    scaled[~finite] = 0.0
    idx = (scaled * 255.0 + 0.5).astype(np.uint8)
    return idx, finite


def colourise(
    field: np.ndarray,
    lut: np.ndarray,
    vmin: float,
    vmax: float,
    valid: np.ndarray | None = None,
) -> np.ndarray:
    """Field -> RGBA via the shared LUT. Invalid or non-finite pixels become (0,0,0,0)."""
    idx, finite = quantise(field, vmin, vmax)
    rgba = lut[idx]
    mask = finite if valid is None else (finite & valid)
    rgba[~mask] = 0
    return rgba


def save_webp(rgba: np.ndarray, out_path: pathlib.Path) -> int:
    """Write RGBA as WebP lossless with exact alpha, verify the round-trip, return bytes.

    The re-decode is not paranoia: it is the operational form of the §5.5
    guarantee, run on every frame, so a Pillow/libwebp upgrade that breaks
    `exact` semantics fails the build the moment it happens.
    """
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError(f"expected (H, W, 4) uint8 RGBA, got {rgba.dtype} {rgba.shape}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(
        out_path,
        format="WEBP",
        lossless=True,
        exact=True,   # MANDATORY: default rewrites RGB under alpha=0 (§5.5, proven in tests)
        method=6,
    )
    back = np.asarray(Image.open(out_path).convert("RGBA"), dtype=np.uint8)
    if not np.array_equal(rgba, back):
        raise EncodeError(
            f"{out_path}: WebP round-trip is not bit-identical — exact-alpha support "
            "is broken in the installed Pillow/libwebp; refusing to emit corrupt frames"
        )
    return out_path.stat().st_size


def encode_frame(
    field: np.ndarray,
    lut: np.ndarray,
    vmin: float,
    vmax: float,
    out_path: pathlib.Path,
    valid: np.ndarray | None = None,
) -> int:
    """One field -> one verified lossless WebP. Returns the file size in bytes."""
    return save_webp(colourise(field, lut, vmin, vmax, valid), out_path)


@dataclasses.dataclass
class LayerRecord:
    """Everything the manifest needs about one emitted layer, as ACTUALLY encoded.

    vmin/vmax here are what the frames were quantised with — manifest.py compares
    them against ramps.yaml, so a per-layer override cannot survive to a manifest.
    """

    layer_id: str
    kind: str            # "global" | "hero-fine" | "hero-coarse"
    variable: str
    label: str
    units: str
    rect: list[float]    # [west, south, east, north]
    size: list[int]      # [width, height]
    lut: str             # path relative to the manifest
    vmin: float
    vmax: float
    identity: str
    frames: list[str]    # relative WebP path per frame, same order as manifest frames
    pair_with: str | None = None
    native_km: float | None = None   # source model resolution, stated by the UI


def make_layer_record(
    layer_id: str,
    kind: str,
    spec: RampSpec,
    lut_sha256: str,
    lut_rel_path: str,
    rect: list[float],
    size: list[int],
    frames: list[str],
    label_suffix: str = "",
    pair_with: str | None = None,
    native_km: float | None = None,
) -> LayerRecord:
    """Build a LayerRecord whose identity is derived from the GLOBAL RampSpec."""
    return LayerRecord(
        layer_id=layer_id,
        kind=kind,
        variable=spec.variable,
        label=spec.label + label_suffix,
        units=spec.units,
        rect=rect,
        size=size,
        lut=lut_rel_path,
        vmin=spec.vmin,
        vmax=spec.vmax,
        identity=identity_checksum(spec.variable, lut_sha256, spec.vmin, spec.vmax, spec.alpha),
        frames=frames,
        pair_with=pair_with,
        native_km=native_km,
    )


def verify_identity(layers: list[LayerRecord]) -> None:
    """§7.2(b) gate 1: all layers of one variable must share one identity checksum."""
    by_variable: dict[str, dict[str, list[str]]] = {}
    for layer in layers:
        by_variable.setdefault(layer.variable, {}).setdefault(layer.identity, []).append(
            layer.layer_id
        )
    conflicts = {var: ids for var, ids in by_variable.items() if len(ids) > 1}
    if conflicts:
        detail = "; ".join(
            f"{var}: " + " vs ".join(f"{sha[:12]}…({', '.join(layers)})" for sha, layers in ids.items())
            for var, ids in conflicts.items()
        )
        raise RampIdentityError(
            f"coarse/fine ramp identity mismatch — the reveal would compare two colour "
            f"pipelines, not two resolutions. {detail}"
        )


def verify_global_ranges(layers: list[LayerRecord], specs: dict[str, RampSpec]) -> None:
    """§7.2(b) gate 2: every layer must use the GLOBAL vmin/vmax from ramps.yaml."""
    for layer in layers:
        spec = specs.get(layer.variable)
        if spec is None:
            raise GlobalRangeError(f"layer {layer.layer_id}: variable {layer.variable!r} has no ramp")
        if layer.vmin != spec.vmin or layer.vmax != spec.vmax:
            raise GlobalRangeError(
                f"layer {layer.layer_id} used vmin/vmax {layer.vmin}..{layer.vmax} but the "
                f"GLOBAL range for {layer.variable!r} is {spec.vmin}..{spec.vmax}. Per-layer "
                "normalisation erases exactly the finding the hero exists to show (§7.1)."
            )
