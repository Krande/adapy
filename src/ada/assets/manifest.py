"""``asset.json`` -- one manifest per subject-revision. The schema is core's; the contents mostly
are not.

Core owns the envelope (who published, when, against which hierarchy, what the delivery claim is)
and exactly three artefact roles: ``manifest``, ``hierarchy`` and ``attributes``. Every other role
is an opaque label on an opaque filename, and core's only uses for those entries are refcounting
on unpublish and passing keys through to a build job. That is deliberate -- it is what lets a
provider keep a private source format that core has no reader for.

Three properties kept from the prior art because the reasoning holds for core too:

* **Manifests are written LAST.** A half-written publish is then invisible rather than
  discoverable-and-broken, because the manifest is what makes a revision findable.
* **One manifest per root, sharing one source blob** by absolute ``key`` -- so N leaf manifests
  can reference one uploaded source without copying it N times.
* **No stored-artefact role for the built model.** A build output is derived, lives under
  ``_derived/``, and is keyed by fingerprint; recording it here would make it look restorable.

Not kept: the "exactly one root" validator and the vendor-shaped join report. Core's invariant is
``subject == encode(node)``, checked against ``node``; anything about what joined to what is a
``counts`` entry.

The filename is ``asset.json`` and not ``manifest.json`` on purpose: a versions/ resolver
elsewhere scans for ``manifest.json`` with a loose arity guard, and while ``assets/`` is outside
its reach, sharing the name buys nothing and risks everything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

from ada.assets.keys import AssetKeyError, is_valid_segment

__all__ = [
    "MANIFEST_FILENAME",
    "HIERARCHY_FILENAME",
    "MANIFEST_SCHEMA",
    "Actor",
    "ArtefactEntry",
    "AssetManifest",
    "BuildSpec",
    "ChangeRecord",
    "ManifestError",
    "CORE_ARTEFACT_ROLES",
    "parse_manifest",
]

MANIFEST_FILENAME = "asset.json"
HIERARCHY_FILENAME = "hierarchy.json"
MANIFEST_SCHEMA = "ada.assets/manifest@1"

# The only roles core reads. Everything else is a label it carries without interpreting.
CORE_ARTEFACT_ROLES = ("manifest", "hierarchy", "attributes")

DeliveryKind = Literal["none", "mesh", "build"]
_DELIVERY_KINDS = ("none", "mesh", "build")
_ACTIONS = ("added", "modified", "deleted")
_VIA = ("user", "service")


class ManifestError(ValueError):
    """A manifest that cannot be read. Never a partially-read one -- see parse_manifest."""


@dataclass(frozen=True)
class ArtefactEntry:
    """One file belonging to a revision.

    Exactly one of ``file`` (a sibling under this revision's prefix) or ``key`` (an absolute key
    in the same scope) is set. ``key`` is how N leaf manifests share a single uploaded source.
    """

    role: str
    sha256: str
    size: int
    file: str | None = None
    key: str | None = None
    schema_version: int | None = None  # the provider's own axis; core never interprets it

    def __post_init__(self) -> None:
        if (self.file is None) == (self.key is None):
            raise ManifestError(
                f"artefact role={self.role!r}: set exactly one of 'file' (a sibling filename) or "
                f"'key' (an absolute key in this scope), not both and not neither"
            )
        if self.file is not None and not is_valid_segment(self.file):
            raise AssetKeyError(f"artefact role={self.role!r}: invalid filename {self.file!r}")
        if self.size < 0:
            raise ManifestError(f"artefact role={self.role!r}: negative size {self.size}")


@dataclass(frozen=True)
class Actor:
    """Whoever made a change. One shape, three trust levels, told apart by WHO WROTE IT rather
    than by anything in the value -- see ChangeRecord."""

    id: str
    display: str | None = None
    application: str | None = None  # "<name> <version>" of the tool, when known


@dataclass(frozen=True)
class ChangeRecord:
    """Shaped on IfcOwnerHistory. EVERY field is optional, and an absent ChangeRecord is a
    complete manifest rather than a degraded one -- a provider whose source carries no authorship
    is first class, and the browser shows nothing instead of a gap.

    The trust split is the point: ``published_by`` is stamped by CORE from the authenticated
    caller and is the only field a reader may treat as verified. ``source_actor`` is RELAYED by
    the provider from its own source; core cannot verify it and never will, so the two never
    merge into one "author" field.
    """

    published_by: Actor | None = None
    published_via: Literal["user", "service"] | None = None
    source_actor: Actor | None = None
    action: Literal["added", "modified", "deleted"] | None = None
    source_instant: str | None = None  # when the SOURCE says it changed; not produced_at/published_at

    def __post_init__(self) -> None:
        if self.published_via is not None and self.published_via not in _VIA:
            raise ManifestError(f"published_via {self.published_via!r} not in {_VIA}")
        if self.action is not None and self.action not in _ACTIONS:
            raise ManifestError(f"action {self.action!r} not in {_ACTIONS}")


@dataclass(frozen=True)
class BuildSpec:
    """The provider's half of a ``build`` claim. ``options`` is opaque: core hashes it and
    forwards it verbatim to the provider's job entrypoint, and never derives a key from any value
    inside it -- so a provider cannot smuggle a source path into a key core has to understand."""

    capability: str
    options: Mapping[str, Any] = field(default_factory=dict)
    fingerprint_inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssetManifest:
    provider: str
    collection: str
    subject: str
    revision: str
    produced_at: str  # the EXTRACTION instant -- the revision's source of truth
    published_at: str
    delivery: DeliveryKind = "none"
    node: str | None = None  # the node this subject encodes; None at collection level
    change: ChangeRecord | None = None
    hierarchy_revision: str | None = None  # which collection hierarchy this was derived against
    build: BuildSpec | None = None
    artefacts: tuple[ArtefactEntry, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    schema: str = MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        if self.delivery not in _DELIVERY_KINDS:
            raise ManifestError(f"delivery {self.delivery!r} not in {_DELIVERY_KINDS}")
        if self.delivery == "build" and self.build is None:
            raise ManifestError("delivery='build' requires a build spec (capability + options)")
        if self.delivery != "build" and self.build is not None:
            raise ManifestError(f"build spec present but delivery is {self.delivery!r}, not 'build'")

    def matches_key(self, key: "AssetKeyLike") -> bool:
        """The stored location and the recorded identity must agree; otherwise neither is auditable."""
        return self.collection == key.collection and self.subject == key.subject and self.revision == key.revision

    def to_json(self) -> bytes:
        return json.dumps(_manifest_to_dict(self), indent=2, sort_keys=False).encode("utf-8")


class AssetKeyLike:  # pragma: no cover - typing shim for matches_key without importing AssetKey
    collection: str
    subject: str
    revision: str


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _actor_to_dict(a: Actor | None) -> dict | None:
    return None if a is None else _drop_none({"id": a.id, "display": a.display, "application": a.application})


def _manifest_to_dict(m: AssetManifest) -> dict:
    out: dict[str, Any] = {
        "schema": m.schema,
        "provider": m.provider,
        "collection": m.collection,
        "subject": m.subject,
        "revision": m.revision,
        "node": m.node,
        "produced_at": m.produced_at,
        "published_at": m.published_at,
        "delivery": m.delivery,
    }
    if m.hierarchy_revision is not None:
        out["hierarchy_revision"] = m.hierarchy_revision
    if m.change is not None:
        c = m.change
        change = _drop_none(
            {
                "published_by": _actor_to_dict(c.published_by),
                "published_via": c.published_via,
                "source_actor": _actor_to_dict(c.source_actor),
                "action": c.action,
                "source_instant": c.source_instant,
            }
        )
        if change:
            out["change"] = change
    if m.build is not None:
        out["build"] = {
            "capability": m.build.capability,
            "options": dict(m.build.options),
            "fingerprint_inputs": list(m.build.fingerprint_inputs),
        }
    out["artefacts"] = [
        _drop_none(
            {
                "role": a.role,
                "file": a.file,
                "key": a.key,
                "sha256": a.sha256,
                "size": a.size,
                "schema_version": a.schema_version,
            }
        )
        for a in m.artefacts
    ]
    out["counts"] = dict(m.counts)
    return _drop_none(out) if m.node is None else out


def _actor_from(d: Any, where: str) -> Actor | None:
    if d is None:
        return None
    if not isinstance(d, dict) or "id" not in d:
        raise ManifestError(f"{where}: expected an object with an 'id', got {d!r}")
    return Actor(id=str(d["id"]), display=d.get("display"), application=d.get("application"))


def parse_manifest(doc: bytes | str) -> AssetManifest:
    """Read an ``asset.json``.

    An unknown ``schema`` is REFUSED rather than partially read: a manifest from a future core
    may put new meaning on fields this one recognises, so reading the familiar subset would be a
    guess presented as a fact.
    """
    try:
        raw = json.loads(doc)
    except (ValueError, TypeError) as exc:
        raise ManifestError(f"{MANIFEST_FILENAME} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{MANIFEST_FILENAME} must be a JSON object, got {type(raw).__name__}")

    schema = raw.get("schema")
    if schema != MANIFEST_SCHEMA:
        raise ManifestError(
            f"unknown manifest schema {schema!r}: this core reads {MANIFEST_SCHEMA!r} only. "
            f"Refusing rather than reading the fields it recognises -- a newer schema may give "
            f"them different meaning."
        )

    missing = [
        k for k in ("provider", "collection", "subject", "revision", "produced_at", "published_at") if k not in raw
    ]
    if missing:
        raise ManifestError(f"{MANIFEST_FILENAME} missing required field(s): {', '.join(missing)}")

    change_raw = raw.get("change")
    change = None
    if change_raw is not None:
        if not isinstance(change_raw, dict):
            raise ManifestError(f"'change' must be an object, got {type(change_raw).__name__}")
        change = ChangeRecord(
            published_by=_actor_from(change_raw.get("published_by"), "change.published_by"),
            published_via=change_raw.get("published_via"),
            source_actor=_actor_from(change_raw.get("source_actor"), "change.source_actor"),
            action=change_raw.get("action"),
            source_instant=change_raw.get("source_instant"),
        )

    build_raw = raw.get("build")
    build = None
    if build_raw is not None:
        if not isinstance(build_raw, dict) or "capability" not in build_raw:
            raise ManifestError("'build' must be an object with a 'capability'")
        build = BuildSpec(
            capability=str(build_raw["capability"]),
            options=dict(build_raw.get("options") or {}),
            fingerprint_inputs=tuple(build_raw.get("fingerprint_inputs") or ()),
        )

    artefacts = []
    for entry in raw.get("artefacts") or ():
        if not isinstance(entry, dict):
            raise ManifestError(f"artefact entries must be objects, got {type(entry).__name__}")
        try:
            artefacts.append(
                ArtefactEntry(
                    role=str(entry["role"]),
                    sha256=str(entry["sha256"]),
                    size=int(entry["size"]),
                    file=entry.get("file"),
                    key=entry.get("key"),
                    schema_version=entry.get("schema_version"),
                )
            )
        except KeyError as exc:
            raise ManifestError(f"artefact entry missing {exc}") from exc

    return AssetManifest(
        schema=schema,
        provider=str(raw["provider"]),
        collection=str(raw["collection"]),
        subject=str(raw["subject"]),
        revision=str(raw["revision"]),
        node=raw.get("node"),
        produced_at=str(raw["produced_at"]),
        published_at=str(raw["published_at"]),
        delivery=raw.get("delivery", "none"),
        change=change,
        hierarchy_revision=raw.get("hierarchy_revision"),
        build=build,
        artefacts=tuple(artefacts),
        counts=dict(raw.get("counts") or {}),
    )
