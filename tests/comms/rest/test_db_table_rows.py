"""Admin -> Database: paging through one table's rows (``db/table_rows.py``).

Always-on tests cover the cell rules (bounds, redaction) and the route's answers with the DB
layer stubbed. The live-Postgres tests (skipped unless ADA_TEST_POSTGRES_URL is set) read only
throwaway ``zz_test_rows_*`` tables and a view they create, and drop them again; nothing they
do writes outside those.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from datetime import datetime, timezone

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import asyncpg
import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import db as dbm
from ada.comms.rest.app import create_app
from ada.comms.rest.db import table_rows as tr

from .test_db_storage import _settings

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")


# --- always on ----------------------------------------------------------------------------------


def test_secret_looking_columns_are_hidden():
    for col in ("token", "api_token", "client_secret", "password", "password_hash", "key_hash", "credentials"):
        assert tr.is_secret_column("anything", col), col
    assert tr.is_secret_column("cli_tokens", "hint") and not tr.is_secret_column("users", "hint")
    for col in ("jti", "sub", "email", "created_at", "key", "value"):
        assert not tr.is_secret_column("cli_tokens", col), col


def test_cells_are_json_safe_and_bounded():
    ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert tr.cell_value(ts, type_="timestamp with time zone", secret=False) == "2026-01-02T03:04:05+00:00"
    assert tr.cell_value(1234, type_="bytea", secret=False) == "<1234 bytes>"
    assert tr.cell_value(True, type_="text", secret=True) == tr.REDACTED
    assert tr.cell_value(False, type_="text", secret=True) is None  # unset stays unset
    assert tr.cell_value(2**60, type_="bigint", secret=False) == str(2**60)
    assert tr.cell_value(float("nan"), type_="double precision", secret=False) == "nan"
    cut = tr.cell_value("x" * (tr.CELL_MAX_CHARS + 1), type_="text", secret=False)
    assert cut == "x" * tr.CELL_MAX_CHARS + tr.TRUNCATED_MARK
    assert tr.cell_value("x" * tr.CELL_MAX_CHARS, type_="text", secret=False) == "x" * tr.CELL_MAX_CHARS


def test_a_key_value_table_hides_the_value_of_a_secret_key():
    names = ["key", "value", "updated_by"]
    assert tr._redact_key_values(names, ["issue.token", "abc", "me"]) == ["issue.token", tr.REDACTED, "me"]
    assert tr._redact_key_values(names, ["db.retention.sweeps.days", "30", "me"])[1] == "30"


def test_the_route_answers_404_422_and_504(tmp_path, monkeypatch):
    calls = []

    async def rows(pool, table, *, offset, limit):
        calls.append((table, offset, limit))
        if table == "missing":
            raise tr.TableNotFound(table)
        if table == "slow":
            raise asyncpg.QueryCanceledError("canceling statement due to statement timeout")
        return {"table": table, "rows": [], "offset": offset, "limit": limit}

    monkeypatch.setattr(tr, "table_rows", rows)
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        assert c.get("/api/admin/db/tables/audit_log/rows").json()["limit"] == 50
        assert calls[-1] == ("audit_log", 0, 50)
        assert c.get("/api/admin/db/tables/audit_log/rows?offset=100&limit=200").status_code == 200
        assert c.get("/api/admin/db/tables/missing/rows").status_code == 404
        assert c.get("/api/admin/db/tables/slow/rows").status_code == 504
        n = len(calls)
        assert c.get("/api/admin/db/tables/audit_log/rows?limit=201").status_code == 422
        assert c.get("/api/admin/db/tables/audit_log/rows?offset=-1").status_code == 422
        assert len(calls) == n


# --- live Postgres ------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _pool():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        yield pool
    finally:
        await dbm.close_pool(pool)


@contextlib.asynccontextmanager
async def _throwaway(pool):
    """A keyed table of 120 rows, an unkeyed one of 5, a key/value one and a view."""
    tag = uuid.uuid4().hex[:8]
    keyed, plain, kv, view = (f"zz_test_rows_{tag}_{s}" for s in ("keyed", "plain", "kv", "view"))
    try:
        await pool.execute(
            f'CREATE TABLE "{keyed}" (id INT PRIMARY KEY, created_at TIMESTAMPTZ, blob BYTEA, doc JSONB, '
            "note TEXT, api_token TEXT, password_hash TEXT, amount NUMERIC)"
        )
        # Inserted out of order: the page order must be the key's, not the insert order.
        await pool.execute(
            f"""INSERT INTO "{keyed}" SELECT g, TIMESTAMPTZ '2026-01-01' + g * INTERVAL '1 hour',
                decode(repeat('ab', 10), 'hex'), jsonb_build_object('k', repeat('j', 900)),
                CASE WHEN g = 1 THEN repeat('n', 2000) ELSE 'short' END,
                CASE WHEN g % 2 = 0 THEN 'tok-' || g END, 'pw-' || g, 1.5
                FROM generate_series(120, 1, -1) g"""
        )
        await pool.execute(f'CREATE TABLE "{plain}" (label TEXT)')
        await pool.execute(f"INSERT INTO \"{plain}\" VALUES ('e'), ('d'), ('c'), ('b'), ('a')")
        await pool.execute(f'CREATE TABLE "{kv}" (key TEXT PRIMARY KEY, value TEXT)')
        await pool.execute(f"INSERT INTO \"{kv}\" VALUES ('a.client_secret', 's3'), ('b.days', '30')")
        await pool.execute(f'CREATE VIEW "{view}" AS SELECT * FROM "{plain}"')
        yield keyed, plain, kv, view
    finally:
        await pool.execute(f'DROP VIEW IF EXISTS "{view}"')
        for t in (keyed, plain, kv):
            await pool.execute(f'DROP TABLE IF EXISTS "{t}"')


@needs_postgres
@pytest.mark.asyncio
async def test_pages_are_ordered_bounded_and_redacted():
    async with _pool() as pool, _throwaway(pool) as (keyed, plain, kv, _view):
        first = await tr.table_rows(pool, keyed, offset=0, limit=50)
        names = [c["name"] for c in first["columns"]]
        assert names == ["id", "created_at", "blob", "doc", "note", "api_token", "password_hash", "amount"]
        types = {c["name"]: c["type"] for c in first["columns"]}
        assert types["id"] == "integer" and types["doc"] == "jsonb" and types["blob"] == "bytea"
        assert [c["name"] for c in first["columns"] if c["redacted"]] == ["api_token", "password_hash"]
        assert first["order_by"] == ["id"] and first["total"] == 120 and first["total_exact"] is True
        assert [r[0] for r in first["rows"]] == list(range(1, 51))
        row = dict(zip(names, first["rows"][0]))
        assert row["created_at"] == "2026-01-01T01:00:00+00:00"
        assert row["blob"] == "<10 bytes>" and row["amount"] == "1.5"
        assert row["note"].endswith(tr.TRUNCATED_MARK) and len(row["note"]) == tr.CELL_MAX_CHARS + len(
            tr.TRUNCATED_MARK
        )
        assert row["doc"].startswith('{"k": "jjj') and row["doc"].endswith(tr.TRUNCATED_MARK)
        assert row["api_token"] is None and row["password_hash"] == tr.REDACTED  # id 1: no token set
        assert dict(zip(names, first["rows"][1]))["api_token"] == tr.REDACTED
        assert "tok-" not in repr(first) and "pw-" not in repr(first)

        last = await tr.table_rows(pool, keyed, offset=100, limit=50)
        assert [r[0] for r in last["rows"]] == list(range(101, 121))
        assert (await tr.table_rows(pool, keyed, offset=500, limit=50))["rows"] == []

        unkeyed = await tr.table_rows(pool, plain, offset=1, limit=2)
        assert unkeyed["order_by"] == ["ctid"] and unkeyed["rows"] == [["d"], ["c"]]  # physical order

        settings = await tr.table_rows(pool, kv)
        assert settings["rows"] == [["a.client_secret", tr.REDACTED], ["b.days", "30"]]


@needs_postgres
@pytest.mark.asyncio
async def test_only_tables_the_storage_view_reports_can_be_read():
    async with _pool() as pool, _throwaway(pool) as (keyed, _plain, _kv, view):
        for name in (
            "pg_class",
            "pg_authid",
            "pg_stat_activity",
            "information_schema.tables",
            "pg_catalog.pg_class",
            view,
            keyed.upper(),
            f'{keyed}"; DROP TABLE "{keyed}"; --',
            f"zz_test_rows_missing_{uuid.uuid4().hex[:8]}",
        ):
            with pytest.raises(tr.TableNotFound):
                await tr.table_rows(pool, name)
        assert await pool.fetchval(f'SELECT count(*) FROM "{keyed}"') == 120
        with pytest.raises(ValueError):
            await tr.table_rows(pool, keyed, limit=tr.ROWS_MAX_LIMIT + 1)


@needs_postgres
@pytest.mark.asyncio
async def test_the_read_is_read_only(monkeypatch):
    async with _pool() as pool, _throwaway(pool) as (_keyed, plain, _kv, _view):
        # Smuggle a write into the read's own transaction: it must be refused.
        monkeypatch.setattr(
            tr, "_SIZE_SQL", f"WITH w AS (INSERT INTO \"{plain}\" VALUES ('x') RETURNING 1) " + tr._SIZE_SQL
        )
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await tr.table_rows(pool, plain)
        assert await pool.fetchval(f'SELECT count(*) FROM "{plain}"') == 5
