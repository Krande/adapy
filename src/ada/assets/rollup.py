"""The GEOMETRY ROLL-UP of a collection: which rows carry loadable geometry, and which rows hold such
a row somewhere below them -- answered over the WHOLE collection tree, not the part a browser has
opened.

Why the server answers it. The browser loads its tree a level at a time, so a row whose branch was
never opened cannot say whether anything under it carries geometry; on a collection of a few hundred
sites and tens of thousands of rows that is nearly every row, and a "contains geometry" overlay made
of "unknown" says nothing. The server can read every published hierarchy document once, and the
answer is small: the subjects with geometry (``here``) and the union of their ancestors (``below``).

Pure: the route does the reading (``ada.comms.rest.routes.assets``) and hands this module what it
read, so the rules live in one place and are driven by tests without a store.

The rules mirror the browser's, and each has its counterpart there:

* WHICH REVISION speaks for a subject per provider -- the newest COMPLETE revision (a manifest
  exists) that provider wrote and that carries content (delivery other than ``none``); the subject
  has geometry for that provider when that revision's delivery is ``mesh`` or ``build``
  (``resolve.ts``'s ``byProvider`` under the default ``latest`` mode, read by ``geometryMarks.ts``).
  ``any`` is the union over providers.
* WHICH TREE -- the collection's own index documents (every complete revision of the collection
  subject that carries a ``hierarchy.json``, a UNION) plus, for every subject whose row the tree
  reaches, the spine of its newest complete revision when that revision carries one
  (``collectionIndexRevisions`` / ``firstLevel``). A spine names its own top with no parent: true of
  the document, not of the forest -- the row keeps the parent another document gave it, and any
  OTHER parentless row of a spine sits under the spine's subject (the tree route's ``_Spine``).
* PLACED -- a subject is placed when its row is reachable from a top of the collection index. A
  subject only its own spine mentions floats; it is reported under ``unplaced`` rather than guessed.
* PATCHED -- the collection's hierarchy patches overlay the documents exactly as the tree route
  overlays them (``TreePlacement.apply_patches``): a moved node is placed under its new parent, and
  a deleted one -- with everything under it -- does not count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ada.assets.index import SubjectEntry
from ada.assets.manifest import HIERARCHY_FILENAME

__all__ = [
    "ROLLUP_SCHEMA",
    "LOADABLE_DELIVERIES",
    "EMPTY_DELIVERY",
    "rollup_delivery",
    "RollupPlan",
    "TreeDocument",
    "TreePlacement",
    "listing_token",
    "plan_rollup",
    "rollup_body",
]

ROLLUP_SCHEMA = "ada.assets/geometry@1"


def listing_token(entries: Iterable, *, salt: str = "", length: int = 20) -> str:
    """A token over a collection's LISTING -- every key with its size and time -- that any publish or
    unpublish under the listed prefix moves.

    What the roll-up keys its cache on, and what a clash check that reads geometry through another
    provider folds into its derived key: both answers depend on what is published in the collection,
    and the listing is the cheapest thing that changes whenever that does. ``entries`` are the
    storage's listing rows (``key``, and ``size``/``last_modified`` where the backend reports them);
    ``salt`` separates two users of one listing.
    """
    import hashlib

    h = hashlib.sha256(f"{salt}\n".encode())
    for e in sorted(entries, key=lambda e: e.key):
        h.update(f"{e.key}|{getattr(e, 'size', '')}|{getattr(e, 'last_modified', '') or ''}\n".encode())
    return h.hexdigest()[:length]


#: A delivery the browser can put in the scene: a mesh to fetch, or one a provider builds on request.
LOADABLE_DELIVERIES = frozenset({"mesh", "build"})

#: A loadable delivery whose own manifest counts no leaf under it: a build there can only fail.
#: Distinct from "none" on purpose -- "none" (a tree-only republish) steps aside for an older
#: content revision, while this IS the provider's newest word on the subject: nothing to draw.
EMPTY_DELIVERY = "empty"


def rollup_delivery(m) -> str:
    """The delivery a roll-up should read off manifest ``m``: its own, or :data:`EMPTY_DELIVERY`
    for a loadable claim whose ``counts`` say it holds no leaf."""
    if m.delivery in LOADABLE_DELIVERIES and (m.counts or {}).get("leaves") == 0:
        return EMPTY_DELIVERY
    return m.delivery


@dataclass(frozen=True)
class TreeDocument:
    """One hierarchy document to read: the collection's index (``subject == collection``) or one
    subject's spine, at the revision the resolution names."""

    subject: str
    revision: str


@dataclass(frozen=True)
class RollupPlan:
    collection: str
    #: provider -> subjects whose resolved content for that provider is loadable geometry.
    here: Mapping[str, frozenset[str]]
    #: The collection's index documents, oldest first.
    index_documents: tuple[TreeDocument, ...]
    #: subject -> its spine document (the newest complete revision, when that revision carries one).
    spines: Mapping[str, TreeDocument]

    @property
    def any_here(self) -> frozenset[str]:
        out: set[str] = set()
        for subjects in self.here.values():
            out |= subjects
        return frozenset(out)


def plan_rollup(
    collection: str,
    subjects: Iterable[SubjectEntry],
    manifests: Mapping[tuple[str, str], tuple[str, str] | None],
) -> RollupPlan:
    """What has geometry, and which documents make up the tree.

    ``subjects`` is the folded listing of the collection (revisions newest first, as
    ``fold_listing`` gives them). ``manifests`` maps ``(subject, revision)`` to the manifest's
    ``(provider, delivery)``, or None for one that could not be read -- which is not content, as in
    the browser: a mark has to be a fact.
    """
    here: dict[str, set[str]] = {}
    index_documents: list[TreeDocument] = []
    spines: dict[str, TreeDocument] = {}
    for entry in subjects:
        complete = [r for r in entry.revisions if r.has_manifest]  # newest first
        if not complete:
            continue
        if entry.subject == collection:
            index_documents.extend(
                TreeDocument(entry.subject, r.revision) for r in reversed(complete) if HIERARCHY_FILENAME in r.files
            )
        elif HIERARCHY_FILENAME in complete[0].files:
            spines[entry.subject] = TreeDocument(entry.subject, complete[0].revision)

        decided: set[str] = set()
        for rev in complete:
            summary = manifests.get((entry.subject, rev.revision))
            if summary is None:
                continue
            provider, delivery = summary
            if provider in decided or delivery == "none":
                continue
            # The newest content-carrying revision this provider wrote speaks for it here.
            decided.add(provider)
            if delivery in LOADABLE_DELIVERIES:
                here.setdefault(provider, set()).add(entry.subject)
    return RollupPlan(
        collection=collection,
        here={p: frozenset(s) for p, s in here.items()},
        index_documents=tuple(index_documents),
        spines=spines,
    )


#: Rank of a parent assignment: the collection index below every spine (the browser merges spines
#: after the index, so a spine's word on a row wins), then by revision, then subject -- so the
#: answer does not depend on the order documents happened to be read in.
_Rank = tuple


#: The overlay's word for a node a hierarchy patch deleted.
_DELETED = object()


@dataclass
class TreePlacement:
    """The merged tree's parent links, accumulated one document at a time (in any order).

    Plus the collection's HIERARCHY PATCHES (:meth:`apply_patches`, docs/design/apply-changes.md
    section 4), read as the tree route reads them: per node, the last patch's word -- a new parent
    (moved or inserted) or deleted -- wins over the documents, unless the document that placed the
    node was published after that patch (then the document already says it, or something newer).
    A node whose chain reaches a deleted one is REMOVED: it is not placed, and it does not count
    (:meth:`removed`).
    """

    collection: str
    parent: dict[str, str | None] = field(default_factory=dict)
    _rank: dict[str, _Rank] = field(default_factory=dict)
    #: Parentless rows of the collection index: the tops of the tree.
    tops: set[str] = field(default_factory=set)
    #: Spines whose rows were merged.
    read: set[str] = field(default_factory=set)
    rows: int = 0
    documents: int = 0
    #: node -> (patch, new parent | _DELETED): the final word of the patches, in order.
    _patched: dict = field(default_factory=dict)

    def apply_patches(self, patches: Iterable) -> None:
        """Fold hierarchy patches in, oldest first. A patch is any object with ``edges()`` (its
        upserted ``(id, parent)``), ``delete`` (ids) and ``threshold(subject)`` (the newest revision
        of that subject's spine it still applies over) -- ``hierarchy_patches.HierarchyPatch``."""
        for patch in patches:
            for node, up in patch.edges():
                self._patched[node] = (patch, up)
                if up is None:
                    self.tops.add(node)
            for node in patch.delete:
                self._patched[node] = (patch, _DELETED)

    def _up(self, node: str):
        """``(known, parent, via_patch)`` -- the overlay's parent when it is in force, else the
        documents'. ``parent`` may be ``_DELETED``."""
        hit = self._patched.get(node)
        if hit is not None:
            patch, up = hit
            rank = self._rank.get(node)
            doc_subject = self.collection if rank is None or rank[0] <= 0 else rank[2]
            if rank is None or rank[1] <= patch.threshold(doc_subject or self.collection):
                return True, up, True
        if node in self.parent:
            return True, self.parent[node], False
        return False, None, False

    def removed(self, node: str) -> bool:
        """Whether ``node`` or an ancestor of it was deleted by a patch in force."""
        if not self._patched:
            return False
        seen: set[str] = set()
        cur = node
        while cur is not None and cur not in seen:
            seen.add(cur)
            known, up, _ = self._up(cur)
            if up is _DELETED:
                return True
            if not known:
                return False
            cur = up
        return False

    def add(self, doc: TreeDocument, edges: Iterable[tuple[str, str | None]]) -> None:
        """Merge one document's ``(id, parent)`` rows."""
        is_index = doc.subject == self.collection
        rank: _Rank = (0, doc.revision, "") if is_index else (1, doc.revision, doc.subject)
        parent, ranks = self.parent, self._rank
        n = 0
        for node, up in edges:
            n += 1
            if up is None:
                if is_index:
                    self.tops.add(node)
                    ranks.setdefault(node, (-1, "", ""))
                    parent.setdefault(node, None)
                    continue
                if node == doc.subject:
                    # The spine's own top: keep whatever parent another document gave it.
                    parent.setdefault(node, None)
                    continue
                up = doc.subject
            prev = ranks.get(node)
            if prev is None or rank >= prev or parent.get(node) is None:
                parent[node] = up
                ranks[node] = rank
        self.rows += n
        self.documents += 1
        if not is_index:
            self.read.add(doc.subject)

    def ancestors(self, node: str) -> list[str] | None:
        """``node``'s ancestors, nearest first -- or None when its chain does not reach a top of
        the collection index (not placed)."""
        if self._patched:
            return self._patched_ancestors(node)
        parent = self.parent
        if node not in parent:
            return None
        out: list[str] = []
        seen = {node}
        below, cur = node, parent[node]
        while cur is not None:
            if cur not in parent:
                # A parent no document holds as a row. The browser draws such a row as a root
                # (``buildHierarchyFrom``); it is reachable when the link came from the collection
                # index, and floats when only a spine says so.
                return out if self._rank.get(below, (1,))[0] <= 0 else None
            if cur in seen:
                return None  # a cycle is not a placement
            seen.add(cur)
            out.append(cur)
            below, cur = cur, parent[cur]
        # Ran off the top: a top of the collection index, or a spine's own top nothing places.
        return out if below in self.tops else None

    def _patched_ancestors(self, node: str) -> list[str] | None:
        """:meth:`ancestors` through the patch overlay: same rules, with a link a patch made
        counting as the collection index's (a patch is the collection's own word on the tree)."""
        known, cur, via_patch = self._up(node)
        if not known or cur is _DELETED:
            return None
        out: list[str] = []
        seen = {node}
        below, below_patched = node, via_patch
        while cur is not None:
            known, nxt, nxt_patched = self._up(cur)
            if not known:
                return out if below_patched or self._rank.get(below, (1,))[0] <= 0 else None
            if nxt is _DELETED or cur in seen:
                return None
            seen.add(cur)
            out.append(cur)
            below, below_patched, cur = cur, nxt_patched, nxt
        return out if below in self.tops else None

    def placed(self, node: str) -> bool:
        return self.ancestors(node) is not None

    def spines_to_read(self, plan: RollupPlan) -> list[TreeDocument]:
        """Spines not yet read whose subject's row the tree now reaches -- what the browser could
        open. A spine of a subject nothing places is never on screen, so it places nothing."""
        return [doc for s, doc in sorted(plan.spines.items()) if s not in self.read and self.placed(s)]


def rollup_body(plan: RollupPlan, placement: TreePlacement, *, index_token: str) -> dict:
    """The route's answer: per provider and for ``any``, ``here`` / ``below`` / ``unplaced``."""
    cache: dict[str, list[str] | None] = {}

    def ancestors(node: str) -> list[str] | None:
        if node not in cache:
            cache[node] = placement.ancestors(node)
        return cache[node]

    def section(subjects: Sequence[str] | frozenset[str]) -> dict:
        below: set[str] = set()
        unplaced: list[str] = []
        # A subject a hierarchy patch deleted (or that hangs under one) is not in the tree: its
        # geometry counts nowhere, not even as unplaced.
        subjects = sorted(s for s in subjects if not placement.removed(s))
        for s in subjects:
            if s == plan.collection:
                continue  # the collection itself is above every row, not a row
            chain = ancestors(s)
            if chain is None:
                unplaced.append(s)
            else:
                below.update(chain)
        return {"here": sorted(subjects), "below": sorted(below), "unplaced": sorted(unplaced)}

    return {
        "schema": ROLLUP_SCHEMA,
        "collection": plan.collection,
        "index_token": index_token,
        "mode": "latest",
        "providers": {p: section(s) for p, s in sorted(plan.here.items())},
        "any": section(plan.any_here),
        "stats": {"documents": placement.documents, "rows": placement.rows},
    }
