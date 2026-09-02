"""Emit catalogue.json — the top-level event index the web app loads FIRST.

The manifest is the contract for ONE event (schema/manifest.schema.json). The
catalogue is the contract for WHICH events exist (schema/catalogue.schema.json):
a short list of {id, title, subtitle, manifest, kind, region, hasHero, default}
that the app fetches before any manifest, so it can draw a switcher — or, with a
single entry, draw no switcher at all — without downloading megabytes to find out
what it has.

The load-bearing rule here is that **the capability fields are derived, never
declared**. `kind` and `hasHero` come from opening each referenced manifest and
looking for a hero-fine layer. A registry entry cannot promise a coarse/fine
reveal the data does not carry, which is exactly the failure mode a hand-written
index invites: the switcher offering "the reveal" for an event whose hero encode
has not landed yet.

Gates, all fatal — nothing is written unless every one passes:

    1. the registry parses, every entry carries exactly the expected keys
    2. ids are unique, and no two entries point at the same manifest
    3. exactly one entry is marked default
    4. every referenced manifest EXISTS under the catalogue root
    5. every referenced manifest passes manifest.schema.json itself
    6. the assembled catalogue passes catalogue.schema.json

Gate 5 matters more than it looks: the catalogue is the app's entry point, so a
catalogue that validates while pointing at a broken manifest converts a build
error into a runtime one, in the browser, for the visitor.

CLI:
    python -m latentsky.catalogue --root data/web [--config configs/catalogue.yaml]
"""

from __future__ import annotations

import argparse
import dataclasses
import functools
import json
import pathlib

import jsonschema
import yaml

from . import manifest as manifest_mod

PIPELINE_DIR = pathlib.Path(__file__).resolve().parents[2]
REPO_ROOT = PIPELINE_DIR.parent
DEFAULT_CONFIG = PIPELINE_DIR / "configs" / "catalogue.yaml"
CATALOGUE_SCHEMA_PATH = REPO_ROOT / "schema" / "catalogue.schema.json"
CATALOGUE_NAME = "catalogue.json"

# A manifest is a "hero" event iff it declares at least one layer of this kind.
# hero-coarse alone is deliberately NOT enough: the coarse layer is the "before"
# of a comparison, and a before with no after is not a reveal (Architecture §6.3).
HERO_FINE_KIND = "hero-fine"

# The registry keys an entry may carry. kind/hasHero are absent on purpose —
# they are derived from the manifest, and accepting them here would let a config
# edit contradict the data.
_REQUIRED_KEYS = frozenset({"id", "title", "subtitle", "manifest", "region"})
_OPTIONAL_KEYS = frozenset({"default"})
_DERIVED_KEYS = frozenset({"kind", "hasHero"})


class CatalogueError(RuntimeError):
    """The catalogue could not be emitted. The build must stop."""


@dataclasses.dataclass(frozen=True)
class EventSpec:
    """One registry row: the display metadata a human writes for an event.

    Everything here is editorial. The capability fields (kind, hasHero) are not
    part of this record because they are read out of the manifest instead.
    """

    id: str
    title: str
    subtitle: str
    manifest: str  # POSIX-relative to the catalogue file
    region: str
    default: bool = False


def load_schema(schema_path: pathlib.Path = CATALOGUE_SCHEMA_PATH) -> dict:
    with open(schema_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def _manifest_validator() -> jsonschema.Draft202012Validator:
    """The committed manifest schema, compiled once — a catalogue indexes several."""
    return jsonschema.Draft202012Validator(manifest_mod.load_schema())


def _schema_errors(validator: jsonschema.Draft202012Validator, doc: dict) -> str:
    """Every violation, JSON-path first. Empty string when the document is valid."""
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    return "\n  ".join(
        f"at {'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
        for err in errors
    )


def load_registry(config_path: pathlib.Path = DEFAULT_CONFIG) -> list[EventSpec]:
    """Parse configs/catalogue.yaml into EventSpecs, in declared display order.

    Strict about shape and silent about content: enums and patterns (region,
    id casing, manifest path safety) live in catalogue.schema.json and are
    enforced once, at write time, so the two cannot drift apart.
    """
    config_path = pathlib.Path(config_path)
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict) or "events" not in raw:
        raise CatalogueError(f"{config_path}: expected a mapping with an 'events' key")
    events = raw["events"]
    if not isinstance(events, list) or not events:
        raise CatalogueError(f"{config_path}: 'events' must be a non-empty list")

    specs: list[EventSpec] = []
    for i, entry in enumerate(events):
        where = f"{config_path} events[{i}]"
        if not isinstance(entry, dict):
            raise CatalogueError(f"{where}: expected a mapping, got {type(entry).__name__}")
        keys = set(entry)
        derived = keys & _DERIVED_KEYS
        if derived:
            raise CatalogueError(
                f"{where}: {sorted(derived)} is derived from the manifest, not declared — "
                "remove it. A registry that can claim a hero is a registry that can lie."
            )
        missing = _REQUIRED_KEYS - keys
        if missing:
            raise CatalogueError(f"{where}: missing {sorted(missing)}")
        unknown = keys - _REQUIRED_KEYS - _OPTIONAL_KEYS
        if unknown:
            raise CatalogueError(f"{where}: unknown keys {sorted(unknown)}")
        for key in sorted(_REQUIRED_KEYS):
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise CatalogueError(f"{where}: {key} must be a non-empty string, got {entry[key]!r}")
        is_default = entry.get("default", False)
        if not isinstance(is_default, bool):
            raise CatalogueError(f"{where}: default must be true or false, got {is_default!r}")
        specs.append(
            EventSpec(
                id=entry["id"],
                title=entry["title"],
                subtitle=entry["subtitle"],
                manifest=entry["manifest"],
                region=entry["region"],
                default=is_default,
            )
        )
    return specs


def single_event(specs: list[EventSpec], event_id: str) -> list[EventSpec]:
    """The one-entry subset for `event_id`, forced default.

    Used by a per-event encoder that owns exactly one manifest: it emits a
    catalogue describing what it just wrote, so its output tree is always a
    working deployable on its own. The multi-event catalogue is then a separate
    build step over the full registry (the CLI below).
    """
    matches = [s for s in specs if s.id == event_id]
    if len(matches) != 1:
        raise CatalogueError(
            f"expected exactly one registry entry with id {event_id!r}, found {len(matches)} "
            f"in {[s.id for s in specs]}"
        )
    return [dataclasses.replace(matches[0], default=True)]


def inspect_manifest(path: pathlib.Path) -> dict:
    """Open, schema-validate and read the capability bits out of one manifest.

    Returns {"hasHero": bool, "kind": "hero"|"global-only", "runId": str}. Raises
    on a missing, unparseable or schema-invalid manifest — the catalogue must
    never point the app at something the app will then reject.
    """
    path = pathlib.Path(path)
    if not path.is_file():
        raise CatalogueError(
            f"manifest does not exist: {path} — the catalogue is emitted after the "
            "manifests it indexes, never before"
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogueError(f"{path}: not valid JSON — {exc}") from exc

    detail = _schema_errors(_manifest_validator(), manifest)
    if detail:
        raise CatalogueError(f"{path} fails manifest.schema.json:\n  {detail}")

    has_hero = any(
        layer["kind"] == HERO_FINE_KIND for layer in manifest["layers"].values()
    )
    return {
        "hasHero": has_hero,
        "kind": "hero" if has_hero else "global-only",
        "runId": manifest["run"]["id"],
    }


def build_catalogue(specs: list[EventSpec], root: pathlib.Path) -> dict:
    """Assemble the catalogue dict, deriving kind/hasHero from the manifests.

    `root` is the directory the catalogue will be written to; every entry's
    `manifest` is resolved relative to it, exactly as the browser resolves it
    against the catalogue URL. Raises on any gate failure; returns a dict that
    still has to survive write_catalogue's schema validation.
    """
    root = pathlib.Path(root)
    if not specs:
        raise CatalogueError("no events — refusing to emit an empty catalogue")

    seen_ids: set[str] = set()
    seen_manifests: dict[str, str] = {}
    for spec in specs:
        if spec.id in seen_ids:
            raise CatalogueError(f"duplicate event id {spec.id!r} — ids must be unique")
        seen_ids.add(spec.id)
        if ".." in spec.manifest.split("/") or spec.manifest.startswith("/"):
            raise CatalogueError(
                f"event {spec.id!r}: manifest path {spec.manifest!r} escapes the catalogue "
                "root — paths are relative to the catalogue and must stay inside its tree"
            )
        if spec.manifest in seen_manifests:
            raise CatalogueError(
                f"events {seen_manifests[spec.manifest]!r} and {spec.id!r} both point at "
                f"{spec.manifest!r} — one manifest, one event"
            )
        seen_manifests[spec.manifest] = spec.id

    defaults = [s.id for s in specs if s.default]
    if len(defaults) != 1:
        raise CatalogueError(
            f"exactly one event must be marked default, found {len(defaults)}"
            + (f": {defaults}" if defaults else " — the app has nothing to open")
        )

    events: list[dict] = []
    for spec in specs:
        facts = inspect_manifest(root / spec.manifest)
        events.append(
            {
                "id": spec.id,
                "title": spec.title,
                "subtitle": spec.subtitle,
                "manifest": spec.manifest,
                "kind": facts["kind"],
                "region": spec.region,
                "hasHero": facts["hasHero"],
                "default": spec.default,
            }
        )
    return {"schemaVersion": 1, "events": events}


def write_catalogue(
    catalogue: dict,
    root: pathlib.Path,
    schema_path: pathlib.Path = CATALOGUE_SCHEMA_PATH,
) -> pathlib.Path:
    """Validate against catalogue.schema.json and only then write catalogue.json."""
    validator = jsonschema.Draft202012Validator(load_schema(schema_path))
    detail = _schema_errors(validator, catalogue)
    if detail:
        raise CatalogueError(f"catalogue fails schema validation:\n  {detail}")

    path = pathlib.Path(root) / CATALOGUE_NAME
    path.write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    return path


def emit(
    specs: list[EventSpec],
    root: pathlib.Path,
    schema_path: pathlib.Path = CATALOGUE_SCHEMA_PATH,
) -> pathlib.Path:
    """build_catalogue + write_catalogue. The one call an encoder needs."""
    return write_catalogue(build_catalogue(specs, root), root, schema_path)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=pathlib.Path, required=True,
                    help="directory holding the manifests; catalogue.json is written here")
    ap.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    ap.add_argument("--only", metavar="EVENT_ID",
                    help="emit a one-entry catalogue for this registry id, forced default")
    args = ap.parse_args(argv)

    specs = load_registry(args.config)
    if args.only:
        specs = single_event(specs, args.only)
    path = emit(specs, args.root)
    catalogue = json.loads(path.read_text(encoding="utf-8"))
    print(f"catalogue validated against schema and written: {path}")
    for event in catalogue["events"]:
        print(
            f"  {event['id']:32s} {event['region']:8s} {event['kind']:12s} "
            f"-> {event['manifest']}" + ("  [default]" if event["default"] else "")
        )


if __name__ == "__main__":
    main()
