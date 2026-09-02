"""catalogue.py — the top-level event index and its derivation gates.

Everything here is synthetic: manifests are written into tmp dirs so the
two-event case can be proved BEFORE either real hero event exists. The last
section asserts the shipped data/web/catalogue.json itself.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from latentsky import catalogue

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA_WEB = REPO_ROOT / "data" / "web"
PIPELINE_DIR = pathlib.Path(__file__).resolve().parents[1]
REGISTRY = PIPELINE_DIR / "configs" / "catalogue.yaml"


# ------------------------------------------------------------------ fixtures/helpers

def _layer(kind: str, variable: str = "wind10m") -> dict:
    return {
        "kind": kind,
        "variable": variable,
        "label": f"{variable} {kind}",
        "units": "m/s",
        "rect": [116.0, 19.5, 125.6, 28.0],
        "size": [8, 8],
        "lut": "luts/wind10m.lut.png",
        "vmin": 0.0,
        "vmax": 55.0,
        "identity": "a" * 64,
        "frames": ["layers/x/000.webp"],
    }


def write_manifest(root: pathlib.Path, rel: str, run_id: str, layers: dict) -> pathlib.Path:
    """A minimal manifest that really passes manifest.schema.json."""
    manifest = {
        "schemaVersion": 1,
        "run": {
            "id": run_id,
            "kind": "dev-sample",
            "model": {"prognostic": "SFNO", "downscaling": "CorrDiffTaiwan"},
            "generatedNote": "Synthetic fixture.",
        },
        "frames": ["2024-07-24T00:00:00Z"],
        "layers": layers,
    }
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def global_only_manifest(root: pathlib.Path, rel: str = "manifest.json",
                         run_id: str = "global-event") -> pathlib.Path:
    return write_manifest(root, rel, run_id, {"wind10m-global": _layer("global")})


def hero_manifest(root: pathlib.Path, rel: str = "taiwan/manifest.json",
                  run_id: str = "hero-event") -> pathlib.Path:
    return write_manifest(root, rel, run_id, {
        "wind10m-global": _layer("global"),
        "wind10m-coarse": _layer("hero-coarse"),
        "wind10m-fine": _layer("hero-fine"),
    })


def spec(event_id: str, rel: str, region: str = "global", default: bool = False) -> catalogue.EventSpec:
    return catalogue.EventSpec(
        id=event_id, title=f"Title {event_id}", subtitle="One honest line",
        manifest=rel, region=region, default=default,
    )


# ------------------------------------------------------------------ derivation

def test_hero_is_derived_from_a_hero_fine_layer(tmp_path):
    hero_manifest(tmp_path, "manifest.json")
    facts = catalogue.inspect_manifest(tmp_path / "manifest.json")
    assert facts == {"hasHero": True, "kind": "hero", "runId": "hero-event"}


def test_global_only_is_derived_when_no_hero_fine_layer_exists(tmp_path):
    global_only_manifest(tmp_path)
    facts = catalogue.inspect_manifest(tmp_path / "manifest.json")
    assert facts == {"hasHero": False, "kind": "global-only", "runId": "global-event"}


def test_hero_coarse_alone_is_not_a_hero_event(tmp_path):
    """A 'before' with no 'after' is not a reveal — kind must stay global-only."""
    write_manifest(tmp_path, "manifest.json", "coarse-only", {
        "wind10m-global": _layer("global"),
        "wind10m-coarse": _layer("hero-coarse"),
    })
    assert catalogue.inspect_manifest(tmp_path / "manifest.json")["hasHero"] is False


def test_derivation_ignores_what_the_registry_says(tmp_path):
    """The registry carries no kind/hasHero at all — the manifest decides."""
    hero_manifest(tmp_path, "manifest.json")
    built = catalogue.build_catalogue([spec("e", "manifest.json", "taiwan", True)], tmp_path)
    assert built["events"][0]["kind"] == "hero"
    assert built["events"][0]["hasHero"] is True
    assert built["events"][0]["region"] == "taiwan", "region stays editorial"


def test_registry_refuses_a_declared_capability(tmp_path):
    cfg = tmp_path / "catalogue.yaml"
    cfg.write_text(
        "events:\n"
        "  - id: e\n    title: T\n    subtitle: S\n    manifest: manifest.json\n"
        "    region: global\n    default: true\n    hasHero: true\n",
        encoding="utf-8",
    )
    with pytest.raises(catalogue.CatalogueError, match="derived from the manifest"):
        catalogue.load_registry(cfg)


# ------------------------------------------------------------------ default gates

def test_zero_defaults_fails(tmp_path):
    global_only_manifest(tmp_path)
    with pytest.raises(catalogue.CatalogueError, match="nothing to open"):
        catalogue.build_catalogue([spec("a", "manifest.json")], tmp_path)


def test_multiple_defaults_fails_and_names_them(tmp_path):
    global_only_manifest(tmp_path, "manifest.json", "a")
    global_only_manifest(tmp_path, "second/manifest.json", "b")
    specs = [spec("a", "manifest.json", default=True),
             spec("b", "second/manifest.json", default=True)]
    with pytest.raises(catalogue.CatalogueError, match=r"found 2: \['a', 'b'\]"):
        catalogue.build_catalogue(specs, tmp_path)


def test_schema_itself_rejects_a_hand_edited_second_default(tmp_path):
    """The browser fetches the catalogue without the builder, so the exactly-one
    rule has to live in the schema too (minContains/maxContains)."""
    global_only_manifest(tmp_path)
    built = catalogue.build_catalogue([spec("a", "manifest.json", default=True)], tmp_path)
    built["events"].append(dict(built["events"][0], id="b", manifest="b.json"))
    with pytest.raises(catalogue.CatalogueError, match="fails schema validation"):
        catalogue.write_catalogue(built, tmp_path)


def test_schema_rejects_kind_hasHero_disagreement(tmp_path):
    global_only_manifest(tmp_path)
    built = catalogue.build_catalogue([spec("a", "manifest.json", default=True)], tmp_path)
    built["events"][0]["hasHero"] = True  # kind is still "global-only"
    with pytest.raises(catalogue.CatalogueError, match="fails schema validation"):
        catalogue.write_catalogue(built, tmp_path)


# ------------------------------------------------------------------ manifest gates

def test_missing_manifest_fails_loudly(tmp_path):
    with pytest.raises(catalogue.CatalogueError, match="manifest does not exist"):
        catalogue.build_catalogue([spec("a", "manifest.json", default=True)], tmp_path)


def test_one_missing_manifest_fails_the_whole_catalogue(tmp_path):
    global_only_manifest(tmp_path, "manifest.json", "a")
    specs = [spec("a", "manifest.json", default=True), spec("b", "taiwan/manifest.json", "taiwan")]
    with pytest.raises(catalogue.CatalogueError, match="taiwan"):
        catalogue.build_catalogue(specs, tmp_path)
    assert not (tmp_path / catalogue.CATALOGUE_NAME).exists(), "nothing written on a failed gate"


def test_schema_invalid_manifest_fails(tmp_path):
    """A catalogue that validates while pointing at a broken manifest converts a
    build error into a browser error — so the manifest's own schema is a gate."""
    path = global_only_manifest(tmp_path)
    broken = json.loads(path.read_text(encoding="utf-8"))
    del broken["frames"]
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(catalogue.CatalogueError, match="fails manifest.schema.json"):
        catalogue.build_catalogue([spec("a", "manifest.json", default=True)], tmp_path)


def test_unparseable_manifest_fails(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(catalogue.CatalogueError, match="not valid JSON"):
        catalogue.build_catalogue([spec("a", "manifest.json", default=True)], tmp_path)


def test_manifest_path_may_not_escape_the_root(tmp_path):
    global_only_manifest(tmp_path)
    escaping = catalogue.EventSpec(
        id="a", title="T", subtitle="S", manifest="../elsewhere/manifest.json",
        region="global", default=True,
    )
    with pytest.raises(catalogue.CatalogueError, match="escapes the catalogue root"):
        catalogue.build_catalogue([escaping], tmp_path)


def test_duplicate_ids_and_duplicate_manifests_fail(tmp_path):
    global_only_manifest(tmp_path, "manifest.json", "a")
    with pytest.raises(catalogue.CatalogueError, match="duplicate event id"):
        catalogue.build_catalogue(
            [spec("a", "manifest.json", default=True), spec("a", "b.json")], tmp_path)
    with pytest.raises(catalogue.CatalogueError, match="one manifest, one event"):
        catalogue.build_catalogue(
            [spec("a", "manifest.json", default=True), spec("b", "manifest.json")], tmp_path)


# ------------------------------------------------------------------ two events

def test_two_event_catalogue_holds_both_and_keeps_declared_order(tmp_path):
    """The whole point of the schema: prove it carries a global-only event and a
    hero event side by side BEFORE either real hero exists."""
    global_only_manifest(tmp_path, "manifest.json", "public-era5")
    hero_manifest(tmp_path, "taiwan/manifest.json", "taiwan-doksuri")
    specs = [
        spec("public-era5-gaemi-week-2024", "manifest.json", "global", default=True),
        spec("taiwan-doksuri-2023", "taiwan/manifest.json", "taiwan"),
    ]
    path = catalogue.emit(specs, tmp_path)
    written = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "catalogue.json"
    assert written["schemaVersion"] == 1
    assert [e["id"] for e in written["events"]] == [s.id for s in specs], "display order is array order"

    public, taiwan = written["events"]
    assert (public["kind"], public["hasHero"], public["default"]) == ("global-only", False, True)
    assert (taiwan["kind"], taiwan["hasHero"], taiwan["default"]) == ("hero", True, False)
    assert taiwan["region"] == "taiwan"
    # The app resolves this against the catalogue URL — it must land on the file.
    assert (tmp_path / taiwan["manifest"]).is_file()


def test_a_third_conus_event_needs_no_schema_change(tmp_path):
    """March 2025 US tornado outbreak, StormCast v1 — a data change, not a schema one."""
    global_only_manifest(tmp_path, "manifest.json", "public-era5")
    hero_manifest(tmp_path, "taiwan/manifest.json", "taiwan-doksuri")
    hero_manifest(tmp_path, "conus/manifest.json", "us-tornado")
    specs = [
        spec("public-era5-gaemi-week-2024", "manifest.json", "global"),
        spec("taiwan-doksuri-2023", "taiwan/manifest.json", "taiwan"),
        spec("us-tornado-outbreak-2025-03", "conus/manifest.json", "conus", default=True),
    ]
    written = json.loads(catalogue.emit(specs, tmp_path).read_text(encoding="utf-8"))
    assert [e["region"] for e in written["events"]] == ["global", "taiwan", "conus"]
    assert [e["default"] for e in written["events"]] == [False, False, True]


def test_single_event_subset_forces_default(tmp_path):
    """What a per-event encoder emits: its own entry, default true, even though
    the registry marks a different event as the site default."""
    specs = [spec("a", "manifest.json", default=True), spec("b", "taiwan/manifest.json", "taiwan")]
    subset = catalogue.single_event(specs, "b")
    assert [s.id for s in subset] == ["b"]
    assert subset[0].default is True
    assert specs[1].default is False, "the registry specs must never be mutated"
    with pytest.raises(catalogue.CatalogueError, match="found 0"):
        catalogue.single_event(specs, "nope")


def test_emit_is_deterministic(tmp_path):
    global_only_manifest(tmp_path)
    first = catalogue.emit([spec("a", "manifest.json", default=True)], tmp_path).read_bytes()
    second = catalogue.emit([spec("a", "manifest.json", default=True)], tmp_path).read_bytes()
    assert first == second


# ------------------------------------------------------------------ the registry

def test_committed_registry_parses_and_matches_the_shipped_run_id():
    """Invariants of the shipped registry — NOT a frozen event list.

    Asserting the exact set of ids would fail every time an event is added, which
    is a data change the registry exists to make cheap. What must hold is: ids are
    unique, exactly one entry opens by default, that entry is the root-level global
    event, and every other event lives in its own subtree (which is what lets
    encode_public.registered_event_dirs tell a real event directory from a stray).
    """
    specs = catalogue.load_registry(REGISTRY)
    ids = [s.id for s in specs]
    assert len(set(ids)) == len(ids), f"duplicate ids in the registry: {ids}"
    assert sum(s.default for s in specs) == 1

    default = next(s for s in specs if s.default)
    assert default.id == "public-era5-gaemi-week-2024"
    assert default.manifest == "manifest.json"

    for s in specs:
        if not s.default:
            assert "/" in s.manifest, f"{s.id}: expected a subtree manifest, got {s.manifest}"


def test_registry_rejects_missing_and_unknown_keys(tmp_path):
    cfg = tmp_path / "catalogue.yaml"
    cfg.write_text("events:\n  - id: e\n    title: T\n", encoding="utf-8")
    with pytest.raises(catalogue.CatalogueError, match=r"missing \['manifest', 'region', 'subtitle'\]"):
        catalogue.load_registry(cfg)
    cfg.write_text(
        "events:\n  - id: e\n    title: T\n    subtitle: S\n    manifest: m.json\n"
        "    region: global\n    colour: blue\n",
        encoding="utf-8",
    )
    with pytest.raises(catalogue.CatalogueError, match=r"unknown keys \['colour'\]"):
        catalogue.load_registry(cfg)


def test_registry_rejects_an_empty_events_list(tmp_path):
    cfg = tmp_path / "catalogue.yaml"
    cfg.write_text("events: []\n", encoding="utf-8")
    with pytest.raises(catalogue.CatalogueError, match="non-empty list"):
        catalogue.load_registry(cfg)


# ------------------------------------------------------------------ the committed tree

def test_committed_catalogue_indexes_the_shipped_manifest():
    """The artefact the site actually loads first."""
    path = DATA_WEB / catalogue.CATALOGUE_NAME
    if not path.is_file():
        pytest.skip("data/web not yet encoded — run: python -m latentsky.encode_public")
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["schemaVersion"] == 1
    assert len(written["events"]) >= 1
    assert sum(e["default"] for e in written["events"]) == 1
    for event in written["events"]:
        target = DATA_WEB / event["manifest"]
        assert target.is_file(), f"{event['id']}: catalogue points at missing {event['manifest']}"
        facts = catalogue.inspect_manifest(target)
        assert event["kind"] == facts["kind"] and event["hasHero"] == facts["hasHero"], (
            f"{event['id']}: committed catalogue disagrees with its own manifest"
        )
