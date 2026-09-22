"""``asset-publish-ifc`` -- stage an IFC file, derive core's schemas from it, write a revision.

The three scopes are ONE derivation, not three code paths: whole-file, ``--root`` and ``--leaf``
differ only in which node(s) the publish declares as its subject(s) -- every subject gets exactly
the same treatment (its own subtree spine, its own manifest, its own occupancy check), and only a
WHOLE-FILE publish additionally writes the collection-level index, because only it has "every
IfcSite" to fold into one.

**Write order, and why it is exactly this order.** Manifests are written LAST relative to what
they describe (Decision 2a: "a half-written publish is invisible rather than
discoverable-and-broken") at TWO granularities here: within one subject, its ``hierarchy.json``
lands before its ``asset.json`` (a manifest whose artefact list names a hash the reader cannot yet
fetch is worse than no manifest); across subjects, the collection-level manifest -- when this
publish writes one at all -- lands after every per-subject manifest, so it is the single signal
that "everything this publish promised is here." A crash between two site manifests leaves the
first site fully valid and the second simply not yet published, which is the correct state for
both.

**Declared roots and delivery, kept apart from "appears in a spine".** Every subject this publish
writes a manifest for claims ``delivery="build"``; every OTHER row in a spine or the collection
index -- an intermediate storey, a beam three levels under a site that was published as a whole --
claims nothing (``delivery=""``), because it has no manifest of its own yet and a `build` claim
without a manifest would be a promise `PublishedAssetProvider.delivery()` could not keep. A node
becomes independently buildable only once ITS OWN scoped publish (``--root``/``--leaf``) gives it a
manifest -- that is the whole of Decision 3's leaf-addressable publishing, expressed as "only the
declared root of THIS publish gets `delivery='build'`".
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from ada.assets.ifc.index import (
    IFC_INDEX_FILENAME,
    IFC_INDEX_ROLE,
    build_ifc_index,
    index_to_json,
)
from ada.assets.ifc.walk import IfcNode
from ada.assets.ifc.walk import ancestor_chain as _ancestor_chain
from ada.assets.ifc.walk import (
    collection_index_nodes,
    declared_site_roots,
    node_dicts,
    subtree_nodes,
    walk_full,
)
from ada.assets.index import fold_listing
from ada.assets.keys import (
    ASSET_PREFIX,
    AssetKeyError,
    asset_key,
    revision_from_instant,
)
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)
from ada.assets.projection import build_hierarchy
from ada.cadit.ifc.store import IfcStore

__all__ = [
    "IFC_PROVIDER_ID",
    "IFC_BUILD_CAPABILITY",
    "SOURCE_FILENAME",
    "IfcAssetStore",
    "IfcPublishError",
    "PublishResult",
    "publish_ifc",
]

IFC_PROVIDER_ID = "ifc"
IFC_BUILD_CAPABILITY = "asset-build-ifc"
SOURCE_FILENAME = "source.ifc"


class IfcPublishError(ValueError):
    """A publish request this module refuses -- occupancy, a missing instant, an unknown node,
    or a scope that does not name exactly one thing to publish."""


class IfcAssetStore(Protocol):
    """The slice of a scope's storage a publish needs. Deliberately three plain callables, the
    same shape as ``ada.assets.published.StorageReader`` plus one write -- kept independent of it
    rather than importing it, because a publisher and a reader are different obligations even
    though today's tests share one in-memory stand-in for both."""

    def list_prefix(self, prefix: str) -> Iterable[str]: ...
    def get_bytes(self, key: str) -> bytes: ...
    def put_bytes(self, key: str, data: bytes) -> None: ...


@dataclass(frozen=True)
class PublishResult:
    dry_run: bool
    collection: str
    revision: str
    subjects: tuple[str, ...]  # every subject this publish gave (or would give) a manifest
    written: tuple[str, ...]  # every key written (or, under dry_run, that WOULD be written)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _occupied(store: IfcAssetStore, collection: str, subject: str, revision: str) -> bool:
    prefix = f"{ASSET_PREFIX}/{collection}/{subject}/{revision}/"
    return any(True for _ in store.list_prefix(prefix))


def _instant_from_project(ifc_store: IfcStore) -> str | None:
    projects = ifc_store.f.by_type("IfcProject")
    if not projects:
        return None
    oh = projects[0].OwnerHistory
    if oh is None or not oh.CreationDate:
        return None
    return datetime.fromtimestamp(oh.CreationDate, tz=timezone.utc).isoformat()


def _hierarchy_revision_for(store: IfcAssetStore, collection: str, ancestors: list[str]) -> str | None:
    """The newest revision of the nearest ancestor (in the STAGED file's real structure, not any
    one subject's spine -- Decision 3, rule 5) that already has a published subject in this
    collection. ``None`` if no ancestor has ever been published -- a leaf published before
    anything above it exists honestly records nothing rather than guessing."""
    if not ancestors:
        return None
    idx = fold_listing(store.list_prefix(f"{ASSET_PREFIX}/{collection}/"))
    for ancestor_id in ancestors:
        entry = idx.subject(collection, ancestor_id)
        if entry is not None:
            complete = entry.latest_complete
            if complete is not None:
                return complete.revision
    return None


def _write(store: IfcAssetStore, planned: list[tuple[str, bytes]], written: list[str], *, dry_run: bool) -> None:
    for key, data in planned:
        written.append(key)
        if not dry_run:
            store.put_bytes(key, data)


def _build_spec(*, source_key_ref: dict, hierarchy_key: str, node_id: str) -> BuildSpec:
    # `options` is OURS to shape (opaque to core, Decision 1) -- `source_key`/`hierarchy_key` are
    # what `IfcAssetBuilder.build()` reads back out of it; `node` restates the subject's own guid
    # for a builder that would rather not thread `request.node` through by hand.
    return BuildSpec(
        capability=IFC_BUILD_CAPABILITY,
        options={"source_key": source_key_ref["key"], "hierarchy_key": hierarchy_key, "node": node_id},
        fingerprint_inputs=("source_key", "hierarchy_key"),
    )


def publish_ifc(
    store: IfcAssetStore,
    *,
    collection: str,
    staged_key: str,
    root: str | None = None,
    leaf: str | None = None,
    source: str | None = None,
    extracted_at: str | None = None,
    published_at: str | None = None,
    dry_run: bool = False,
    replace: bool = False,
) -> PublishResult:
    """Publish a staged IFC file (``assets/_staging/<id>/source.ifc``) into ``collection``.

    ``root=None, leaf=None`` -- whole file: every reachable ``IfcSite`` becomes its own subject,
    plus the collection index. ``root=<guid>``: one branch (site, storey or assembly) and its
    whole subtree become one subject. ``leaf=<guid>``: one product becomes one subject.
    ``source=<existing collection-level key>`` (leaf-without-stem, only with ``leaf``): reuse an
    already-published source blob instead of uploading a fresh one, and record
    ``hierarchy_revision`` against the nearest already-published ancestor.

    Core's ``AssetPublisher.derive()`` protocol is the REST publish job's contract (Phase 4, not
    wired here); this function is that logic with the staging/scope plumbing left to the caller,
    so it is directly callable from a test or a future job handler alike.
    """
    if root is not None and leaf is not None:
        raise IfcPublishError("pass at most one of root= / leaf=, not both")
    if source is not None and leaf is None:
        raise IfcPublishError("source= (leaf-without-stem) requires leaf=")

    raw = store.get_bytes(staged_key)
    tmp = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
    try:
        tmp.write(raw)
        tmp.close()  # closed before reopen-by-path: an open handle + reopen is a Windows trap
        ifc_store = IfcStore.from_ifc(Path(tmp.name))
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    instant = extracted_at or _instant_from_project(ifc_store)
    if instant is None:
        raise IfcPublishError(
            "this file's IfcProject has no OwnerHistory.CreationDate, and no extracted_at was "
            "given -- pass extracted_at= (there is no other source of truth for the revision)"
        )
    try:
        revision = revision_from_instant(instant)
    except AssetKeyError as exc:
        raise IfcPublishError(str(exc)) from exc
    published_at = published_at or datetime.now(timezone.utc).isoformat()

    nodes = walk_full(ifc_store.f)
    by_id = {n.id: n for n in nodes}

    written: list[str] = []
    subjects: list[str] = []
    planned: list[tuple[str, bytes]] = []

    if root is None and leaf is None:
        root_ids = declared_site_roots(nodes)
        if not root_ids:
            raise IfcPublishError("no IfcSite reachable from IfcProject -- pass root= or leaf= explicitly")
        for subject in root_ids:
            if not replace and _occupied(store, collection, subject, revision):
                raise IfcPublishError(
                    f"{ASSET_PREFIX}/{collection}/{subject}/{revision}/ is already occupied; pass replace=True"
                )
        source_key = asset_key(collection, collection, revision, SOURCE_FILENAME)
        planned.append((source_key, raw))
        source_ref = {"key": source_key}

        for subject in root_ids:
            _publish_one_subject(
                store,
                ifc_file=ifc_store.f,
                subject_nodes=subtree_nodes(nodes, subject),
                collection=collection,
                subject=subject,
                revision=revision,
                instant=instant,
                published_at=published_at,
                source_artefact=ArtefactEntry(role="source", key=source_key, sha256=_sha(raw), size=len(raw)),
                source_ref=source_ref,
                hierarchy_revision=None,
                planned=planned,
            )
            subjects.append(subject)

        index_nodes = collection_index_nodes(nodes, root_ids)
        index_slice = build_hierarchy(
            provider=IFC_PROVIDER_ID,
            collection=collection,
            produced_at=instant,
            nodes=node_dicts(index_nodes, delivery_ids=root_ids),
            root=None,
            depth=2,
        )
        index_bytes = index_slice.to_json()
        index_hierarchy_key = asset_key(collection, collection, revision, HIERARCHY_FILENAME)
        planned.append((index_hierarchy_key, index_bytes))
        collection_manifest = AssetManifest(
            provider=IFC_PROVIDER_ID,
            collection=collection,
            subject=collection,
            revision=revision,
            node=None,
            produced_at=instant,
            published_at=published_at,
            delivery="none",
            artefacts=(
                ArtefactEntry(
                    role="hierarchy", file=HIERARCHY_FILENAME, sha256=_sha(index_bytes), size=len(index_bytes)
                ),
                ArtefactEntry(role="source", file=SOURCE_FILENAME, sha256=_sha(raw), size=len(raw)),
            ),
            counts={"sites": len(root_ids), "nodes": len(index_nodes)},
        )
        planned.append((asset_key(collection, collection, revision, MANIFEST_FILENAME), collection_manifest.to_json()))

    else:
        target = root if root is not None else leaf
        if target not in by_id:
            raise IfcPublishError(f"no node {target!r} reachable from IfcProject in this file")
        if not replace and _occupied(store, collection, target, revision):
            raise IfcPublishError(
                f"{ASSET_PREFIX}/{collection}/{target}/{revision}/ is already occupied; pass replace=True"
            )

        if source is not None:
            source_key = source
            source_artefact = ArtefactEntry(role="source", key=source_key, sha256="", size=0)
            source_ref = {"key": source_key}
            hierarchy_revision = _hierarchy_revision_for(store, collection, _ancestor_chain(nodes, target))
        else:
            source_key = asset_key(collection, target, revision, SOURCE_FILENAME)
            planned.append((source_key, raw))
            source_artefact = ArtefactEntry(role="source", file=SOURCE_FILENAME, sha256=_sha(raw), size=len(raw))
            source_ref = {"key": source_key}
            hierarchy_revision = _hierarchy_revision_for(store, collection, _ancestor_chain(nodes, target))

        _publish_one_subject(
            store,
            ifc_file=ifc_store.f,
            subject_nodes=subtree_nodes(nodes, target),
            collection=collection,
            subject=target,
            revision=revision,
            instant=instant,
            published_at=published_at,
            source_artefact=source_artefact,
            source_ref=source_ref,
            hierarchy_revision=hierarchy_revision,
            planned=planned,
        )
        subjects.append(target)

    _write(store, planned, written, dry_run=dry_run)
    return PublishResult(
        dry_run=dry_run, collection=collection, revision=revision, subjects=tuple(subjects), written=tuple(written)
    )


def _publish_one_subject(
    store: IfcAssetStore,
    *,
    ifc_file: Any,
    subject_nodes: list[IfcNode],
    collection: str,
    subject: str,
    revision: str,
    instant: str,
    published_at: str,
    source_artefact: ArtefactEntry,
    source_ref: dict,
    hierarchy_revision: str | None,
    planned: list[tuple[str, bytes]],
) -> None:
    """One subject's own hierarchy.json + ifc.index.json + asset.json, appended to ``planned``
    in that order -- the per-subject half of the write-order contract (the manifest is written
    last because it is the one file whose mere presence a reader treats as "this revision is
    complete")."""
    slice_ = build_hierarchy(
        provider=IFC_PROVIDER_ID,
        collection=collection,
        produced_at=instant,
        nodes=node_dicts(subject_nodes, delivery_ids=(subject,)),
        root=subject,
        depth=_max_depth(subject_nodes),
    )
    hierarchy_bytes = slice_.to_json()
    hierarchy_key = asset_key(collection, subject, revision, HIERARCHY_FILENAME)
    planned.append((hierarchy_key, hierarchy_bytes))

    # The provider-private sweep index (Phase 4's `asset-sweep-ifc` reads it): one hash per node
    # in the subtree, branches and leaves alike, so a changed storey attribute is visible even
    # when nothing beneath it moved.
    index_entries = build_ifc_index(ifc_file, [n.id for n in subject_nodes])
    index_bytes = index_to_json(index_entries)
    index_key = asset_key(collection, subject, revision, IFC_INDEX_FILENAME)
    planned.append((index_key, index_bytes))

    leaves = sum(1 for n in subject_nodes if n.leaf)
    manifest = AssetManifest(
        provider=IFC_PROVIDER_ID,
        collection=collection,
        subject=subject,
        revision=revision,
        node=subject,
        produced_at=instant,
        published_at=published_at,
        delivery="build",
        hierarchy_revision=hierarchy_revision,
        build=_build_spec(source_key_ref=source_ref, hierarchy_key=hierarchy_key, node_id=subject),
        artefacts=(
            source_artefact,
            ArtefactEntry(
                role="hierarchy", file=HIERARCHY_FILENAME, sha256=_sha(hierarchy_bytes), size=len(hierarchy_bytes)
            ),
            ArtefactEntry(
                role=IFC_INDEX_ROLE, file=IFC_INDEX_FILENAME, sha256=_sha(index_bytes), size=len(index_bytes)
            ),
        ),
        counts={"nodes": len(subject_nodes), "leaves": leaves},
    )
    planned.append((asset_key(collection, subject, revision, MANIFEST_FILENAME), manifest.to_json()))


def _max_depth(nodes: list[IfcNode]) -> int:
    by_id = {n.id: n for n in nodes}

    def depth_of(n: IfcNode) -> int:
        d, cur = 1, n
        while cur.parent is not None and cur.parent in by_id:
            cur = by_id[cur.parent]
            d += 1
        return d

    return max((depth_of(n) for n in nodes), default=1)
