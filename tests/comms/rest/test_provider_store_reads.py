"""Reads through provider grants -- phase 2 of docs/design/provider-scope-grants.md.

A scope an admin granted a provider dataset reads it from the provider store through ITS OWN routes
(the URL never names the store), and keeps reading its own legacy copy until the store holds the
dataset. Writes of provider data (publishes) are an admin's and land in the store.

No database here: the grant lookups (``db.list_provider_grants`` / ``db.grant_access`` /
``db.granted_datasets``) are faked, the app runs on local storage with auth disabled, and a member
is swapped in for the dev admin where the rule is about members.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-provider-reads-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.core.assets.fixture_provider.provider import (  # noqa: E402
    FIXTURE_PROVIDER_ID,
    FakeStore,
    publish_fixture,
)

from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.auth import User  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.db import attribute_index as ai  # noqa: E402
from ada.comms.rest.db.provider_grants import merge_access  # noqa: E402
from ada.comms.rest.routes import asset_exports as ex  # noqa: E402
from ada.comms.rest.routes import provider_grants as member_grants  # noqa: E402
from ada.comms.rest.routes import provider_store as ps  # noqa: E402
from ada.comms.rest.routes.attribute_search import (  # noqa: E402
    SearchOrigins,
    search_origins,
)
from ada.comms.rest.scope import Scope  # noqa: E402

COLLECTION = "fixture-a"
MEMBER = User(sub="member-1", email="m@x.invalid", display_name="Member", groups=frozenset(), is_admin=False)


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


@pytest.fixture(autouse=True)
def _fresh_caches():
    from ada.comms.rest.routes import assets as assets_routes

    assets_routes.clear_asset_spine_cache()
    assets_routes.clear_asset_attributes_cache()
    ps.clear_store_cache()
    yield
    assets_routes.clear_asset_spine_cache()
    assets_routes.clear_asset_attributes_cache()
    ps.clear_store_cache()


def _grants(monkeypatch, rows: list[dict]) -> None:
    """Fake the grant table with ``rows`` (``target_scope, provider, collection, attributes,
    geometry``), answered the way db/provider_grants.py answers."""

    def matching(target_scope=None, provider=None, collection=None):
        out = []
        for r in rows:
            full = {"request": False, "id": "g", "granted_at": None, "granted_by": None, "note": None, **r}
            if target_scope is not None and full["target_scope"] != target_scope:
                continue
            if provider is not None and full["provider"] != provider:
                continue
            if collection is not None and full["collection"] not in (None, collection.lower()):
                continue
            out.append(full)
        return out

    async def list_grants(pool, *, target_scope=None, provider=None, collection=None):
        return matching(target_scope, provider, collection)

    async def access(pool, *, target_scope, provider, collection):
        return merge_access(matching(target_scope, provider, collection))

    async def datasets(pool, *, target_scope, permission):
        return [(r["provider"], r["collection"]) for r in matching(target_scope) if r.get(permission)]

    monkeypatch.setattr(dbm, "list_provider_grants", list_grants)
    monkeypatch.setattr(dbm, "grant_access", access)
    monkeypatch.setattr(dbm, "granted_datasets", datasets)


def _publish_into(root, store: FakeStore) -> None:
    for key, data in store.blobs.items():
        dest = root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)


@pytest.fixture
def world(tmp_path):
    """The fixture published into the PROVIDER STORE only; the shared scope holds no copy."""
    store = FakeStore()
    revision = publish_fixture(store)
    _publish_into(tmp_path / "providers", store)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.db_pool = object()  # grants are faked; nothing reaches a database
        yield SimpleNamespace(client=client, revision=revision, root=tmp_path, store=store)
        client.app.dependency_overrides.clear()


def _as_member(client) -> None:
    client.app.dependency_overrides[auth_module.current_user] = lambda: MEMBER


GRANT = {"target_scope": "shared", "provider": FIXTURE_PROVIDER_ID, "collection": COLLECTION}


# --- viewer reads -------------------------------------------------------------------------------


def test_without_a_grant_the_scope_reads_its_own_copy(world, monkeypatch):
    _grants(monkeypatch, [])
    _as_member(world.client)
    r = world.client.get(f"/api/scopes/shared/assets/tree/published/{COLLECTION}")
    assert r.status_code == 404  # the shared scope has none; the store's copy is not its
    assert world.client.get("/api/scopes/shared/assets/collections").json() == {"collections": []}


def test_a_geometry_grant_reads_the_store_through_the_scopes_own_routes(world, monkeypatch):
    _grants(monkeypatch, [{**GRANT, "attributes": True, "geometry": True}])
    _as_member(world.client)
    c = world.client
    assert c.get("/api/scopes/shared/assets/collections").json() == {"collections": [COLLECTION]}
    r = c.get(f"/api/scopes/shared/assets/tree/published/{COLLECTION}")
    assert r.status_code == 200, r.text
    r = c.get(f"/api/scopes/shared/assets/tree/{FIXTURE_PROVIDER_ID}/{COLLECTION}")
    assert r.status_code == 200, r.text
    index = c.get(f"/api/scopes/shared/assets/index?collection={COLLECTION}").json()
    assert COLLECTION in index["collections"]

    # A mesh claim's url stays a KEY, and the scope's own blob route serves it from the store.
    claim = c.get(f"/api/scopes/shared/assets/delivery/{FIXTURE_PROVIDER_ID}/{COLLECTION}/pump-a").json()
    assert claim["kind"] == "mesh"
    blob = c.get(f"/api/scopes/shared/blobs/{claim['url']}")
    assert blob.status_code == 200 and blob.content == (world.root / "providers" / claim["url"]).read_bytes()


def test_an_attributes_only_grant_reads_no_geometry(world, monkeypatch):
    _grants(monkeypatch, [{**GRANT, "attributes": True, "geometry": False}])
    _as_member(world.client)
    c = world.client
    assert c.get(f"/api/scopes/shared/assets/tree/{FIXTURE_PROVIDER_ID}/{COLLECTION}").status_code == 404
    claim_key = next(k for k in world.store.blobs if k.endswith(".glb"))
    assert c.get(f"/api/scopes/shared/blobs/{claim_key}").status_code == 404


def test_a_grant_never_shadows_until_the_store_holds_the_dataset(tmp_path, monkeypatch):
    """The scope's own copy answers while the store is empty, grant or not."""
    store = FakeStore()
    publish_fixture(store)
    _publish_into(tmp_path / "shared", store)
    _grants(monkeypatch, [{**GRANT, "attributes": True, "geometry": True}])
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        c.app.dependency_overrides[auth_module.current_user] = lambda: MEMBER
        assert c.get(f"/api/scopes/shared/assets/tree/published/{COLLECTION}").status_code == 200


def test_a_granted_build_is_read_cached_and_answered_from_the_store(world, monkeypatch):
    """The manifest, the cached check and the job are the store's; the answer says so."""
    from ada.comms.rest.routes import assets as assets_routes

    _grants(monkeypatch, [{**GRANT, "attributes": True, "geometry": True}])
    _as_member(world.client)
    submitted = []

    async def submit(req, before_dispatch=None):
        submitted.append(req)
        return SimpleNamespace(job_id="build-1")

    rest = world.client.app.state.rest
    monkeypatch.setattr(rest.jobs, "require", lambda feature: None)
    monkeypatch.setattr(rest.jobs, "submit", submit)
    monkeypatch.setattr(assets_routes, "_db_pool", lambda request: object())
    body = {"provider": "published", "collection": COLLECTION, "node": "pump-b"}
    r = world.client.post("/api/scopes/shared/assets/build", json=body)
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["scope"] == "providers" and answer["job_id"] == "build-1"
    [req] = submitted
    assert req.scope == Scope.providers()

    # Once the summary is in the store, a repeat (or another granted scope) is a cache hit there.
    dest = world.root / "providers" / answer["derived_key"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b'{"ok":true}')
    again = world.client.post("/api/scopes/shared/assets/build", json={**body, "force": True}).json()
    assert again["cached"] is True and again["scope"] == "providers"  # a member cannot force over it


# --- publishes -----------------------------------------------------------------------------------


def test_a_provider_data_publish_is_an_admins_and_runs_in_the_store(world, monkeypatch):
    monkeypatch.setenv(ps.STORE_PROVIDERS_ENV, "vendor-x")
    staged = world.root / "providers" / "assets" / "_staging" / "stg-1" / "tree.json"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"{}")
    submitted = []

    async def submit(req, before_dispatch=None):
        submitted.append(req)
        return SimpleNamespace(job_id="pub-1")

    rest = world.client.app.state.rest
    monkeypatch.setattr(rest.jobs, "require", lambda feature: None)
    monkeypatch.setattr(rest.jobs, "submit", submit)
    body = {"provider": "vendor-x", "staging_id": "stg-1"}

    _as_member(world.client)
    r = world.client.post("/api/scopes/shared/assets/publish", json=body)
    assert r.status_code == 403 and not submitted

    world.client.app.dependency_overrides.clear()  # the dev admin again
    r = world.client.post("/api/scopes/user:me/assets/publish", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "providers"
    [req] = submitted
    assert req.scope == Scope.providers()
    assert req.conversion_options["staged"] == {"tree.json": "assets/_staging/stg-1/tree.json"}


def test_a_user_upload_publish_stays_in_the_users_scope(world, monkeypatch):
    monkeypatch.setenv(ps.STORE_PROVIDERS_ENV, "vendor-x")
    staged = world.root / "shared" / "assets" / "_staging" / "stg-2" / "model.ifc"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"x")
    submitted = []

    async def submit(req, before_dispatch=None):
        submitted.append(req)
        return SimpleNamespace(job_id="pub-2")

    rest = world.client.app.state.rest
    monkeypatch.setattr(rest.jobs, "require", lambda feature: None)
    monkeypatch.setattr(rest.jobs, "submit", submit)
    _as_member(world.client)
    r = world.client.post("/api/scopes/shared/assets/publish", json={"provider": "published", "staging_id": "stg-2"})
    assert r.status_code == 200, r.text
    assert "scope" not in r.json()
    assert submitted[0].scope == Scope.shared()


# --- the member-facing grant routes ---------------------------------------------------------------


def test_a_scope_lists_what_it_may_read(world, monkeypatch):
    _grants(
        monkeypatch,
        [
            {**GRANT, "attributes": True, "geometry": False},
            {"target_scope": "shared", "provider": "p2", "collection": None, "attributes": True, "geometry": True},
            {
                "target_scope": "shared",
                "provider": "p3",
                "collection": "c",
                "attributes": False,
                "geometry": False,
                "request": True,
            },
            {"target_scope": "projects/x", "provider": "p2", "collection": "c", "attributes": True, "geometry": True},
        ],
    )
    _as_member(world.client)
    grants = world.client.get("/api/scopes/shared/provider-grants").json()["grants"]
    assert grants == [
        {"provider": FIXTURE_PROVIDER_ID, "collection": COLLECTION, "attributes": True, "geometry": False},
        {"provider": "p2", "collection": None, "attributes": True, "geometry": True},
    ]
    # Every scope a provider's collections are granted to: for workers and admins only.
    assert world.client.get("/api/provider-grants/datasets?provider=p2").status_code == 403
    world.client.app.dependency_overrides.clear()
    doc = world.client.get("/api/provider-grants/datasets?provider=p2").json()
    assert doc == {
        "provider": "p2",
        "datasets": [
            {"collection": None, "target_scopes": [{"scope": "shared", "attributes": True, "geometry": True}]},
            {"collection": "c", "target_scopes": [{"scope": "projects/x", "attributes": True, "geometry": True}]},
        ],
    }


def test_the_grant_rows_drop_request_only_grants():
    rows = [{"provider": "p", "collection": "a", "attributes": False, "geometry": False, "request": True}]
    assert member_grants.scope_grant_rows(rows) == []
    assert member_grants.dataset_targets([{**rows[0], "target_scope": "shared"}]) == []


# --- search ---------------------------------------------------------------------------------------


def _part(scope, provider, collection, n="0" * 16):
    return {
        "scope": scope,
        "provider": provider,
        "collection": collection,
        "revision": "1",
        "rows": 3,
        "source_etag": None,
        "ingested_at": None,
        "partition_name": f"attr_p_{provider.replace('-', '_')}_{n}",
        "part_key": f"{scope}|{provider}|{collection}",
        "keys": ["K"],
        "high_card_keys": [],
    }


def test_a_store_partition_shadows_the_scopes_own_copy_and_says_granted():
    own = {**_part("shared", "p", "a"), "shared": False, "admin": False}
    other = {**_part("shared", "p", "b", "1" * 16), "shared": False, "admin": False}
    share = {**_part("projects/x", "p", "a", "2" * 16), "shared": True, "admin": False}
    store = _part("providers", "p", "a", "3" * 16)
    out = ai.merge_granted_partitions([own, other, share], [store], "shared")
    keys = {p["part_key"] for p in out}
    assert own["part_key"] not in keys and other["part_key"] in keys and share["part_key"] in keys
    [granted] = [p for p in out if p.get("granted")]
    assert granted["scope"] == "providers" and granted["shared"] is True
    assert ai._label(granted) == "p/a@providers"
    assert ai._origin(granted, "u1") == {"scope": "providers", "scope_wire": None, "shared": True, "granted": True}


class _Conn:
    """Answers ``_partitions``' two queries: the scope's own partitions, then the store's."""

    def __init__(self, own: list[dict], store: list[dict]) -> None:
        self.own, self.store, self.calls = own, store, []

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        if "unnest" in sql:
            return self.store
        return [{**p, "by_share": False} for p in self.own]


def test_partitions_reads_the_granted_store_partitions_only_when_granted():
    own = [_part("shared", "p", "a"), _part("shared", "q", "z", "4" * 16)]
    store = [_part("providers", "p", "a", "3" * 16)]

    conn = _Conn(own, store)
    plain = asyncio.run(ai._partitions(conn, "shared"))
    assert [p["scope"] for p in plain] == ["shared", "shared"] and len(conn.calls) == 1

    conn = _Conn(own, store)
    parts = asyncio.run(ai._partitions(conn, "shared", granted=[("p", "A"), ("q", None)]))
    assert [(p["scope"], p["provider"], p["collection"]) for p in parts] == [
        ("providers", "p", "a"),
        ("shared", "q", "z"),
    ]
    _, args = conn.calls[1]
    assert args[0] == "providers" and args[1] == ["p", "q"] and args[2] == ["a", None]

    # The store itself (an admin searching it) is never granted into.
    conn = _Conn(own, store)
    asyncio.run(ai._partitions(conn, "providers", granted=[("p", "a")]))
    assert len(conn.calls) == 1


def test_search_origins_carry_the_scopes_attribute_grants(monkeypatch):
    async def none(*a, **kw):
        return []

    monkeypatch.setattr(dbm, "readable_share_origins", none)
    _grants(
        monkeypatch,
        [
            {"target_scope": "shared", "provider": "p", "collection": "a", "attributes": True, "geometry": False},
            {"target_scope": "shared", "provider": "q", "collection": None, "attributes": False, "geometry": True},
        ],
    )
    origins = asyncio.run(search_origins(object(), Scope.shared(), MEMBER))
    assert origins.granted == (("p", "a"),)
    assert origins.grants("p", "A") and not origins.grants("q", "a") and not origins.grants("p", "b")
    assert "providers" not in origins.readable("shared")
    store = asyncio.run(search_origins(object(), Scope.providers(), MEMBER))
    assert store.granted == ()


# --- export ---------------------------------------------------------------------------------------


def _hit(provider="web-x", collection="alpha", scope="providers"):
    return {"provider": provider, "collection": collection, "node_id": "n1", "scope": scope}


def test_a_store_hit_is_exported_only_with_an_attributes_grant():
    origins = SearchOrigins(granted=(("web-x", "alpha"),))
    ex.refuse_unreadable_hits([_hit()], "shared", origins.readable("shared"), origins)
    with pytest.raises(ex.ExportRefused) as err:
        ex.refuse_unreadable_hits([_hit(collection="beta")], "shared", origins.readable("shared"), origins)
    assert err.value.status == 403 and "web-x/beta" in err.value.detail
    with pytest.raises(ex.ExportRefused):
        ex.refuse_unreadable_hits([_hit()], "shared", frozenset({"shared"}))  # no origins: no grants
    # A user-to-user share is unchanged.
    with pytest.raises(ex.ExportRefused) as err:
        ex.refuse_unreadable_hits([_hit(scope="projects/y")], "shared", origins.readable("shared"), origins)
    assert "projects/y" in err.value.detail


def test_design_geometry_is_the_stores_with_a_geometry_grant_else_not_granted(monkeypatch):
    geometry = ex.GEOMETRY_PROVIDER
    ctx = SimpleNamespace(storage=SimpleNamespace(list_child_names=None))

    async def holds(ctx, collection):
        return collection == "alpha"

    monkeypatch.setattr(ex, "store_holds_geometry", holds)
    store, shared, other = Scope.providers(), Scope.shared(), Scope.from_prefix("projects/y")

    def where(origin, collection="alpha"):
        return asyncio.run(ex.geometry_read_scope(ctx, object(), shared, origin, geometry, collection))

    _grants(monkeypatch, [])
    assert where(store) == (store, ex.NOT_GRANTED)  # a store hit without the geometry grant
    assert where(shared) == (shared, None)  # legacy: the hit's own scope
    assert where(other) == (other, None)

    _grants(
        monkeypatch,
        [
            {
                "target_scope": "shared",
                "provider": geometry,
                "collection": "alpha",
                "attributes": False,
                "geometry": True,
            }
        ],
    )
    assert where(store) == (store, None)
    assert where(shared) == (store, None)  # a web3d hit of its own: the design geometry is the store's
    assert where(shared, "beta") == (shared, None)  # not granted there: its own copy
    # An admin exporting from the store reads it directly.
    assert asyncio.run(ex.geometry_read_scope(ctx, object(), store, store, geometry, "beta")) == (store, None)


def test_a_not_granted_batch_writes_no_file_and_says_why():
    from ada.comms.rest.formats.export_search import (
        ExportSearchAssetHandler,
        _geometry_of,
    )

    opts = {
        "origin_scope": "providers",
        "geometry_refused": ex.NOT_GRANTED,
        "geometry_refused_message": "no grant",
        "target_scope": {"kind": "shared"},
    }
    hits = [{"file": "out/a.ifc", "node_id": "n1"}, {"file": "out/b.ifc", "node_id": "n2"}]
    ctx = SimpleNamespace(storage=None, scope=Scope.shared(), db_pool=None)
    summary = asyncio.run(ExportSearchAssetHandler()._export(None, ctx, opts, hits, "web-x", "alpha", Scope.shared()))
    assert summary["counts"] == {"written": 0, "no_geometry": 2, "failed": 0}
    assert {f["reason"] for f in summary["files"]} == {ex.NOT_GRANTED}
    assert _geometry_of({"geometry_scope": "providers"}, Scope.shared()) == Scope.providers()
    assert _geometry_of({}, Scope.shared()) == Scope.shared()


def test_a_member_is_told_up_front_that_geometry_requests_are_an_admins():
    spec = {"asset_provider_id": ex.GEOMETRY_PROVIDER}
    req = SimpleNamespace(plugin_id="design-plugin", spec=spec, label="Request")
    allowed, why = asyncio.run(ex.request_permission(None, MEMBER, req))
    assert allowed is False and "administrators" in why


def test_preflight_counts_store_hits_without_a_geometry_grant_as_not_granted(monkeypatch):
    async def none(*a, **kw):
        return []

    async def no_sweep(pool, provider):
        return None

    async def no_request(ctx):
        return None

    monkeypatch.setattr(dbm, "readable_share_origins", none)
    monkeypatch.setattr(dbm, "running_asset_sweep", no_sweep)
    monkeypatch.setattr(ex.greq, "geometry_node_request", no_request)
    _grants(
        monkeypatch,
        [{"target_scope": "shared", "provider": "web-x", "collection": "alpha", "attributes": True, "geometry": False}],
    )
    hit = {**_hit(), "full_name": "/A", "attrs": {}}
    body = {"query": {}, "hits": [hit]}
    pre = asyncio.run(
        ex.export_preflight(SimpleNamespace(storage=None), object(), scope_obj=Scope.shared(), body=body, user=MEMBER)
    )
    assert pre[ex.NOT_GRANTED] == 1 and pre["ready"] == 0
    [entry] = pre["collections"]
    assert entry["scope"] == "providers" and entry["granted"] is True and entry[ex.NOT_GRANTED] == 1


def test_store_provider_ids_follow_the_live_specs_and_the_env(monkeypatch):
    async def specs(ctx):
        return {"a": {"asset_provider_id": "web-x"}, "b": {"slug": "other"}}

    monkeypatch.setattr("ada.comms.rest.routes.plugins.online_plugin_specs", specs)
    monkeypatch.setenv(ps.STORE_PROVIDERS_ENV, "cli-only")
    assert asyncio.run(ps.store_provider_ids(None)) == {"web-x", "cli-only"}
    assert asyncio.run(ps.is_store_provider(None, "web-x")) is True
    assert asyncio.run(ps.is_store_provider(None, "published")) is False
    assert ps.granted_covers([("p", None)], "p", "Any") and not ps.granted_covers([("p", "a")], "p", "b")


def test_blob_reads_outside_a_dataset_never_ask_the_grants(world, monkeypatch):
    async def boom(*a, **kw):
        raise AssertionError("asked")

    monkeypatch.setattr(dbm, "list_provider_grants", boom)
    (world.root / "shared").mkdir(exist_ok=True)
    (world.root / "shared" / "note.txt").write_text("hi")
    _as_member(world.client)
    assert world.client.get("/api/scopes/shared/blobs/note.txt").text == "hi"
