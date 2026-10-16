"""What the database itself holds, by feature area, and pruning the parts that are only history.

This database is NOT the system of record for run history, audit rows or the attribute index:
each is either a copy of something kept elsewhere (the attribute index is rebuilt from a
provider's export) or an operational trail that only needs to reach back so far. So an admin can
see what each area costs and drop what is older than they care about -- by hand from Admin ->
Database, or on a daily tick from a retention setting (``routes/admin_db.py``).

Reading is cheap on purpose: sizes and row counts come from the catalogue (``pg_class``,
``reltuples``), exact counts only for small tables, and an age range only where an index leads
with the timestamp (or the table is small) -- the storage view never scans a large table.

Pruning is per area, always by age except the attribute index (whole collections). Its rules:

* A dry run is the default and changes nothing; it counts what would go, cascaded children
  included, and estimates the bytes from the table's average row size.
* Nothing in flight is ever deleted: a ``running`` sweep or export, a ``queued`` change check, a
  ``running`` audit run, an audit row still ``queued``/``running`` or belonging to a running run.
  Those are reported as ``protected``.
* Rows go in small batches, so a prune never holds a long lock on a table the API writes to.
* Deleted rows free space for reuse only after ``VACUUM``; the prune runs ``VACUUM (ANALYZE)`` on
  each table it touched (outside any transaction) unless asked not to. Plain VACUUM does not
  shrink the files on disk -- the space is reused by new rows -- whereas a dropped attribute
  partition returns its space at once.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import asyncpg

from .attribute_index import drop_attr_collection

#: Tables at or under this heap size get an exact ``count(*)`` and an age range even without an
#: index on the timestamp: reading them is cheaper than being vague about them.
SMALL_TABLE_BYTES = 8 * 1024 * 1024
#: Rows per DELETE statement.
PRUNE_BATCH_ROWS = 5_000
#: One prune at a time across every API replica (``pg_try_advisory_lock``).
PRUNE_LOCK_ID = 0x0ADA_DB_9E
#: Smallest age a prune accepts: "older than 0 days" would be everything, which is a reset,
#: not a prune.
MIN_PRUNE_DAYS = 1

#: ``app_settings`` key of one area's retention: ``db.retention.<area>.days`` = an integer.
RETENTION_KEY = "db.retention.{area}.days"
RETENTION_PREFIX = "db.retention."
#: ``app_settings`` key of the last automatic prune: ``{"at": iso, "result": {...}}``.
RETENTION_LAST_RUN_KEY = "db.retention.last_run"


@dataclass(frozen=True)
class Step:
    """One table an area prunes by age. ``where`` selects the rows that go (``$1`` = cutoff);
    ``protect`` selects the rows that are old enough but in flight. ``children`` are tables whose
    rows go with them through ``ON DELETE CASCADE``: ``(table, fk column)`` onto ``key``."""

    table: str
    where: str
    protect: Optional[str] = None
    key: str = "id"
    children: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Area:
    id: str
    label: str
    description: str
    tables: tuple[str, ...] = ()
    #: Table-name prefixes that belong here too (the attribute index's partitions).
    prefixes: tuple[str, ...] = ()
    #: ``"age"``, ``"collections"`` (attribute index) or None (not prunable here).
    prune: Optional[str] = None
    steps: tuple[Step, ...] = ()
    #: What pruning this area does to the app, shown next to its form.
    prune_note: Optional[str] = None


_NOT_IN_FLIGHT_AUDIT = (
    "COALESCE(status, '') NOT IN ('queued', 'running') AND (audit_run_id IS NULL OR NOT EXISTS "
    "(SELECT 1 FROM audit_runs r WHERE r.id = audit_log.audit_run_id AND r.status = 'running'))"
)

AREAS: tuple[Area, ...] = (
    Area(
        "attribute_index",
        "Attribute index",
        "Searchable source attributes: one partition per provider collection, its value histogram, "
        "its catalogue row and the scopes it is shared with. Rebuilt by the next attribute sweep of "
        "that collection.",
        tables=("attr_nodes", "attr_collections", "attr_values", "attr_shares", "attr_provider_shares"),
        prefixes=("attr_p_", "attr_n_"),
        prune="collections",
        prune_note="Dropped collections disappear from attribute search until they are swept again. "
        "A collection whose source has not changed is not re-ingested, so its ingest date can be old "
        "although it is current.",
    ),
    Area(
        "change_checks",
        "Change checks",
        "One row per change check (scheduled or from the Sources tab), with its changed-node list.",
        tables=("asset_change_runs",),
        prune="age",
        steps=(
            Step(
                "asset_change_runs", "created_at < $1 AND status <> 'queued'", "created_at < $1 AND status = 'queued'"
            ),
        ),
        prune_note="Older checks leave the Sources tab's change history; the users a check reported "
        "are offered as exclusions only while a check that reported them is kept.",
    ),
    Area(
        "change_feed",
        "Source change feed",
        "When each source node last changed and by whom, as providers report it.",
        tables=("source_nodes",),
        prune="age",
        steps=(Step("source_nodes", "observed_at < $1"),),
        prune_note="Nodes not reported again for the period lose their last-changed stamp until "
        "a provider reports them again.",
    ),
    Area(
        "sweeps",
        "Sweeps",
        "Provider sweep runs and their per-collection rows with phase timings.",
        tables=("asset_sweep_runs", "asset_sweep_items"),
        prune="age",
        steps=(
            Step(
                "asset_sweep_runs",
                "created_at < $1 AND status <> 'running'",
                "created_at < $1 AND status = 'running'",
                children=(("asset_sweep_items", "run_id"),),
            ),
        ),
    ),
    Area(
        "exports",
        "Search exports",
        "Search-result export runs and their per-batch rows.",
        tables=("asset_export_runs", "asset_export_items"),
        prune="age",
        steps=(
            Step(
                "asset_export_runs",
                "created_at < $1 AND status <> 'running'",
                "created_at < $1 AND status = 'running'",
                children=(("asset_export_items", "run_id"),),
            ),
        ),
        prune_note="Only the run records go; the exported files stay where they were written.",
    ),
    # Before the audit log: a prune of both clears the metrics of rows the audit step then
    # deletes, and counts them where they were asked for.
    Area(
        "metrics",
        "Metrics & profiles",
        "Parsed profile hotspots, plus the metrics samples and client metrics carried on audit rows.",
        tables=("profile_function_stats",),
        prune="age",
        prune_note="Clears the metrics samples and client metrics of older audit rows and deletes "
        "their parsed profile hotspots; the audit rows themselves stay.",
    ),
    Area(
        "audit_log",
        "Audit log",
        "One row per upload, view, conversion and job, with its timings and metrics.",
        tables=("audit_log",),
        prune="age",
        steps=(
            Step(
                "audit_log",
                f"ts < $1 AND {_NOT_IN_FLIGHT_AUDIT}",
                f"ts < $1 AND NOT ({_NOT_IN_FLIGHT_AUDIT})",
                children=(("profile_function_stats", "audit_id"),),
            ),
        ),
        prune_note="Profile blobs a pruned row pointed at stay in object storage.",
    ),
    Area(
        "audit_runs",
        "Audit runs",
        "Regression audit runs with their parity results and issue re-checks.",
        tables=("audit_runs", "audit_parity", "audit_issue_rechecks", "audit_issue_claims"),
        prune="age",
        steps=(
            Step(
                "audit_runs",
                "started_at < $1 AND status <> 'running'",
                "started_at < $1 AND status = 'running'",
                children=(("audit_parity", "audit_run_id"), ("audit_issue_rechecks", "run_id")),
            ),
        ),
        prune_note="The audit-log rows of a pruned run are kept (detached from it).",
    ),
    Area(
        "schedules",
        "Job schedules",
        "Plugin-job, provider and audit schedules (removed ones are archived, not deleted).",
        tables=("plugin_job_schedules", "audit_schedules"),
        prune="age",
        steps=(
            Step("plugin_job_schedules", "archived_at IS NOT NULL AND archived_at < $1"),
            Step("audit_schedules", "archived_at IS NOT NULL AND archived_at < $1"),
        ),
        prune_note="Only schedules removed (archived) before the cutoff; live schedules are never touched.",
    ),
    Area("other", "Other", "Users, projects, settings, catalogues and everything else."),
)
AREA_BY_ID = {a.id: a for a in AREAS}
PRUNABLE_AREAS = tuple(a.id for a in AREAS if a.prune)

#: The timestamp an age range reads, per table. Others are probed for ``created_at``.
TS_COLUMNS: dict[str, str] = {
    "audit_log": "ts",
    "audit_parity": "ts",
    "audit_runs": "started_at",
    "asset_sweep_items": "finished_at",
    "asset_export_items": "finished_at",
    "attr_collections": "ingested_at",
    "attr_shares": "shared_at",
    "attr_provider_shares": "shared_at",
    "source_nodes": "observed_at",
    "users": "last_seen_at",
    "app_settings": "updated_at",
}


def area_of(table: str, parent: Optional[str] = None) -> str:
    name = parent or table
    for area in AREAS:
        if name in area.tables or any(name.startswith(p) for p in area.prefixes):
            return area.id
    return "other"


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _q(name: str) -> str:
    """A catalogue-supplied identifier, quoted."""
    return '"' + name.replace('"', '""') + '"'


# --- reading ------------------------------------------------------------------------------------


_TABLES_SQL = """
SELECT c.oid, c.relname, c.relkind::text AS relkind, c.reltuples::bigint AS reltuples,
       pg_total_relation_size(c.oid) AS total_bytes,
       pg_table_size(c.oid) AS table_bytes,
       pg_relation_size(c.oid) AS heap_bytes,
       pg_indexes_size(c.oid) AS index_bytes,
       COALESCE(pg_total_relation_size(NULLIF(c.reltoastrelid, 0)), 0) AS toast_bytes,
       p.relname AS parent
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_inherits i ON i.inhrelid = c.oid
LEFT JOIN pg_class p ON p.oid = i.inhparent
WHERE c.relkind IN ('r', 'p') AND n.nspname = current_schema()
ORDER BY c.relname
"""

#: For every table: the candidate timestamp columns it has and whether an index leads with each.
_TS_SQL = """
SELECT c.relname, a.attname,
       EXISTS (SELECT 1 FROM pg_index x WHERE x.indrelid = c.oid AND x.indkey[0] = a.attnum) AS indexed
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind = 'r' AND n.nspname = current_schema()
  AND a.atttypid IN ('timestamptz'::regtype, 'timestamp'::regtype)
"""


async def _age_range(conn, table: str, column: str) -> tuple[Optional[str], Optional[str]]:
    row = await conn.fetchrow(f"SELECT min({_q(column)}) AS lo, max({_q(column)}) AS hi FROM {_q(table)}")
    return _iso(row["lo"]), _iso(row["hi"])


def attribute_duplicates(partitions: list[dict]) -> dict:
    """The (provider, collection)s indexed in more than one scope -- the same collection held as
    separate copies (``db/attribute_index.py``, the one-copy rule) -- from the storage report's
    partitions.

    ``collections``: one entry per duplicated collection, its members (scope, revision, rows,
    bytes, ingested_at) the shared scope's first. ``groups``: one summary per (provider, scope
    pair), so a provider swept twice reads as one line, not a line per collection: how many
    collections both scopes hold, how many at the same revision, which side was ingested later, and
    -- when one side is the shared scope, the only copy a fold may keep -- the bytes dropping the
    other side's copies would free."""
    by_key: dict[tuple[str, str], list[dict]] = {}
    for p in partitions:
        if p.get("scope") and p.get("provider") and p.get("collection"):
            by_key.setdefault((p["provider"], p["collection"]), []).append(p)

    def member(p: dict) -> dict:
        return {
            "scope": p["scope"],
            "revision": p.get("revision"),
            "rows": int(p["indexed_rows"] if p.get("indexed_rows") is not None else p.get("rows") or 0),
            "bytes": int(p.get("total_bytes") or 0),
            "ingested_at": p.get("ingested_at"),
        }

    collections: list[dict] = []
    groups: dict[tuple[str, str, str], dict] = {}
    for (provider, collection), parts in sorted(by_key.items()):
        if len(parts) < 2:
            continue
        members = sorted((member(p) for p in parts), key=lambda m: (m["scope"] != "shared", m["scope"]))
        collections.append({"provider": provider, "collection": collection, "members": members})
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                g = groups.setdefault(
                    (provider, a["scope"], b["scope"]),
                    {
                        "provider": provider,
                        "scopes": [a["scope"], b["scope"]],
                        "keep_scope": "shared" if a["scope"] == "shared" else None,
                        "drop_scope": b["scope"] if a["scope"] == "shared" else None,
                        "collections": 0,
                        "same_revision": 0,
                        "newer_ingest": {a["scope"]: 0, b["scope"]: 0},
                        "bytes": {a["scope"]: 0, b["scope"]: 0},
                        "rows": {a["scope"]: 0, b["scope"]: 0},
                    },
                )
                g["collections"] += 1
                if a["revision"] is not None and a["revision"] == b["revision"]:
                    g["same_revision"] += 1
                elif a["ingested_at"] and b["ingested_at"] and a["ingested_at"] != b["ingested_at"]:
                    g["newer_ingest"][max((a, b), key=lambda m: m["ingested_at"])["scope"]] += 1
                for m in (a, b):
                    g["bytes"][m["scope"]] += m["bytes"]
                    g["rows"][m["scope"]] += m["rows"]
    out_groups = []
    for g in groups.values():
        g["reclaimable_bytes"] = g["bytes"][g["drop_scope"]] if g["drop_scope"] else None
        out_groups.append(g)
    out_groups.sort(key=lambda g: (-(g["reclaimable_bytes"] or 0), g["provider"], g["scopes"]))
    return {"groups": out_groups, "collections": collections}


async def storage_report(pool: asyncpg.Pool) -> dict:
    """Total size, every table grouped by area (partitions under their parent), and the
    attribute index's partitions per collection. See the module docstring for what it reads."""
    t0 = time.monotonic()
    async with pool.acquire() as conn:
        db = await conn.fetchrow("SELECT current_database() AS name, pg_database_size(current_database()) AS size")
        rows = await conn.fetch(_TABLES_SQL)
        ts_cols: dict[str, dict[str, bool]] = {}
        for r in await conn.fetch(_TS_SQL):
            ts_cols.setdefault(r["relname"], {})[r["attname"]] = r["indexed"]
        collections = (
            {
                r["partition_name"]: r
                for r in await conn.fetch(
                    "SELECT scope, provider, collection, revision, rows, ingested_at, partition_name FROM attr_collections"
                )
            }
            if "attr_collections" in {r["relname"] for r in rows}
            else {}
        )
        # Per partition, the scopes it is shared into (migration 039), oldest share first.
        shared_with: dict[tuple[str, str, str], list[str]] = {}
        if "attr_shares" in {r["relname"] for r in rows}:
            for s in await conn.fetch(
                "SELECT scope, provider, collection, target_scope FROM attr_shares ORDER BY shared_at, target_scope"
            ):
                shared_with.setdefault((s["scope"], s["provider"], s["collection"]), []).append(s["target_scope"])
        # Per (scope, provider) group, the scopes the whole group is shared into (migration 040).
        provider_shares: list[dict] = []
        shared_with_group: dict[tuple[str, str], list[str]] = {}
        if "attr_provider_shares" in {r["relname"] for r in rows}:
            for s in await conn.fetch(
                "SELECT scope, provider, target_scope, shared_at, shared_by FROM attr_provider_shares "
                "ORDER BY shared_at, target_scope"
            ):
                shared_with_group.setdefault((s["scope"], s["provider"]), []).append(s["target_scope"])
                provider_shares.append(
                    {
                        "scope": s["scope"],
                        "provider": s["provider"],
                        "target_scope": s["target_scope"],
                        "shared_at": _iso(s["shared_at"]),
                        "shared_by": s["shared_by"],
                    }
                )

        tables: dict[str, dict] = {}
        partitions: list[dict] = []
        for r in rows:
            small = r["heap_bytes"] <= SMALL_TABLE_BYTES
            rows_est, exact = r["reltuples"], False
            if r["relkind"] == "r" and (small or rows_est < 0):
                rows_est, exact = await conn.fetchval(f"SELECT count(*) FROM {_q(r['relname'])}"), True
            entry = {
                "name": r["relname"],
                "area": area_of(r["relname"], r["parent"]),
                "partitioned": r["relkind"] == "p",
                "rows": max(int(rows_est), 0) if r["relkind"] == "r" else 0,
                # A partitioned parent's count is its partitions' (summed below).
                "rows_exact": exact or r["relkind"] == "p",
                "total_bytes": int(r["total_bytes"]),
                "table_bytes": int(r["table_bytes"]),
                "index_bytes": int(r["index_bytes"]),
                "toast_bytes": int(r["toast_bytes"]),
                "oldest": None,
                "newest": None,
                "age_column": None,
            }
            if r["parent"]:
                c = collections.get(r["relname"])
                partitions.append(
                    {
                        **{k: entry[k] for k in ("name", "rows", "rows_exact", "total_bytes", "table_bytes")},
                        "index_bytes": entry["index_bytes"],
                        "toast_bytes": entry["toast_bytes"],
                        "parent": r["parent"],
                        "scope": c["scope"] if c else None,
                        "provider": c["provider"] if c else None,
                        "collection": c["collection"] if c else None,
                        "revision": c["revision"] if c else None,
                        "indexed_rows": int(c["rows"]) if c else None,
                        "ingested_at": _iso(c["ingested_at"]) if c else None,
                        "shared_with": shared_with.get((c["scope"], c["provider"], c["collection"]), []) if c else [],
                        "shared_with_group": shared_with_group.get((c["scope"], c["provider"]), []) if c else [],
                    }
                )
                continue
            if r["relname"].startswith("attr_p_") or r["relname"].startswith("attr_n_"):
                # Not attached: an ingest building it right now, or one that died half way
                # (the next ingest of that collection cleans it up).
                entry["detached"] = True
            cols = ts_cols.get(r["relname"], {})
            column = TS_COLUMNS.get(r["relname"]) or ("created_at" if "created_at" in cols else None)
            if column in cols and (cols[column] or small):
                entry["oldest"], entry["newest"] = await _age_range(conn, r["relname"], column)
                entry["age_column"] = column
            tables[r["relname"]] = entry

    # A partitioned parent has no storage of its own: it reports the sum of its partitions.
    for part in partitions:
        parent = tables.get(part["parent"])
        if parent is not None:
            parent["rows"] += part["rows"]
            parent["rows_exact"] = parent["rows_exact"] and part["rows_exact"]
            for k in ("total_bytes", "table_bytes", "index_bytes", "toast_bytes"):
                parent[k] += part[k]
            parent["partitions"] = parent.get("partitions", 0) + 1

    areas = []
    for area in AREAS:
        mine = sorted((t for t in tables.values() if t["area"] == area.id), key=lambda t: -t["total_bytes"])
        olds = [t["oldest"] for t in mine if t["oldest"]]
        news = [t["newest"] for t in mine if t["newest"]]
        areas.append(
            {
                "id": area.id,
                "label": area.label,
                "description": area.description,
                "prune": area.prune,
                "prune_note": area.prune_note,
                "rows": sum(t["rows"] for t in mine),
                **{k: sum(t[k] for t in mine) for k in ("total_bytes", "table_bytes", "index_bytes", "toast_bytes")},
                "oldest": min(olds) if olds else None,
                "newest": max(news) if news else None,
                "tables": mine,
            }
        )
    partitions.sort(key=lambda p: -p["total_bytes"])
    return {
        "database": {
            "name": db["name"],
            "size_bytes": int(db["size"]),
            "tables_bytes": sum(t["total_bytes"] for t in tables.values()),
        },
        "areas": areas,
        "attribute_partitions": partitions,
        "attribute_provider_shares": provider_shares,
        "attribute_duplicates": attribute_duplicates(partitions),
        "took_ms": round((time.monotonic() - t0) * 1000),
    }


# --- pruning ------------------------------------------------------------------------------------


class PruneBusy(RuntimeError):
    """Another prune (by hand or the retention tick) holds the lock."""


@dataclass
class _Tally:
    table: str
    rows: int = 0
    est_bytes: int = 0
    note: Optional[str] = None

    def doc(self) -> dict:
        out = {"table": self.table, "rows": self.rows, "est_bytes": self.est_bytes}
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class _AreaResult:
    id: str
    older_than_days: Optional[int] = None
    cutoff: Optional[str] = None
    tables: list[_Tally] = field(default_factory=list)
    protected: int = 0
    collections: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def doc(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id,
            "older_than_days": self.older_than_days,
            "cutoff": self.cutoff,
            "rows": sum(t.rows for t in self.tables),
            "est_bytes": sum(t.est_bytes for t in self.tables),
            "protected": self.protected,
            "tables": [t.doc() for t in self.tables],
        }
        if self.id == "attribute_index":
            out["collections"] = self.collections
        if self.errors:
            out["errors"] = self.errors
        return out


async def _avg_row_bytes(conn, table: str) -> float:
    """The table's bytes per row (indexes and TOAST included) -- the estimate a deleted row frees."""
    r = await conn.fetchrow(
        "SELECT pg_total_relation_size(c.oid) AS total, c.reltuples::bigint AS n, pg_relation_size(c.oid) AS heap "
        "FROM pg_class c WHERE c.oid = to_regclass($1)",
        table,
    )
    if r is None:
        return 0.0
    n = r["n"]
    if n <= 0:
        n = await conn.fetchval(f"SELECT count(*) FROM {_q(table)}")
    return r["total"] / n if n else 0.0


async def _exists(conn, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table))


async def _delete_batched(conn, table: str, where: str, cutoff: datetime) -> int:
    done = 0
    while True:
        status = await conn.execute(
            f"DELETE FROM {_q(table)} WHERE ctid = ANY(ARRAY(SELECT ctid FROM {_q(table)} WHERE {where} LIMIT $2))",
            cutoff,
            PRUNE_BATCH_ROWS,
        )
        n = int(status.split()[-1])
        done += n
        if n < PRUNE_BATCH_ROWS:
            return done


async def _prune_steps(conn, area: Area, cutoff: datetime, res: _AreaResult, dry_run: bool) -> set[str]:
    touched: set[str] = set()
    for step in area.steps:
        if not await _exists(conn, step.table):
            continue
        n = await conn.fetchval(f"SELECT count(*) FROM {_q(step.table)} WHERE {step.where}", cutoff)
        if step.protect:
            res.protected += await conn.fetchval(f"SELECT count(*) FROM {_q(step.table)} WHERE {step.protect}", cutoff)
        tally = _Tally(step.table, int(n), round(n * await _avg_row_bytes(conn, step.table)))
        children = []
        for child, fk in step.children:
            if not await _exists(conn, child):
                continue
            m = await conn.fetchval(
                f"SELECT count(*) FROM {_q(child)} WHERE {_q(fk)} IN "
                f"(SELECT {_q(step.key)} FROM {_q(step.table)} WHERE {step.where})",
                cutoff,
            )
            children.append(_Tally(child, int(m), round(m * await _avg_row_bytes(conn, child)), note="cascade"))
        if not dry_run and n:
            tally.rows = await _delete_batched(conn, step.table, step.where, cutoff)
            touched.add(step.table)
            touched.update(c.table for c in children if c.rows)
        res.tables.append(tally)
        res.tables.extend(children)
    return touched


_METRICS_OLD = (
    "ts < $1 AND (metrics_samples IS NOT NULL OR client_metrics IS NOT NULL) "
    "AND COALESCE(status, '') NOT IN ('queued', 'running')"
)


async def _prune_metrics(conn, cutoff: datetime, res: _AreaResult, dry_run: bool) -> set[str]:
    """Metrics live in two places: the ``profile_function_stats`` rows parsed from an audit row's
    profile, and two JSON columns on the audit row itself. Both go; the audit row stays."""
    touched: set[str] = set()
    if await _exists(conn, "profile_function_stats"):
        stats_where = "audit_id IN (SELECT id FROM audit_log WHERE ts < $1)"
        n = await conn.fetchval(f"SELECT count(*) FROM profile_function_stats WHERE {stats_where}", cutoff)
        tally = _Tally(
            "profile_function_stats", int(n), round(n * await _avg_row_bytes(conn, "profile_function_stats"))
        )
        if not dry_run and n:
            tally.rows = await _delete_batched(conn, "profile_function_stats", stats_where, cutoff)
            touched.add("profile_function_stats")
        res.tables.append(tally)
    row = await conn.fetchrow(
        "SELECT count(*) AS n, COALESCE(sum(COALESCE(pg_column_size(metrics_samples), 0) "
        f"+ COALESCE(pg_column_size(client_metrics), 0)), 0) AS bytes FROM audit_log WHERE {_METRICS_OLD}",
        cutoff,
    )
    tally = _Tally("audit_log", int(row["n"]), int(row["bytes"]), note="metrics columns cleared; rows kept")
    if not dry_run and row["n"]:
        done = 0
        while True:
            status = await conn.execute(
                "UPDATE audit_log SET metrics_samples = NULL, client_metrics = NULL WHERE ctid = ANY(ARRAY("
                f"SELECT ctid FROM audit_log WHERE {_METRICS_OLD} LIMIT $2))",
                cutoff,
                PRUNE_BATCH_ROWS,
            )
            n = int(status.split()[-1])
            done += n
            if n < PRUNE_BATCH_ROWS:
                break
        tally.rows = done
        touched.add("audit_log")
    res.tables.append(tally)
    return touched


async def _prune_attribute_index(
    pool, conn, cutoff: Optional[datetime], selected: Optional[list[dict]], res: _AreaResult, dry_run: bool
) -> set[str]:
    if not await _exists(conn, "attr_collections"):
        return set()
    rows = await conn.fetch(
        """
        SELECT c.scope, c.provider, c.collection, c.rows, c.ingested_at, c.partition_name, c.part_key,
               COALESCE(pg_total_relation_size(to_regclass(c.partition_name)), 0) AS bytes,
               (SELECT count(*) FROM attr_values v WHERE v.part_key = c.part_key) AS value_rows
        FROM attr_collections c
        """
    )
    if selected is not None:
        wanted = {(str(s.get("scope")), str(s.get("provider")), str(s.get("collection")).lower()) for s in selected}
        rows = [r for r in rows if (r["scope"], r["provider"], r["collection"].lower()) in wanted]
    else:
        rows = [r for r in rows if cutoff is not None and r["ingested_at"] < cutoff]
    values_avg = await _avg_row_bytes(conn, "attr_values")
    nodes, values, catalogue = _Tally("attr_nodes"), _Tally("attr_values"), _Tally("attr_collections")
    for r in rows:
        doc = {
            "scope": r["scope"],
            "provider": r["provider"],
            "collection": r["collection"],
            "rows": int(r["rows"]),
            "ingested_at": _iso(r["ingested_at"]),
            "partition": r["partition_name"],
            "est_bytes": int(r["bytes"]) + round(r["value_rows"] * values_avg),
        }
        if not dry_run:
            try:
                doc["dropped"] = await drop_attr_collection(
                    pool, scope=r["scope"], provider=r["provider"], collection=r["collection"]
                )
            except Exception as exc:  # noqa: BLE001 - one collection's failure is reported, not fatal
                doc["dropped"] = False
                res.errors.append(f"{r['provider']}/{r['collection']}: {exc}")
                continue
        res.collections.append(doc)
        nodes.rows += int(r["rows"])
        nodes.est_bytes += int(r["bytes"])
        values.rows += int(r["value_rows"])
        values.est_bytes += round(r["value_rows"] * values_avg)
        catalogue.rows += 1
    nodes.note = "partitions dropped: space returned at once"
    res.tables.extend([nodes, values, catalogue])
    return {"attr_values", "attr_collections"} if not dry_run and rows else set()


def _cutoff(days: Any, now: datetime) -> tuple[int, datetime]:
    try:
        n = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"older_than_days must be a whole number of days, not {days!r}") from exc
    if n < MIN_PRUNE_DAYS:
        raise ValueError(f"older_than_days must be at least {MIN_PRUNE_DAYS}")
    return n, now - timedelta(days=n)


def parse_prune_request(areas: Any) -> dict[str, dict]:
    """``{area: {"older_than_days": n} | {"collections": [{scope, provider, collection}]}}``,
    checked. Raises ValueError naming what is wrong."""
    if not isinstance(areas, dict) or not areas:
        raise ValueError("name at least one area: {area: {older_than_days: n}}")
    out: dict[str, dict] = {}
    for area_id, spec in areas.items():
        area = AREA_BY_ID.get(area_id)
        if area is None or not area.prune:
            raise ValueError(f"{area_id!r} is not a prunable area ({', '.join(PRUNABLE_AREAS)})")
        spec = spec if isinstance(spec, dict) else {"older_than_days": spec}
        collections = spec.get("collections")
        if collections is not None:
            if area.prune != "collections":
                raise ValueError(f"{area_id!r} is pruned by age, not by collection")
            if not isinstance(collections, list) or not all(
                isinstance(c, dict) and c.get("scope") and c.get("provider") and c.get("collection")
                for c in collections
            ):
                raise ValueError("collections must be a list of {scope, provider, collection}")
            out[area_id] = {"collections": collections}
            continue
        _cutoff(spec.get("older_than_days"), datetime.now(timezone.utc))  # validates
        out[area_id] = {"older_than_days": int(spec["older_than_days"])}
    return out


async def prune(
    pool: asyncpg.Pool,
    areas: dict[str, dict],
    *,
    dry_run: bool = True,
    vacuum: bool = True,
    now: Optional[datetime] = None,
) -> dict:
    """Prune (or with ``dry_run`` only count) the named areas -- ``areas`` as
    :func:`parse_prune_request` returns it. Raises :class:`PruneBusy` when another prune runs."""
    t0 = time.monotonic()
    now = now or datetime.now(timezone.utc)
    results: list[_AreaResult] = []
    touched: set[str] = set()
    async with pool.acquire() as conn:
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", PRUNE_LOCK_ID):
            raise PruneBusy("another database prune is running")
        try:
            for area in AREAS:  # declaration order: see the note on "metrics"
                spec = areas.get(area.id)
                if spec is None:
                    continue
                res = _AreaResult(area.id)
                cutoff = None
                if "older_than_days" in spec:
                    res.older_than_days, cutoff = _cutoff(spec["older_than_days"], now)
                    res.cutoff = cutoff.isoformat()
                try:
                    if area.id == "attribute_index":
                        touched |= await _prune_attribute_index(
                            pool, conn, cutoff, spec.get("collections"), res, dry_run
                        )
                    elif area.id == "metrics":
                        touched |= await _prune_metrics(conn, cutoff, res, dry_run)
                    else:
                        touched |= await _prune_steps(conn, area, cutoff, res, dry_run)
                except Exception as exc:  # noqa: BLE001 - one area's failure is its own
                    res.errors.append(str(exc))
                results.append(res)
            vacuumed, vacuum_errors = [], []
            if not dry_run and vacuum:
                for table in sorted(touched):
                    try:
                        await conn.execute(f"VACUUM (ANALYZE) {_q(table)}")
                        vacuumed.append(table)
                    except Exception as exc:  # noqa: BLE001 - autovacuum will get there
                        vacuum_errors.append(f"{table}: {exc}")
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", PRUNE_LOCK_ID)
    docs = [r.doc() for r in results]
    if dry_run:
        note = "Dry run: nothing was changed."
    elif vacuum:
        note = (
            "VACUUM (ANALYZE) ran on each table rows were deleted from: the space is free for reuse "
            "by new rows, but the files do not shrink (that needs VACUUM FULL, which locks the table). "
            "Dropped attribute partitions returned their space at once."
        )
    else:
        note = "Autovacuum will make the deleted rows' space reusable; the files do not shrink."
    return {
        "dry_run": dry_run,
        "areas": docs,
        "rows": sum(d["rows"] for d in docs),
        "est_bytes": sum(d["est_bytes"] for d in docs),
        "vacuum": {
            "ran": vacuumed if not dry_run and vacuum else [],
            "errors": vacuum_errors if not dry_run and vacuum else [],
        },
        "note": note,
        "took_ms": round((time.monotonic() - t0) * 1000),
    }


# --- retention settings -------------------------------------------------------------------------


async def get_retention(pool: asyncpg.Pool) -> dict[str, int]:
    """Each prunable area's retention in days; areas without one are absent."""
    out: dict[str, int] = {}
    for r in await pool.fetch("SELECT key, value FROM app_settings WHERE key LIKE 'db.retention.%.days'"):
        area = r["key"][len(RETENTION_PREFIX) : -len(".days")]
        try:
            days = int(r["value"])
        except (TypeError, ValueError):
            continue
        if area in PRUNABLE_AREAS and days >= MIN_PRUNE_DAYS:
            out[area] = days
    return out


async def set_retention(pool: asyncpg.Pool, retention: dict[str, Any], *, updated_by: Optional[str]) -> dict[str, int]:
    """Set (an integer) or clear (null) each named area's retention."""
    for area, days in retention.items():
        if area not in PRUNABLE_AREAS:
            raise ValueError(f"{area!r} is not a prunable area ({', '.join(PRUNABLE_AREAS)})")
        if days is not None and days != "":
            _cutoff(days, datetime.now(timezone.utc))
    for area, days in retention.items():
        key = RETENTION_KEY.format(area=area)
        if days is None or days == "":
            await pool.execute("DELETE FROM app_settings WHERE key = $1", key)
        else:
            await pool.execute(
                """
                INSERT INTO app_settings (key, value, updated_by) VALUES ($1, $2, $3)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by
                """,
                key,
                str(int(days)),
                updated_by,
            )
    return await get_retention(pool)


async def get_retention_last_run(pool: asyncpg.Pool) -> Optional[dict]:
    raw = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", RETENTION_LAST_RUN_KEY)
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


async def set_retention_last_run(pool: asyncpg.Pool, doc: dict) -> None:
    await pool.execute(
        """
        INSERT INTO app_settings (key, value, updated_by) VALUES ($1, $2, 'system')
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW(), updated_by = 'system'
        """,
        RETENTION_LAST_RUN_KEY,
        json.dumps(doc),
    )
