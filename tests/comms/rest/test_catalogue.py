"""The model-building catalogue: material overrides (migration 050) merged over the provider's values,
the scope catalogue endpoints (sections / materials, read through grants), and the ``ada.catalogue``
client -- sections and materials built from rows, the whitelist check, and the E3D writer emitting
the remembered refs.

The live-Postgres tests (skipped unless ADA_TEST_POSTGRES_URL is set) use throwaway providers and
drop everything they made.
"""

from __future__ import annotations

import gzip
import inspect
import json
import os
import tempfile
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest  # noqa: E402

from ada.catalogue import CATALOGUE_KEY, scope_catalogue  # noqa: E402
from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest.db import catalogue as cat_db  # noqa: E402
from ada.comms.rest.db import reference_data as rd  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

# --- overrides and merging ----------------------------------------------------------------------


def test_an_override_names_only_known_properties_with_checked_values():
    ok = cat_db.parse_override(
        {
            "provider": "p",
            "collection": "Col",
            "kind": "material",
            "ref": "/M",
            "props": {"E": 2e11, "v": 0.3, "grade": "S355", "rho": None},
        }
    )
    assert ok["collection"] == "col" and ok["props"] == {"E": 2e11, "v": 0.3, "grade": "S355"}
    base = {"provider": "p", "collection": "c", "kind": "material", "ref": "/M"}
    for bad in (
        {**base, "props": {"E": -1}},
        {**base, "props": {"v": 0.7}},
        {**base, "props": {"family": "wood"}},
        {**base, "props": {"colour": "red"}},
        {**base, "props": {"E": None}},
        {**base, "kind": "spco", "props": {"E": 1.0}},
        {"provider": "p", "kind": "material", "ref": "/M", "props": {"E": 1.0}},
    ):
        with pytest.raises(cat_db.OverrideError):
            cat_db.parse_override(bad)


def test_the_override_wins_key_by_key_and_says_so():
    merged = cat_db.merge_material({"rho": 7850.0, "family": "carbon_steel"}, {"rho": 7800.0, "sig_y": 3.55e8})
    assert merged["effective"] == {"rho": 7800.0, "family": "carbon_steel", "sig_y": 3.55e8}
    assert merged["source"] == {"rho": "override", "family": "provider", "sig_y": "override"}
    assert cat_db.merge_material({"rho": 1.0}, None)["override"] is None


def test_a_provider_without_a_material_object_still_says_its_density():
    assert cat_db.provider_material({"density": 7850, "description": "Steel"}) == {
        "rho": 7850.0,
        "description": "Steel",
    }


# --- live Postgres -------------------------------------------------------------------------------


def _bytes(provider: str, collection: str, rows: list[dict]) -> bytes:
    header = {"schema": rd.REFERENCE_DATA_SCHEMA, "provider": provider, "collection": collection, "revision": "r1"}
    return gzip.compress(("\n".join(json.dumps(x) for x in [header, *rows]) + "\n").encode("utf-8"))


async def _chunks(data: bytes, size: int = 2048):
    for i in range(0, len(data), size):
        yield data[i : i + size]


REF_ROWS = [
    {"kind": "spco", "ref": "/SEC/HEA200", "owner": "/SEC", "use": "section",
     "attrs": {"gtype": "BEAM", "section": {"type": "I", "dims": {"h": 190.0, "w_top": 200.0, "t_w": 6.5, "t_ftop": 10.0}, "units": "mm"}}},
    {"kind": "spco", "ref": "/PLT/PL10", "owner": "/PLT", "use": "plate",
     "attrs": {"section": {"type": "PLATE", "dims": {"t": 10.0}, "units": "mm"}}},
    {"kind": "spco", "ref": "/FIT/F1", "owner": "/FIT", "use": "fitting", "attrs": {}},
    {"kind": "material", "ref": "/STEEL-A", "use": "material",
     "attrs": {"material": {"family": "carbon_steel", "grade": "S355", "rho": 7850.0}}},
]  # fmt: skip


def with_pg(fn):
    params = list(inspect.signature(fn).parameters.values())[1:]

    async def run(**kwargs):
        pool = await dbm.init_pool(POSTGRES_URL)
        provider = f"test-{uuid.uuid4().hex[:6]}"
        try:
            await fn(
                SimpleNamespace(pool=pool, provider=provider, scope=f"projects/test-cat-{uuid.uuid4().hex[:8]}"),
                **kwargs,
            )
        finally:
            await pool.execute("DELETE FROM reference_sets WHERE provider = $1", provider)
            await pool.execute("DELETE FROM reference_overrides WHERE provider = $1", provider)
            await dbm.close_pool(pool)

    run.__name__ = fn.__name__
    run.__signature__ = inspect.Signature(params)
    return run


@needs_postgres
@pytest.mark.asyncio
@with_pg
async def test_overrides_survive_a_reingest_and_merge_into_the_catalogue(pg):
    await rd.ingest_reference_data(
        pg.pool, scope=pg.scope, provider=pg.provider, chunks=_chunks(_bytes(pg.provider, "col", REF_ROWS))
    )
    override = cat_db.parse_override(
        {
            "provider": pg.provider,
            "collection": "col",
            "kind": "material",
            "ref": "/STEEL-A",
            "props": {"sig_y": 3.45e8, "E": 2.05e11},
        }
    )
    stored = await cat_db.put_override(pg.pool, override=override, updated_by="admin-1")
    assert stored["updated_by"] == "admin-1"

    # A re-export replaces the reference set whole; the override is kept apart and still applies.
    await rd.ingest_reference_data(
        pg.pool, scope=pg.scope, provider=pg.provider, chunks=_chunks(_bytes(pg.provider, "col", REF_ROWS))
    )
    sets = await rd.list_reference_sets(pg.pool, scopes=[pg.scope])
    mats = await cat_db.query_catalogue(pg.pool, sets=sets, what="materials", query=cat_db.parse_catalogue_query({}))
    steel = next(i for i in mats["items"] if i["ref"] == "/STEEL-A")
    assert steel["material"]["effective"] == {
        "family": "carbon_steel",
        "grade": "S355",
        "rho": 7850.0,
        "sig_y": 3.45e8,
        "E": 2.05e11,
    }
    assert steel["material"]["source"]["sig_y"] == "override" and steel["override_meta"]["updated_by"] == "admin-1"

    secs = await cat_db.query_catalogue(pg.pool, sets=sets, what="sections", query=cat_db.parse_catalogue_query({}))
    assert [i["ref"] for i in secs["items"]] == ["/PLT/PL10", "/SEC/HEA200"], "fittings are not sections"
    only_i = await cat_db.query_catalogue(
        pg.pool, sets=sets, what="sections", query=cat_db.parse_catalogue_query({"type": "i"})
    )
    assert [i["ref"] for i in only_i["items"]] == ["/SEC/HEA200"]

    # The browser merges the same override into its material rows.
    page = await rd.query_reference_items(
        pg.pool,
        scope=pg.scope,
        query=rd.parse_reference_query({"provider": pg.provider, "collection": "col", "kind": "material"}),
    )
    assert page["items"][0]["material"]["effective"]["sig_y"] == 3.45e8

    listed = await cat_db.list_overrides(pg.pool, provider=pg.provider)
    assert [o["orphaned"] for o in listed] == [False]
    await rd.ingest_reference_data(
        pg.pool, scope=pg.scope, provider=pg.provider, chunks=_chunks(_bytes(pg.provider, "col", REF_ROWS[:1]))
    )
    assert [o["orphaned"] for o in await cat_db.list_overrides(pg.pool, provider=pg.provider)] == [True]
    assert await cat_db.delete_override(
        pg.pool, provider=pg.provider, collection="col", kind="material", ref="/STEEL-A"
    )


@pytest.fixture
def pg_app(tmp_path):
    from fastapi.testclient import TestClient

    from ada.comms.rest.app import create_app
    from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings

    settings = Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(url=None, stream="ada", subject="ada.viewer.jobs.convert", kv_bucket="ada-viewer-jobs",
                          durable="ada-viewer-worker"),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url=POSTGRES_URL,
    )  # fmt: skip
    with TestClient(create_app(settings)) as client:
        yield client
        client.app.dependency_overrides.clear()


@needs_postgres
def test_the_scope_catalogue_endpoint_reads_the_store_through_a_grant(pg_app):
    import asyncio

    from ada.comms.rest import auth as auth_module
    from ada.comms.rest.auth import User

    provider = f"test-{uuid.uuid4().hex[:6]}"
    member = User(sub="member-1", email="m@x.invalid", display_name="Member", groups=frozenset(), is_admin=False)

    async def setup(grant: bool):
        pool = await dbm.init_pool(POSTGRES_URL)
        try:
            await pool.execute("DELETE FROM provider_grants WHERE provider = $1", provider)
            if grant:
                await dbm.upsert_provider_grant(
                    pool, target_scope="shared", provider=provider, collection="col",
                    attributes=True, geometry=False, request=False, granted_by="t", note=None,
                )  # fmt: skip
            else:
                await rd.ingest_reference_data(
                    pool, scope="providers", provider=provider, chunks=_chunks(_bytes(provider, "col", REF_ROWS))
                )
        finally:
            await dbm.close_pool(pool)

    async def cleanup():
        pool = await dbm.init_pool(POSTGRES_URL)
        try:
            await pool.execute("DELETE FROM provider_grants WHERE provider = $1", provider)
            await pool.execute("DELETE FROM reference_sets WHERE provider = $1", provider)
            await pool.execute("DELETE FROM reference_overrides WHERE provider = $1", provider)
        finally:
            await dbm.close_pool(pool)

    asyncio.run(setup(False))
    try:
        c = pg_app
        c.app.dependency_overrides[auth_module.current_user] = lambda: member
        url = f"/api/scopes/shared/catalogue/sections?provider={provider}"
        assert c.get(url).json()["items"] == [], "no grant: the store's set is not the scope's"

        asyncio.run(setup(True))
        body = c.get(url).json()
        assert [i["ref"] for i in body["items"]] == ["/PLT/PL10", "/SEC/HEA200"]
        assert body["items"][1]["section"]["type"] == "I" and body["items"][1]["collection"] == "col"

        # Overrides are an admin's: a member is refused, the admin's shows in the member's catalogue.
        payload = {
            "provider": provider,
            "collection": "col",
            "kind": "material",
            "ref": "/STEEL-A",
            "props": {"sig_y": 3.5e8},
        }
        assert c.put("/api/admin/reference/overrides", json=payload).status_code == 403
        c.app.dependency_overrides.clear()  # the dev admin
        r = c.put("/api/admin/reference/overrides", json=payload)
        assert r.status_code == 200, r.text
        assert c.put("/api/admin/reference/overrides", json={**payload, "props": {"v": 2}}).status_code == 400
        c.app.dependency_overrides[auth_module.current_user] = lambda: member
        mats = c.get(f"/api/scopes/shared/catalogue/materials?provider={provider}").json()["items"]
        assert mats[0]["material"]["effective"]["sig_y"] == 3.5e8

        # And the client reads the same through the API (the test client as its transport).
        def transport(full: str) -> dict:
            return c.get(full.replace("http://testserver", "")).json()

        cat = scope_catalogue("shared", url="http://testserver", transport=transport)
        assert cat.section("/SEC/HEA200").metadata[CATALOGUE_KEY]["collection"] == "col"
        assert cat.material("/STEEL-A").model.sig_y == 3.5e8
        assert c.get("/api/scopes/shared/catalogue/sections?use=fitting").status_code == 400
    finally:
        asyncio.run(cleanup())
