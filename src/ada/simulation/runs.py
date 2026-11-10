"""A simulation run's record and where a run keeps its files.

A run is a directory under ``_derived/`` in the scope it was started in, like the other results
a job writes there (``_derived/clash/...``, ``_derived/plugin_jobs/...``)::

    _derived/simulations/<run_id>/run.json          the run record (this module)
    _derived/simulations/<run_id>/artefacts/<name>  what the provider stored
    _derived/simulations/<run_id>/cancel            present once a cancel was asked for

The record is a JSON manifest beside the artefacts, the way a clash run is kept as a document in
storage: it needs no database, so it exists the same on a single-node viewer and in a cluster,
and it outlives the job status, which the queue forgets soon after the job ends.

Being under ``_derived/`` means what it means for every key there: a browser cannot write into
it through the blob route, reading it is not logged as a download, and "clear temp files"
removes it -- a run's results are produced by a job and can be produced again.

Stdlib only: the API reads and writes records without the worker's stack.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = [
    "ACTIVE_STATUSES",
    "RUN_SCHEMA",
    "RUNS_PREFIX",
    "STATUS_CANCELLED",
    "STATUS_DONE",
    "STATUS_ERROR",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "TERMINAL_STATUSES",
    "RunRecordError",
    "artefact_key",
    "cancel_key",
    "dump_run_record",
    "new_run_id",
    "new_run_record",
    "parse_run_record",
    "run_id_of_key",
    "run_prefix",
    "run_record_key",
    "safe_artefact_name",
    "utc_now",
]

RUNS_PREFIX = "_derived/simulations/"
RUN_SCHEMA = "ada.simulation/run@1"

# Spelled as the job queue spells them, so a run's status and its job's read the same.
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
ACTIVE_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})
TERMINAL_STATUSES = frozenset({STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED})

_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_NAME_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RunRecordError(ValueError):
    """A stored run record is not one this module wrote."""


def utc_now() -> str:
    """Now, as the ISO-8601 UTC string a record stores (sortable as text)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_run_id() -> str:
    """A run id: 32 hex characters that sort by creation time (milliseconds since the epoch,
    then random), so the newest runs of a scope are the last names of one directory listing and
    a list of runs reads only the records it returns."""
    return f"{int(time.time() * 1000):012x}{uuid.uuid4().hex[:20]}"


def _checked(run_id: str) -> str:
    if not isinstance(run_id, str) or not _RUN_ID.match(run_id):
        raise ValueError(f"not a simulation run id: {run_id!r}")
    return run_id


def run_prefix(run_id: str) -> str:
    """The run's directory, with its trailing slash."""
    return f"{RUNS_PREFIX}{_checked(run_id)}/"


def run_record_key(run_id: str) -> str:
    return f"{run_prefix(run_id)}run.json"


def cancel_key(run_id: str) -> str:
    return f"{run_prefix(run_id)}cancel"


def run_id_of_key(key: str) -> str | None:
    """The run id of a ``run.json`` key, or None for any other key."""
    if not key.startswith(RUNS_PREFIX) or not key.endswith("/run.json"):
        return None
    run_id = key[len(RUNS_PREFIX) : -len("/run.json")]
    return run_id if _RUN_ID.match(run_id) else None


def safe_artefact_name(name: str) -> str:
    """``name`` as a relative path inside the run's ``artefacts/`` directory.

    Sub-directories are allowed (``frames/0001.bin``); anything that could leave the directory
    or is not a plain file name raises ValueError.
    """
    if not isinstance(name, str) or not name or len(name) > 240:
        raise ValueError(f"not an artefact name: {name!r}")
    parts = name.split("/")
    if any(not _NAME_SEGMENT.match(p) or p in (".", "..") for p in parts):
        raise ValueError(
            f"not an artefact name: {name!r} (letters, digits, '.', '_' and '-', in '/'-separated segments)"
        )
    return name


def artefact_key(run_id: str, name: str) -> str:
    return f"{run_prefix(run_id)}artefacts/{safe_artefact_name(name)}"


def new_run_record(
    *,
    run_id: str,
    provider: Mapping[str, Any],
    parameters: Mapping[str, Any],
    inputs: list[str],
    started_by: Mapping[str, Any],
    kind: str | None = None,
) -> dict[str, Any]:
    """The record of a run that has just been accepted and not yet picked up."""
    return {
        "schema": RUN_SCHEMA,
        "run_id": _checked(run_id),
        "provider": {
            "id": str(provider.get("id") or ""),
            "domain": str(provider.get("domain") or ""),
            "engine": str(provider.get("engine") or ""),
            "version": str(provider.get("version") or ""),
        },
        "kind": kind,
        "parameters": dict(parameters),
        "inputs": list(inputs),
        "started_by": {"id": str(started_by.get("id") or ""), "display": started_by.get("display")},
        "created_at": utc_now(),
        "started_at": None,
        "finished_at": None,
        "status": STATUS_QUEUED,
        "stage": "queued",
        "progress": 0.0,
        "error": None,
        "job_id": None,
        "artefacts": [],
        "summary": {},
    }


def dump_run_record(record: Mapping[str, Any]) -> bytes:
    return json.dumps(record, sort_keys=True).encode("utf-8")


def parse_run_record(raw: bytes | str) -> dict[str, Any]:
    """A stored record, checked just far enough to be read safely."""
    try:
        record = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise RunRecordError(f"not JSON: {exc}") from exc
    if not isinstance(record, dict) or record.get("schema") != RUN_SCHEMA:
        raise RunRecordError(f"not a {RUN_SCHEMA} document")
    if not isinstance(record.get("run_id"), str) or not _RUN_ID.match(record["run_id"]):
        raise RunRecordError("the record has no run id")
    if not isinstance(record.get("provider"), dict) or not record["provider"].get("id"):
        raise RunRecordError("the record names no provider")
    if not isinstance(record.get("artefacts"), list):
        record["artefacts"] = []
    return record
