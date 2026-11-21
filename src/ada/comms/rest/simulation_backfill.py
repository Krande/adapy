"""Put runs that were started before batches existed into a batch, after the fact.

A batch is stamped on its runs as they are started (``ada.simulation.batches``). Runs queued by a
client that predates the field carry none and are listed one by one. This finds such runs by
what they do have -- their provider, the start of their label, when they were started -- and
writes the batch on each record and into the batch index, as a start with ``batch`` would have.

``scripts/simulation/backfill_batch.py`` is the command line. With ``dry`` nothing is written:
the report says what would be.

A record a worker is still writing (a running run) may be written again by it from the copy it
read before the batch was set, and so lose ``batch``; its index entry stays, so the batch still
lists it. Running the backfill again once those runs have ended sets it on their records too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ada.simulation.batches import batch_of_record, check_batch

from .scope import Scope
from .simulation_runs import (
    SETTLED,
    batch_index,
    delete_batch_member,
    list_run_ids,
    read_runs,
    write_batch_member,
    write_run,
)

__all__ = ["BackfillReport", "backfill_batch", "run_label"]


def run_label(record: dict) -> str:
    label = (record.get("parameters") or {}).get("label")
    return label if isinstance(label, str) else ""


@dataclass
class BackfillReport:
    batch: dict
    scanned: int = 0
    matched: int = 0
    #: Records the batch is written on (or would be, dry).
    stamped: int = 0
    #: Index entries written (or that would be): a record already stamped whose entry was missing too.
    indexed: int = 0
    #: Already in this batch: nothing to do.
    already: int = 0
    #: In another batch: left as they are unless ``replace``.
    elsewhere: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)
    dry: bool = True

    def lines(self) -> list[str]:
        verb = "would be" if self.dry else "were"
        statuses = ", ".join(f"{s} {n}" for s, n in sorted(self.by_status.items())) or "none"
        out = [
            f"batch {self.batch['id']!r} ({self.batch['label']!r})",
            f"records read: {self.scanned}; matching: {self.matched} ({statuses})",
            f"records that {verb} given the batch: {self.stamped}; index entries that {verb} written: {self.indexed}",
            f"already in this batch: {self.already}; in another batch (left alone): {self.elsewhere}",
        ]
        out += [f"  e.g. {e}" for e in self.examples]
        if self.dry:
            out.append("dry run: nothing was written")
        return out


async def backfill_batch(
    storage,
    scope: Scope,
    *,
    batch: Any,
    provider: str,
    label_prefix: str,
    exclude_label_prefixes: Sequence[str] = (),
    created_from: str | None = None,
    created_to: str | None = None,
    replace: bool = False,
    dry: bool = True,
) -> BackfillReport:
    """Give every run of ``provider`` whose label starts with ``label_prefix`` (and with none of
    ``exclude_label_prefixes``), started between ``created_from`` and ``created_to`` (ISO strings,
    either end open), the batch ``batch`` (``{id, label?, source?}``). A run already in another
    batch is left in it unless ``replace``."""
    target = check_batch(batch)
    assert target is not None
    report = BackfillReport(batch=target, dry=dry)
    indexed = set((await batch_index(storage, scope)).get(target["id"], []))
    records = await read_runs(storage, scope, await list_run_ids(storage, scope))
    report.scanned = len(records)
    for record in records:
        if (record.get("provider") or {}).get("id") != provider:
            continue
        label = run_label(record)
        if not label.startswith(label_prefix) or any(label.startswith(x) for x in exclude_label_prefixes):
            continue
        created = str(record.get("created_at") or "")
        if (created_from and created < created_from) or (created_to and created > created_to):
            continue
        report.matched += 1
        status = str(record.get("status") or "")
        report.by_status[status] = report.by_status.get(status, 0) + 1
        current = batch_of_record(record)
        if current is not None and current["id"] != target["id"] and not replace:
            report.elsewhere += 1
            continue
        stamp = current is None or current != target
        index = record["run_id"] not in indexed
        if not stamp and not index:
            report.already += 1
            continue
        if stamp:
            report.stamped += 1
        if index:
            report.indexed += 1
        if len(report.examples) < 5:
            report.examples.append(f"{record['run_id']} {status:<9} {label}")
        if dry:
            continue
        if stamp:
            if current is not None and current["id"] != target["id"]:
                await delete_batch_member(storage, scope, current["id"], record["run_id"])
            await write_run(storage, scope, {**record, "batch": target})
            SETTLED.forget(scope, record["run_id"])
        if index:
            await write_batch_member(storage, scope, target["id"], record["run_id"])
    return report
