"""``asset-sweep-ifc`` -- compare a STAGED newer IFC file against the newest published spine of
the same collection, and say what changed. Decision 4's "Change feed -- what is honest for a
file-backed source":

    "An IFC file has no upstream" holds for a PUBLISHED file: nothing moves underneath it. What
    CAN move is a newer file that has been imported but not yet published. So the provider
    separates sweep from publish.

**What this does NOT do.** It never writes to the asset store (that's ``publish_ifc``'s job) and
it never opens a database itself (that's the caller's -- the worker's ``source_nodes`` facade,
``ada.comms.rest.worker.source_nodes``, or the REST route, ``routes.source_nodes``).
:func:`sweep_ifc` only DERIVES rows; :func:`run_ifc_sweep` is the thin entry a worker calls to
hand them to whichever recorder it was given. The same "a provider PLANS" discipline
``ada.assets.publish`` applies to writing a revision applies here to writing a change feed: a
sweep that wrote directly would have to re-implement retries, chunking and the REST-vs-pool
choice, and get it right in every provider that ever sweeps a source. There is no REST sweep
route in this phase (the plan's own Phase 4 scope): a worker runs :func:`run_ifc_sweep` as a job.

**How coverage is decided.** A "root" this sweep can report on is a PUBLISHED SUBJECT -- one with
its own manifest. An intermediate storey that was never independently published is not a root: it
has no row of its own to be ``current``/``behind``, and its content only matters as part of
whichever published root it sits under (a cousin of Decision 4's "no widen-on-failure" for
builds: coverage cannot be wider than what was actually published). With no ``root=``, every
subject the collection index lists as independently published is covered; with ``root=<guid>``,
only that one subject is -- and every OTHER published subject in the collection is, correctly,
untouched by this call. Its absence from the returned rows is exactly what makes a reader see
``not-recorded`` rather than ``current`` for it (the ``--root``-scoped acceptance in the plan).

**Per-product verdict, then roll-up.** Comparing the staged file's own hash
(``ada.assets.ifc.index.build_ifc_index`` -- the SAME function publish uses, so a sweep and a
publish can never disagree about what "unchanged" means) against the published ``ifc.index.json``
gives added/modified/deleted per product; every ancestor between a touched product and its
covered root -- AND the root itself -- gets its ``last_changed_at`` pulled forward too (the
roll-up IS the writer's job, ``028_source_nodes.sql``), but WITHOUT an ``action`` of its own: only
the touched product carries one (Decision 7's "adopt partially" -- ``action`` is per-node
evidence, not a compound history; ``NOCHANGE``/``MODIFIEDADDED``/``MODIFIEDDELETED`` are not
values this table stores).

**The "current" row, and why it is not a NOCHANGE value.** A covered root whose subtree the sweep
found untouched still gets exactly ONE row, re-affirming the PUBLISHED ``produced_at`` as its own
``last_changed_at`` -- never advancing it. That is an administrative "I looked, and as of what is
already published, nothing has moved" stamp: one row per covered root, never one row per
unchanged product. It is what lets a reader tell ``current`` (a row exists, and its
``last_changed_at`` does not exceed what is published) from ``not-recorded`` (no row at all)
without this table ever holding a per-node ``NOCHANGE`` value -- Decision 7 rejects exactly that.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ada.assets.ifc.index import IFC_INDEX_FILENAME, build_ifc_index, parse_ifc_index
from ada.assets.ifc.publish import IfcAssetStore, _instant_from_project
from ada.assets.ifc.walk import subtree_nodes, walk_full
from ada.assets.index import fold_listing
from ada.assets.keys import ASSET_PREFIX, asset_key
from ada.assets.manifest import HIERARCHY_FILENAME, MANIFEST_FILENAME, parse_manifest
from ada.assets.projection import parse_hierarchy
from ada.cadit.ifc.store import IfcStore

__all__ = [
    "IFC_SOURCE_ID",
    "IfcSweepError",
    "SweepRow",
    "SweepResult",
    "run_ifc_sweep",
    "sweep_ifc",
]

# `source_nodes.source` for every row this module writes. It EQUALS the provider id, which is the
# convention that lets the browser ask one question per provider: the tab groups its evidence
# fetches by the producing provider of each subject, and a source id that differed from it would
# need a second mapping nobody owns.
IFC_SOURCE_ID = "ifc"

_ACTIONS = ("added", "modified", "deleted")


class IfcSweepError(ValueError):
    """A sweep this module refuses: an unknown ``--root``, or a staged file with no instant."""


@dataclass(frozen=True)
class SweepRow:
    """One ``source_nodes`` row this sweep would write. ``action`` is ``None`` for a roll-up
    (an ancestor of a touched product, or a covered root's own currency row) -- ONLY a product
    this sweep itself found added/modified/deleted carries one (see the module docstring)."""

    node_ref: str
    parent_ref: str | None
    name: str | None
    last_changed_at: str  # ISO-8601
    action: str | None = None

    def __post_init__(self) -> None:
        if self.action is not None and self.action not in _ACTIONS:
            raise IfcSweepError(f"action {self.action!r} not in {_ACTIONS}")

    def to_dict(self) -> dict:
        """Shaped exactly for ``record_source_nodes`` / the sync facade's ``record()`` --
        migration 030's optional ``action`` column, omitted (not sent as null) when this row is a
        roll-up rather than a verdict."""
        out = {"node_ref": self.node_ref, "parent_ref": self.parent_ref, "name": self.name}
        out["last_changed_at"] = self.last_changed_at
        if self.action is not None:
            out["action"] = self.action
        return out


@dataclass(frozen=True)
class SweepResult:
    collection: str
    source: str
    covered_roots: tuple[str, ...]
    rows: tuple[SweepRow, ...] = field(default_factory=tuple)

    def to_records(self) -> list[dict]:
        return [r.to_dict() for r in self.rows]


def sweep_ifc(
    store: IfcAssetStore,
    *,
    collection: str,
    staged_key: str,
    root: str | None = None,
    extracted_at: str | None = None,
) -> SweepResult:
    """Derive the rows a sweep of ``staged_key`` against ``collection``'s published spine(s)
    would write. Pure: reads through ``store`` (the same read-only slice ``publish_ifc`` uses),
    writes nothing anywhere.
    """
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
        raise IfcSweepError(
            "this file's IfcProject has no OwnerHistory.CreationDate, and no extracted_at was "
            "given -- pass extracted_at= (there is no other source of truth for when this sweep "
            "saw the file)"
        )

    staged_nodes = walk_full(ifc_store.f)
    staged_by_id = {n.id: n for n in staged_nodes}

    idx = fold_listing(store.list_prefix(f"{ASSET_PREFIX}/{collection}/"))
    published_roots = [
        s.subject for s in idx.subjects(collection) if s.subject != collection and s.latest_complete is not None
    ]
    if root is not None:
        if root not in published_roots:
            raise IfcSweepError(
                f"{root!r} has no published manifest in collection {collection!r} -- nothing to "
                f"sweep against (published roots: {sorted(published_roots)})"
            )
        targets = [root]
    else:
        targets = published_roots

    rows: list[SweepRow] = []
    for subject in targets:
        rows.extend(_sweep_one_subject(store, ifc_store, staged_nodes, staged_by_id, collection, subject, instant, idx))

    return SweepResult(collection=collection, source=IFC_SOURCE_ID, covered_roots=tuple(targets), rows=tuple(rows))


def _sweep_one_subject(
    store: IfcAssetStore,
    ifc_store: IfcStore,
    staged_nodes: list,
    staged_by_id: dict,
    collection: str,
    subject: str,
    instant: str,
    idx: Any,
) -> list[SweepRow]:
    revision = idx.subject(collection, subject).latest_complete.revision
    manifest = parse_manifest(store.get_bytes(asset_key(collection, subject, revision, MANIFEST_FILENAME)))
    spine = parse_hierarchy(store.get_bytes(asset_key(collection, subject, revision, HIERARCHY_FILENAME)))
    published_hashes = parse_ifc_index(store.get_bytes(asset_key(collection, subject, revision, IFC_INDEX_FILENAME)))
    published_records = {r["id"]: r for r in spine.records()}
    published_parent = {node_id: r["parent"] for node_id, r in published_records.items()}
    published_ids = set(published_hashes)

    subject_in_staged = subject in staged_by_id
    staged_subtree = subtree_nodes(staged_nodes, subject) if subject_in_staged else []
    staged_ids = {n.id for n in staged_subtree}
    staged_parent = {n.id: n.parent for n in staged_subtree}

    current_hashes = {e.id: e.hash for e in build_ifc_index(ifc_store.f, sorted(staged_ids))} if staged_ids else {}

    touched: dict[str, str] = {}
    for node_id, h in current_hashes.items():
        old = published_hashes.get(node_id)
        if old is None:
            touched[node_id] = "added"
        elif old != h:
            touched[node_id] = "modified"
    for node_id in published_ids:
        if node_id not in staged_ids:
            touched[node_id] = "deleted"
    if not subject_in_staged and subject not in touched:
        # the root itself is gone -- nothing more specific to blame, so it is its own deleted
        # entry rather than silently vanishing from the swept output.
        touched[subject] = "deleted"

    def _parent_of(node_id: str) -> "str | None":
        if node_id in staged_parent:
            return staged_parent[node_id]
        return published_parent.get(node_id)

    def _name_of(node_id: str) -> "str | None":
        n = staged_by_id.get(node_id)
        if n is not None:
            return n.label
        rec = published_records.get(node_id)
        return rec["label"] if rec else None

    ancestors_to_bump: set[str] = set()
    for node_id in touched:
        if node_id == subject:
            continue
        cur = _parent_of(node_id)
        guard = 0
        while cur is not None and guard < 10_000:
            ancestors_to_bump.add(cur)
            if cur == subject:
                break
            cur = _parent_of(cur)
            guard += 1

    rows: list[SweepRow] = []
    for node_id, action in touched.items():
        rows.append(
            SweepRow(
                node_ref=node_id,
                parent_ref=_parent_of(node_id),
                name=_name_of(node_id),
                last_changed_at=instant,
                action=action,
            )
        )
    for anc_id in ancestors_to_bump - set(touched):
        rows.append(
            SweepRow(
                node_ref=anc_id,
                parent_ref=_parent_of(anc_id),
                name=_name_of(anc_id),
                last_changed_at=instant,
                action=None,
            )
        )

    if not touched:
        # nothing changed anywhere in this root's subtree: one administrative "still current as
        # of what is published" row (see the module docstring) -- never advances last_changed_at.
        rows.append(
            SweepRow(
                node_ref=subject,
                parent_ref=published_parent.get(subject),
                name=_name_of(subject),
                last_changed_at=manifest.produced_at,
                action=None,
            )
        )

    return rows


def run_ifc_sweep(
    *,
    storage: Any,
    record: Callable[[str, list], int],
    collection: str,
    staged_key: str,
    root: str | None = None,
    extracted_at: str | None = None,
) -> int:
    """The worker-side entry: sweep, then hand the rows to whichever recorder the caller holds.

    ``storage`` is the same synchronous facade a builder/publisher gets (``get_bytes`` /
    ``list_keys``); ``record`` is ``_SyncSourceNodesFacade.record`` / ``_RestSourceNodesRecorder.
    record`` (``ada.comms.rest.worker.source_nodes``) -- or ``None``'s absence entirely is how a
    caller with no database represents ``no-feed``: this function is simply not called, same as a
    plugin that has nothing to record today.
    """
    from ada.assets.ifc.publisher import _ReadOnlyIfcStoreAdapter

    result = sweep_ifc(
        _ReadOnlyIfcStoreAdapter(storage),
        collection=collection,
        staged_key=staged_key,
        root=root,
        extracted_at=extracted_at,
    )
    return record(result.source, result.to_records())
