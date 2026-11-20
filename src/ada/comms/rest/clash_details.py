"""The detail models a scope's clash runs produced, kept and listed: one directory per detail run.

A detail run (``POST /clash-detail`` from a clash result, ``POST /clash-contacts/detail`` from a
convex-contact run) builds one group of joints with one spec into a GLB and its take-off. Those used
to be written under ``_derived/clash/...``: the convert cache, which "Clear temp files" empties, at a
key made of the spec, the result, the joint ids and the options -- so detailing the same selection
again overwrote the last model, and nothing listed what had been made. Each run now gets its own
directory beside the clash runs (``_clash/``, user content rather than a cache)::

    _clash/details/<detail_id>/record.json        what it was made from, and how it ended (this module)
    _clash/details/<detail_id>/model.glb          the model the scene loads
    _clash/details/<detail_id>/result.stats.json  its take-off (the joints, their plates and welds)

THE RECORD IS THE INDEX LINE. The scope's list is its records, read newest first: one small document
per run rather than one index document every run rewrites, so detail runs finishing together (the
panel's "detail everything" runs one job per spec) cannot drop each other's line -- which a shared
read-modify-write index would.

WHO WRITES IT. The route writes the record once, after the job is submitted (a refused submit leaves
nothing behind). The job writes only the model and the take-off, as it always did. How a run ended is
read back when the list is asked for: a take-off present means done (its counts are then stored in
the record, once); otherwise the job's own status says whether it is still running or failed.

RETENTION. The newest :data:`KEEP` per scope are kept; older ones are deleted with their files when a
new one is recorded. The clash runs' index keeps its newest 50 lines per target the same way, but
leaves the runs themselves in place -- a detail model's GLB is the large part, so here the files go too.

LIGHT ON PURPOSE: stdlib only, so the slim API imports it.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = [
    "DETAIL_MODEL_SCHEMA",
    "DETAILS_PREFIX",
    "KEEP",
    "DetailPaths",
    "delete_model",
    "detail_paths",
    "is_detail_id",
    "list_models",
    "make_record",
    "new_detail_id",
    "parse_record",
    "read_model",
    "record_new",
    "summary_from_stats",
]

DETAIL_MODEL_SCHEMA = "ada.clash/detail-model@1"
DETAILS_PREFIX = "_clash/details/"
#: How many detail models a scope keeps.
KEEP = 50

STATUS_QUEUED = "queued"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
TERMINAL = (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)

#: ``<UTC yyyymmddThhmmss><milliseconds>Z-<8 hex>``.
_ID = re.compile(r"^[0-9]{8}T[0-9]{9}Z-[0-9a-f]{8}$")


class DetailPaths:
    """The keys of one detail run."""

    def __init__(self, detail_id: str) -> None:
        self.detail_id = detail_id
        self.prefix = f"{DETAILS_PREFIX}{detail_id}/"
        self.record = f"{self.prefix}record.json"
        self.glb = f"{self.prefix}model.glb"
        self.stats = f"{self.prefix}result.stats.json"


def new_detail_id(now: datetime | None = None) -> str:
    """A new run's id: its UTC time, so ids sort as the runs were made, and a random tail."""
    now = now or datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S')}{now.microsecond // 1000:03d}Z-{uuid.uuid4().hex[:8]}"


def is_detail_id(value: str) -> bool:
    return bool(_ID.match(value or ""))


def detail_paths(detail_id: str) -> DetailPaths:
    if not is_detail_id(detail_id):
        raise ValueError(f"{detail_id!r} is not a detail model id")
    return DetailPaths(detail_id)


def make_record(
    paths: DetailPaths,
    *,
    spec: str,
    capability: str | None,
    source: Mapping[str, Any],
    joint_ids: list[str],
    options: Mapping[str, Any],
    job_id: str | None,
    created_at: str | None = None,
) -> dict:
    """A new run's record, as the route writes it. ``source`` says what it was detailed from:
    ``{"kind": "result", "result_key", "source_key", "target": {...}, "label"}`` for a clash
    result, ``{"kind": "run", "run_key", "target": {...}, "label"}`` for a convex-contact run."""
    created = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": DETAIL_MODEL_SCHEMA,
        "detail_id": paths.detail_id,
        "created_at": created,
        "status": STATUS_QUEUED,
        "job_id": job_id,
        "error": None,
        "spec": spec,
        "capability": capability,
        "source": dict(source),
        "label": f"{spec} · {source.get('label') or 'model'}",
        "requested": len(joint_ids),
        # Which joints, so a joint can be looked up later as built, not built or never asked for.
        "joint_ids": list(joint_ids),
        "options": dict(options),
        "glb_key": paths.glb,
        "stats_key": paths.stats,
        "counts": None,
        "built_by": None,
    }


def parse_record(raw: bytes | str | Mapping[str, Any]) -> dict:
    """A stored record, or ``ValueError`` when it is not one."""
    doc = json.loads(raw) if isinstance(raw, (bytes, str)) else dict(raw)
    if (
        not isinstance(doc, dict)
        or doc.get("schema") != DETAIL_MODEL_SCHEMA
        or not is_detail_id(str(doc.get("detail_id") or ""))
    ):
        raise ValueError("not a detail model record")
    return doc


def summary_from_stats(stats: Mapping[str, Any]) -> dict:
    """What the list shows of a take-off: how many joints, welds and plates were built, how many
    joints were skipped, and which specs built them (a spec may fall through to a sibling).

    ``not_built`` (joints) and ``weld_failures`` (seams of built joints) count the take-off's records
    of what did not survive (``ada.clash.detail.build_detail``); a take-off from before those were
    written counts its ``skipped`` lines as not built and no failed welds."""
    joints = stats.get("joints") if isinstance(stats.get("joints"), Mapping) else {}
    items = [i for i in (joints.get("items") or []) if isinstance(i, Mapping)]

    def total(field: str) -> int:
        return sum(int(i.get(field) or 0) for i in items)

    skipped = len(stats.get("skipped") or [])
    not_built = stats.get("not_built")
    weld_failures = stats.get("weld_failures")
    built_by = stats.get("built_by")
    return {
        "counts": {
            "joints": int(joints.get("count") or len(items)),
            "welds": total("welds"),
            "plates": total("plates"),
            "skipped": skipped,
            "not_built": len(not_built) if isinstance(not_built, list) else skipped,
            "weld_failures": len(weld_failures) if isinstance(weld_failures, list) else 0,
        },
        "built_by": dict(built_by) if isinstance(built_by, Mapping) else None,
    }


# ── the async halves, over the API's scope storage ───────────────────────────────────────────


async def _put_record(storage, scope, record: dict) -> None:
    paths = detail_paths(record["detail_id"])
    await storage.put_bytes(scope, paths.record, json.dumps(record).encode("utf-8"), content_encoding="gzip")


async def _ids(storage, scope) -> list[str]:
    """Every detail run id in the scope, newest first."""
    names = await storage.list_child_names(scope, DETAILS_PREFIX)
    return sorted((n for n in names if is_detail_id(n)), reverse=True)


async def delete_model(storage, scope, detail_id: str) -> list[str]:
    """Delete one run's directory. Returns the keys deleted (none: there was no such run)."""
    paths = detail_paths(detail_id)
    keys = [e.key for e in await storage.list_prefix(scope, paths.prefix)]
    if keys:
        await storage.delete_many(scope, keys)
    return keys


async def record_new(storage, scope, record: dict, *, keep: int | None = None) -> list[str]:
    """Store a new run's record, then delete the runs beyond the newest ``keep`` (:data:`KEEP`).
    Returns the ids deleted. A prune that fails is not the new run's failure: the next one retries."""
    keep = KEEP if keep is None else keep
    await _put_record(storage, scope, record)
    dropped: list[str] = []
    try:
        for old in (await _ids(storage, scope))[keep:]:
            if old != record["detail_id"]:
                await delete_model(storage, scope, old)
                dropped.append(old)
    except Exception:  # noqa: BLE001 - retention is housekeeping
        pass
    return dropped


async def _settle(storage, scope, record: dict, jobs) -> dict:
    """The record with how its run ended, stored once that is known. A take-off present means done;
    otherwise the job's own status, while the transport still knows the job."""
    if record.get("status") in TERMINAL:
        return record
    paths = detail_paths(record["detail_id"])
    try:
        stats = json.loads(await storage.get_bytes(scope, paths.stats))
    except (FileNotFoundError, KeyError, ValueError):
        stats = None
    except Exception:  # noqa: BLE001 - a backend's own "not found"
        stats = None
    if isinstance(stats, dict):
        settled = {**record, **summary_from_stats(stats), "status": STATUS_DONE, "error": None}
        await _put_record(storage, scope, settled)
        return settled

    snap = None
    if record.get("job_id") and jobs is not None:
        try:
            snap = await jobs.status(str(record["job_id"]))
        except Exception:  # noqa: BLE001 - a status we cannot read is not an answer
            return record
    if snap is None:
        settled = {**record, "status": STATUS_ERROR, "error": "the job making it is no longer known"}
    elif snap.status in ("error", "failed"):
        settled = {**record, "status": STATUS_ERROR, "error": (snap.payload or {}).get("error") or "the job failed"}
    elif snap.status == STATUS_CANCELLED:
        settled = {**record, "status": STATUS_CANCELLED}
    elif snap.status == STATUS_DONE:
        settled = {**record, "status": STATUS_ERROR, "error": "the job finished without writing its take-off"}
    else:
        # Still queued or running: reported as the job says, not stored.
        return {**record, "status": str(snap.status)}
    await _put_record(storage, scope, settled)
    return settled


async def read_model(storage, scope, jobs, detail_id: str) -> tuple[dict, dict | None] | None:
    """One detail model: its (settled) record and its take-off, None while there is none. None for
    a model that does not exist or whose record cannot be read."""
    paths = detail_paths(detail_id)
    try:
        record = parse_record(await storage.get_bytes(scope, paths.record))
    except Exception:  # noqa: BLE001 - missing or unreadable: no such model
        return None
    record = await _settle(storage, scope, record, jobs)
    stats = None
    if record.get("status") == STATUS_DONE:
        try:
            stats = json.loads(await storage.get_bytes(scope, paths.stats))
        except Exception:  # noqa: BLE001 - a take-off that cannot be read is reported as absent
            stats = None
    return record, stats if isinstance(stats, dict) else None


async def list_models(storage, scope, jobs, *, limit: int | None = None) -> list[dict]:
    """The scope's detail models, newest first. A record that cannot be read is left out."""
    out: list[dict] = []
    for detail_id in (await _ids(storage, scope))[: KEEP if limit is None else limit]:
        try:
            record = parse_record(await storage.get_bytes(scope, detail_paths(detail_id).record))
        except Exception:  # noqa: BLE001 - a run whose record is missing or unreadable is not listed
            continue
        out.append(await _settle(storage, scope, record, jobs))
    return out
