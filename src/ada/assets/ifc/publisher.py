"""``IfcAssetPublisher`` -- the ``AssetPublisher`` (Phase 4) for provider id ``ifc``.

**A provider PLANS; core WRITES (Decision 2a).** This class wraps ``_derive_ifc_plan`` -- the
SAME derivation ``publish_ifc`` has always used -- with ``enforce_occupancy=False``: core's own
``ada.assets.publish.apply_publish_plan`` decides occupancy and ``replace`` for a job-driven
publish (it lists the scope's storage itself, ``formats/asset_publish.py``'s "Occupancy is read
HERE, not inside the provider"), so this class must not decide it a second, possibly
disagreeing, time. What IS still enforced here -- because it is about the STAGED FILE, not the
store -- is everything ``_derive_ifc_plan`` always checked: root/leaf mutual exclusion, an
unknown node id, a file with no instant.

**``derive()`` reads through ``storage``, never through the caller's own facade.** The protocol
(``ada.assets.provider.AssetPublisher.derive``) hands this class the SAME synchronous storage
facade a builder gets -- ``get_bytes`` / ``list_keys`` / ``put_bytes`` -- and this class uses only
the read half (:class:`_ReadOnlyIfcStoreAdapter`): writing through it would bypass the owner gate
and the manifests-last ordering, which are the two reasons core does the writing at all.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ada.assets.ifc.publish import (
    IFC_PROVIDER_ID,
    SOURCE_FILENAME,
    IfcPublishError,
    _derive_ifc_plan,
)
from ada.assets.publish import PlannedWrite, PublishPlan

__all__ = ["IfcAssetPublisher"]


class _ReadOnlyIfcStoreAdapter:
    """Adapts the ``AssetPublisher`` storage facade (``get_bytes`` / ``list_keys`` / ``put_bytes``)
    to the read-only slice ``ada.assets.ifc.publish`` needs (``IfcAssetStore``'s ``list_prefix`` /
    ``get_bytes``). ``put_bytes`` is refused outright: ``derive()`` must not write."""

    def __init__(self, storage: Any):
        self._storage = storage

    def list_prefix(self, prefix: str) -> Iterable[str]:
        return self._storage.list_keys(prefix)

    def get_bytes(self, key: str) -> bytes:
        return self._storage.get_bytes(key)

    def put_bytes(self, key: str, data: bytes) -> None:  # pragma: no cover - structural guard
        raise RuntimeError(
            "IfcAssetPublisher.derive() must not write (Decision 2a: a provider PLANS, core "
            "WRITES) -- this adapter's put_bytes should never be called"
        )


def _resolve_staged_key(staged: Mapping[str, str]) -> str:
    """Which staged blob is the IFC source. ``staged`` maps ROLE (the staged filename) -> key
    (``routes/assets.py``'s publish route: everything under ``assets/_staging/<id>/`` keyed by
    what follows the prefix). The IFC publisher's own role is ``source.ifc``
    (:data:`ada.assets.ifc.publish.SOURCE_FILENAME`); a caller staging exactly one file under any
    other name is accepted too, since there is nothing else it could mean, but two or more staged
    files with none of them named ``source.ifc`` is refused rather than guessed."""
    if SOURCE_FILENAME in staged:
        return staged[SOURCE_FILENAME]
    if len(staged) == 1:
        return next(iter(staged.values()))
    raise IfcPublishError(
        f"staged={sorted(staged)}: expected a {SOURCE_FILENAME!r} role, or exactly one staged "
        f"file -- the IFC publisher does not guess which staged blob is the source"
    )


class IfcAssetPublisher:
    """Registered for provider id ``ifc`` (``ada.assets.ifc.__init__``, ``ensure_core_publishers``
    -- see ``ada.assets.publishers``). One instance is reused across publishes; it carries no
    per-publish state."""

    id = IFC_PROVIDER_ID

    def derive(
        self,
        scope: Any,
        staged: Mapping[str, str],
        *,
        storage: Any,
        collection: str | None = None,
        options: Mapping[str, Any] | None = None,
        dry_run: bool = False,
    ) -> PublishPlan:
        del scope, dry_run  # neither changes what WOULD be written -- core decides whether to write it
        if not collection:
            raise IfcPublishError("derive() needs a 'collection': the IFC provider has no default one to publish into")
        opts = dict(options or {})
        staged_key = _resolve_staged_key(staged)

        plan = _derive_ifc_plan(
            _ReadOnlyIfcStoreAdapter(storage),
            collection=collection,
            staged_key=staged_key,
            root=opts.get("root"),
            leaf=opts.get("leaf"),
            source=opts.get("source"),
            extracted_at=opts.get("extracted_at"),
            # The tree and what each node IS, with no geometry promise. An opaque publish option
            # like every other: core hashes and forwards `options` without reading them, so a
            # job-driven publish reaches the same derivation as the direct entry point.
            structure_only=bool(opts.get("structure_only", False)),
            enforce_occupancy=False,
        )
        return PublishPlan(
            collection=plan.collection,
            revision=plan.revision,
            subjects=plan.subjects,
            writes=tuple(PlannedWrite(key=key, data=data) for key, data in plan.writes),
            counts=plan.counts,
        )
