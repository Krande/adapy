"""Admin endpoint tests.

Same split as test_db.py:

* Always-on tests use the create_app pipeline with auth disabled. The
  synthetic local-dev user is admin, so admin gating is exercised by
  flipping User.local_dev to a non-admin variant via monkeypatch and
  asserting 403. Without DB the admin endpoints 503 — also tested.

* Live-Postgres tests (skipped unless ADA_TEST_POSTGRES_URL is set)
  exercise the full project CRUD + audit listing against real
  Postgres, including admin filters.
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import uuid

# Importing ada.comms.rest.app evaluates a module-level `create_app()`
# which materializes a local Storage. Point it at a temp dir so the
# import succeeds in environments without `./viewer-data`.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault(
    "ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-")
)

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import auth as auth_module
from ada.comms.rest import db as dbm
from ada.comms.rest.app import create_app
from ada.comms.rest.config import (
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)


POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)


def _settings(tmp_path, *, db_url: str = "") -> Settings:
    # Use a local-storage sandbox so create_app() doesn't reach for S3.
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=None,
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="",
        ),
        database_url=db_url,
    )


def test_admin_endpoint_requires_admin(monkeypatch, tmp_path):
    """Non-admin user → 403 across the admin namespace."""
    monkeypatch.setattr(
        auth_module.User,
        "local_dev",
        classmethod(
            lambda cls: cls(
                sub="non-admin",
                email="x@y",
                display_name="X",
                groups=frozenset(),
                is_admin=False,
            )
        ),
    )
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        for path, method in (
            ("/api/admin/audit", "GET"),
            ("/api/admin/projects", "GET"),
            ("/api/admin/projects", "POST"),
            (f"/api/admin/projects/{uuid.uuid4()}/members", "GET"),
            (f"/api/admin/projects/{uuid.uuid4()}/ci-bot", "POST"),
        ):
            r = client.request(method, path, json={})
            assert r.status_code == 403, f"{method} {path}: {r.status_code}"


def test_admin_endpoints_503_without_db(tmp_path):
    """Local-dev user is admin, but no DATABASE_URL → 503."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/api/admin/audit")
        assert r.status_code == 503
        r = client.get("/api/admin/projects")
        assert r.status_code == 503
        r = client.post(f"/api/admin/projects/{uuid.uuid4()}/ci-bot")
        assert r.status_code == 503


def test_admin_create_project_validates_slug(tmp_path):
    """Slug shape is enforced before the DB call. Spoof a pool so we
    pass the 503 gate; the validator should fire before any DB use."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        app.state.db_pool = object()  # set inside lifespan so it sticks
        r = client.post("/api/admin/projects", json={"slug": "Bad Slug!", "name": "Demo"})
        assert r.status_code == 400, r.text
        r = client.post("/api/admin/projects", json={"slug": "", "name": "Demo"})
        assert r.status_code == 400
        r = client.post("/api/admin/projects", json={"slug": "ok", "name": ""})
        assert r.status_code == 400


def test_admin_uuid_validation(tmp_path):
    """Non-UUID project_id should 400 before any DB lookup."""
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        app.state.db_pool = object()
        r = client.get("/api/admin/projects/not-a-uuid/members")
        assert r.status_code == 400


# ── Live-Postgres path ───────────────────────────────────────────────


@needs_postgres
@pytest.mark.asyncio
async def test_admin_project_lifecycle(tmp_path):
    """Round-trip: create project → list → add member → list → archive.

    Uses the dbm helpers directly because reusing TestClient's loop with
    a separately-built asyncpg pool runs into the same loop-mismatch
    issue the test_db.py file already documents.
    """
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    try:
        slug = f"test-{uuid.uuid4().hex[:12]}"
        proj = await dbm.create_project(pool, slug, "Test")
        assert proj["slug"] == slug
        assert proj["member_count"] == 0

        # Duplicate slug raises ValueError.
        with pytest.raises(ValueError):
            await dbm.create_project(pool, slug, "Dup")

        sub = f"sub-{uuid.uuid4().hex[:12]}"
        added = await dbm.add_project_member(pool, proj["id"], sub, "owner")
        assert added is True
        # Idempotent re-add returns False.
        assert await dbm.add_project_member(pool, proj["id"], sub) is False

        members = await dbm.list_project_members(pool, proj["id"])
        assert any(m["user_sub"] == sub and m["role"] == "owner" for m in members)

        all_projects = await dbm.list_all_projects(pool)
        assert any(p["id"] == proj["id"] and p["member_count"] == 1 for p in all_projects)

        # Member removal.
        assert await dbm.remove_project_member(pool, proj["id"], sub) is True
        assert await dbm.remove_project_member(pool, proj["id"], sub) is False

        # Archive flips archived_at and the project drops out of user listings.
        assert await dbm.archive_project(pool, proj["id"]) is True
        assert await dbm.archive_project(pool, proj["id"]) is False  # already archived
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_project_id_from_slug_resolves_and_skips_archived():
    """Slug → UUID resolver feeds the scope URL parser. Archived
    projects must not resolve, so a stale slug doesn't quietly
    re-target a recreated project."""
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    try:
        slug = f"slug-{uuid.uuid4().hex[:12]}"
        proj = await dbm.create_project(pool, slug, "Slug Test")
        assert await dbm.project_id_from_slug(pool, slug) == proj["id"]
        assert await dbm.project_id_from_slug(pool, "definitely-no-such-slug") is None
        await dbm.archive_project(pool, proj["id"])
        assert await dbm.project_id_from_slug(pool, slug) is None
    finally:
        await dbm.close_pool(pool)


@needs_postgres
def test_admin_ci_bot_provision_lifecycle(tmp_path):
    """End-to-end: POST /ci-bot creates the bot user, adds membership,
    mints a token; re-calling rotates."""
    import time

    base = _settings(tmp_path, db_url=POSTGRES_URL)
    # Need cli_token_secret set for mint_cli_token to work; auth stays
    # disabled so the local-dev synthetic user is admin and the admin
    # router lets us through. Settings is frozen, so we copy-replace.
    settings = dataclasses.replace(
        base,
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="ci-bot-test-secret",
        ),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        slug = f"ci-{uuid.uuid4().hex[:12]}"
        r = client.post(
            "/api/admin/projects", json={"slug": slug, "name": "CI Test"}
        )
        assert r.status_code == 201, r.text
        body = r.json()
        pid = body["id"]
        # Creator (synthetic ``local-dev`` user in auth-disabled mode)
        # is auto-added as owner so the project shows up in their
        # /api/me.scopes immediately.
        assert body["member_count"] == 1, body
        r = client.get(f"/api/admin/projects/{pid}/members")
        assert r.status_code == 200
        creator_members = r.json()["members"]
        assert any(
            m["user_sub"] == "local-dev" and m["role"] == "owner"
            for m in creator_members
        ), creator_members

        # First provision.
        r = client.post(f"/api/admin/projects/{pid}/ci-bot")
        assert r.status_code == 201, r.text
        first = r.json()
        assert first["user_sub"] == f"ci:{slug}"
        assert first["token"]
        assert first["expires_at"] > int(time.time())

        # Bot is a member with role 'ci' alongside the creator.
        r = client.get(f"/api/admin/projects/{pid}/members")
        assert r.status_code == 200
        members = r.json()["members"]
        assert any(
            m["user_sub"] == f"ci:{slug}" and m["role"] == "ci" for m in members
        )
        assert any(
            m["user_sub"] == "local-dev" and m["role"] == "owner" for m in members
        )

        # Re-provisioning rotates the token.
        # Sleep 1s so the new token's iat strictly exceeds the revoke
        # cutoff; without this the integer-second iat may equal the
        # cutoff and the < check would land on the boundary case (still
        # valid, but the test asserts a *new* token, not the same one).
        time.sleep(1)
        r = client.post(f"/api/admin/projects/{pid}/ci-bot")
        assert r.status_code == 201, r.text
        second = r.json()
        assert second["token"] != first["token"]
        assert second["expires_at"] >= first["expires_at"]

        # 404 for an unknown project (after archiving the one we made
        # — same UUID, but archived_at non-null).
        r = client.delete(f"/api/admin/projects/{pid}")
        assert r.status_code == 204
        r = client.post(f"/api/admin/projects/{pid}/ci-bot")
        assert r.status_code == 404


@needs_postgres
@pytest.mark.asyncio
async def test_audit_job_lifecycle():
    """The convert-job audit pattern: API inserts a 'queued' row with a
    job_id, the worker patches it to 'done' or 'error' once it finishes."""
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    try:
        job_id = uuid.uuid4().hex
        sub = f"sub-{uuid.uuid4().hex[:12]}"
        await dbm.insert_audit(
            pool,
            user_sub=sub,
            scope_kind="user",
            scope_id=sub,
            action="convert",
            key="model.ifc",
            target_format="glb",
            status="queued",
            job_id=job_id,
        )
        rows = await dbm.list_audit(pool, user_sub=sub)
        assert len(rows) == 1
        assert rows[0]["status"] == "queued"

        # Worker happy path → done + duration set, error stays NULL.
        await dbm.update_audit_by_job(
            pool, job_id=job_id, status="done", error=None, duration_ms=4321
        )
        rows = await dbm.list_audit(pool, user_sub=sub)
        assert rows[0]["status"] == "done"
        assert rows[0]["duration_ms"] == 4321

        # Worker error path on a second job → error + message recorded.
        job_id_b = uuid.uuid4().hex
        await dbm.insert_audit(
            pool,
            user_sub=sub,
            scope_kind="user",
            scope_id=sub,
            action="convert",
            status="queued",
            job_id=job_id_b,
        )
        await dbm.update_audit_by_job(
            pool, job_id=job_id_b, status="error", error="conversion crashed", duration_ms=120
        )
        rows = await dbm.list_audit(pool, user_sub=sub, action="convert")
        # Newest first, so job_id_b is row 0.
        assert rows[0]["status"] == "error"
        assert rows[0]["error"] == "conversion crashed"

        # Update for an unknown job_id is a no-op (doesn't insert, doesn't raise).
        await dbm.update_audit_by_job(
            pool, job_id="never-existed", status="error", error="x"
        )
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_admin_audit_filters(tmp_path):
    """list_audit honours the per-column filters and keyset pagination."""
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    try:
        marker = uuid.uuid4().hex[:12]
        sub_a = f"sub-A-{marker}"
        sub_b = f"sub-B-{marker}"
        for i in range(3):
            await dbm.insert_audit(
                pool,
                user_sub=sub_a,
                scope_kind="user",
                scope_id=sub_a,
                action="upload",
                key=f"a{i}.ifc",
                status="ok",
            )
        await dbm.insert_audit(
            pool,
            user_sub=sub_b,
            scope_kind="user",
            scope_id=sub_b,
            action="convert",
            key="b.ifc",
            status="queued",
        )

        only_a = await dbm.list_audit(pool, user_sub=sub_a)
        assert len(only_a) == 3
        assert all(r["user_sub"] == sub_a for r in only_a)

        only_convert = await dbm.list_audit(pool, action="convert", user_sub=sub_b)
        assert len(only_convert) == 1
        assert only_convert[0]["key"] == "b.ifc"

        # Keyset pagination: ask for one row, then continue from there.
        page1 = await dbm.list_audit(pool, user_sub=sub_a, limit=1)
        assert len(page1) == 1
        page2 = await dbm.list_audit(
            pool, user_sub=sub_a, limit=1, before_id=page1[0]["id"]
        )
        assert len(page2) == 1
        assert page2[0]["id"] < page1[0]["id"]
    finally:
        await dbm.close_pool(pool)
