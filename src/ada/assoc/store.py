"""Where association rows are kept: a ``LinkStore``, over files always and Postgres when there is one.

FILES (:class:`FilesLinkStore`) -- every deployment, the shared-only one and the local viewer too::

    _assoc/<kind>/<doc id>.json     one SHEET per producing document and kind: every row it gives
    _assoc/_state/<source>.json     a source's newest element fingerprints (``SourceState``)
    _assoc/_intents/<id>.json       "a run of this connection is being started" (simulation batches)

A writer replaces its document's sheet whole (:meth:`LinkStore.put_sheet`), so no two writers share
a read-modify-write and writing the same document again changes nothing. A query reads the sheets
of the kinds it asks for (cached by size and time) -- a scan, so a deployment that pages through
links of a hundred thousand elements runs Postgres.

POSTGRES (``ada.comms.rest.db.element_links``, migration 055) mirrors the same rows in tables a
query pages with indexes. Writers that have a pool write both (:class:`MirroredLinkStore`), and read
from Postgres; one without (a worker without a database URL) writes the files only, and a reindex
brings Postgres level with them.

Both are projections of the producing documents, which stay the truth.

STDLIB ONLY (Postgres is reached through the backend the caller hands in).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Sequence

from .records import (
    KINDS,
    ElementState,
    LinkRow,
    SourceState,
    decode_sheet,
    decode_state,
    doc_id,
    encode_sheet,
    encode_state,
    flag_rows,
    group_rows,
    newest_by_group,
)

__all__ = [
    "ASSOC_PREFIX",
    "FilesLinkStore",
    "Intent",
    "LinkQuery",
    "LinkStore",
    "MirroredLinkStore",
    "intent_key",
    "query_rows",
    "sheet_key",
    "sheet_writes",
    "state_key",
]

ASSOC_PREFIX = "_assoc/"
STATE_PREFIX = f"{ASSOC_PREFIX}_state/"
INTENTS_PREFIX = f"{ASSOC_PREFIX}_intents/"
#: The most groups one query page holds.
MAX_LIMIT = 500


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sheet_key(kind: str, doc_key: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown link kind {kind!r}")
    return f"{ASSOC_PREFIX}{kind}/{doc_id(doc_key)}.json"


def state_key(source: str) -> str:
    return f"{STATE_PREFIX}{hashlib.sha256(str(source).encode('utf-8')).hexdigest()[:32]}.json"


def sheet_writes(doc_key: str, kinds: Iterable[str], rows: Iterable[LinkRow]) -> list[tuple[str, bytes]]:
    """``(key, bytes)`` of every sheet ``doc_key`` has for ``kinds``: a kind with no rows is written
    empty, so a row the document no longer gives is gone."""
    rows = list(rows)
    at = utc_now()
    return [(sheet_key(k, doc_key), encode_sheet(k, doc_key, rows, written_at=at)) for k in dict.fromkeys(kinds)]


@dataclass(frozen=True)
class Intent:
    """One "start a run of this connection" claim: unique per scope, ck, provider, parameters and
    generation (a re-run asked for on purpose is the next generation)."""

    ck: str
    provider: str
    params_hash: str
    generation: int = 0


def intent_key(intent: Intent) -> str:
    raw = f"{intent.ck}|{intent.provider}|{intent.params_hash}|{intent.generation}"
    return f"{INTENTS_PREFIX}{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]}.json"


@dataclass
class LinkQuery:
    """What a links query asks for. ``under``: element keys -- a row is under one when it is the
    element or the element is among its ancestors. ``element_keys``: exactly these elements.
    ``cks``: these connections. None for each is no restriction."""

    under: Sequence[str] | None = None
    element_keys: Sequence[str] | None = None
    cks: Sequence[str] | None = None
    kinds: Sequence[str] | None = None
    doc_keys: Sequence[str] | None = None
    include_stale: bool = True
    latest_only: bool = False
    cursor: str | None = None
    limit: int = 100
    #: Only ``sim_run`` rows in these statuses (the sweeper: the active ones).
    sim_statuses: Sequence[str] | None = None

    def matches(self, row: LinkRow) -> bool:
        if self.kinds is not None and row.kind not in self.kinds:
            return False
        if self.cks is not None and row.ck not in self.cks:
            return False
        if self.doc_keys is not None and row.doc_key not in self.doc_keys:
            return False
        if self.element_keys is not None and row.element_key not in self.element_keys:
            return False
        if self.under is not None:
            wanted = set(self.under)
            if row.element_key not in wanted and not any(a in wanted for a in row.anc):
                return False
        if self.sim_statuses is not None and row.kind == "sim_run":
            if row.data.get("status") not in self.sim_statuses:
                return False
        return True


def _summary(flagged: Sequence[Mapping[str, Any]]) -> dict:
    kinds: dict[str, int] = {}
    sims: dict[str, int] = {}
    elements: set[str] = set()
    groups: set[str] = set()
    stale = orphan = superseded = 0
    for d in flagged:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
        elements.add(d["element_key"])
        groups.add(d.get("group") or f"doc:{d.get('doc_key')}")
        stale += bool(d.get("stale"))
        orphan += bool(d.get("orphan"))
        superseded += bool(d.get("superseded"))
        if d["kind"] == "sim_run":
            status = str((d.get("data") or {}).get("status") or "")
            sims[status] = sims.get(status, 0) + 1
    return {
        "rows": len(flagged),
        "elements": len(elements),
        "groups": len(groups),
        "kinds": kinds,
        "sim_runs": sims,
        "stale": stale,
        "orphan": orphan,
        "superseded": superseded,
    }


def query_rows(
    rows: Sequence[LinkRow],
    q: LinkQuery,
    *,
    states: Mapping[str, ElementState] | None = None,
    sources: Mapping[str, SourceState] | None = None,
) -> dict:
    """A query answered from rows in hand (the files backend): ``{summary, groups, next}``, the
    groups (``group_rows``) after ``q.cursor``, at most ``q.limit`` of them, and the cursor of the
    next page (None at the end). ``summary`` counts every matching row, not just the page's."""
    matching = [r for r in rows if q.matches(r)]
    flagged = flag_rows(matching, states=states, sources=sources, newest=newest_by_group(matching))
    if not q.include_stale:
        flagged = [d for d in flagged if not d["stale"] and not d["orphan"]]
    if q.latest_only:
        flagged = [d for d in flagged if not d["superseded"]]
    summary = _summary(flagged)
    groups = group_rows(flagged)
    if q.cursor:
        groups = [g for g in groups if g["group"] > q.cursor]
    limit = max(1, min(int(q.limit or 100), MAX_LIMIT))
    page, rest = groups[:limit], groups[limit:]
    for g in page:
        g["rows"].sort(key=lambda d: (str(d.get("created_at") or ""), d["kind"], d["element_key"]), reverse=True)
    return {"summary": summary, "groups": page, "next": page[-1]["group"] if rest else None}


class LinkStore:
    """The interface every backend has."""

    async def put_sheet(self, doc_key: str, kinds: Iterable[str], rows: Iterable[LinkRow]) -> None:
        raise NotImplementedError

    async def put_source_state(self, state: SourceState) -> None:
        raise NotImplementedError

    async def rows(self, q: LinkQuery) -> list[LinkRow]:
        """Every row matching ``q`` (its paging ignored)."""
        raise NotImplementedError

    async def query(self, q: LinkQuery) -> dict:
        raise NotImplementedError

    async def take_intent(self, intent: Intent, run_id: str | None = None) -> bool:
        """Claim ``intent``: True when this call took it, False when it was already taken."""
        raise NotImplementedError

    async def set_intent_run(self, intent: Intent, run_id: str) -> None:
        raise NotImplementedError

    async def release_intent(self, intent: Intent) -> None:
        raise NotImplementedError

    async def intent_run(self, intent: Intent) -> str | None | bool:
        """The run an intent was taken for; None when taken with no run yet; False when not taken."""
        raise NotImplementedError


#: One lock per scope for the files backend's intents: a claim is "read, then write if absent".
_INTENT_LOCKS: dict[str, asyncio.Lock] = {}

#: Sheets read, by scope and key: ``(size, last_modified) -> rows``. Bounded.
_SHEET_CACHE: dict[tuple[str, str], tuple[tuple, Any]] = {}
_SHEET_CACHE_MAX = 50_000


def _scope_id(scope) -> str:
    prefix = getattr(scope, "prefix", None)
    return prefix() if callable(prefix) else str(scope)


class FilesLinkStore(LinkStore):
    """Rows as sheets in the scope's own storage (the API's async ``Storage``)."""

    def __init__(self, storage, scope) -> None:
        self.storage, self.scope = storage, scope
        self._sid = _scope_id(scope)

    async def put_sheet(self, doc_key: str, kinds: Iterable[str], rows: Iterable[LinkRow]) -> None:
        for key, data in sheet_writes(doc_key, kinds, rows):
            await self.storage.put_bytes(self.scope, key, data, content_encoding="gzip")
            _SHEET_CACHE.pop((self._sid, key), None)

    async def put_source_state(self, state: SourceState) -> None:
        await self.storage.put_bytes(self.scope, state_key(state.source), encode_state(state), content_encoding="gzip")
        _SHEET_CACHE.pop((self._sid, state_key(state.source)), None)

    async def _read_cached(self, entry, decode):
        ck = (self._sid, entry.key)
        stamp = (getattr(entry, "size", None), getattr(entry, "last_modified", None))
        hit = _SHEET_CACHE.get(ck)
        if hit is not None and hit[0] == stamp and stamp != (None, None):
            return hit[1]
        try:
            value = decode(await self.storage.get_bytes(self.scope, entry.key))
        except Exception:  # noqa: BLE001 - a sheet that cannot be read gives no rows
            value = None
        if len(_SHEET_CACHE) >= _SHEET_CACHE_MAX:
            _SHEET_CACHE.clear()
        _SHEET_CACHE[ck] = (stamp, value)
        return value

    async def _list(self, prefix: str):
        try:
            return [e for e in await self.storage.list_prefix(self.scope, prefix) if e.key.endswith(".json")]
        except (FileNotFoundError, KeyError):
            return []

    async def rows(self, q: LinkQuery) -> list[LinkRow]:
        out: list[LinkRow] = []
        for kind in q.kinds or KINDS:
            entries = await self._list(f"{ASSOC_PREFIX}{kind}/")
            sheets = await asyncio.gather(*(self._read_cached(e, decode_sheet) for e in entries))
            for sheet in sheets:
                if sheet is not None:
                    out.extend(r for r in sheet[2] if q.matches(r))
        return out

    async def sources(self, names: Iterable[str]) -> dict[str, SourceState]:
        out: dict[str, SourceState] = {}
        wanted = {n for n in names if n}
        if not wanted:
            return out
        for name in wanted:
            key = state_key(name)
            try:
                meta = await self.storage.head(self.scope, key)
            except Exception:  # noqa: BLE001 - a backend that cannot say: read it
                meta = {}
            if meta is None:
                continue
            entry = SimpleNamespace(key=key, size=meta.get("size"), last_modified=meta.get("last_modified"))
            state = await self._read_cached(entry, decode_state)
            if state is not None:
                out[name] = state
        return out

    async def query(self, q: LinkQuery) -> dict:
        rows = await self.rows(LinkQuery(**{**q.__dict__, "cursor": None}))
        sources = await self.sources(r.source for r in rows if r.source)
        return query_rows(rows, q, sources=sources)

    # intents: one claim document each, under a per-scope lock

    def _lock(self) -> asyncio.Lock:
        lock = _INTENT_LOCKS.get(self._sid)
        if lock is None:
            lock = _INTENT_LOCKS[self._sid] = asyncio.Lock()
        return lock

    async def take_intent(self, intent: Intent, run_id: str | None = None) -> bool:
        key = intent_key(intent)
        async with self._lock():
            if await self.storage.exists(self.scope, key):
                return False
            await self.storage.put_bytes(self.scope, key, _intent_doc(intent, run_id))
            return True

    async def set_intent_run(self, intent: Intent, run_id: str) -> None:
        await self.storage.put_bytes(self.scope, intent_key(intent), _intent_doc(intent, run_id))

    async def release_intent(self, intent: Intent) -> None:
        try:
            await self.storage.delete(self.scope, intent_key(intent))
        except Exception:  # noqa: BLE001 - nothing to release
            pass

    async def intent_run(self, intent: Intent) -> str | None | bool:
        try:
            doc = json.loads(await self.storage.get_bytes(self.scope, intent_key(intent)))
        except Exception:  # noqa: BLE001 - not taken
            return False
        return doc.get("run_id") if isinstance(doc, dict) else None


def _intent_doc(intent: Intent, run_id: str | None) -> bytes:
    return json.dumps({**intent.__dict__, "run_id": run_id, "taken_at": utc_now()}).encode("utf-8")


class MirroredLinkStore(LinkStore):
    """Files always, Postgres too: writes go to both, reads and intents to Postgres."""

    def __init__(self, files: FilesLinkStore, pg: LinkStore) -> None:
        self.files, self.pg = files, pg

    async def put_sheet(self, doc_key, kinds, rows) -> None:
        rows = list(rows)
        await self.files.put_sheet(doc_key, kinds, rows)
        await self.pg.put_sheet(doc_key, kinds, rows)

    async def put_source_state(self, state) -> None:
        await self.files.put_source_state(state)
        await self.pg.put_source_state(state)

    async def rows(self, q):
        return await self.pg.rows(q)

    async def query(self, q):
        return await self.pg.query(q)

    async def take_intent(self, intent, run_id=None):
        return await self.pg.take_intent(intent, run_id)

    async def set_intent_run(self, intent, run_id):
        await self.pg.set_intent_run(intent, run_id)

    async def release_intent(self, intent):
        await self.pg.release_intent(intent)

    async def intent_run(self, intent):
        return await self.pg.intent_run(intent)
