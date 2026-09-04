"""First-class run hints: stormName / heroFrame / placeLabel.

The art pass flagged two heuristics — the UI regex-parsing the storm name out of
run.generatedNote and guessing the hero frame as "last". These fields make both
facts data. Tests cover the config->run mapping, the heroFrame bounds gate in
build_manifest (a JSON schema cannot cross-reference frames), schema acceptance,
and that every committed event config carries hints consistent with its own
hero window.
"""

from __future__ import annotations

import pathlib

import jsonschema
import pytest
import yaml

from latentsky import encode, manifest
from latentsky.encode_dev import DEV_HINTS

PIPELINE_DIR = pathlib.Path(__file__).resolve().parents[1]
EVENT_CONFIGS = sorted((PIPELINE_DIR / "configs").glob("event_*.yaml"))

RUN_BASE = {
    "id": "test",
    "kind": "dev-sample",
    "model": {"prognostic": "x", "downscaling": "y"},
    "generatedNote": "test",
}
THREE_TIMES = ["2021-01-01T00:00:00Z", "2021-01-01T06:00:00Z", "2021-01-01T12:00:00Z"]


def _wind_record(specs, lut_dir):
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    return encode.LayerRecord(
        layer_id="wind10m-fine",
        kind="hero-fine",
        variable=spec.variable,
        label=spec.label,
        units=spec.units,
        rect=[116.0, 19.0, 126.0, 28.0],
        size=[8, 8],
        lut=f"luts/{spec.lut_filename}",
        vmin=spec.vmin,
        vmax=spec.vmax,
        identity=encode.identity_checksum(spec.variable, sha, spec.vmin, spec.vmax, spec.alpha),
        frames=["a.webp", "b.webp", "c.webp"],
    )


# ------------------------------------------------------------------ run_hints

def test_run_hints_maps_config_keys_to_schema_fields():
    hints = manifest.run_hints(
        {"storm_name": "Typhoon Gaemi", "hero_frame": 5,
         "place_label": "Taiwan · CWA model domain", "nsteps": 20}
    )
    assert hints == {
        "stormName": "Typhoon Gaemi",
        "heroFrame": 5,
        "placeLabel": "Taiwan · CWA model domain",
    }


def test_run_hints_absent_keys_stay_absent():
    """No hints in the config -> no hint fields in the run object — the web app
    must see genuine absence, not empty strings, to trigger its fallbacks."""
    assert manifest.run_hints({"init": "2024-07-24T00:00:00"}) == {}
    assert manifest.run_hints({"storm_name": None, "hero_frame": None}) == {}
    assert manifest.run_hints({"hero_frame": 0}) == {"heroFrame": 0}


@pytest.mark.parametrize(
    "cfg,match",
    [
        ({"hero_frame": -1}, "hero_frame"),
        ({"hero_frame": "3"}, "hero_frame"),
        ({"hero_frame": 3.0}, "hero_frame"),
        ({"hero_frame": True}, "hero_frame"),
        ({"storm_name": ""}, "storm_name"),
        ({"storm_name": "   "}, "storm_name"),
        ({"storm_name": 7}, "storm_name"),
        ({"place_label": ""}, "place_label"),
    ],
)
def test_run_hints_rejects_bad_values_loudly(cfg, match):
    with pytest.raises(manifest.ManifestError, match=match):
        manifest.run_hints(cfg)


# ------------------------------------------------------ build_manifest gating

def test_build_manifest_carries_hints_and_validates_against_schema(specs, lut_dir, tmp_path):
    record = _wind_record(specs, lut_dir)
    run = {**RUN_BASE, **manifest.run_hints(DEV_HINTS), "heroFrame": 2}
    built = manifest.build_manifest(run, THREE_TIMES, [record], specs)
    assert built["run"]["stormName"] == "Typhoon Chanthu"
    assert built["run"]["heroFrame"] == 2
    assert built["run"]["placeLabel"] == "Taiwan · CWA model domain"
    # And the extended schema accepts them (write_manifest's gate 5, run directly
    # so no frame files are needed on disk).
    jsonschema.Draft202012Validator(manifest.load_schema()).validate(built)


@pytest.mark.parametrize("hero_frame", [3, 99, -1])
def test_build_manifest_rejects_out_of_range_hero_frame(specs, lut_dir, hero_frame):
    record = _wind_record(specs, lut_dir)
    run = {**RUN_BASE, "heroFrame": hero_frame}
    with pytest.raises(manifest.ManifestError, match="heroFrame"):
        manifest.build_manifest(run, THREE_TIMES, [record], specs)


def test_build_manifest_rejects_non_integer_hero_frame(specs, lut_dir):
    record = _wind_record(specs, lut_dir)
    for bad in (True, 1.0, "1"):
        with pytest.raises(manifest.ManifestError, match="heroFrame"):
            manifest.build_manifest({**RUN_BASE, "heroFrame": bad}, THREE_TIMES, [record], specs)


def test_schema_rejects_wrongly_typed_hints():
    schema = manifest.load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    base = {
        "schemaVersion": 1,
        "run": dict(RUN_BASE),
        "frames": THREE_TIMES,
        "layers": {
            "l": {
                "kind": "hero-fine", "variable": "wind10m", "label": "x", "units": "m/s",
                "rect": [1.0, 2.0, 3.0, 4.0], "size": [8, 8], "lut": "luts/x.png",
                "vmin": 0.0, "vmax": 55.0, "identity": "f" * 64,
                "frames": ["a.webp", "b.webp", "c.webp"],
            }
        },
    }
    validator.validate(base)  # sanity: valid without hints
    for field, bad in (("stormName", 3), ("stormName", ""), ("heroFrame", "3"),
                       ("heroFrame", -1), ("placeLabel", 3)):
        broken = {**base, "run": {**RUN_BASE, field: bad}}
        assert not validator.is_valid(broken), f"schema accepted run.{field}={bad!r}"


# ------------------------------------------------------------- event configs

def test_dev_hints_are_the_specified_values():
    """The dev sample's hints are load-bearing for the UI: frame 3 IS the
    2021-09-12T00Z Typhoon Chanthu frame of the five-frame dev sample."""
    assert DEV_HINTS == {
        "storm_name": "Typhoon Chanthu",
        "hero_frame": 3,
        "place_label": "Taiwan · CWA model domain",
    }


@pytest.mark.parametrize("path", EVENT_CONFIGS, ids=lambda p: p.stem)
def test_every_event_config_carries_consistent_hints(path):
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    hints = manifest.run_hints(cfg)
    assert {"stormName", "heroFrame", "placeLabel"} <= set(hints) <= {
        "stormName", "heroFrame", "placeLabel", "reportUrl", "defaultVariable"
    }, (
        f"{path.name}: every event config must carry all three run hints"
    )
    assert hints["stormName"].strip(), f"{path.name}: stormName must not be blank"

    # The hero window differs by model family. A CorrDiff event downscales only
    # the first hero_steps steps, so its window is hero_steps frames. A StormCast
    # event is convection-allowing throughout — every step IS hero output — so its
    # window is the whole run, nsteps + 1 frames including the initial condition.
    window = cfg["hero_steps"] if "hero_steps" in cfg else cfg["nsteps"] + 1
    assert 0 <= hints["heroFrame"] < window, (
        f"{path.name}: hero_frame {hints['heroFrame']} outside the "
        f"{window}-frame hero window"
    )


def test_event_configs_exist():
    """The shipped events are all present and parse — NOT a frozen count.

    Asserting an exact number fails every time an event is added, which is a data
    change the config directory exists to make cheap.
    """
    stems = {p.stem for p in EVENT_CONFIGS}
    for expected in ("event_gaemi_2024", "event_doksuri_2023", "event_dixie_2025"):
        assert expected in stems, f"{expected}.yaml missing from configs/"
    for path in EVENT_CONFIGS:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        for key in ("init", "nsteps", "coarse_variables", "output"):
            assert key in cfg, f"{path.name}: missing required key {key!r}"


def test_report_becomes_report_url():
    assert manifest.run_hints({"report": "/verification/x.html"}) == {"reportUrl": "/verification/x.html"}


def test_default_variable_becomes_a_run_hint():
    assert manifest.run_hints({"default_variable": "refc"}) == {"defaultVariable": "refc"}
