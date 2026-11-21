"""Per-element associations in Postgres (migration 055): the ``LinkStore`` a deployment with a
database queries (``ada.assoc.store``).

Rows are replaced per producing document and kind (``put_sheet``), exactly as the files backend
replaces a sheet, so the two hold the same rows. A query flags each row stale / orphan / superseded
in SQL -- against ``element_state`` and ``element_sources`` -- and pages by group (the ck, or a
contact's member pair) with a keyset cursor, so a node with a hundred thousand links pages without
a cap.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

import asyncpg

from ada.assoc.records import LinkRow, SourceState
from ada.assoc.store import MAX_LIMIT, Intent, LinkQuery, LinkStore

from ._common import _loads_jsonb

__all__ = ["PgLinkStore"]

_COLUMNS = (
    "scope, kind, element_key, anc, doc_key, ck, grp, source, revision, geometry_sha256, fp, created_at, status, data"
)


def _record(scope: str, row: LinkRow) -> tuple:
    status = row.data.get("status") if row.kind in ("sim_run", "connection") else None
    return (
        scope,
        row.kind,
        row.element_key,
        list(row.anc),
        row.doc_key,
        row.ck,
        row.group_key,
        row.source,
        row.revision,
        row.geometry_sha256,
        row.fp,
        row.created_at,
        str(status) if status is not None else None,
        json.dumps(row.data, default=str),
    )


def _where(scope: str, q: LinkQuery) -> tuple[str, list]:
    params: list[Any] = [scope]
    clauses = ["l.scope = $1"]

    def p(value) -> str:
        params.append(value)
        return f"${len(params)}"

    if q.kinds is not None:
        clauses.append(f"l.kind = ANY({p(list(q.kinds))}::text[])")
    if q.cks is not None:
        clauses.append(f"l.ck = ANY({p(list(q.cks))}::text[])")
    if q.doc_keys is not None:
        clauses.append(f"l.doc_key = ANY({p(list(q.doc_keys))}::text[])")
    if q.element_keys is not None:
        clauses.append(f'l.element_key = ANY({p(list(q.element_keys))}::text[] COLLATE "C")')
    if q.under is not None:
        u = p(list(q.under))
        clauses.append(f'(l.element_key = ANY({u}::text[] COLLATE "C") OR l.anc && {u}::text[])')
    if q.sim_statuses is not None:
        clauses.append(f"(l.kind <> 'sim_run' OR l.status = ANY({p(list(q.sim_statuses))}::text[]))")
    return " AND ".join(clauses), params


def _flagged_cte(where: str) -> str:
    """Every matching row with its flags, as ``f``."""
    return f"""
        WITH m AS (
            SELECT l.* FROM element_links l WHERE {where}
        ), f AS (
            SELECT m.*,
                (src.seen_at IS NOT NULL AND src.seen_at >= COALESCE(m.created_at, '')) AS measured,
                (st.seen_at IS NOT NULL AND st.seen_at >= src.seen_at AND st.source = m.source) AS present,
                st.fp AS current_fp,
                COALESCE(m.created_at, '') < MAX(COALESCE(m.created_at, ''))
                    OVER (PARTITION BY m.kind, m.element_key, m.grp) AS superseded
            FROM m
            LEFT JOIN element_sources src ON src.scope = m.scope AND src.source = m.source
            LEFT JOIN element_state st ON st.scope = m.scope AND st.element_key = m.element_key
        ), g AS (
            SELECT f.*,
                (f.measured AND NOT f.present) AS orphan,
                (f.measured AND f.present AND f.fp IS NOT NULL AND f.current_fp IS NOT NULL
                    AND f.current_fp <> f.fp) AS stale
            FROM f
        )
    """


def _keep(q: LinkQuery) -> str:
    out = []
    if not q.include_stale:
        out.append("NOT g.stale AND NOT g.orphan")
    if q.latest_only:
        out.append("NOT g.superseded")
    return " AND ".join(out) or "TRUE"


def _row_dict(r) -> dict:
    out: dict[str, Any] = {"kind": r["kind"], "element_key": r["element_key"], "doc_key": r["doc_key"]}
    if r["anc"]:
        out["anc"] = list(r["anc"])
    for name in ("ck", "source", "revision", "geometry_sha256", "fp", "created_at"):
        if r[name] is not None:
            out[name] = r[name]
    out["group"] = r["grp"]
    out["data"] = _loads_jsonb(r["data"]) or {}
    return out


class PgLinkStore(LinkStore):
    def __init__(self, pool: asyncpg.Pool, scope: str) -> None:
        self.pool, self.scope = pool, scope

    async def put_sheet(self, doc_key: str, kinds: Iterable[str], rows: Iterable[LinkRow]) -> None:
        kinds = list(dict.fromkeys(kinds))
        records = [_record(self.scope, r) for r in rows if r.kind in kinds]
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM element_links WHERE scope = $1 AND doc_key = $2 AND kind = ANY($3::text[])",
                self.scope,
                doc_key,
                kinds,
            )
            if records:
                await conn.executemany(
                    f"INSERT INTO element_links ({_COLUMNS}) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb)",
                    records,
                )

    async def put_source_state(self, state: SourceState) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO element_sources (scope, source, revision, geometry_sha256, seen_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (scope, source) DO UPDATE SET
                    revision = EXCLUDED.revision, geometry_sha256 = EXCLUDED.geometry_sha256,
                    seen_at = EXCLUDED.seen_at
                WHERE element_sources.seen_at <= EXCLUDED.seen_at
                """,
                self.scope,
                state.source,
                state.revision,
                state.geometry_sha256,
                state.seen_at,
            )
            if state.fps:
                await conn.executemany(
                    """
                    INSERT INTO element_state (scope, element_key, source, revision, fp, seen_at)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (scope, element_key) DO UPDATE SET
                        source = EXCLUDED.source, revision = EXCLUDED.revision, fp = EXCLUDED.fp,
                        seen_at = EXCLUDED.seen_at
                    WHERE element_state.seen_at <= EXCLUDED.seen_at
                    """,
                    [
                        (self.scope, key, state.source, state.revision, fp, state.seen_at)
                        for key, fp in state.fps.items()
                    ],
                )

    async def rows(self, q: LinkQuery) -> list[LinkRow]:
        where, params = _where(self.scope, q)
        found = await self.pool.fetch(f"SELECT * FROM element_links l WHERE {where} ORDER BY l.grp, l.id", *params)
        return [LinkRow.from_dict(_row_dict(r)) for r in found]

    async def query(self, q: LinkQuery) -> dict:
        where, params = _where(self.scope, q)
        cte = _flagged_cte(where)
        keep = _keep(q)
        limit = max(1, min(int(q.limit or 100), MAX_LIMIT))
        async with self.pool.acquire() as conn:
            summary = await conn.fetchrow(
                f"""{cte}
                SELECT COUNT(*) AS n, COUNT(DISTINCT g.element_key) AS elements, COUNT(DISTINCT g.grp) AS groups,
                    COUNT(*) FILTER (WHERE g.stale) AS stale, COUNT(*) FILTER (WHERE g.orphan) AS orphan,
                    COUNT(*) FILTER (WHERE g.superseded) AS superseded
                FROM g WHERE {keep}
                """,
                *params,
            )
            by_kind = await conn.fetch(
                f"{cte} SELECT g.kind, g.status, COUNT(*) AS n FROM g WHERE {keep} GROUP BY g.kind, g.status",
                *params,
            )
            cursor_param = len(params) + 1
            groups = [
                r["grp"]
                for r in await conn.fetch(
                    f"""{cte}
                    SELECT g.grp FROM g WHERE {keep} AND (${cursor_param}::text IS NULL OR g.grp > ${cursor_param}::text)
                    GROUP BY g.grp ORDER BY g.grp COLLATE "C" LIMIT {limit + 1}
                    """,
                    *params,
                    q.cursor,
                )
            ]
            more = len(groups) > limit
            groups = groups[:limit]
            found = (
                await conn.fetch(
                    f"""{cte}
                    SELECT g.* FROM g WHERE {keep} AND g.grp = ANY(${cursor_param}::text[])
                    ORDER BY g.grp COLLATE "C", g.created_at DESC NULLS LAST, g.kind, g.element_key
                    """,
                    *params,
                    groups,
                )
                if groups
                else []
            )
        kinds: dict[str, int] = {}
        sims: dict[str, int] = {}
        for r in by_kind:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + int(r["n"])
            if r["kind"] == "sim_run":
                sims[str(r["status"] or "")] = sims.get(str(r["status"] or ""), 0) + int(r["n"])
        out_groups: dict[str, dict] = {}
        for r in found:
            d = {
                **_row_dict(r),
                "stale": bool(r["stale"]),
                "orphan": bool(r["orphan"]),
                "superseded": bool(r["superseded"]),
            }
            g = out_groups.setdefault(r["grp"], {"group": r["grp"], "ck": r["ck"], "kinds": {}, "rows": []})
            g["kinds"][d["kind"]] = g["kinds"].get(d["kind"], 0) + 1
            g["rows"].append(d)
        return {
            "summary": {
                "rows": int(summary["n"]),
                "elements": int(summary["elements"]),
                "groups": int(summary["groups"]),
                "kinds": kinds,
                "sim_runs": sims,
                "stale": int(summary["stale"]),
                "orphan": int(summary["orphan"]),
                "superseded": int(summary["superseded"]),
            },
            "groups": [out_groups[g] for g in groups if g in out_groups],
            "next": groups[-1] if more and groups else None,
        }

    async def take_intent(self, intent: Intent, run_id: str | None = None) -> bool:
        got = await self.pool.fetchval(
            """
            INSERT INTO sim_intents (scope, ck, provider, params_hash, generation, run_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (scope, ck, provider, params_hash, generation) DO NOTHING
            RETURNING 1
            """,
            self.scope,
            intent.ck,
            intent.provider,
            intent.params_hash,
            intent.generation,
            run_id,
        )
        return got == 1

    async def set_intent_run(self, intent: Intent, run_id: str) -> None:
        await self.pool.execute(
            "UPDATE sim_intents SET run_id = $6 WHERE scope = $1 AND ck = $2 AND provider = $3 "
            "AND params_hash = $4 AND generation = $5",
            self.scope,
            intent.ck,
            intent.provider,
            intent.params_hash,
            intent.generation,
            run_id,
        )

    async def release_intent(self, intent: Intent) -> None:
        await self.pool.execute(
            "DELETE FROM sim_intents WHERE scope = $1 AND ck = $2 AND provider = $3 AND params_hash = $4 "
            "AND generation = $5",
            self.scope,
            intent.ck,
            intent.provider,
            intent.params_hash,
            intent.generation,
        )

    async def intent_run(self, intent: Intent) -> str | None | bool:
        row = await self.pool.fetchrow(
            "SELECT run_id FROM sim_intents WHERE scope = $1 AND ck = $2 AND provider = $3 AND params_hash = $4 "
            "AND generation = $5",
            self.scope,
            intent.ck,
            intent.provider,
            intent.params_hash,
            intent.generation,
        )
        return False if row is None else row["run_id"]
