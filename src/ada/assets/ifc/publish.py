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

**Phase 4: this module PLANS, it does not decide whether to write (Decision 2a's "a provider
PLANS; core WRITES", ``ada.assets.publish``).** :func:`_derive_ifc_plan` is the one derivation --
it builds the ordered write list and, when ``enforce_occupancy=True``, the SAME occupancy refusal
:func:`publish_ifc` has always made (kept here because :func:`publish_ifc` is still a direct,
side-effecting entry point used by tests and any caller without a job queue in front of it).
``ada.assets.ifc.publisher.IfcAssetPublisher.derive()`` calls the identical function with
``enforce_occupancy=False`` -- core's own ``apply_publish_plan`` decides occupancy and ``replace``
for a job-driven publish, and a provider deciding it twice would just be two places that could
disagree. Both callers get the OTHER guarantee this split does not change: a plan half-derived
because a node id does not exist, or because the file has no instant, still raises
:class:`IfcPublishError` before anything is written.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from ada.assets.attributes import ATTRIBUTES_FILENAME, ATTRIBUTES_ROLE, build_attributes
from ada.assets.ifc.attributes import attributes_for_nodes
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
    Actor,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
    ChangeRecord,
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


@dataclass(frozen=True)
class _DerivedIfcPlan:
    """What :func:`_derive_ifc_plan` computes -- the shared shape both :func:`publish_ifc` (which
    writes it) and ``IfcAssetPublisher.derive()`` (which hands it to core as a ``PublishPlan``,
    unwritten) build on."""

    collection: str
    revision: str
    subjects: tuple[str, ...]
    writes: tuple[tuple[str, bytes], ...]  # IN ORDER -- see the module docstring's write-order note
    counts: dict[str, int]


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


def _actor_from_owner_history(oh: Any) -> Actor | None:
    """``IfcOwnerHistory`` -> ``Actor`` (Decision 7's adopt-renamed carrier). Best-effort: a
    history missing ``OwningUser`` entirely (legal in the schema) yields ``None`` rather than a
    half-filled actor with no identity."""
    if oh is None:
        return None
    person_org = getattr(oh, "OwningUser", None)
    person = getattr(person_org, "ThePerson", None) if person_org is not None else None
    org = getattr(person_org, "TheOrganization", None) if person_org is not None else None
    ident: str | None = None
    display: str | None = None
    if person is not None:
        parts = [p for p in (getattr(person, "GivenName", None), getattr(person, "FamilyName", None)) if p]
        display = " ".join(parts) if parts else None
        # `IfcPerson.Identification` -- NOT `.Id` (that is ifcopenshell's own STEP line number,
        # `entity.id()`, an unrelated concept this actor's identity must never be keyed by).
        ident = getattr(person, "Identification", None) or display
    if ident is None and org is not None:
        ident = getattr(org, "Name", None)
        display = display or ident
    if ident is None:
        return None
    application = None
    app = getattr(oh, "OwningApplication", None)
    if app is not None:
        name = getattr(app, "ApplicationFullName", None)
        version = getattr(app, "Version", None)
        if name:
            application = f"{name} {version}".strip() if version else str(name)
    return Actor(id=str(ident), display=display, application=application)


def _relay_source_actor(ifc_file: Any, project: Any, product: Any) -> Actor | None:
    """Decision 6's IFC rule, verbatim: relay ``source_actor`` from the product's own
    ``IfcOwnerHistory`` ONLY when the file has more than one owner history, or this product's
    history differs from the project's -- otherwise a single file-wide author says nothing about
    this one leaf and is left out rather than repeated on every subject."""
    product_oh = getattr(product, "OwnerHistory", None)
    if product_oh is None:
        return None
    project_oh = getattr(project, "OwnerHistory", None) if project is not None else None
    histories = ifc_file.by_type("IfcOwnerHistory")
    has_multiple = len(histories) > 1
    differs_from_project = project_oh is not None and product_oh.id() != project_oh.id()
    if not has_multiple and not differs_from_project:
        return None
    return _actor_from_owner_history(product_oh)


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

    A direct, side-effecting entry point: derives the plan (:func:`_derive_ifc_plan`, occupancy
    enforced) and writes it here. ``ada.assets.ifc.publisher.IfcAssetPublisher.derive()`` is the
    OTHER caller of the same derivation -- see the module docstring's Phase-4 note for why
    occupancy is enforced in exactly one of the two places for each.
    """
    plan = _derive_ifc_plan(
        store,
        collection=collection,
        staged_key=staged_key,
        root=root,
        leaf=leaf,
        source=source,
        extracted_at=extracted_at,
        published_at=published_at,
        enforce_occupancy=True,
        replace=replace,
    )
    written: list[str] = []
    _write(store, list(plan.writes), written, dry_run=dry_run)
    return PublishResult(
        dry_run=dry_run,
        collection=plan.collection,
        revision=plan.revision,
        subjects=plan.subjects,
        written=tuple(written),
    )


def _derive_ifc_plan(
    store: IfcAssetStore,
    *,
    collection: str,
    staged_key: str,
    root: str | None = None,
    leaf: str | None = None,
    source: str | None = None,
    extracted_at: str | None = None,
    published_at: str | None = None,
    enforce_occupancy: bool,
    replace: bool = False,
) -> _DerivedIfcPlan:
    """The one derivation behind both ``publish_ifc`` and ``IfcAssetPublisher.derive()`` --
    identical logic, only whether occupancy is enforced HERE differs (see the module docstring).
    Never writes: the caller decides that.
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

    projects = ifc_store.f.by_type("IfcProject")
    project = projects[0] if projects else None

    nodes = walk_full(ifc_store.f)
    by_id = {n.id: n for n in nodes}

    subjects: list[str] = []
    planned: list[tuple[str, bytes]] = []
    counts: dict[str, int] = {}

    if root is None and leaf is None:
        root_ids = declared_site_roots(nodes)
        if not root_ids:
            raise IfcPublishError("no IfcSite reachable from IfcProject -- pass root= or leaf= explicitly")
        if enforce_occupancy:
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
                project=project,
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
        counts = {"sites": len(root_ids), "nodes": len(index_nodes)}
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
            counts=counts,
        )
        planned.append((asset_key(collection, collection, revision, MANIFEST_FILENAME), collection_manifest.to_json()))

    else:
        target = root if root is not None else leaf
        if target not in by_id:
            raise IfcPublishError(f"no node {target!r} reachable from IfcProject in this file")
        if enforce_occupancy and not replace and _occupied(store, collection, target, revision):
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

        counts = _publish_one_subject(
            store,
            ifc_file=ifc_store.f,
            project=project,
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

    return _DerivedIfcPlan(
        collection=collection,
        revision=revision,
        subjects=tuple(subjects),
        writes=tuple(planned),
        counts=counts,
    )


def _publish_one_subject(
    store: IfcAssetStore,
    *,
    ifc_file: Any,
    project: Any,
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
) -> dict[str, int]:
    """One subject's own hierarchy.json + ifc.index.json + attributes.json + asset.json, appended
    to ``planned`` in that order -- the per-subject half of the write-order contract (the manifest is written
    last because it is the one file whose mere presence a reader treats as "this revision is
    complete"). Returns this subject's own ``counts``, for a scoped (``--root``/``--leaf``)
    publish's plan to report."""
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

    # What each node IS, for the selection panel. Written here, on a walk the publish is already
    # making, so that answering a click needs neither ifcopenshell nor the source file -- see
    # `ada.assets.attributes`. Nodes with nothing to say are dropped by `build_attributes`, which
    # on a spatial-heavy subtree is most of them.
    attributes_doc = build_attributes(
        provider=IFC_PROVIDER_ID,
        collection=collection,
        root=subject,
        produced_at=instant,
        nodes=attributes_for_nodes(ifc_file, [n.id for n in subject_nodes]),
    )
    attributes_bytes = attributes_doc.to_json()
    attributes_key = asset_key(collection, subject, revision, ATTRIBUTES_FILENAME)
    planned.append((attributes_key, attributes_bytes))

    leaves = sum(1 for n in subject_nodes if n.leaf)
    counts = {"nodes": len(subject_nodes), "leaves": leaves}

    # Decision 6's IFC rule: relay `source_actor` from THIS subject's own product only when the
    # file has more than one owner history, or this product's differs from the project's --
    # `change` stays absent otherwise (never `published_by`/`published_via`: those are core's,
    # and a provider that set them is refused at the publish job, `ada.assets.publish`).
    change = None
    subject_product = ifc_file.by_guid(subject) if hasattr(ifc_file, "by_guid") else None
    relayed = _relay_source_actor(ifc_file, project, subject_product) if subject_product is not None else None
    if relayed is not None:
        change = ChangeRecord(source_actor=relayed)

    manifest = AssetManifest(
        provider=IFC_PROVIDER_ID,
        collection=collection,
        subject=subject,
        revision=revision,
        node=subject,
        produced_at=instant,
        published_at=published_at,
        delivery="build",
        change=change,
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
            ArtefactEntry(
                role=ATTRIBUTES_ROLE,
                file=ATTRIBUTES_FILENAME,
                sha256=_sha(attributes_bytes),
                size=len(attributes_bytes),
            ),
        ),
        counts=counts,
    )
    planned.append((asset_key(collection, subject, revision, MANIFEST_FILENAME), manifest.to_json()))
    return counts


def _max_depth(nodes: list[IfcNode]) -> int:
    by_id = {n.id: n for n in nodes}

    def depth_of(n: IfcNode) -> int:
        d, cur = 1, n
        while cur.parent is not None and cur.parent in by_id:
            cur = by_id[cur.parent]
            d += 1
        return d

    return max((depth_of(n) for n in nodes), default=1)
