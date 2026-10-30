"""Provider grants (migration 045) and the provider-store scope -- phase 1 scaffolding of
docs/design/provider-scope-grants.md. Nothing reads through a grant yet; these pin the shapes the
read path will build on."""

from __future__ import annotations

import contextlib
import os
import tempfile
import uuid

# Importing app.py builds a default app; point its local storage somewhere that exists.
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-grants-"))

import pytest  # noqa: E402
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ada.comms.rest import db as dbm
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.db import provider_grants as pg
from ada.comms.rest.queue import Job
from ada.comms.rest.routes import admin_provider_grants as apg
from ada.comms.rest.scope import Scope, can_access
from ada.comms.rest.worker.state import _scope_of

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")


class _User:
    def __init__(self, sub: str, is_admin: bool = False) -> None:
        self.sub = sub
        self.is_admin = is_admin


# --- the provider-store scope -------------------------------------------------------------------


def test_the_provider_store_is_one_scope_with_one_prefix_and_wire():
    s = Scope.providers()
    assert s.id is None and s.prefix() == "providers" and s.wire() == "providers"
    assert Scope.from_prefix("providers") == s
    with pytest.raises(ValueError):
        Scope(kind="providers", id="x")
    # A project scope never collides with it.
    assert Scope.from_prefix("projects/abc").kind == "project"


@pytest.mark.asyncio
async def test_only_an_admin_opens_the_provider_store_directly():
    assert await can_access(_User("a", is_admin=True), Scope.providers()) is True
    assert await can_access(_User("b"), Scope.providers()) is False


def test_a_provider_store_job_comes_back_as_the_provider_store():
    job = Job(
        job_id="j", source_key="k", derived_key="d", status="queued", target_format="plugin_job", scope_kind="providers"
    )
    assert _scope_of(job) == Scope.providers()


# --- grant access, pure -------------------------------------------------------------------------


def test_a_provider_wide_and_a_collection_grant_add_up():
    rows = [
        {"attributes": True, "geometry": False, "request": False},
        {"attributes": False, "geometry": True, "request": True},
    ]
    access = pg.merge_access(rows)
    assert access == pg.GrantAccess(attributes=True, geometry=True, request=True)
    assert not pg.merge_access([])
    assert access.allows("geometry")
    with pytest.raises(ValueError):
        access.allows("write")


def test_grant_bodies_are_checked():
    ref = apg.parse_grant_ref({"target_scope": "shared", "provider": "p", "collection": " ALPHA "})
    assert ref == {"target_scope": "shared", "provider": "p", "collection": "alpha"}
    assert apg.parse_grant_ref({"target_scope": "users/u1", "provider": "p"})["collection"] is None
    pid = str(uuid.uuid4())
    assert (
        apg.parse_grant_ref({"target_scope": f"projects/{pid}", "provider": "p", "collection": ""})["collection"]
        is None
    )
    for bad in (
        {"provider": "p"},
        {"target_scope": "shared"},
        {"target_scope": "providers", "provider": "p"},  # never the store itself
        {"target_scope": "corpus/x", "provider": "p"},
        {"target_scope": "projects/not-a-uuid", "provider": "p"},
        {"target_scope": "project:abc", "provider": "p"},  # a wire form, not a prefix
        {"target_scope": "shared", "provider": "p", "collection": 3},
    ):
        with pytest.raises(HTTPException) as exc:
            apg.parse_grant_ref(bad)
        assert exc.value.status_code == 400

    assert apg.parse_permissions({}) == {"attributes": True, "geometry": True, "request": False}
    assert apg.parse_permissions({"geometry": False, "request": True}) == {
        "attributes": True,
        "geometry": False,
        "request": True,
    }
    for bad in ({"attributes": "yes"}, {"attributes": False, "geometry": False}):
        with pytest.raises(HTTPException):
            apg.parse_permissions(bad)


# --- routes ------------------------------------------------------------------------------------


def _settings(tmp_path) -> Settings:
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
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


def test_the_grant_routes_store_what_they_are_given(tmp_path, monkeypatch):
    calls = []

    async def upsert(pool, **kw):
        calls.append(("put", kw))
        return {**kw, "id": "g1"}

    async def revoke(pool, **kw):
        calls.append(("delete", kw))
        return True

    async def listing(pool, **kw):
        calls.append(("list", kw))
        return []

    async def exists(pool, pid):
        return True

    monkeypatch.setattr(dbm, "upsert_provider_grant", upsert)
    monkeypatch.setattr(dbm, "revoke_provider_grant", revoke)
    monkeypatch.setattr(dbm, "list_provider_grants", listing)
    monkeypatch.setattr(dbm, "project_exists", exists)
    pid = str(uuid.uuid4())
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        r = c.put(
            "/api/admin/provider-grants",
            json={"target_scope": f"projects/{pid}", "provider": "p", "collection": "Alpha", "request": True},
        )
        assert r.status_code == 200, r.text
        assert calls[-1][1] | {"granted_by": None} == {
            "target_scope": f"projects/{pid}",
            "provider": "p",
            "collection": "alpha",
            "attributes": True,
            "geometry": True,
            "request": True,
            "granted_by": None,
            "note": None,
        }
        assert (
            c.put("/api/admin/provider-grants", json={"target_scope": "providers", "provider": "p"}).status_code == 400
        )
        r = c.request("DELETE", "/api/admin/provider-grants", json={"target_scope": "shared", "provider": "p"})
        assert r.json() == {"removed": True}
        assert calls[-1] == ("delete", {"target_scope": "shared", "provider": "p", "collection": None})
        assert c.get("/api/admin/provider-grants?provider=p").json() == {"grants": []}


# --- live Postgres ------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _pool():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        yield pool
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_grants_round_trip_and_add_up():
    provider = f"zz-test-{uuid.uuid4().hex[:8]}"
    target = f"users/{provider}"
    async with _pool() as pool:
        try:
            wide = await pg.upsert_provider_grant(
                pool, target_scope=target, provider=provider, attributes=True, geometry=False
            )
            assert wide["collection"] is None and wide["geometry"] is False
            one = await pg.upsert_provider_grant(
                pool, target_scope=target, provider=provider, collection="ALPHA", attributes=False, request=True
            )
            assert one["collection"] == "alpha"
            # Replacing keeps one row per (target, provider, collection).
            again = await pg.upsert_provider_grant(
                pool, target_scope=target, provider=provider, collection="alpha", attributes=False, geometry=True
            )
            assert again["id"] == one["id"] and again["request"] is False

            access = await pg.grant_access(pool, target_scope=target, provider=provider, collection="Alpha")
            assert access == pg.GrantAccess(attributes=True, geometry=True, request=False)
            other = await pg.grant_access(pool, target_scope=target, provider=provider, collection="beta")
            assert other == pg.GrantAccess(attributes=True)
            assert not await pg.grant_access(pool, target_scope="shared", provider=provider, collection="alpha")

            assert await pg.granted_datasets(pool, target_scope=target, permission="geometry") == [(provider, "alpha")]
            listed = await pg.list_provider_grants(pool, provider=provider, collection="alpha")
            assert [g["collection"] for g in listed] == [None, "alpha"]

            assert await pg.revoke_provider_grant(pool, target_scope=target, provider=provider) is True
            assert await pg.revoke_provider_grant(pool, target_scope=target, provider=provider) is False
            assert [g["collection"] for g in await pg.list_provider_grants(pool, provider=provider)] == ["alpha"]
            with pytest.raises(Exception):
                await pg.upsert_provider_grant(pool, target_scope="providers", provider=provider)
        finally:
            await pool.execute("DELETE FROM provider_grants WHERE provider = $1", provider)
