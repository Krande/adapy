"""Hierarchy patches: the tree's half of "apply changes" (docs/design/apply-changes.md section 4).

A provider that applies only what changed since its last run cannot republish a whole spine to say
"this element moved" -- a site spine is tens of MB, and rewriting it would undo the whole point of
applying at the lowest level. So it stages ``hierarchy-patch.json`` instead, and core keeps it
BESIDE the published spines, never in them:

* **Stored durably, per collection, in order.** ``assets/<collection>/_patches/hierarchy/<seq>.json``,
  where ``<seq>`` is a microsecond UTC instant plus a short random tail, so LEXICAL order is the
  order the patches were stored in (the same trick the revision segment plays). ``_patches`` sits
  where a subject would, and a leading ``_`` is reserved for core (``ada.assets.keys``): the index
  fold skips it, it can never be a subject, and the collection's own listing still carries it -- so
  everything keyed on that listing (the geometry roll-up's token, a clash check's) moves when a
  patch lands, with no extra invalidation.
* **Overlaid at read time.** The tree route serves a spine with every non-superseded patch applied
  in order: an upsert replaces or inserts a row BY ID (its ``parent`` decides where it hangs, so a
  moved node leaves its old parent), a delete drops the row and its whole subtree, and the
  ``children`` counts of a level follow. The overlay is keyed by PARENT ID, not by spine, so a row
  whose new parent lives in another subject's spine appears when THAT spine is served.
* **Superseded by a newer full publish.** Each patch records the instant it was stored (``at``) and
  the spine revisions it was written over (``applies_over``: the collection index and the spines of
  the subjects it touches, as they stood). Serving spine ``S@R``, a patch whose threshold for ``S``
  (its ``applies_over`` entry, else ``at``) is older than ``R`` is ignored: that spine was published
  after the patch and already says what the patch said -- or something newer. Applying a patch over
  a spine that already reflects it is harmless (every upsert and delete is idempotent), which is why
  the same-second boundary errs on the side of applying.

A spine is never rewritten to apply a patch; compacting patches into a new spine is the next full
export's job, and :func:`prune_superseded_hierarchy_patches` drops the ones it made redundant.

The writer (:func:`store_hierarchy_patch`) is called by the publish job when a staging carries the
role; the readers are the tree route, the geometry roll-up and the build route
(``ada.comms.rest.routes.assets``).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from ada.assets.index import fold_listing
from ada.assets.keys import ASSET_PREFIX, is_valid_segment, revision_from_instant
from ada.assets.manifest import HIERARCHY_FILENAME
from ada.assets.projection import _as_leaf as as_leaf
from ada.config import logger

__all__ = [
    "HIERARCHY_PATCH_SCHEMA",
    "PATCHES_SEGMENT",
    "base_descendants",
    "base_parent_map",
    "HierarchyOverlay",
    "HierarchyPatch",
    "HierarchyPatchError",
    "OverlaidSpine",
    "clear_hierarchy_patch_caches",
    "collection_hierarchy_patches",
    "hierarchy_patch_prefix",
    "is_patch_key",
    "list_hierarchy_patch_keys",
    "parse_hierarchy_patch",
    "patch_keys_in",
    "prune_superseded_hierarchy_patches",
    "read_hierarchy_patches",
    "store_hierarchy_patch",
    "validate_hierarchy_patch",
]

HIERARCHY_PATCH_SCHEMA = "ada.assets/hierarchy-patch@1"

#: The core-reserved segment patches live under, in the SUBJECT position of a collection.
PATCHES_SEGMENT = "_patches"
_HIERARCHY_KIND = "hierarchy"


class HierarchyPatchError(ValueError):
    """A ``hierarchy-patch.json`` core refuses. The message names the field and what it expected,
    because it surfaces to a provider author who cannot see this module."""


def hierarchy_patch_prefix(collection: str) -> str:
    """``assets/<collection>/_patches/hierarchy/`` -- where a collection's hierarchy patches live."""
    return f"{ASSET_PREFIX}/{collection}/{PATCHES_SEGMENT}/{_HIERARCHY_KIND}/"


def is_patch_key(key: str) -> bool:
    """True for any key under a collection's ``_patches/`` (of any kind)."""
    parts = key.split("/", 3)
    return len(parts) >= 3 and parts[0] == ASSET_PREFIX and parts[2] == PATCHES_SEGMENT


def patch_keys_in(keys: Iterable[str], collection: str) -> list[str]:
    """The hierarchy patch keys of ``collection`` among ``keys`` (a listing of the collection),
    oldest first -- lexical order is store order by construction of ``<seq>``."""
    prefix = hierarchy_patch_prefix(collection)
    return sorted(k for k in keys if k.startswith(prefix) and k.endswith(".json"))


# -- the document ------------------------------------------------------------------------------


@dataclass(frozen=True)
class HierarchyPatch:
    """One stored patch, in store order.

    ``upsert`` rows are positional against ``cols`` (as in a spine); ``records()`` gives them by
    name. ``at`` is the compact UTC instant it was stored at -- the same form as a revision, so the
    two compare lexically -- and ``applies_over`` the spine revisions it was written over."""

    key: str
    seq: str
    at: str
    provider: str
    collection: str
    cols: tuple[str, ...]
    upsert: tuple[tuple, ...]
    delete: tuple[str, ...]
    applies_over: Mapping[str, str] = field(default_factory=dict)

    def threshold(self, subject: str) -> str:
        """The newest revision of ``subject``'s spine this patch still applies over."""
        return self.applies_over.get(subject) or self.at

    def superseded_for(self, subject: str, revision: str) -> bool:
        """Whether spine ``subject@revision`` was published after this patch (and so already says
        what it said)."""
        return revision > self.threshold(subject)

    def records(self) -> list[dict]:
        return [dict(zip(self.cols, row)) for row in self.upsert]

    def edges(self) -> list[tuple[str, str | None]]:
        """``(id, parent)`` of every upserted row -- what the geometry roll-up reads."""
        id_at, parent_at = self.cols.index("id"), self.cols.index("parent")
        return [(str(r[id_at]), None if r[parent_at] is None else str(r[parent_at])) for r in self.upsert]


def validate_hierarchy_patch(doc: Any, *, collection: str) -> tuple[tuple[str, ...], list[list], list[str]]:
    """``(cols, upsert rows, delete ids)`` of a staged patch, or :class:`HierarchyPatchError`.

    The contract (apply-changes.md section 3): ``schema`` is ``ada.assets/hierarchy-patch@1``,
    ``collection`` is the one being applied, ``cols`` names the spine columns the rows carry and
    holds at least ``id`` and ``parent``, every ``upsert`` row is positional against ``cols`` with a
    non-empty ``id`` and a ``parent`` that is an id or null, and ``delete`` is a list of ids."""
    if not isinstance(doc, Mapping):
        raise HierarchyPatchError(f"a hierarchy patch must be a JSON object, got {type(doc).__name__}")
    if doc.get("schema") != HIERARCHY_PATCH_SCHEMA:
        raise HierarchyPatchError(
            f"unknown hierarchy patch schema {doc.get('schema')!r}: this core reads {HIERARCHY_PATCH_SCHEMA!r} only"
        )
    named = doc.get("collection")
    # Case-blind, as every collection lookup in the provider store is (``lower(collection)``).
    if not isinstance(named, str) or named.strip().lower() != str(collection).strip().lower():
        raise HierarchyPatchError(
            f"hierarchy patch is for collection {doc.get('collection')!r}, not {collection!r} -- a patch applies "
            f"to the collection it was produced for and nothing else"
        )
    cols = doc.get("cols")
    if not isinstance(cols, (list, tuple)) or not all(isinstance(c, str) and c for c in cols):
        raise HierarchyPatchError("'cols' must be a list of column names (the spine's)")
    if len(set(cols)) != len(cols):
        raise HierarchyPatchError(f"'cols' names a column twice: {list(cols)}")
    for required in ("id", "parent"):
        if required not in cols:
            raise HierarchyPatchError(f"'cols' must contain {required!r}: a row is placed by its id and its parent")
    id_at, parent_at = cols.index("id"), cols.index("parent")
    # ``leaf`` as a spine carries it (1/0), whatever the provider wrote: an overlaid row and a
    # spine row of the same level must read the same. Also run on read, so stored patches comply.
    leaf_at = cols.index("leaf") if "leaf" in cols else None
    upsert = doc.get("upsert") or []
    delete = doc.get("delete") or []
    if not isinstance(upsert, (list, tuple)):
        raise HierarchyPatchError("'upsert' must be a list of rows")
    if not isinstance(delete, (list, tuple)):
        raise HierarchyPatchError("'delete' must be a list of node ids")
    rows: list[list] = []
    for i, row in enumerate(upsert):
        if not isinstance(row, (list, tuple)) or len(row) != len(cols):
            raise HierarchyPatchError(
                f"upsert row {i} has {len(row) if isinstance(row, (list, tuple)) else 'no'} values but there are "
                f"{len(cols)} columns -- rows are positional against 'cols'"
            )
        node, up = row[id_at], row[parent_at]
        if not isinstance(node, str) or not node:
            raise HierarchyPatchError(f"upsert row {i}: 'id' must be a non-empty string, got {node!r}")
        if up is not None and (not isinstance(up, str) or not up):
            raise HierarchyPatchError(f"upsert row {i} ({node}): 'parent' must be a node id or null, got {up!r}")
        if up == node:
            raise HierarchyPatchError(f"upsert row {i}: {node!r} cannot be its own parent")
        row = list(row)
        if leaf_at is not None:
            row[leaf_at] = 1 if as_leaf(row[leaf_at]) else 0
        rows.append(row)
    ids: list[str] = []
    for i, node in enumerate(delete):
        if not isinstance(node, str) or not node:
            raise HierarchyPatchError(f"delete entry {i} must be a non-empty node id, got {node!r}")
        ids.append(node)
    return tuple(cols), rows, ids


def _utcnow() -> _dt.datetime:
    """The clock, one place (tests move it)."""
    return _dt.datetime.now(_dt.timezone.utc)


_SEQ_LOCK = threading.Lock()
_LAST_SEQ = ""


def _next_seq(now: _dt.datetime) -> str:
    """A key segment that sorts in store order: microsecond UTC instant + random tail, and never
    below the last one this process minted (two patches in one microsecond still keep order)."""
    global _LAST_SEQ
    with _SEQ_LOCK:
        stamp = now.strftime("%Y%m%dT%H%M%S%fZ")
        seq = f"{stamp}-{uuid.uuid4().hex[:8]}"
        if seq <= _LAST_SEQ:
            seq = f"{_LAST_SEQ.rsplit('-', 1)[0]}-{uuid.uuid4().hex[:8]}"
            if seq <= _LAST_SEQ:
                seq = f"{_LAST_SEQ}0"
        _LAST_SEQ = seq
        return seq


async def store_hierarchy_patch(storage, scope, *, provider: str, collection: str, doc: dict) -> dict:
    """Validate a staged ``hierarchy-patch.json`` and store it as the collection's next patch.

    Answers ``{key, upserted, deleted}``; ``key`` is None for a patch that changes nothing (nothing
    is stored -- an empty patch would only move every listing token). Raises
    :class:`HierarchyPatchError` for a document core refuses; nothing is written then.

    NO BASELINE, NO PATCH (apply-changes.md section 3): a collection with no published spine at
    all has nothing to overlay, so the patch is not stored and the answer carries ``refused: True``
    and a ``warning`` ("run a full export first") -- for that role only; the rest of the publish
    goes on.

    ``applies_over`` is recorded from ONE listing of the collection: the newest complete revision
    carrying a spine of the collection index and of every subject the patch names (upserted ids,
    their parents, deleted ids). That is what lets a reader tell a patch a later full publish
    superseded from one still in force (module docstring).
    """
    if not is_valid_segment(collection):
        raise HierarchyPatchError(f"invalid collection {collection!r}")
    if not isinstance(provider, str) or not provider:
        raise HierarchyPatchError("a hierarchy patch needs the provider that produced it")
    cols, rows, deletes = validate_hierarchy_patch(doc, collection=collection)
    if not rows and not deletes:
        return {"key": None, "upserted": 0, "deleted": 0}

    id_at, parent_at = cols.index("id"), cols.index("parent")
    touched = {collection, *deletes}
    for row in rows:
        touched.add(row[id_at])
        if row[parent_at] is not None:
            touched.add(row[parent_at])
    applies_over: dict[str, str] = {}
    baseline: bool | None = None  # unknown when the listing fails: never refuse on a guess
    try:
        listing = await storage.list_prefix(scope, f"{ASSET_PREFIX}/{collection}/")
        index = fold_listing(e.key for e in listing)
        baseline = False
        for entry in index.subjects(collection):
            spine = next((r for r in entry.revisions if r.has_manifest and HIERARCHY_FILENAME in r.files), None)
            if spine is None:
                continue
            baseline = True
            if entry.subject in touched:
                applies_over[entry.subject] = spine.revision
    except Exception as exc:  # noqa: BLE001 - `at` still orders the patch; the record is a refinement
        logger.warning("hierarchy patch: could not list %s to record the spines it applies over: %s", collection, exc)
    if baseline is False:
        warning = f"no baseline for {provider}/{collection}: no published hierarchy to patch; run a full export first"
        logger.warning("hierarchy patch refused: %s", warning)
        return {"key": None, "upserted": 0, "deleted": 0, "refused": True, "warning": warning}

    now = _utcnow()
    seq = _next_seq(now)
    key = f"{hierarchy_patch_prefix(collection)}{seq}.json"
    stored = {
        "schema": HIERARCHY_PATCH_SCHEMA,
        "collection": collection,
        "provider": provider,
        "seq": seq,
        "at": revision_from_instant(now),
        "stored_at": now.isoformat(),
        "applies_over": applies_over,
        "cols": list(cols),
        "upsert": rows,
        "delete": deletes,
    }
    for passthrough in ("since", "next_cursor"):
        if doc.get(passthrough) is not None:
            stored[passthrough] = doc[passthrough]
    await storage.put_bytes(scope, key, json.dumps(stored, separators=(",", ":")).encode("utf-8"))
    _forget_listing(collection)
    logger.info(
        "hierarchy patch %s stored: %d upserted, %d deleted (provider %s)", key, len(rows), len(deletes), provider
    )
    return {"key": key, "upserted": len(rows), "deleted": len(deletes)}


def parse_hierarchy_patch(raw: bytes | str, key: str) -> HierarchyPatch:
    """Read a STORED patch (what :func:`store_hierarchy_patch` wrote)."""
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise HierarchyPatchError(f"{key}: not valid JSON: {exc}") from exc
    collection = doc.get("collection") if isinstance(doc, dict) else None
    cols, rows, deletes = validate_hierarchy_patch(doc, collection=str(collection or ""))
    at = str(doc.get("at") or "")
    if not at:
        raise HierarchyPatchError(f"{key}: a stored patch carries the instant it was stored at ('at')")
    over = doc.get("applies_over") or {}
    return HierarchyPatch(
        key=key,
        seq=str(doc.get("seq") or key.rsplit("/", 1)[-1].removesuffix(".json")),
        at=at,
        provider=str(doc.get("provider") or ""),
        collection=str(collection),
        cols=cols,
        upsert=tuple(tuple(r) for r in rows),
        delete=tuple(deletes),
        applies_over={str(k): str(v) for k, v in over.items()} if isinstance(over, dict) else {},
    )


# -- reading -----------------------------------------------------------------------------------
#
# A patch is immutable at its key (the seq is in it), so a parsed one is cached by key and needs no
# invalidation. The LISTING of a collection's patches is not: a new patch can land at any time, from
# a worker in another process. The tree route asks per expanded row, so the listing is held for a
# few seconds (``ADA_ASSET_PATCH_LISTING_TTL_S``) -- a patch shows up within that, and a level
# answer costs no listing round trip in between. A patch stored by THIS process drops its
# collection's entry at once.

_LISTING_TTL_S = float(os.environ.get("ADA_ASSET_PATCH_LISTING_TTL_S", "5"))
_LISTINGS: dict[tuple[str, str], tuple[float, list[str]]] = {}
_PATCH_CACHE_ENTRIES = int(os.environ.get("ADA_ASSET_PATCH_CACHE_ENTRIES", "4096"))
_PATCHES: "OrderedDict[tuple[str, str], HierarchyPatch]" = OrderedDict()


def _scope_key(scope) -> str:
    try:
        return scope.prefix()
    except Exception:  # noqa: BLE001 - a bare test scope
        return repr(scope)


def _forget_listing(collection: str) -> None:
    for k in [k for k in _LISTINGS if k[1] == collection]:
        _LISTINGS.pop(k, None)


def clear_hierarchy_patch_caches() -> None:
    """Test hook: forget every cached listing and parsed patch."""
    _LISTINGS.clear()
    _PATCHES.clear()


async def list_hierarchy_patch_keys(storage, scope, collection: str, *, cached: bool = True) -> list[str]:
    """The collection's patch keys, oldest first (one bounded listing, held briefly)."""
    ck = (_scope_key(scope), collection)
    if cached:
        hit = _LISTINGS.get(ck)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
    try:
        entries = await storage.list_prefix(scope, hierarchy_patch_prefix(collection))
    except (FileNotFoundError, KeyError):
        entries = []
    keys = patch_keys_in((e.key for e in entries), collection)
    _LISTINGS[ck] = (time.monotonic() + _LISTING_TTL_S, keys)
    return keys


async def read_hierarchy_patches(storage, scope, keys: Sequence[str]) -> list[HierarchyPatch]:
    """The patches at ``keys``, in the order given. One that is gone or unreadable is skipped with a
    log line: a broken patch must not cost the whole tree."""
    sk = _scope_key(scope)
    out: list[HierarchyPatch] = []
    for key in keys:
        hit = _PATCHES.get((sk, key))
        if hit is None:
            try:
                hit = parse_hierarchy_patch(await storage.get_bytes(scope, key), key)
            except (FileNotFoundError, KeyError):
                continue
            except HierarchyPatchError as exc:
                logger.warning("hierarchy patch %s unreadable, ignored: %s", key, exc)
                continue
            _PATCHES[(sk, key)] = hit
            while len(_PATCHES) > _PATCH_CACHE_ENTRIES:
                _PATCHES.popitem(last=False)
        else:
            _PATCHES.move_to_end((sk, key))
        out.append(hit)
    return out


async def collection_hierarchy_patches(storage, scope, collection: str) -> list[HierarchyPatch]:
    """Every patch of ``collection``, in store order."""
    return await read_hierarchy_patches(storage, scope, await list_hierarchy_patch_keys(storage, scope, collection))


async def prune_superseded_hierarchy_patches(storage, scope, collection: str, *, dry_run: bool = False) -> list[str]:
    """Delete the patches a newer full publish made redundant; answers the keys (that would be)
    deleted.

    A patch is redundant once the collection INDEX has a complete spine revision newer than the
    patch's threshold for it -- a full export of the collection, which re-states every row the patch
    touched -- AND every other spine it recorded in ``applies_over`` has one too. Anything less
    (one site's spine republished) only supersedes it for that spine, which the readers already
    handle; the patch stays for the rest.
    """
    listing = await storage.list_prefix(scope, f"{ASSET_PREFIX}/{collection}/")
    keys = [e.key for e in listing]
    newest: dict[str, str] = {}
    for entry in fold_listing(keys).subjects(collection):
        spine = next((r for r in entry.revisions if r.has_manifest and HIERARCHY_FILENAME in r.files), None)
        if spine is not None:
            newest[entry.subject] = spine.revision
    redundant: list[str] = []
    for patch in await read_hierarchy_patches(storage, scope, patch_keys_in(keys, collection)):
        subjects = {collection, *patch.applies_over}
        if all(s in newest and patch.superseded_for(s, newest[s]) for s in subjects):
            redundant.append(patch.key)
    if not dry_run:
        for key in redundant:
            try:
                await storage.delete(scope, key)
            except Exception as exc:  # noqa: BLE001 - one key's failure is not the others'
                logger.warning("hierarchy patch prune: %s not deleted: %s", key, exc)
        _forget_listing(collection)
    return redundant


# -- the overlay -------------------------------------------------------------------------------

_UPSERT, _DELETE = "upsert", "delete"
_MISSING = object()


class HierarchyOverlay:
    """The FINAL state of the patches that apply over one spine (``subject@revision``), in order.

    Per id: the last upserted record and the index of the patch that last upserted it, and the index
    of the last delete. The later of the two decides the state, so "upsert, then delete" is a delete
    and "delete, then upsert" re-inserts. Records are by column name, so a patch carrying fewer
    columns than the spine (or more) is read against the spine's own ``cols`` by the reader.
    """

    __slots__ = ("records", "upsert_seq", "delete_seq", "providers")

    def __init__(self, patches: Iterable[HierarchyPatch], subject: str | None = None, revision: str | None = None):
        self.records: dict[str, dict] = {}
        self.upsert_seq: dict[str, int] = {}
        self.delete_seq: dict[str, int] = {}
        self.providers: dict[str, str] = {}
        i = 0
        for patch in patches:
            if subject is not None and revision is not None and patch.superseded_for(subject, revision):
                continue
            for rec in patch.records():
                node = str(rec["id"])
                self.records[node] = rec
                self.upsert_seq[node] = i
                self.providers[node] = patch.provider
            for node in patch.delete:
                self.delete_seq[node] = i
            i += 1

    def __bool__(self) -> bool:
        return bool(self.upsert_seq or self.delete_seq)

    def state(self, node: str) -> str | None:
        up, down = self.upsert_seq.get(node), self.delete_seq.get(node)
        if up is None and down is None:
            return None
        if down is None or (up is not None and up > down):
            return _UPSERT
        return _DELETE

    def deleted_ids(self) -> set[str]:
        return {n for n in self.delete_seq if self.state(n) == _DELETE}


class OverlaidSpine:
    """A parsed spine (the tree route's ``_Spine``) seen through a :class:`HierarchyOverlay`.

    Answers what the bare spine answers -- one level (``children_slice``) or the whole document
    (``slice_body``) -- with the overlay applied. Built once per (spine, newest patch) and cached by
    the route, so a level costs O(level) plus the ancestry walks of the rows it returns; the per-id
    answers are memoised here.

    THE RULES. A row's effective parent is its overlay record's ``parent`` when the overlay upserts
    it, else the spine's (a parentless row other than the subject's own top hangs under the subject,
    as in ``_Spine``). A row is HIDDEN when it, or any ancestor on its effective chain, is deleted --
    or when an ancestor was deleted by a patch LATER than the one that placed the row (an ancestor
    deleted and re-inserted does not bring back what hung under it before).
    """

    def __init__(self, spine, overlay: HierarchyOverlay, subject: str, to_dict: Callable[..., dict]):
        self.spine = spine
        self.overlay = overlay
        self.subject = subject
        self._to_dict = to_dict
        slice_ = spine.slice_
        self.cols = tuple(slice_.cols)
        self._id_at = slice_.column("id")
        self._parent_at = slice_.column("parent")
        self._provider_at = self.cols.index("provider") if "provider" in self.cols else None
        self._slice_provider = slice_.provider
        self._base_parent: dict[str, str | None] | None = None
        self._base_index: dict[str, int] | None = None
        self._hidden: dict[str, bool] = {}
        self._children: dict[str, list[str]] = {}
        self._body: dict | None = None
        # Upserted ids by their effective parent, in patch order.
        ov_children: dict[str, list[str]] = {}
        for node, rec in overlay.records.items():
            if overlay.state(node) != _UPSERT:
                continue
            up = rec.get("parent")
            ov_children.setdefault(subject if up is None else str(up), []).append(node)
        for kids in ov_children.values():
            kids.sort(key=lambda n: overlay.upsert_seq[n])
        self._ov_children = ov_children

    # -- base lookups (lazy: a level answer of a huge spine should not pay for a map it never uses)

    def _base(self) -> tuple[dict[str, str | None], dict[str, int]]:
        if self._base_parent is None:
            index: dict[str, int] = {}
            for i, row in enumerate(self.spine.slice_.rows):
                index.setdefault(str(row[self._id_at]), i)
            self._base_parent, self._base_index = base_parent_map(self.spine, self.subject), index
        return self._base_parent, self._base_index  # type: ignore[return-value]

    def effective_parent(self, node: str):
        if self.overlay.state(node) == _UPSERT:
            up = self.overlay.records[node].get("parent")
            return self.subject if up is None and node != self.subject else (None if up is None else str(up))
        parents, _ = self._base()
        return parents.get(node, _MISSING)

    def hidden(self, node: str) -> bool:
        hit = self._hidden.get(node)
        if hit is not None:
            return hit
        overlay = self.overlay
        placed_at = overlay.upsert_seq.get(node, -1) if overlay.state(node) == _UPSERT else -1
        result = False
        seen: set[str] = set()
        cur: Any = node
        while isinstance(cur, str) and cur not in seen:
            seen.add(cur)
            if overlay.state(cur) == _DELETE:
                result = True
                break
            if cur != node:
                down = overlay.delete_seq.get(cur)
                if down is not None and down > placed_at:
                    result = True
                    break
            cur = self.effective_parent(cur)
        self._hidden[node] = result
        return result

    def child_ids(self, parent: str) -> list[str]:
        """The effective children of ``parent`` in this spine, in spine order then patch order."""
        hit = self._children.get(parent)
        if hit is not None:
            return hit
        rows = self.spine.slice_.rows
        out: list[str] = []
        seen: set[str] = set()
        for i in self.spine.children.get(parent, ()):
            node = str(rows[i][self._id_at])
            if node in seen:
                continue
            if self.overlay.state(node) == _UPSERT and self.effective_parent(node) != parent:
                continue  # moved away: it hangs under its new parent now
            seen.add(node)
            out.append(node)
        for node in self._ov_children.get(parent, ()):
            if node not in seen:
                seen.add(node)
                out.append(node)
        out = [n for n in out if not self.hidden(n)]
        self._children[parent] = out
        return out

    def row(self, node: str) -> tuple:
        """``node``'s row against the SPINE's columns: its overlay record when upserted (a column
        the patch does not carry reads null, ``provider`` the patch's provider), else the spine's."""
        if self.overlay.state(node) == _UPSERT:
            rec = self.overlay.records[node]
            values = [rec.get(c) for c in self.cols]
            if self._provider_at is not None and not values[self._provider_at]:
                provider = self.overlay.providers.get(node)
                if provider and provider != self._slice_provider:
                    values[self._provider_at] = provider
            return tuple(values)
        _, index = self._base()
        return tuple(self.spine.slice_.rows[index[node]])

    def children_slice(self, parent: str) -> dict:
        """One level, shaped exactly as ``_Spine.children_slice`` answers it."""
        ids = [] if self.hidden(parent) else self.child_ids(parent)
        rows = [self.row(n) for n in ids]
        body = self._to_dict(self.spine.slice_, rows=rows)
        body["cols"].append("children")
        for out, node in zip(body["rows"], ids):
            out.append(len(self.child_ids(node)))
        body["parent"] = parent
        body["depth"] = 1
        return body

    def slice_body(self) -> dict:
        """The whole document with the overlay applied: spine rows in their order (an upserted one
        in place, carrying its new parent), minus the hidden ones and the ones moved to a parent
        this document does not hold; then the inserted rows (and rows moved in) whose parent this
        document holds, in patch order."""
        if self._body is not None:
            return self._body
        parents, index = self._base()
        kept: list[str] = []
        held: set[str] = set()
        for node in index:
            if self.hidden(node):
                continue
            if self.overlay.state(node) == _UPSERT:
                up = self.effective_parent(node)
                if up is not None and up != self.subject and up not in parents and up not in self.overlay.records:
                    continue  # moved under a parent another document holds
            kept.append(node)
            held.add(node)
        added: list[str] = []
        frontier = [self.subject, *kept]
        while frontier:
            nxt: list[str] = []
            for parent in frontier:
                for node in self._ov_children.get(parent, ()):
                    if node in held or self.hidden(node):
                        continue
                    held.add(node)
                    added.append(node)
                    nxt.append(node)
            frontier = nxt
        # A row kept above whose overlay parent is itself an insertion was placed before the
        # insertion was: still valid, the browser builds the tree by id.
        rows = [self.row(n) for n in kept + added]
        self._body = self._to_dict(self.spine.slice_, rows=rows)
        return self._body

    def descendants(self, node: str) -> list[str]:
        """Every effective descendant of ``node`` in this spine, breadth first."""
        out: list[str] = []
        seen = {node}
        frontier = [node]
        while frontier:
            nxt: list[str] = []
            for parent in frontier:
                for child in self.child_ids(parent):
                    if child not in seen:
                        seen.add(child)
                        out.append(child)
                        nxt.append(child)
            frontier = nxt
        return out


def base_parent_map(spine, subject: str) -> dict[str, str | None]:
    """``id -> parent`` of the bare spine, a parentless row other than the subject's own top
    hanging under the subject (``_Spine``'s rule)."""
    slice_ = spine.slice_
    id_at, parent_at = slice_.column("id"), slice_.column("parent")
    out: dict[str, str | None] = {}
    for row in slice_.rows:
        node = str(row[id_at])
        up = row[parent_at]
        if up is None and node != subject:
            up = subject
        out[node] = None if up is None else str(up)
    return out


def base_descendants(spine, node: str) -> list[str]:
    """Every descendant of ``node`` in the bare spine (no overlay), breadth first."""
    rows = spine.slice_.rows
    id_at = spine.slice_.column("id")
    out: list[str] = []
    seen = {node}
    frontier = [node]
    while frontier:
        nxt: list[str] = []
        for parent in frontier:
            for i in spine.children.get(parent, ()):
                child = str(rows[i][id_at])
                if child not in seen:
                    seen.add(child)
                    out.append(child)
                    nxt.append(child)
        frontier = nxt
    return out
