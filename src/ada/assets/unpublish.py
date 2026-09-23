"""Unpublishing a subject-revision, with the refcount check core owes its publishers.

THE HAZARD, AND WHY IT IS CORE'S NOW. One publish writes its source blob ONCE and every manifest
of that publish -- and of later leaf publishes derived from it -- references that blob by absolute
key (Decision 3, "leaf without stem"). So deleting a revision can delete the source another
revision still names, leaving a manifest that points at nothing: an asset that lists, resolves and
badges exactly like a working one and fails only at load, minutes later, phrased as a storage
error. The prior art documented this as "the publisher's obligation"; an obligation every
publisher must remember is one that will eventually be forgotten, so the check moves here and the
route refuses rather than the publisher remembering.

WHAT IS AND IS NOT CASCADED. Derived builds under ``_derived/assets/...`` are NOT deleted: they
are keyed by the source's identity and simply become unreachable, not wrong (Decision 8 of
2026-09-19), and cascading would make an unpublish a recursive delete over a keyspace the caller
never named. An admin sweep collects them by prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from ada.assets.keys import ASSET_PREFIX, AssetKeyError, parse_asset_key
from ada.assets.manifest import MANIFEST_FILENAME, ManifestError, parse_manifest

__all__ = ["UnpublishPlan", "plan_unpublish"]


@dataclass(frozen=True)
class UnpublishPlan:
    """What an unpublish would do. ``kept`` with a reason is a refusal, not a partial success."""

    collection: str
    subject: str
    revision: str
    deleted: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    reason: str | None = None
    #: Subject-revisions whose manifests still name a key this unpublish would have deleted.
    held_by: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = field(default_factory=tuple)

    @property
    def refused(self) -> bool:
        return self.reason is not None

    def to_dict(self) -> dict:
        return {
            "collection": self.collection,
            "subject": self.subject,
            "revision": self.revision,
            "deleted": list(self.deleted),
            "kept": list(self.kept),
            "reason": self.reason,
            "held_by": list(self.held_by),
            "unreadable": list(self.unreadable),
        }


def plan_unpublish(
    *,
    collection: str,
    subject: str,
    revision: str,
    collection_keys: Iterable[str],
    manifest_bytes: Mapping[str, bytes],
) -> UnpublishPlan:
    """Decide what may be deleted, from the collection's own listing and its surviving manifests.

    ``collection_keys`` is every key under ``assets/<collection>/``; ``manifest_bytes`` maps each
    SURVIVING manifest key (i.e. excluding the subject-revision being removed) to its bytes. Pure:
    the route does the listing and the reads, this decides.

    A manifest core cannot read is reported in ``unreadable`` and treated as HOLDING everything
    it might have named. That is deliberately the cautious direction -- refusing a delete that
    would have been fine is recoverable, deleting a blob a manifest still needs is not.
    """
    target_prefix = f"{ASSET_PREFIX}/{collection}/{subject}/{revision}/"
    target_keys = sorted(k for k in collection_keys if k.startswith(target_prefix))
    if not target_keys:
        return UnpublishPlan(
            collection=collection,
            subject=subject,
            revision=revision,
            reason=f"nothing published at {collection}/{subject}@{revision}",
        )

    referenced: dict[str, list[str]] = {}
    unreadable: list[str] = []
    for key, raw in manifest_bytes.items():
        if key.startswith(target_prefix):
            continue  # the set being removed cannot hold itself alive
        try:
            manifest = parse_manifest(raw)
        except ManifestError:
            unreadable.append(key)
            continue
        try:
            parsed = parse_asset_key(key)
            holder = f"{parsed.subject}@{parsed.revision}"
        except AssetKeyError:
            holder = key
        for artefact in manifest.artefacts:
            if artefact.key:
                referenced.setdefault(artefact.key, []).append(holder)

    held = {k: sorted(set(v)) for k, v in referenced.items() if k in target_keys}
    if held or unreadable:
        first_key = sorted(held)[0] if held else None
        holders = sorted({h for hs in held.values() for h in hs})
        if held:
            reason = (
                f"{len(held)} blob(s) at this revision are still named by {len(holders)} other "
                f"manifest(s) (e.g. {first_key} held by {holders[0]}). Unpublish those first: a "
                f"manifest pointing at a deleted blob fails at load, not here."
            )
        else:
            reason = (
                f"{len(unreadable)} manifest(s) in this collection could not be read, so whether "
                f"they name a blob at this revision is unknown (e.g. {unreadable[0]}). Refusing "
                f"rather than guessing."
            )
        return UnpublishPlan(
            collection=collection,
            subject=subject,
            revision=revision,
            kept=tuple(target_keys),
            reason=reason,
            held_by=tuple(holders),
            unreadable=tuple(sorted(unreadable)),
        )

    # Manifest first in the DELETE order, mirroring manifests-last on the way in: while a partial
    # delete is in flight, the set must look unpublished rather than published-and-incomplete.
    manifest_key = f"{target_prefix}{MANIFEST_FILENAME}"
    ordered = ([manifest_key] if manifest_key in target_keys else []) + [k for k in target_keys if k != manifest_key]
    return UnpublishPlan(
        collection=collection,
        subject=subject,
        revision=revision,
        deleted=tuple(ordered),
    )
