"""Reindexing a scope's element links from its documents: the backfill after an upgrade, and the
repair after a writer filed nothing (a worker without a database, a link that failed to file).

Every producing document the scope holds is read and its rows are filed again (``assoc_links``),
in the order the later kinds need the earlier ones:

1. clash runs (``_clash/<target>/<run>.json``): contacts, with the keys their annotate resolved, or
   -- for a run from before that -- the keys its GLB's hierarchy gives (read when it is small enough
   to read here; no fingerprints, those need a worker);
2. classic clash results (``_derived/clash/**/result.json``, while the cache holds them): contacts
   by guid or name;
3. detail models (``_clash/details/<id>/``): connections and welds, with their ck;
4. subjects: the files under ``_clash/subjects/`` (their members from the connection rows of
   their ck), and the subject indexes another tool kept (``_assoc/adopt/*.json``, schema
   ``ada.assoc/subject-index@1`` -- adopted as they are);
5. simulation runs (``_simulations/<run id>/run.json``): one row per member of the connection its
   subject is of, with its status and batch.

Filing is idempotent (a document's rows are replaced whole), so a reindex may run again at any time.
It runs once by itself in a scope that has never been indexed (:func:`ensure_indexed`, from the
links query), and on request (``POST /scopes/{s}/elements/links/reindex``).

LIGHT: stdlib, ``ada.assoc``, ``ada.clash.sidecar`` / ``result`` and the API's storage.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field

from ada.assoc.rows import (
    adopted_subject_rows,
    ck_of_subject_key,
    classic_rows,
    contact_rows,
    subject_rows,
)
from ada.assoc.store import ASSOC_PREFIX, utc_now
from ada.config import logger

from .assoc_links import link_store, members_of_ck, sim_run_link, store_detail_links

__all__ = ["ADOPT_PREFIX", "ReindexReport", "ensure_indexed", "reindex_scope", "reindex_state"]

ADOPT_PREFIX = f"{ASSOC_PREFIX}adopt/"
SUBJECT_INDEX_SCHEMA = "ada.assoc/subject-index@1"
MARKER_KEY = f"{ASSOC_PREFIX}_reindexed.json"
#: Bump to have every scope reindexed once more after an upgrade that changes what rows say.
REINDEX_VERSION = 1
#: The largest GLB read here to resolve the keys of a run its annotate never saw [bytes].
MAX_GLB_READ = 32 * 1024 * 1024

_RUN_KEY = re.compile(r"^_clash/[0-9a-f]{16}/[^/]+\.json$")


@dataclass
class ReindexReport:
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    runs: int = 0
    classic: int = 0
    details: int = 0
    subjects: int = 0
    adopted: int = 0
    simulations: int = 0
    rows: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


async def _keys(storage, scope, prefix: str) -> list[str]:
    try:
        return [e.key for e in await storage.list_prefix(scope, prefix)]
    except (FileNotFoundError, KeyError):
        return []


async def _json(storage, scope, key: str):
    return json.loads(await storage.get_bytes(scope, key))


async def _glb_identities(storage, scope, run):
    """The members' keys from the run's GLB(s), when small enough to read here; None otherwise."""
    from ada.assoc.resolve import glb_json, member_identities

    group = run.target.get("kind") == "group"
    parts = list(run.parts) if group else [{"key": run.geometry.get("key")}]
    jsons = []
    for part in parts:
        key = str(part.get("key") or "")
        try:
            head = await storage.head(scope, key) if key else None
            if not head or int(head.get("size") or 0) > MAX_GLB_READ:
                jsons.append(None)
                continue
            jsons.append(glb_json(await storage.get_bytes(scope, key)))
        except Exception:  # noqa: BLE001 - names alone then
            jsons.append(None)
    if all(j is None for j in jsons):
        return None
    return member_identities(run, jsons if group else jsons[0])


async def reindex_scope(storage, scope, *, pool=None) -> ReindexReport:
    """File every link of ``scope`` again from its documents. Returns what was done."""
    from ada.clash.result import parse_clash_result
    from ada.clash.sidecar import parse_run
    from ada.simulation.runs import RUNS_PREFIX

    from .clash_details import DETAILS_PREFIX, detail_paths, is_detail_id, parse_record
    from .simulation_runs import list_run_ids, read_run

    report = ReindexReport()
    store = link_store(storage, scope, pool)

    async def put(doc_key, kinds, rows):
        await store.put_sheet(doc_key, kinds, rows)
        report.rows += len(rows)

    # 1. clash runs
    for key in await _keys(storage, scope, "_clash/"):
        if not _RUN_KEY.match(key) or key.endswith("/index.json"):
            continue
        try:
            run = parse_run(await storage.get_bytes(scope, key))
            identities = None
            if "elements" not in (run.provenance or {}):
                identities = await _glb_identities(storage, scope, run)
            await put(key, ("contact",), contact_rows(run, key, identities))
            report.runs += 1
        except Exception as exc:  # noqa: BLE001 - one document is not the reindex
            report.errors.append(f"{key}: {type(exc).__name__}: {exc}")

    # 2. classic clash results
    for key in await _keys(storage, scope, "_derived/clash/"):
        if not key.endswith("/result.json") or "/detail/" in key:
            continue
        try:
            result = parse_clash_result(await storage.get_bytes(scope, key))
            await put(key, ("contact",), classic_rows(result, key))
            report.classic += 1
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{key}: {type(exc).__name__}: {exc}")

    # 3. detail models
    try:
        detail_ids = [n for n in await storage.list_child_names(scope, DETAILS_PREFIX) if is_detail_id(n)]
    except Exception:  # noqa: BLE001
        detail_ids = []
    for detail_id in detail_ids:
        paths = detail_paths(detail_id)
        try:
            record = parse_record(await storage.get_bytes(scope, paths.record))
            stats = await _json(storage, scope, paths.stats)
        except Exception:  # noqa: BLE001 - not finished, or gone: nothing to file
            continue
        rows = await store_detail_links(storage, scope, record, stats, pool=pool)
        report.details += 1
        report.rows += len(rows or ())

    # 4. subjects, and the subject indexes other tools kept
    for key in await _keys(storage, scope, "_clash/subjects/"):
        ck = ck_of_subject_key(key)
        if not ck:
            continue
        members = await members_of_ck(store, ck)
        if not members:
            continue
        await put(key, ("subject",), subject_rows(key, ck, members, created_at=utc_now()))
        report.subjects += 1
    for key in await _keys(storage, scope, ADOPT_PREFIX):
        try:
            doc = await _json(storage, scope, key)
            if not isinstance(doc, dict) or doc.get("schema") != SUBJECT_INDEX_SCHEMA:
                continue
            rows = adopted_subject_rows(doc, key)
            await put(key, ("subject",), rows)
            report.adopted += len({r.data.get("subject_key") for r in rows})
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{key}: {type(exc).__name__}: {exc}")

    # 5. simulation runs
    for run_id in await list_run_ids(storage, scope):
        try:
            record = await read_run(storage, scope, run_id)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{RUNS_PREFIX}{run_id}: {type(exc).__name__}: {exc}")
            continue
        if record is not None and await sim_run_link(storage, scope, record, pool=pool, force=True):
            report.simulations += 1

    report.finished_at = utc_now()
    try:
        await storage.put_bytes(
            scope, MARKER_KEY, json.dumps({"version": REINDEX_VERSION, **report.to_dict()}).encode("utf-8")
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("assoc reindex: the marker of %s was not written: %s", scope.prefix(), exc)
    return report


#: Reindexes running in this process, by scope prefix.
_RUNNING: dict[str, asyncio.Task] = {}


async def reindex_state(storage, scope) -> dict:
    """``{indexed, running, report}``: whether the scope has been reindexed at the current version."""
    running = scope.prefix() in _RUNNING and not _RUNNING[scope.prefix()].done()
    try:
        marker = await _json(storage, scope, MARKER_KEY)
    except Exception:  # noqa: BLE001
        marker = None
    indexed = isinstance(marker, dict) and int(marker.get("version") or 0) >= REINDEX_VERSION
    return {"indexed": indexed, "running": running, "report": marker if isinstance(marker, dict) else None}


def start_reindex(storage, scope, *, pool=None) -> asyncio.Task:
    """Reindex in the background (once at a time per scope); the task, already running or new."""
    prefix = scope.prefix()
    task = _RUNNING.get(prefix)
    if task is not None and not task.done():
        return task

    async def _go():
        try:
            report = await reindex_scope(storage, scope, pool=pool)
            logger.info("assoc reindex of %s: %s", prefix, report.to_dict())
            return report
        except Exception:  # noqa: BLE001
            logger.exception("assoc reindex of %s failed", prefix)
            raise

    task = _RUNNING[prefix] = asyncio.create_task(_go(), name=f"assoc-reindex-{prefix}")
    return task


async def ensure_indexed(storage, scope, *, pool=None) -> bool:
    """The one-shot sweep after an upgrade: start a reindex of a scope never indexed at the current
    version. True while one is running (a query's answer may still be filling in)."""
    state = await reindex_state(storage, scope)
    if state["indexed"]:
        return False
    start_reindex(storage, scope, pool=pool)
    return True


__all__ += ["start_reindex"]
