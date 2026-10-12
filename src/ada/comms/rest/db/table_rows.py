"""One page of a table's rows for Admin -> Database, read so it cannot hurt the database or leak.

The storage view (``storage_insight.py``) says how big each table is; this lets an admin look
inside one. It is a debugging window, not a query tool, so it is narrow on purpose:

* Only a table the storage view itself reports can be read: a plain or partitioned table in the
  app's schema, looked up in the catalogue by name. The name the client sent is never put into
  SQL -- the catalogue's own spelling is, quoted. Anything else (a system table, a view, a typo)
  is "not found".
* The read runs in a READ ONLY transaction with a short ``statement_timeout`` and
  ``lock_timeout``: a page that would take long fails instead of loading the server, and the
  read never queues behind (or in front of) an attribute ingest swapping a partition.
* Rows come in a stable order -- the primary key, else the physical position (``ctid``, per
  partition for a partitioned table) -- so paging neither skips nor repeats a row while the
  table is at rest.
* Values are made JSON-safe and bounded IN the query: long text, JSON and arrays arrive cut to
  :data:`CELL_MAX_CHARS`, a ``bytea`` arrives as its length only.
* Secret-looking columns (:data:`SECRET_COLUMN`) never leave the database: the query selects
  only whether they are set. A key/value table (``app_settings``) also hides the value of a
  secret-looking key. CLI tokens are never stored, but ``cli_tokens.hint`` (the signature's
  tail) is hidden as well.
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, time as dtime
from typing import Any, Optional

import asyncpg

from .storage_insight import SMALL_TABLE_BYTES, _q

ROWS_DEFAULT_LIMIT = 50
ROWS_MAX_LIMIT = 200
#: Per page read; a table that cannot answer in this time is too big to page this way.
STATEMENT_TIMEOUT_MS = 5_000
#: Waiting for a lock (an ingest swapping a partition) is not worth it at all.
LOCK_TIMEOUT_MS = 1_000
CELL_MAX_CHARS = 500
TRUNCATED_MARK = "…[truncated]"
REDACTED = "<redacted>"
#: A column whose name looks like it holds a secret. Over-matching (a content hash) only hides
#: a value an admin can read elsewhere; under-matching would leak.
SECRET_COLUMN = re.compile(r"token|secret|passw|hash|credential|api_?key|private_?key", re.IGNORECASE)
#: Columns hidden in one table although their name does not look secret.
REDACT_EXTRA: dict[str, frozenset[str]] = {"cli_tokens": frozenset({"hint"})}

#: Types that come back as themselves (timestamps then as ISO text). Everything else is cast to
#: text and cut in the query.
_NATIVE_TYPES = frozenset(
    {"boolean", "smallint", "integer", "bigint", "real", "double precision", "date", "timestamp with time zone",
     "timestamp without time zone"}
)  # fmt: skip
_JS_SAFE_INT = 2**53 - 1


class TableNotFound(LookupError):
    """Not a table the storage view reports."""


_TABLE_SQL = """
SELECT c.oid, c.relname, c.relkind::text AS relkind, n.nspname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND n.nspname = current_schema() AND c.relname::text = $1::text
"""

_COLUMNS_SQL = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod) AS type
FROM pg_attribute a
WHERE a.attrelid = $1 AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

_PK_SQL = """
SELECT a.attname
FROM pg_index x
JOIN LATERAL unnest(x.indkey) WITH ORDINALITY AS k(attnum, pos) ON true
JOIN pg_attribute a ON a.attrelid = x.indrelid AND a.attnum = k.attnum
WHERE x.indrelid = $1 AND x.indisprimary
ORDER BY k.pos
"""

#: The table's (or, partitioned, its partitions') planner row count and heap size.
_SIZE_SQL = """
SELECT COALESCE(sum(GREATEST(c.reltuples, 0)), 0)::bigint AS est,
       COALESCE(sum(pg_relation_size(c.oid)), 0)::bigint AS heap
FROM pg_class c
WHERE c.oid = $1 OR c.oid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = $1)
"""


def is_secret_column(table: str, column: str) -> bool:
    return bool(SECRET_COLUMN.search(column)) or column in REDACT_EXTRA.get(table, frozenset())


def _select_expr(name: str, type_: str, secret: bool) -> str:
    col = _q(name)
    if secret:
        return f"({col} IS NOT NULL)"
    if type_ == "bytea":
        return f"octet_length({col})"
    if type_ in _NATIVE_TYPES:
        return col
    # One character more than shown tells "exactly 500" from "cut".
    return f"left({col}::text, {CELL_MAX_CHARS + 1})"


def cell_value(value: Any, *, type_: str, secret: bool) -> Any:
    """One cell as the page shows it, from what :func:`_select_expr` selected."""
    if secret:
        return REDACTED if value else None
    if value is None:
        return None
    if type_ == "bytea":
        return f"<{value} bytes>"
    if isinstance(value, (datetime, date, dtime)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= _JS_SAFE_INT else str(value)
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else str(value)
    text = value if isinstance(value, str) else str(value)
    return text[:CELL_MAX_CHARS] + TRUNCATED_MARK if len(text) > CELL_MAX_CHARS else text


def _redact_key_values(names: list[str], row: list[Any]) -> list[Any]:
    """A key/value table (``app_settings``): a secret-looking key hides its value."""
    if "key" in names and "value" in names:
        key = row[names.index("key")]
        if isinstance(key, str) and SECRET_COLUMN.search(key) and row[names.index("value")] is not None:
            row[names.index("value")] = REDACTED
    return row


async def table_rows(pool: asyncpg.Pool, table: str, *, offset: int = 0, limit: int = ROWS_DEFAULT_LIMIT) -> dict:
    """One page of ``table``. Raises :class:`TableNotFound` for anything the storage view does
    not report, ``ValueError`` for a bad page, and lets ``asyncpg.QueryCanceledError`` (the
    statement timeout) through for the route to answer."""
    if offset < 0 or not 1 <= limit <= ROWS_MAX_LIMIT:
        raise ValueError(f"offset must be >= 0 and limit 1..{ROWS_MAX_LIMIT}")
    t0 = time.monotonic()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
            await conn.execute(f"SET LOCAL lock_timeout = {int(LOCK_TIMEOUT_MS)}")
            rel = await conn.fetchrow(_TABLE_SQL, table)
            if rel is None:
                raise TableNotFound(table)
            name = rel["relname"]
            target = f"{_q(rel['nspname'])}.{_q(name)}"
            columns = [
                {"name": r["attname"], "type": r["type"], "redacted": is_secret_column(name, r["attname"])}
                for r in await conn.fetch(_COLUMNS_SQL, rel["oid"])
            ]
            pk = [r["attname"] for r in await conn.fetch(_PK_SQL, rel["oid"])]
            if pk:
                order = ", ".join(_q(c) for c in pk)
            else:
                order = "tableoid, ctid" if rel["relkind"] == "p" else "ctid"
            exprs = [_select_expr(c["name"], c["type"], c["redacted"]) for c in columns]
            records = await conn.fetch(
                f"SELECT {', '.join(exprs) or 'NULL'} FROM {target} ORDER BY {order} OFFSET $1 LIMIT $2",
                offset,
                limit,
            )
            size = await conn.fetchrow(_SIZE_SQL, rel["oid"])
            if size["heap"] <= SMALL_TABLE_BYTES:
                total, exact = await conn.fetchval(f"SELECT count(*) FROM {target}"), True
            else:
                total, exact = int(size["est"]), False

    names = [c["name"] for c in columns]
    rows = []
    for rec in records:
        row = [cell_value(rec[i], type_=c["type"], secret=c["redacted"]) for i, c in enumerate(columns)]
        rows.append(_redact_key_values(names, row))
    # An estimate below what was just paged through is plainly low.
    if not exact:
        total = max(total, offset + len(rows))
    return {
        "table": name,
        "columns": columns,
        "order_by": pk or ["ctid"],
        "rows": rows,
        "offset": offset,
        "limit": limit,
        "total": int(total),
        "total_exact": exact,
        "took_ms": round((time.monotonic() - t0) * 1000),
    }


def page_error(exc: BaseException) -> Optional[str]:
    """What to tell the admin when a page read was cut short, or None if ``exc`` is not that."""
    if isinstance(exc, asyncpg.QueryCanceledError):
        return f"the table did not answer within {STATEMENT_TIMEOUT_MS // 1000} s; try an earlier page"
    if isinstance(exc, asyncpg.LockNotAvailableError):
        return "the table is locked right now (an ingest swapping it?); try again in a moment"
    return None
