"""The ``build`` delivery kind: derived keys, fingerprints, and the summary a build must write.

Decision 1's second delivery kind. A provider claims ``build`` and hands core a capability plus
OPAQUE options; core enqueues the job, reads the summary at a key IT composed, validates the
summary's provenance against the request, and only then loads the GLB.

THE TWO RULES THIS MODULE EXISTS TO HOLD:

1. **Core composes the key; the provider never does.** ``derived_key`` is built from
   ``(provider, collection, subject, revision, node, fingerprint)`` -- identity core can audit --
   and never from a value inside ``options``. A provider that could put a path into the key would
   be handing core a source format to understand, which is the layering rule this store is built
   on.
2. **A build says what it is, twice.** The summary carries a provenance block, and the GLB carries
   the same block under ``asset.extras``. A built artefact that could not name its own version
   would have no advantage over a stored one -- the whole argument for building on demand.

Fingerprint inputs are NAMED BY THE PROVIDER and read by core out of the options it was given, so
adding an option nobody sets cannot re-key the world: an absent option and an option at its
default are the same request, byte for byte, because a default is never emitted.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "BUILD_SCHEMA",
    "DERIVED_ASSET_PREFIX",
    "BuildError",
    "BuildProvenance",
    "BuildSummary",
    "build_fingerprint",
    "derived_asset_key",
    "derived_asset_prefix",
    "parse_build_summary",
    "patch_glb_provenance",
    "validate_build_summary",
]

BUILD_SCHEMA = "ada.assets/build@1"

# Under `_derived/`, which the blob route protects and the asset routes never list: a build is
# reachable by its key and is not restorable as an asset (Decision 8 of 2026-09-19).
DERIVED_ASSET_PREFIX = "_derived/assets"

# The node segment for a build of a whole subject rather than one node beneath it.
ALL_NODES = "all"


class BuildError(ValueError):
    """A build request, or the summary answering it, that core refuses."""


def _fp_segment(value: str, what: str) -> str:
    if not isinstance(value, str) or not value or "/" in value or ".." in value:
        raise BuildError(f"invalid {what} {value!r}: must be a non-empty single path segment")
    return value


def derived_asset_prefix(
    *, provider: str, collection: str, subject: str, revision: str, node: str | None, fingerprint: str
) -> str:
    """``_derived/assets/<provider>/<collection>/<subject>/<revision>/<node|all>/<fingerprint>``.

    Every segment is identity core owns: which provider produced the subject, which subject at
    which revision, which node inside it, and the fingerprint of the request. Two builds that
    differ in any of them are two keys; two identical requests are one key, which is what makes a
    repeat answerable from the store without enqueueing anything.
    """
    return "/".join(
        (
            DERIVED_ASSET_PREFIX,
            _fp_segment(provider, "provider"),
            _fp_segment(collection, "collection"),
            _fp_segment(subject, "subject"),
            _fp_segment(revision, "revision"),
            _fp_segment(node or ALL_NODES, "node"),
            _fp_segment(fingerprint, "fingerprint"),
        )
    )


def derived_asset_key(
    *,
    provider: str,
    collection: str,
    subject: str,
    revision: str,
    node: str | None,
    fingerprint: str,
    filename: str = "summary.json",
) -> str:
    return f"{derived_asset_prefix(provider=provider, collection=collection, subject=subject, revision=revision, node=node, fingerprint=fingerprint)}/{_fp_segment(filename, 'filename')}"


def build_fingerprint(
    *,
    options: Mapping[str, Any],
    fingerprint_inputs: Sequence[str],
    node: str | None,
    hierarchy_source: str,
    provider_version: str | None = None,
) -> str:
    """Hash of the NAMED inputs, the node, and the hierarchy that placed it.

    ``fingerprint_inputs`` names which option keys matter; core reads those values and ignores
    every other option, so a provider adding an option that nobody sets does not re-key existing
    builds. An input the options do not carry is hashed as ABSENT rather than as a default,
    because a request without an option and a request with it set to the default are the same
    request and must be the same key.

    ``hierarchy_source`` is in the hash because two builds resolved through different hierarchies
    are two builds: the same node id can sit under a different parent after a re-publish, and a
    cached GLB from the old placement would be wrong in a way nothing else would catch.
    """
    payload = {
        "inputs": {name: options[name] for name in sorted(set(fingerprint_inputs)) if name in options},
        "node": node,
        "hierarchy_source": hierarchy_source,
        "provider_version": provider_version,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass(frozen=True)
class BuildProvenance:
    """What a built artefact says about itself. Core validates the identity half and reads
    nothing else; ``sources`` and the versions are the provider's word about its own inputs."""

    provider: str
    collection: str
    subject: str
    revision: str
    node: str | None
    fingerprint: str
    built_at: str
    adapy_version: str | None = None
    provider_version: str | None = None
    hierarchy_source: str | None = None
    started_at: str | None = None
    sources: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "provider": self.provider,
            "collection": self.collection,
            "subject": self.subject,
            "revision": self.revision,
            "node": self.node,
            "fingerprint": self.fingerprint,
            "built_at": self.built_at,
        }
        for name in ("adapy_version", "provider_version", "hierarchy_source", "started_at"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.sources:
            out["sources"] = [dict(s) for s in self.sources]
        return out


@dataclass(frozen=True)
class BuildSummary:
    """``ada.assets/build@1`` -- the document a builder returns and core stores at the derived key."""

    ok: bool
    glb_key: str
    provenance: BuildProvenance
    glb_size: int | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    error: str | None = None
    schema: str = BUILD_SCHEMA

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "schema": self.schema,
            "ok": self.ok,
            "glb_key": self.glb_key,
            "provenance": self.provenance.to_dict(),
            # Name-keyed and OMITTING what was not measured rather than zeroing it: a zero is a
            # measurement, and a build that never counted something must not claim it found none.
            "counts": dict(self.counts),
        }
        if self.glb_size is not None:
            out["glb_size"] = self.glb_size
        if self.warnings:
            out["warnings"] = list(self.warnings)
        if self.error is not None:
            out["error"] = self.error
        return out

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=False).encode("utf-8")


def parse_build_summary(doc: bytes | str | Mapping[str, Any]) -> BuildSummary:
    """Read a summary. An unknown schema is REFUSED, never partially read."""
    if isinstance(doc, (bytes, str)):
        try:
            raw = json.loads(doc)
        except (ValueError, TypeError) as exc:
            raise BuildError(f"build summary is not valid JSON: {exc}") from exc
    else:
        raw = doc
    if not isinstance(raw, dict):
        raise BuildError(f"build summary must be a JSON object, got {type(raw).__name__}")
    schema = raw.get("schema")
    if schema != BUILD_SCHEMA:
        raise BuildError(
            f"unknown build summary schema {schema!r}: this core reads {BUILD_SCHEMA!r} only. "
            f"Refusing rather than reading the fields it recognises."
        )
    prov_raw = raw.get("provenance")
    if not isinstance(prov_raw, dict):
        raise BuildError("build summary has no 'provenance' object -- a build must name what it built")
    missing = [k for k in ("provider", "collection", "subject", "revision", "fingerprint") if k not in prov_raw]
    if missing:
        raise BuildError(f"provenance missing required field(s): {', '.join(missing)}")
    provenance = BuildProvenance(
        provider=str(prov_raw["provider"]),
        collection=str(prov_raw["collection"]),
        subject=str(prov_raw["subject"]),
        revision=str(prov_raw["revision"]),
        node=prov_raw.get("node"),
        fingerprint=str(prov_raw["fingerprint"]),
        built_at=str(prov_raw.get("built_at") or ""),
        adapy_version=prov_raw.get("adapy_version"),
        provider_version=prov_raw.get("provider_version"),
        hierarchy_source=prov_raw.get("hierarchy_source"),
        started_at=prov_raw.get("started_at"),
        sources=tuple(prov_raw.get("sources") or ()),
    )
    counts = raw.get("counts") or {}
    if not isinstance(counts, dict):
        raise BuildError(f"'counts' must be an object, got {type(counts).__name__}")
    return BuildSummary(
        ok=bool(raw.get("ok", False)),
        glb_key=str(raw.get("glb_key") or ""),
        provenance=provenance,
        glb_size=raw.get("glb_size"),
        counts={str(k): int(v) for k, v in counts.items()},
        warnings=tuple(str(w) for w in (raw.get("warnings") or ())),
        error=raw.get("error"),
        schema=schema,
    )


def validate_build_summary(
    summary: BuildSummary,
    *,
    provider: str,
    collection: str,
    subject: str,
    revision: str,
    node: str | None,
    fingerprint: str,
    derived_prefix: str,
) -> None:
    """Refuse a summary that does not answer THIS request, naming the disagreement.

    Core validates identity and nothing else: the fields it composed the key from, plus that the
    GLB sits under the prefix it composed. Everything else in the document is the provider's.

    Why the disagreement is named rather than reported as "invalid": the two ways this fires are a
    builder writing another request's summary (a cache key bug) and a stale blob under a reused
    key, and only the field that disagrees tells those apart.
    """
    if not summary.ok:
        raise BuildError(summary.error or "build reported ok=false without an error")
    p = summary.provenance
    for field_name, want, got in (
        ("provider", provider, p.provider),
        ("collection", collection, p.collection),
        ("subject", subject, p.subject),
        ("revision", revision, p.revision),
        ("node", node, p.node),
        ("fingerprint", fingerprint, p.fingerprint),
    ):
        if want != got:
            raise BuildError(
                f"build summary answers a different request: provenance.{field_name} is {got!r}, "
                f"this request is {want!r}"
            )
    if not summary.glb_key:
        raise BuildError("build summary has no 'glb_key'")
    if not summary.glb_key.startswith(f"{derived_prefix}/"):
        raise BuildError(
            f"build summary's glb_key {summary.glb_key!r} is outside the prefix core composed "
            f"({derived_prefix}/) -- a build may only write under its own derived prefix"
        )


# --- the GLB's own copy ---------------------------------------------------------------------------

_GLB_MAGIC = b"glTF"
_JSON_CHUNK = b"JSON"


def patch_glb_provenance(data: bytes, provenance: BuildProvenance, *, generator: str | None = None) -> bytes:
    """Write ``provenance`` into the GLB's ``asset.extras``, rewriting ONLY the JSON chunk.

    The second copy of §2d. Parsing and re-emitting the binary chunk would re-encode geometry a
    builder just streamed; this reads the JSON chunk, edits the ``asset`` object, pads back to a
    4-byte boundary and rewrites the header length. Everything after the JSON chunk is copied
    through untouched.
    """
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise BuildError("not a GLB: missing the glTF magic")
    (version, _total_len) = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise BuildError(f"unsupported GLB container version {version}")
    (json_len, chunk_type) = struct.unpack_from("<I4s", data, 12)
    if chunk_type != _JSON_CHUNK:
        raise BuildError("first GLB chunk is not JSON")
    json_start = 20
    json_end = json_start + json_len
    try:
        doc = json.loads(data[json_start:json_end].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BuildError(f"GLB JSON chunk is unreadable: {exc}") from exc

    asset = doc.setdefault("asset", {})
    extras = asset.setdefault("extras", {})
    extras["provenance"] = provenance.to_dict()
    if generator:
        asset["generator"] = generator

    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)  # JSON chunks pad with spaces, binary chunks with zeros
    rest = data[json_end:]
    new_total = 12 + 8 + len(body) + len(rest)
    out = bytearray()
    out += _GLB_MAGIC
    out += struct.pack("<II", 2, new_total)
    out += struct.pack("<I4s", len(body), _JSON_CHUNK)
    out += body
    out += rest
    return bytes(out)


def read_glb_provenance(data: bytes) -> dict | None:
    """The provenance block a GLB carries, or None. The parity check's reader."""
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        return None
    (json_len, chunk_type) = struct.unpack_from("<I4s", data, 12)
    if chunk_type != _JSON_CHUNK:
        return None
    try:
        doc = json.loads(data[20 : 20 + json_len].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    extras = (doc.get("asset") or {}).get("extras") or {}
    prov = extras.get("provenance")
    return prov if isinstance(prov, dict) else None
