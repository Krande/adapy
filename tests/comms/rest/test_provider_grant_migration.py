"""Grants as the only scope -> provider-collection control: the one-time migration of the per-scope
collection matrix and the attribute shares, the Access tab's batch route, and the matrix refused."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid

# Importing app.py builds a default app; point its local storage somewhere that exists.
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-grant-migration-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest import provider_grant_migration as m  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.db import provider_grants as pg  # noqa: E402

from .test_provider_grants import _settings  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

P1, P2 = str(uuid.uuid4()), str(uuid.uuid4())


# --- the rules -----------------------------------------------------------------------------------


def test_matrix_keys_map_to_grant_targets():
    assert m.matrix_target("shared") == "shared"
    assert m.matrix_target(f"project:{P1}") == f"projects/{P1}"
    assert m.matrix_target("user:abc") == "users/abc"
    for nobody in ("user:me", "user:", "project:", "junk", ""):
        assert m.matrix_target(nobody) is None


def test_the_matrix_is_read_as_the_frontend_read_it():
    raw = json.dumps({"shared": {"vendor": ["A", " ", 3, "B"], "bad": "A"}, "junk": 3})
    assert m.parse_matrix(raw) == {"shared": {"vendor": ["A", "B"]}}
    assert m.parse_matrix("not json") == {} and m.parse_matrix(None) == {}


def test_explicit_entries_become_read_grants_and_missing_ones_grant_what_was_reachable():
    plan = m.plan_matrix_grants(
        {
            "shared": {"vendor": ["ALPHA"]},  # restricted to one
            f"project:{P1}": {"vendor": []},  # restricted to none
            "user:someone": {"vendor": ["GAMMA"]},
            "project:gone": {"vendor": ["ALPHA"]},  # not a live project: dropped
            "user:me": {"vendor": ["ALPHA"]},  # names no one scope: dropped
        },
        live_projects=[P1, P2],
        store_collections={"vendor": {"alpha", "beta"}, "other": {"delta"}},
        scope_collections={f"projects/{P2}": {"vendor": {"local"}}},
    )
    got = {(t, p, c) for t, p, c, _ in plan}
    assert ("shared", "vendor", "alpha") in got and ("shared", "vendor", "beta") not in got
    assert not any(t == f"projects/{P1}" and p == "vendor" for t, p, _ in got), "an empty list enabled nothing"
    assert ("users/someone", "vendor", "gamma") in got
    # Unrestricted: P2 had no entry -- every store collection plus its own copy, for every provider.
    assert {(c) for t, p, c in got if t == f"projects/{P2}" and p == "vendor"} == {"alpha", "beta", "local"}
    assert ("shared", "other", "delta") in got and (f"projects/{P1}", "other", "delta") in got
    # A personal scope gets its explicit entries only.
    assert {(p, c) for t, p, c in got if t == "users/someone"} == {("vendor", "gamma")}
    assert not any(t.startswith("projects/gone") or t == "users/me" for t, _, _ in got)


def test_an_unconfigured_deployment_grants_shared_and_projects_everything_the_store_holds():
    plan = m.plan_matrix_grants({}, live_projects=[P1], store_collections={"vendor": {"alpha"}}, scope_collections={})
    assert {(t, p, c) for t, p, c, _ in plan} == {("shared", "vendor", "alpha"), (f"projects/{P1}", "vendor", "alpha")}


# --- routes ------------------------------------------------------------------------------------


def test_the_batch_route_grants_and_revokes_collections(tmp_path, monkeypatch):
    calls = []

    async def batch(pool, **kw):
        calls.append(kw)
        return {"granted": 2, "revoked": 1}

    async def listing(pool, **kw):
        return [{"collection": "alpha"}]

    async def exists(pool, pid):
        return pid == P1

    monkeypatch.setattr(dbm, "set_collection_grants", batch)
    monkeypatch.setattr(dbm, "list_provider_grants", listing)
    monkeypatch.setattr(dbm, "project_exists", exists)
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        body = {"target_scope": f"projects/{P1}", "provider": "vendor", "grant": ["ALPHA", " "], "revoke": ["beta"]}
        r = c.post("/api/admin/provider-grants/collections", json=body)
        assert r.status_code == 200, r.text
        assert r.json() == {"granted": 2, "revoked": 1, "grants": [{"collection": "alpha"}]}
        assert calls[-1] | {"granted_by": None} == {
            "target_scope": f"projects/{P1}",
            "provider": "vendor",
            "grant": ["ALPHA"],
            "revoke": ["beta"],
            "granted_by": None,
        }
        bad = c.post("/api/admin/provider-grants/collections", json={**body, "grant": "ALPHA"})
        assert bad.status_code == 400
        gone = c.post("/api/admin/provider-grants/collections", json={**body, "target_scope": f"projects/{P2}"})
        assert gone.status_code == 404
        store = c.post("/api/admin/provider-grants/collections", json={**body, "target_scope": "providers"})
        assert store.status_code == 400


def test_the_matrix_setting_can_no_longer_be_written(tmp_path, monkeypatch):
    async def never(*a, **kw):
        raise AssertionError("must not be stored")

    monkeypatch.setattr(dbm, "set_setting", never)
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        r = c.post(f"/api/admin/settings/{m.SCOPE_COLLECTIONS_SETTING}", json={"value": "{}"})
        assert r.status_code == 410 and "provider grants" in r.json()["detail"]


# --- live Postgres ------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _pool():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        yield pool
    finally:
        await dbm.close_pool(pool)


async def _catalogue_row(pool, scope: str, provider: str, collection: str) -> None:
    key = f"{scope}|{provider}|{collection}"
    await pool.execute(
        "INSERT INTO attr_collections (scope, provider, collection, partition_name, part_key) VALUES ($1, $2, $3, $4, $5)",
        scope,
        provider,
        collection,
        f"attr_p_test_{uuid.uuid4().hex[:12]}",
        key,
    )


@needs_postgres
@pytest.mark.asyncio
async def test_batch_grants_widen_and_revoke_only_collection_grants():
    provider = f"zz-test-{uuid.uuid4().hex[:8]}"
    target = f"users/{provider}"
    async with _pool() as pool:
        try:
            await pg.upsert_provider_grant(
                pool, target_scope=target, provider=provider, attributes=True, geometry=False
            )
            await pg.upsert_provider_grant(
                pool, target_scope=target, provider=provider, collection="beta", attributes=True, geometry=False
            )
            out = await pg.set_collection_grants(
                pool, target_scope=target, provider=provider, grant=["ALPHA", "beta"], revoke=["gamma"]
            )
            assert out == {"granted": 2, "revoked": 0}
            rows = {g["collection"]: g for g in await pg.list_provider_grants(pool, target_scope=target)}
            assert rows["alpha"]["attributes"] and rows["alpha"]["geometry"] and not rows["alpha"]["request"]
            assert rows["beta"]["geometry"], "a narrower grant is widened to read access"
            again = await pg.set_collection_grants(pool, target_scope=target, provider=provider, grant=["alpha"])
            assert again == {"granted": 0, "revoked": 0}
            out = await pg.set_collection_grants(pool, target_scope=target, provider=provider, revoke=["alpha", "beta"])
            assert out == {"granted": 0, "revoked": 2}
            left = await pg.list_provider_grants(pool, target_scope=target)
            assert [g["collection"] for g in left] == [None], "the provider-wide grant is its own checkbox"
        finally:
            await pool.execute("DELETE FROM provider_grants WHERE provider = $1", provider)


@needs_postgres
@pytest.mark.asyncio
async def test_the_migration_writes_grants_once_and_retires_the_matrix():
    provider = f"zz-test-{uuid.uuid4().hex[:8]}"
    slug = f"zz-{uuid.uuid4().hex[:8]}"
    async with _pool() as pool:
        saved = {
            k: await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", k)
            for k in (m.SCOPE_COLLECTIONS_SETTING, m.MIGRATED_SETTING)
        }
        project = str(await pool.fetchval("INSERT INTO projects (slug, name) VALUES ($1, $1) RETURNING id", slug))
        try:
            await _catalogue_row(pool, "providers", provider, "alpha")
            await _catalogue_row(pool, "providers", provider, "beta")
            await _catalogue_row(pool, "users/origin", provider, "gamma")
            await pool.execute(
                "INSERT INTO attr_shares (scope, provider, collection, target_scope) VALUES ('users/origin', $1, 'gamma', $2)",
                provider,
                f"projects/{project}",
            )
            await pool.execute(
                "INSERT INTO attr_provider_shares (scope, provider, target_scope) VALUES ('users/origin', $1, 'users/reader')",
                provider,
            )
            # An admin's grant that the migration must not narrow.
            await pg.upsert_provider_grant(
                pool,
                target_scope="shared",
                provider=provider,
                collection="beta",
                attributes=True,
                geometry=True,
                request=True,
            )
            matrix = {"shared": {provider: ["ALPHA"]}, "user:me": {provider: ["BETA"]}}
            await dbm.set_setting(pool, m.SCOPE_COLLECTIONS_SETTING, json.dumps(matrix), updated_by=None)
            await pool.execute("DELETE FROM app_settings WHERE key = $1", m.MIGRATED_SETTING)

            counts = await m.migrate_legacy_access(pool)
            assert counts is not None and counts["shares"] >= 1 and counts["provider_shares"] >= 1

            grants = {
                (g["target_scope"], g["collection"]): g for g in await pg.list_provider_grants(pool, provider=provider)
            }
            assert grants[("shared", "alpha")]["geometry"], "an explicit entry is read access"
            assert grants[("shared", "beta")]["request"], "an admin's grant is never narrowed"
            assert ("shared", None) not in grants
            # The project had no entry: unrestricted, so every store collection, read access.
            assert grants[(f"projects/{project}", "alpha")]["geometry"]
            assert grants[(f"projects/{project}", "beta")]["attributes"]
            # Shares: attributes only.
            assert grants[(f"projects/{project}", "gamma")]["attributes"]
            assert not grants[(f"projects/{project}", "gamma")]["geometry"]
            assert grants[("users/reader", None)]["attributes"] and not grants[("users/reader", None)]["geometry"]

            assert await pool.fetchval("SELECT 1 FROM app_settings WHERE key = $1", m.SCOPE_COLLECTIONS_SETTING) is None
            record = json.loads(
                await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", m.MIGRATED_SETTING)
            )
            assert record["scope_collections"] == matrix
            assert await m.migrate_legacy_access(pool) is None, "it runs once"
            assert await m.migrate_legacy_access(pool, force=True) == {
                "matrix": 0,
                "unrestricted": 0,
                "shares": 0,
                "provider_shares": 0,
            }, "and a forced second run adds nothing"
        finally:
            # The scratch database may hold other providers' catalogue rows; the run granted those too.
            await pool.execute(
                "DELETE FROM provider_grants WHERE provider = $1 OR granted_by = $2", provider, m.MIGRATED_BY
            )
            await pool.execute("DELETE FROM attr_provider_shares WHERE provider = $1", provider)
            await pool.execute("DELETE FROM attr_collections WHERE provider = $1", provider)
            await pool.execute("DELETE FROM projects WHERE id = $1::uuid", project)
            for key, value in saved.items():
                if value is None:
                    await pool.execute("DELETE FROM app_settings WHERE key = $1", key)
                else:
                    await dbm.set_setting(pool, key, value, updated_by=None)
