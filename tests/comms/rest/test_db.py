"""DB layer tests.

Two paths:

* No-DB path tests (always run): verify ``init_pool('')`` returns
  ``None`` and the lifespan handles it gracefully.

* Live-Postgres tests (skipped unless ``ADA_TEST_POSTGRES_URL`` is set):
  spin up against a real Postgres, apply migrations, exercise the
  repo helpers. Marked so CI can opt in only when a Postgres service
  container is available.

Set ``ADA_TEST_POSTGRES_URL=postgres://test:test@localhost:55432/test``
and run ``docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test
-e POSTGRES_USER=test -e POSTGRES_DB=test postgres:16-alpine`` first.
"""

from __future__ import annotations

import os

import pytest

from ada.comms.rest import db as dbm

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)


@pytest.mark.asyncio
async def test_no_db_returns_none():
    pool = await dbm.init_pool("")
    assert pool is None
    # close_pool tolerates None — that's the API contract callers rely on.
    await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_migrations_create_expected_schema():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        rows = await pool.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' ORDER BY table_name
            """
        )
        names = {r["table_name"] for r in rows}
        for required in (
            "users",
            "projects",
            "project_members",
            "audit_log",
            "schema_version",
        ):
            assert required in names, f"missing {required}"
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_migrations_are_idempotent():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        before = await pool.fetchval("SELECT count(*) FROM schema_version")
        # Re-run; second pass should be a no-op.
        await dbm._apply_migrations(pool)
        after = await pool.fetchval("SELECT count(*) FROM schema_version")
        assert before == after
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_user_upsert_and_project_listing(tmp_path):
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        # Use unique slugs/subs so the test doesn't collide with previous
        # runs against a long-lived dev Postgres.
        sub = f"user-{tmp_path.name}"
        slug = f"proj-{tmp_path.name}"

        await dbm.upsert_user(pool, sub, "a@b.com", "Alice")
        await dbm.upsert_user(pool, sub, "a2@b.com", "Alice2")  # second is the upsert
        rows = await pool.fetch("SELECT email, display_name FROM users WHERE sub=$1", sub)
        assert len(rows) == 1
        assert rows[0]["email"] == "a2@b.com"
        assert rows[0]["display_name"] == "Alice2"

        proj_id = await pool.fetchval(
            "INSERT INTO projects (slug, name) VALUES ($1, $2) RETURNING id",
            slug,
            "Demo",
        )
        await pool.execute(
            "INSERT INTO project_members (project_id, user_sub) VALUES ($1, $2)",
            proj_id,
            sub,
        )

        ps = await dbm.list_user_projects(pool, sub)
        assert len(ps) == 1 and ps[0].slug == slug and ps[0].role == "member"

        assert await dbm.is_project_member(pool, str(proj_id), sub) is True
        assert await dbm.is_project_member(pool, str(proj_id), "stranger") is False

        # Archived projects drop out of the listing.
        await pool.execute("UPDATE projects SET archived_at = NOW() WHERE id=$1", proj_id)
        assert await dbm.list_user_projects(pool, sub) == []

        # …and out of membership checks (so callers can't access via stale URLs).
        assert await dbm.is_project_member(pool, str(proj_id), sub) is False
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_audit_insert(tmp_path):
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        before = await pool.fetchval("SELECT count(*) FROM audit_log")
        await dbm.insert_audit(
            pool,
            user_sub=f"user-{tmp_path.name}",
            scope_kind="user",
            scope_id=f"user-{tmp_path.name}",
            action="upload",
            key="foo.ifc",
            status="ok",
            duration_ms=12,
        )
        after = await pool.fetchval("SELECT count(*) FROM audit_log")
        assert after == before + 1
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_local_audit_lifecycle(tmp_path):
    """The browser (WASM) two-phase flow: insert a 'running' row with a
    ``wasm-`` job id + ``wasm:`` image tag, look up its owner, then patch
    it terminal with metrics — mirroring the audit/local endpoints."""
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        user_sub = f"user-{tmp_path.name}"
        job_id = f"wasm-{tmp_path.name}"
        await dbm.insert_audit(
            pool,
            user_sub=user_sub,
            scope_kind="user",
            scope_id=user_sub,
            action="convert",
            key="m.step",
            target_format="glb",
            status="running",
            job_id=job_id,
            worker_image_tag="wasm:pyodide-0.27.7",
        )
        owner = await dbm.get_audit_owner_by_job(pool, job_id)
        assert owner is not None
        assert owner["user_sub"] == user_sub
        assert owner["status"] == "running"
        assert owner["audit_run_id"] is None

        await dbm.update_audit_by_job(
            pool,
            job_id=job_id,
            status="done",
            duration_ms=1234,
            read_bytes=100,
            write_bytes=50,
            peak_rss_kb=4096,
        )
        row = await pool.fetchrow(
            "SELECT status, duration_ms, write_bytes, peak_rss_kb, worker_image_tag"
            " FROM audit_log WHERE job_id = $1",
            job_id,
        )
        assert row["status"] == "done"
        assert row["duration_ms"] == 1234
        assert row["write_bytes"] == 50
        assert row["peak_rss_kb"] == 4096
        # worker_image_tag set at insert survives the COALESCE update.
        assert row["worker_image_tag"] == "wasm:pyodide-0.27.7"

        # Unknown job id → no owner row.
        assert await dbm.get_audit_owner_by_job(pool, "wasm-does-not-exist") is None
    finally:
        await dbm.close_pool(pool)
