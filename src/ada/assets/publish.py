"""Publishing into the store: a provider PLANS, core WRITES.

The split is the whole design of this module, and it is what makes two rules structural instead
of procedural:

1. **The owner gate (Decision 6).** ``change.published_by`` says who pushed this revision into
   the scope. A provider cannot be trusted to state that -- it is core's authenticated caller --
   so a provider that sets it is refused BY NAME here, and core stamps the field itself into
   every manifest of the publish. A provider can still RELAY what its source says
   (``source_actor``, ``action``, ``source_instant``), which is a different claim and is labelled
   as one.
2. **Manifests last (Decision 2a).** A half-written publish must be invisible rather than
   discoverable-and-broken. If each provider wrote its own blobs, that ordering would be a rule
   every provider had to re-implement; here the plan is an ORDERED list and core does the writing,
   so a provider gets the discipline whether or not it thought about it.

Core edits only the documents it owns the schema of -- ``asset.json`` -- and never looks inside
the provider's own artefacts, which travel as opaque bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence

from ada.assets.keys import (
    ASSET_PREFIX,
    STAGING_SEGMENT,
    AssetKeyError,
    parse_asset_key,
)
from ada.assets.manifest import (
    MANIFEST_FILENAME,
    Actor,
    AssetManifest,
    ChangeRecord,
    ManifestError,
    parse_manifest,
)

__all__ = [
    "PlannedWrite",
    "PublishError",
    "PublishPlan",
    "PublishOutcome",
    "apply_publish_plan",
    "staged_prefix",
    "stamp_publish",
]


class PublishError(ValueError):
    """A plan core refuses to write, naming what is wrong with it."""


@dataclass(frozen=True)
class PlannedWrite:
    """One blob a publish would write. ``data`` is opaque to core unless the key names a manifest."""

    key: str
    data: bytes


@dataclass(frozen=True)
class PublishPlan:
    """What a provider's ``derive()`` returns: everything this publish would write, IN ORDER.

    Order is part of the contract, not a detail: within a subject its artefacts precede its
    manifest, and across subjects a collection-level manifest comes last. A provider that emits
    them in another order gets that order preserved -- so this is checked, not assumed
    (:func:`apply_publish_plan`).
    """

    collection: str
    revision: str
    subjects: tuple[str, ...]
    writes: tuple[PlannedWrite, ...]
    counts: Mapping[str, int] = field(default_factory=dict)
    #: What the provider RELAYS about the change from its own source. Never `published_by`.
    change: ChangeRecord | None = None


@dataclass(frozen=True)
class PublishOutcome:
    """What actually happened (or, under ``dry_run``, what would have)."""

    collection: str
    revision: str
    subjects: tuple[str, ...]
    written: tuple[str, ...]
    dry_run: bool
    replaced: tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "collection": self.collection,
            "revision": self.revision,
            "subjects": list(self.subjects),
            "written": list(self.written),
            # Echoed back deliberately: a caller that asked for a dry run and got a summary
            # without the flag cannot tell a plan from a publish, and the difference is every
            # byte in the scope.
            "dry_run": self.dry_run,
            "replaced": list(self.replaced),
            "counts": dict(self.counts),
        }


def staged_prefix(staging_id: str) -> str:
    return f"{ASSET_PREFIX}/{STAGING_SEGMENT}/{staging_id}/"


def stamp_publish(
    manifest: AssetManifest,
    *,
    published_by: Actor,
    published_via: str,
) -> AssetManifest:
    """Put core's authorship on a manifest, keeping whatever the provider relayed.

    THE REFUSAL IS THE POINT. A provider that set ``published_by`` is not corrected quietly: a
    publish that records the wrong author is worse than one that fails, because the record is
    what a later reader trusts. The message names the field so the provider's author can find it.
    """
    provider_change = manifest.change
    if provider_change is not None and provider_change.published_by is not None:
        raise PublishError(
            "a provider may not set change.published_by: that field records WHO PUSHED this "
            "revision into the scope, which only core's authenticated caller can say. Relay what "
            "the source says in change.source_actor instead."
        )
    if provider_change is not None and provider_change.published_via is not None:
        raise PublishError(
            "a provider may not set change.published_via: it names how CORE was called "
            "(user or service), not anything the provider can observe."
        )
    change = provider_change or ChangeRecord()
    return replace(
        manifest,
        change=replace(change, published_by=published_by, published_via=published_via),
    )


def _is_manifest(key: str) -> bool:
    return key.rsplit("/", 1)[-1] == MANIFEST_FILENAME


def _check_order(writes: Sequence[PlannedWrite]) -> None:
    """Every manifest must come after the artefacts of its own subject-revision."""
    seen_manifest: set[tuple[str, str]] = set()
    for write in writes:
        try:
            parsed = parse_asset_key(write.key)
        except AssetKeyError as exc:
            raise PublishError(f"{write.key}: {exc}") from exc
        ident = (parsed.subject, parsed.revision)
        if _is_manifest(write.key):
            seen_manifest.add(ident)
        elif ident in seen_manifest:
            raise PublishError(
                f"{write.key} is written AFTER the manifest of {parsed.subject}@{parsed.revision}. "
                f"Manifests are written last so a half-written publish is invisible rather than "
                f"discoverable-and-broken."
            )


def apply_publish_plan(
    plan: PublishPlan,
    *,
    published_by: Actor,
    published_via: str,
    dry_run: bool,
    replace_existing: bool,
    occupied: "Iterable[str] | None" = None,
    write: "Any" = None,
) -> PublishOutcome:
    """Validate a plan, stamp its manifests, and (unless ``dry_run``) write it in order.

    ``occupied`` is the set of keys already present under the subject-revisions this plan touches;
    ``write(key, data)`` is how a key is stored. Both are injected so this stays a pure decision
    function with one side effect the caller supplies -- it is driven in tests against a dict.
    """
    if not plan.writes:
        raise PublishError("a publish plan with no writes is not a publish")
    _check_order(plan.writes)

    occupied_set = set(occupied or ())
    clash = sorted(k for k in occupied_set if any(k == w.key for w in plan.writes))
    # An occupied prefix is refused rather than merged: a revision is immutable by construction
    # (Decision 3), so two publishes landing on one revision means one of them is wrong about
    # what it published. `replace` is the operator saying which.
    if clash and not replace_existing:
        raise PublishError(
            f"{len(clash)} key(s) already exist at this revision (first: {clash[0]}). "
            f"A revision is immutable; re-publish with replace=true to overwrite it deliberately."
        )

    stamped: list[PlannedWrite] = []
    for planned in plan.writes:
        if not _is_manifest(planned.key):
            stamped.append(planned)  # opaque to core -- a provider artefact, passed through
            continue
        try:
            manifest = parse_manifest(planned.data)
        except ManifestError as exc:
            raise PublishError(f"{planned.key}: {exc}") from exc
        stamped.append(
            PlannedWrite(
                key=planned.key,
                data=stamp_publish(manifest, published_by=published_by, published_via=published_via).to_json(),
            )
        )

    if not dry_run:
        if write is None:
            raise PublishError("a real publish needs a write callable")
        for planned in stamped:
            write(planned.key, planned.data)

    return PublishOutcome(
        collection=plan.collection,
        revision=plan.revision,
        subjects=plan.subjects,
        written=tuple(w.key for w in stamped),
        dry_run=dry_run,
        replaced=tuple(clash),
        counts=plan.counts,
    )
