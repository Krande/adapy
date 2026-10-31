"""Geometry comparison state: the current verdict per (provider A, provider B, collection, element).
Written by ``routes/geometry_compare.py`` after each comparison of a collection (replace-all for that
collection), read by the next comparison (its memory) and by the admin gallery. See migration 051.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

import asyncpg

from .asset_changes import _iso

_COLS = (
    "provider_a, provider_b, collection, key, name, verdict, reason, metrics, fp_a, fp_b, side_a, side_b, "
    "picture_a, picture_b, run_id, compared_at"
)

#: Metrics a gallery may sort by (descending), each a key of ``metrics``.
SORTABLE_METRICS = ("box_delta_mm", "hausdorff_mm", "p95_mm", "volume_ratio", "area_ratio", "triangle_ratio")


def _loads(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v


def _row(r) -> dict:
    return {
        "provider_a": r["provider_a"],
        "provider_b": r["provider_b"],
        "collection": r["collection"],
        "key": r["key"],
        "name": r["name"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "metrics": _loads(r["metrics"]) or {},
        "fp_a": r["fp_a"],
        "fp_b": r["fp_b"],
        "a": _loads(r["side_a"]),
        "b": _loads(r["side_b"]),
        "picture_a": r["picture_a"],
        "picture_b": r["picture_b"],
        "run_id": str(r["run_id"]) if r["run_id"] else None,
        "compared_at": _iso(r["compared_at"]),
    }


async def geometry_compare_previous(
    pool: asyncpg.Pool, *, provider_a: str, provider_b: str, collection: str
) -> dict[str, dict]:
    """``{key: {fp_a, fp_b, verdict, reason, metrics, picture_a, picture_b}}`` of the collection's last
    comparison -- what a comparison reuses for an unchanged pair."""
    rows = await pool.fetch(
        "SELECT key, verdict, reason, metrics, fp_a, fp_b, picture_a, picture_b FROM geometry_compare_elements "
        "WHERE provider_a = $1 AND provider_b = $2 AND collection = $3",
        provider_a,
        provider_b,
        collection.lower(),
    )
    return {
        r["key"]: {
            "fp_a": r["fp_a"],
            "fp_b": r["fp_b"],
            "verdict": r["verdict"],
            "reason": r["reason"],
            "metrics": _loads(r["metrics"]) or {},
            "picture_a": r["picture_a"],
            "picture_b": r["picture_b"],
        }
        for r in rows
    }


async def replace_geometry_compare(
    pool: asyncpg.Pool,
    *,
    provider_a: str,
    provider_b: str,
    collection: str,
    rows: Iterable[dict],
    run_id: Optional[str] = None,
) -> int:
    """The collection's comparison is ``rows`` now: upserted, and every element no longer in it
    removed -- in one transaction. Returns how many rows were written."""
    coll = collection.lower()
    records = []
    for r in rows:
        records.append(
            (
                provider_a,
                provider_b,
                coll,
                str(r["key"]),
                r.get("name"),
                str(r["verdict"]),
                r.get("reason"),
                json.dumps(r.get("metrics") or {}),
                r.get("fp_a"),
                r.get("fp_b"),
                json.dumps(r["a"]) if r.get("a") is not None else None,
                json.dumps(r["b"]) if r.get("b") is not None else None,
                r.get("picture_a"),
                r.get("picture_b"),
                run_id,
            )
        )
    async with pool.acquire() as con:
        async with con.transaction():
            await con.execute(
                "CREATE TEMP TABLE _gc (LIKE geometry_compare_elements INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            await con.copy_records_to_table(
                "_gc",
                records=records,
                columns=[
                    "provider_a",
                    "provider_b",
                    "collection",
                    "key",
                    "name",
                    "verdict",
                    "reason",
                    "metrics",
                    "fp_a",
                    "fp_b",
                    "side_a",
                    "side_b",
                    "picture_a",
                    "picture_b",
                    "run_id",
                ],
            )
            await con.execute(
                "DELETE FROM geometry_compare_elements g WHERE g.provider_a = $1 AND g.provider_b = $2 "
                "AND g.collection = $3 AND NOT EXISTS (SELECT 1 FROM _gc WHERE _gc.key = g.key)",
                provider_a,
                provider_b,
                coll,
            )
            await con.execute(
                f"""
                INSERT INTO geometry_compare_elements ({_COLS})
                SELECT provider_a, provider_b, collection, key, name, verdict, reason, metrics, fp_a, fp_b,
                       side_a, side_b, picture_a, picture_b, run_id, NOW() FROM _gc
                ON CONFLICT (provider_a, provider_b, collection, key) DO UPDATE SET
                    name = EXCLUDED.name, verdict = EXCLUDED.verdict, reason = EXCLUDED.reason,
                    metrics = EXCLUDED.metrics, fp_a = EXCLUDED.fp_a, fp_b = EXCLUDED.fp_b,
                    side_a = EXCLUDED.side_a, side_b = EXCLUDED.side_b, picture_a = EXCLUDED.picture_a,
                    picture_b = EXCLUDED.picture_b, run_id = EXCLUDED.run_id, compared_at = EXCLUDED.compared_at
                """
            )
    return len(records)


async def list_geometry_compare(
    pool: asyncpg.Pool,
    *,
    provider_a: str,
    provider_b: Optional[str] = None,
    collections: Optional[list[str]] = None,
    verdicts: Optional[list[str]] = None,
    query: Optional[str] = None,
    sort: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """``{items, total, counts}``: the rows of a gallery (filtered, newest-worst first or by a metric),
    the filtered total, and the counts per verdict over the same filter without the verdict."""
    where = ["provider_a = $1"]
    args: list[Any] = [provider_a]

    def add(clause: str, value: Any) -> None:
        args.append(value)
        where.append(clause.replace("$?", f"${len(args)}"))

    if provider_b:
        add("provider_b = $?", provider_b)
    if collections:
        add("collection = ANY($?::text[])", [c.lower() for c in collections])
    if query:
        add("(key ILIKE $? OR name ILIKE $?)", f"%{query.strip()}%")
    base_where = " AND ".join(where)
    counts_rows = await pool.fetch(
        f"SELECT verdict, count(*) AS n FROM geometry_compare_elements WHERE {base_where} GROUP BY verdict", *args
    )
    if verdicts:
        add("verdict = ANY($?::text[])", list(verdicts))
    full_where = " AND ".join(where)
    order = "compared_at DESC, key"
    if sort in SORTABLE_METRICS:
        order = f"(metrics->>'{sort}')::float8 DESC NULLS LAST, key"
    total = await pool.fetchval(f"SELECT count(*) FROM geometry_compare_elements WHERE {full_where}", *args)
    rows = await pool.fetch(
        f"SELECT {_COLS} FROM geometry_compare_elements WHERE {full_where} ORDER BY {order} "
        f"LIMIT {max(1, min(int(limit), 500))} OFFSET {max(0, int(offset))}",
        *args,
    )
    return {
        "items": [_row(r) for r in rows],
        "total": int(total or 0),
        "counts": {r["verdict"]: int(r["n"]) for r in counts_rows},
    }
