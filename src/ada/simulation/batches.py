"""Batches of simulation runs: many runs started together, listed as one.

A client that starts many runs at once -- one per connection of a saved group, one per member of
a node -- stamps each with the same ``batch``::

    {"id": "group-7-20261121T1419", "label": "Frame A", "source": {"kind": "group", "key": "group-7"}}

``id`` names the batch (letters, digits, ``.``, ``_`` and ``-``); ``label`` is what a list shows;
``source`` is optional and says what the runs were started from: a saved ``group`` or a ``node``
of a model, by key. The record keeps it as ``batch`` (``ada.simulation.runs``), and the API keeps
an index beside the records, one empty object per member::

    _simulations/_batches/<batch_id>/<run_id>

so a batch's runs are listed (and paged) from one listing, without reading every record of the
scope, and the runs that belong to no batch are found without reading the ones that do.

A provider never sees the batch: it is not a parameter. A run started by a client that predates
it has none and is listed on its own, as before.

Stdlib only, like ``ada.simulation.runs``.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from .runs import (
    RUNS_PREFIX,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_QUEUED,
    STATUS_RUNNING,
)

__all__ = [
    "BATCHES_PREFIX",
    "BATCH_SOURCE_KINDS",
    "BatchError",
    "batch_member_key",
    "batch_of_record",
    "batch_prefix",
    "check_batch",
    "summarise_batch",
]

BATCHES_PREFIX = f"{RUNS_PREFIX}_batches/"
#: What a batch can be started from.
BATCH_SOURCE_KINDS = ("group", "node")

_BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_LABEL = 200
_MAX_SOURCE_KEY = 1024
_STATUSES = (STATUS_DONE, STATUS_RUNNING, STATUS_QUEUED, STATUS_ERROR, STATUS_CANCELLED)


class BatchError(ValueError):
    """A ``batch`` that is not one: the message says what is wrong with it."""


def check_batch(raw: Any) -> dict[str, Any] | None:
    """``raw`` as the batch a record keeps: ``{"id", "label", "source"}`` (``source`` None when not
    given), or None for no batch. A bare string is a batch of that id and label. Raises
    BatchError for anything else."""
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = {"id": raw}
    if not isinstance(raw, Mapping):
        raise BatchError('\'batch\' must be {"id": ..., "label"?: ..., "source"?: {"kind", "key"}}')
    batch_id = raw.get("id")
    if not isinstance(batch_id, str) or not _BATCH_ID.match(batch_id):
        raise BatchError(
            f"'batch.id' must be 1 to 128 letters, digits, '.', '_' or '-', starting with a letter or digit "
            f"(got {batch_id!r})"
        )
    label = raw.get("label")
    if label is None:
        label = batch_id
    if not isinstance(label, str) or not label.strip():
        raise BatchError("'batch.label' must be a non-empty string")
    label = label.strip()[:_MAX_LABEL]
    source = raw.get("source")
    if source is not None:
        if (
            not isinstance(source, Mapping)
            or source.get("kind") not in BATCH_SOURCE_KINDS
            or not isinstance(source.get("key"), str)
            or not source["key"].strip()
            or len(source["key"]) > _MAX_SOURCE_KEY
        ):
            raise BatchError(f'\'batch.source\' must be {{"kind": one of {list(BATCH_SOURCE_KINDS)}, "key": ...}}')
        source = {"kind": str(source["kind"]), "key": source["key"].strip()}
    return {"id": batch_id, "label": label, "source": source}


def batch_of_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The batch a stored record belongs to, or None. A record kept with one that cannot be read
    is read as belonging to none rather than failing a listing."""
    try:
        return check_batch(record.get("batch"))
    except BatchError:
        return None


def batch_prefix(batch_id: str) -> str:
    """The batch's index directory, with its trailing slash."""
    if not isinstance(batch_id, str) or not _BATCH_ID.match(batch_id):
        raise ValueError(f"not a batch id: {batch_id!r}")
    return f"{BATCHES_PREFIX}{batch_id}/"


def batch_member_key(batch_id: str, run_id: str) -> str:
    """The index entry that says ``run_id`` belongs to the batch."""
    return f"{batch_prefix(batch_id)}{run_id}"


def summarise_batch(batch: Mapping[str, Any], records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """One line for a batch: what it is, how many runs, how many in each status, how far along it
    is, and when its newest and oldest runs were started.

    ``progress`` is the share of the runs that are not cancelled which has finished (done or
    error), a running run counting for its own progress. ``provider`` is the newest run's; a batch
    whose runs ran on more than one names each in ``providers``."""
    counts = {s: 0 for s in _STATUSES}
    providers: dict[str, dict] = {}
    newest: Mapping[str, Any] | None = None
    oldest_at: str | None = None
    done_share = 0.0
    total = 0
    for record in records:
        total += 1
        status = str(record.get("status") or STATUS_QUEUED)
        counts[status if status in counts else STATUS_QUEUED] += 1
        provider = record.get("provider") or {}
        pid = str(provider.get("id") or "")
        if pid and pid not in providers:
            providers[pid] = {"id": pid, "domain": str(provider.get("domain") or "")}
        created = str(record.get("created_at") or "")
        if newest is None or created > str(newest.get("created_at") or ""):
            newest = record
        if oldest_at is None or (created and created < oldest_at):
            oldest_at = created
        if status in (STATUS_DONE, STATUS_ERROR):
            done_share += 1.0
        elif status == STATUS_RUNNING:
            try:
                done_share += max(0.0, min(1.0, float(record.get("progress") or 0.0)))
            except (TypeError, ValueError):
                pass
    counted = total - counts[STATUS_CANCELLED]
    newest_provider = (newest or {}).get("provider") or {}
    return {
        "id": batch["id"],
        "label": batch.get("label") or batch["id"],
        "source": batch.get("source"),
        "provider": {"id": str(newest_provider.get("id") or ""), "domain": str(newest_provider.get("domain") or "")},
        "providers": sorted(providers.values(), key=lambda p: p["id"]),
        "count": total,
        "counts": counts,
        "active": counts[STATUS_QUEUED] + counts[STATUS_RUNNING],
        "progress": round(done_share / counted, 4) if counted else 1.0,
        "newest_at": (newest or {}).get("created_at"),
        "oldest_at": oldest_at,
    }
