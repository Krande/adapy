"""Filing association rows (``ada.assoc``) as their documents are written: the writers' half.

Each writer calls one function here right after its document is stored -- a clash run, a settled
detail model, a subject file, a simulation run started or ended -- and the rows go to the scope's
``LinkStore``: its files always, Postgres too when this process has a pool
(``ada.assoc.store.MirroredLinkStore``). A link that cannot be filed is logged and never fails the
writer: the document is the truth and a reindex files it again.

LIGHT, like ``clash_runs``: stdlib, ``ada.assoc`` and the Postgres backend's module (asyncpg, which
the API has).
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from ada.assoc.records import ACTIVE_SIM_STATUSES, LinkRow, SourceState
from ada.assoc.rows import (
    ck_of_subject_key,
    contact_rows,
    detail_rows,
    sim_run_rows,
    subject_rows,
)
from ada.assoc.store import (
    FilesLinkStore,
    LinkQuery,
    LinkStore,
    MirroredLinkStore,
    sheet_writes,
)
from ada.config import logger

__all__ = [
    "drop_sim_run_links",
    "link_store",
    "members_of_ck",
    "pinned_detail_ids",
    "sim_run_link",
    "store_detail_links",
    "store_run_links",
    "store_run_links_sync",
    "store_source_states",
    "store_subject_links",
]


def link_store(storage, scope, pool=None) -> LinkStore:
    """The scope's links: its files, mirrored in Postgres when ``pool`` is a live pool."""
    files = FilesLinkStore(storage, scope)
    if pool is None:
        return files
    from .db.element_links import PgLinkStore

    return MirroredLinkStore(files, PgLinkStore(pool, scope.prefix()))


async def _put(store: LinkStore, doc_key: str, kinds: Iterable[str], rows: list[LinkRow], what: str) -> None:
    try:
        await store.put_sheet(doc_key, kinds, rows)
    except Exception as exc:  # noqa: BLE001 - a link not filed is not the writer's failure
        logger.warning("assoc: the %s links of %s were not filed: %s", what, doc_key, exc)


async def store_run_links(storage, scope, run, run_key: str, *, pool=None) -> list[LinkRow]:
    """File a clash run's contacts (two rows per clash)."""
    try:
        rows = contact_rows(run, run_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("assoc: the contacts of %s were not read: %s", run_key, exc)
        return []
    await _put(link_store(storage, scope, pool), run_key, ("contact",), rows, "contact")
    return rows


def store_run_links_sync(storage, run, run_key: str) -> None:
    """The same through a job's synchronous facade (the queue-less viewer: files only)."""
    try:
        for key, data in sheet_writes(run_key, ("contact",), contact_rows(run, run_key)):
            storage.put_bytes(key, data, content_encoding="gzip")
    except Exception as exc:  # noqa: BLE001
        logger.warning("assoc: the contacts of %s were not filed: %s", run_key, exc)


def store_source_states_sync(storage, states: Iterable[Mapping[str, Any]]) -> None:
    """The element fingerprints, through a job's synchronous facade (files only)."""
    from ada.assoc.records import encode_state
    from ada.assoc.store import state_key

    for raw in states:
        try:
            state = SourceState(**dict(raw))
            storage.put_bytes(state_key(state.source), encode_state(state), content_encoding="gzip")
        except Exception as exc:  # noqa: BLE001
            logger.warning("assoc: an element state was not filed: %s", exc)


def store_subject_links_sync(storage, subjects: Iterable[Mapping[str, Any]]) -> None:
    """The subjects a job made, through its synchronous facade (files only)."""
    for s in subjects:
        try:
            rows = _subject_rows_of(s)
            for key, data in sheet_writes(str(s["key"]), ("subject",), rows):
                storage.put_bytes(key, data, content_encoding="gzip")
        except Exception as exc:  # noqa: BLE001
            logger.warning("assoc: the subject links of %s were not filed: %s", s.get("key"), exc)


def _subject_rows_of(s: Mapping[str, Any]) -> list[LinkRow]:
    return subject_rows(
        str(s["key"]),
        str(s["ck"]),
        [tuple(m) for m in s.get("members") or ()],
        subject_kind=str(s.get("kind") or "weld-assembly"),
        detail_id=s.get("detail_id"),
        created_at=s.get("created_at"),
    )


async def store_source_states(storage, scope, states: Iterable[Mapping[str, Any] | SourceState], *, pool=None) -> None:
    """File the element fingerprints an annotate measured, one snapshot per source."""
    store = link_store(storage, scope, pool)
    for raw in states:
        state = raw if isinstance(raw, SourceState) else SourceState(**dict(raw))
        try:
            await store.put_source_state(state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("assoc: the element state of source %s was not filed: %s", state.source, exc)


async def store_detail_links(storage, scope, record: Mapping[str, Any], stats: Mapping[str, Any], *, pool=None):
    """File a settled detail model's connections and welds (``ada.assoc.rows.detail_rows``), bound
    to elements through the clash run it was detailed from."""
    from ada.clash.sidecar import parse_run

    from .clash_details import detail_paths

    record_key = detail_paths(str(record["detail_id"])).record
    run = None
    run_key = (record.get("source") or {}).get("run_key")
    if run_key:
        try:
            run = parse_run(await storage.get_bytes(scope, str(run_key)))
        except Exception as exc:  # noqa: BLE001 - its connections are then known by name only
            logger.info("assoc: the run %s of detail %s was not read: %s", run_key, record.get("detail_id"), exc)
    try:
        rows = detail_rows(record, stats, record_key=record_key, run=run)
    except Exception as exc:  # noqa: BLE001
        logger.warning("assoc: the connections of detail %s were not read: %s", record.get("detail_id"), exc)
        return []
    await _put(link_store(storage, scope, pool), record_key, ("connection", "weld"), rows, "connection")
    return rows


async def store_subject_links(storage, scope, subjects: Iterable[Mapping[str, Any]], *, pool=None) -> None:
    """File the subjects a job wrote (``ada.clash.subjects.weld_assemblies_for_detail``'s list)."""
    store = link_store(storage, scope, pool)
    for s in subjects:
        await _put(store, str(s["key"]), ("subject",), _subject_rows_of(s), "subject")


async def members_of_ck(store: LinkStore, ck: str) -> list[tuple]:
    """A connection's members as ``ada.assoc.rows.Member`` tuples, from its subject rows, else its
    connection rows (the newest detail that built it)."""
    for kind in ("subject", "connection"):
        rows = await store.rows(LinkQuery(cks=[ck], kinds=[kind]))
        if kind == "connection":
            rows = [r for r in rows if r.data.get("status") == "built"] or rows
        if rows:
            newest = max(str(r.created_at or "") for r in rows)
            doc = next(r.doc_key for r in rows if str(r.created_at or "") == newest)
            return [
                (r.data.get("role") or "member", r.element_key, r.anc, r.fp, r.source) for r in rows if r.doc_key == doc
            ]
    return []


async def _subject_ck(storage, scope, subject_key: str, store: LinkStore | None = None) -> str | None:
    """The ck a subject is of: from its key under ``_clash/subjects/``, else a subject row naming it
    (an adopted subject index), else its file's ``source.ck``."""
    ck = ck_of_subject_key(subject_key)
    if ck:
        return ck
    if store is not None:
        try:
            rows = await store.rows(LinkQuery(kinds=["subject"]))
        except Exception:  # noqa: BLE001
            rows = []
        hit = next((r.ck for r in rows if r.data.get("subject_key") == subject_key and r.ck), None)
        if hit:
            return hit
    try:
        import gzip

        raw = await storage.get_bytes(scope, subject_key)
        doc = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    except Exception:  # noqa: BLE001 - a subject that cannot be read is not of a known connection
        return None
    ck = ((doc or {}).get("source") or {}).get("ck") if isinstance(doc, dict) else None
    return str(ck) if ck else None


#: The status this process last filed per (scope, run id): a run is filed again only when it moves.
_FILED_STATUS: dict[tuple[str, str], str] = {}
_FILED_MAX = 100_000


async def sim_run_link(storage, scope, record: Mapping[str, Any], *, pool=None, force: bool = False) -> bool:
    """File (or refile) a simulation run's ``sim_run`` rows: one per member of the connection its
    subject is of. Nothing for a run on no connection's subject. A run is filed again only when its
    status has moved since this process last filed it (``force`` files it whatever). Returns whether
    rows were written."""
    from ada.simulation.runs import run_record_key

    subject = record.get("subject") or {}
    run_id = str(record.get("run_id") or "")
    if not subject.get("key") or not run_id:
        return False
    marker = (scope.prefix(), run_id)
    status = str(record.get("status") or "")
    if not force and _FILED_STATUS.get(marker) == status:
        return False
    try:
        store = link_store(storage, scope, pool)
        ck = await _subject_ck(storage, scope, str(subject["key"]), store)
        members = await members_of_ck(store, ck) if ck else []
        if not members:
            # Not a connection's subject (or one not filed yet): looked at once per status, not per read.
            _FILED_STATUS[marker] = status
            return False
        key = run_record_key(run_id)
        await store.put_sheet(key, ("sim_run",), sim_run_rows(dict(record), ck, members, record_key=key))
    except Exception as exc:  # noqa: BLE001
        logger.warning("assoc: the links of simulation run %s were not filed: %s", run_id, exc)
        return False
    if len(_FILED_STATUS) >= _FILED_MAX:
        _FILED_STATUS.clear()
    _FILED_STATUS[marker] = status
    if status in ACTIVE_SIM_STATUSES:
        ACTIVE_SCOPES.add(scope.prefix())
    return True


async def drop_sim_run_links(storage, scope, record: Mapping[str, Any], *, pool=None) -> None:
    """Drop a deleted run's ``sim_run`` rows (an empty sheet in their place). Nothing for a run on no
    subject, which never had any."""
    from ada.simulation.runs import run_record_key

    run_id = str(record.get("run_id") or "")
    if not (record.get("subject") or {}).get("key") or not run_id:
        return
    _FILED_STATUS.pop((scope.prefix(), run_id), None)
    await link_store(storage, scope, pool).put_sheet(run_record_key(run_id), ("sim_run",), [])


#: The scopes this process filed a queued or running run in: what the sweeper visits without Postgres.
ACTIVE_SCOPES: set[str] = set()


def scope_of_prefix(prefix: str):
    """The :class:`~.scope.Scope` a storage prefix names (``shared``, ``projects/<id>``, ...)."""
    from .scope import Scope

    kind, _, sid = str(prefix).partition("/")
    kinds = {"projects": "project", "users": "user", "corpus": "corpus"}
    if kind in ("shared", "providers") and not sid:
        return Scope(kind)
    if kind in kinds and sid:
        return Scope(kinds[kind], sid)
    raise ValueError(f"{prefix!r} is not a scope prefix")


async def sweep_active_runs(storage, jobs, *, pool=None, limit: int = 500) -> int:
    """Bring the links of every run they hold as queued or running up to date with the run: its
    record, brought up to date with its job (``simulation_runs.run_view``). Returns how many runs
    were looked at. A run handed to a remote studio is left to its reads."""
    from .simulation_runs import read_run, run_view

    prefixes = set(ACTIVE_SCOPES)
    if pool is not None:
        try:
            rows = await pool.fetch(
                "SELECT DISTINCT scope FROM element_links WHERE kind = 'sim_run' AND status IN ('queued', 'running')"
            )
            prefixes |= {r["scope"] for r in rows}
        except Exception as exc:  # noqa: BLE001
            logger.debug("assoc sweeper: active scopes not read: %s", exc)
    seen = 0
    for prefix in sorted(prefixes):
        try:
            scope = scope_of_prefix(prefix)
        except ValueError:
            continue
        run_ids = await active_sim_runs(storage, scope, pool=pool)
        if not run_ids:
            ACTIVE_SCOPES.discard(prefix)
        for run_id in run_ids[:limit]:
            seen += 1
            try:
                record = await read_run(storage, scope, run_id)
                if record is None or record.get("remote"):
                    continue
                await sim_run_link(storage, scope, await run_view(storage, scope, jobs, record), pool=pool, force=True)
            except Exception as exc:  # noqa: BLE001 - one run is not the sweep
                logger.debug("assoc sweeper: run %s not swept: %s", run_id, exc)
    return seen


async def sweeper_loop(storage, jobs, pool=None, interval_s: float = 30.0) -> None:
    """:func:`sweep_active_runs` every ``interval_s``, for the life of the API."""
    import asyncio

    while True:
        await asyncio.sleep(interval_s)
        try:
            await sweep_active_runs(storage, jobs, pool=pool)
        except Exception as exc:  # noqa: BLE001 - the next tick tries again
            logger.warning("assoc sweeper: %s", exc)


async def active_sim_runs(storage, scope, *, pool=None) -> list[str]:
    """The run ids the links still hold as queued or running: what the sweeper looks at."""
    store = link_store(storage, scope, pool)
    rows = await store.rows(LinkQuery(kinds=["sim_run"], sim_statuses=list(ACTIVE_SIM_STATUSES)))
    return sorted({str(r.data.get("run_id")) for r in rows if r.data.get("run_id")})


async def pinned_detail_ids(storage, scope, detail_ids: Iterable[str], *, pool=None) -> set[str]:
    """Of ``detail_ids``, the detail models a subject or a simulation run is of (through a ck one of
    their connections has): retention never deletes them."""
    from .clash_details import detail_paths

    ids = list(detail_ids)
    if not ids:
        return set()
    store = link_store(storage, scope, pool)
    keyed = {detail_paths(i).record: i for i in ids}
    conns = await store.rows(LinkQuery(kinds=["connection"], doc_keys=list(keyed)))
    cks = {r.ck for r in conns if r.ck}
    if not cks:
        return set()
    held = await store.rows(LinkQuery(kinds=["subject", "sim_run"], cks=sorted(cks)))
    pinned_cks = {r.ck for r in held}
    return {keyed[r.doc_key] for r in conns if r.ck in pinned_cks}


__all__ += [
    "ACTIVE_SCOPES",
    "active_sim_runs",
    "scope_of_prefix",
    "store_source_states_sync",
    "store_subject_links_sync",
    "sweep_active_runs",
    "sweeper_loop",
]
