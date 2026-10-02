"""``GET /assets/geometry/{collection}`` -- the server-computed geometry roll-up.

A synthetic collection in a local-storage scope, shaped like a real mixed one:

* a tree-only publish (delivery ``none``) of the collection index: proj > site-a/b/c > area > unit;
* a mesh provider publishing geometry at two sites, each with its OWN spine rooted at the site
  (parent null there) -- one of them carrying a deeper ``deck`` row the index does not hold;
* a member provider publishing a build at that deeper deck, which only site-a's spine places;
* a tree-only per-node publish at site-c;
* a mesh publish at a site no tree mentions (``unplaced``).

Provider ids are neutral on purpose (vocabulary gate).
"""

import os
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-assets-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.assets.keys import asset_key  # noqa: E402
from ada.assets.manifest import AssetManifest, BuildSpec  # noqa: E402
from ada.assets.projection import build_hierarchy  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402

from .test_asset_routes import _settings  # noqa: E402

COLLECTION = "plant"
TREE = "tree-maker"
MESH = "mesh-only"
MEMBER = "member-reader"


@pytest.fixture(autouse=True)
def _fresh_route_caches():
    from ada.comms.rest.routes import assets as assets_routes

    assets_routes.clear_asset_spine_cache()
    yield
    assets_routes.clear_asset_spine_cache()


class _Scope:
    def __init__(self, root) -> None:
        self.root = root / "users" / "local-dev"

    def put(self, key: str, data: bytes) -> None:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def publish(self, subject, revision, provider, delivery="none", nodes=None, root=None) -> None:
        """One subject-revision: its spine (when ``nodes``) and its manifest, written last."""
        if nodes is not None:
            slice_ = build_hierarchy(
                provider=provider,
                collection=COLLECTION,
                produced_at="2026-09-01T10:00:00Z",
                nodes=[{"leaf": False, "delivery": "", **n} for n in nodes],
                root=root,
                depth=99,
            )
            self.put(asset_key(COLLECTION, subject, revision, "hierarchy.json"), slice_.to_json())
        manifest = AssetManifest(
            provider=provider,
            collection=COLLECTION,
            subject=subject,
            revision=revision,
            node=None if subject == COLLECTION else subject,
            produced_at="2026-09-01T10:00:00Z",
            published_at="2026-09-01T10:00:00Z",
            delivery=delivery,
            build=BuildSpec(capability="member-build") if delivery == "build" else None,
        )
        self.put(asset_key(COLLECTION, subject, revision, "asset.json"), manifest.to_json())


def _n(node, parent):
    return {"id": node, "parent": parent, "label": node, "kind": "x"}


R1, R2, R3, R4 = "20260901T100000Z", "20260902T100000Z", "20260903T100000Z", "20260904T100000Z"


def _seed(scope: _Scope) -> None:
    scope.publish(
        COLLECTION,
        R1,
        TREE,
        nodes=[
            _n("proj", None),
            _n("site-a", "proj"),
            _n("site-b", "proj"),
            _n("site-c", "proj"),
            _n("area-a1", "site-a"),
            _n("unit-a1x", "area-a1"),
            _n("unit-a1y", "area-a1"),
            _n("area-b1", "site-b"),
            _n("unit-b1x", "area-b1"),
            _n("area-c1", "site-c"),
        ],
    )
    # Mesh geometry per site, each spine rooted at the site: the site row has NO parent in it.
    scope.publish(
        "site-a",
        R2,
        MESH,
        delivery="mesh",
        root="site-a",
        nodes=[
            _n("site-a", None),
            _n("area-a1", "site-a"),
            _n("unit-a1x", "area-a1"),
            _n("deck-a1x-1", "unit-a1x"),
            _n("unit-a1y", "area-a1"),
        ],
    )
    scope.publish(
        "site-b", R2, MESH, delivery="mesh", root="site-b", nodes=[_n("site-b", None), _n("area-b1", "site-b")]
    )
    # A site no published tree mentions.
    scope.publish("ghost-site", R2, MESH, delivery="mesh", root="ghost-site", nodes=[_n("ghost-site", None)])
    # A member build at a node only site-a's spine holds.
    scope.publish("deck-a1x-1", R3, MEMBER, delivery="build")
    # Tree-only per-node publish.
    scope.publish(
        "site-c",
        R4,
        TREE,
        root="site-c",
        nodes=[_n("site-c", None), _n("area-c1", "site-c"), _n("unit-c1x", "area-c1")],
    )


@pytest.fixture
def scope_and_client(tmp_path):
    scope = _Scope(tmp_path)
    _seed(scope)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield scope, client


def _get(client, collection=COLLECTION):
    r = client.get(f"/api/scopes/user:me/assets/geometry/{collection}")
    assert r.status_code == 200, r.text
    return r.json()


def test_rollup_places_every_subject_in_the_collection_tree(scope_and_client):
    _, client = scope_and_client
    body = _get(client)
    assert body["schema"] == "ada.assets/geometry@1"
    assert body["collection"] == COLLECTION
    assert body["index_token"]

    mesh = body["providers"][MESH]
    assert mesh["here"] == ["ghost-site", "site-a", "site-b"]
    assert mesh["below"] == ["proj"]
    assert mesh["unplaced"] == ["ghost-site"]

    member = body["providers"][MEMBER]
    assert member["here"] == ["deck-a1x-1"]
    # Placed through site-a's spine, whose own top has no parent: the index places site-a.
    assert member["below"] == ["area-a1", "proj", "site-a", "unit-a1x"]
    assert member["unplaced"] == []

    # Tree-only publishes are not geometry, wherever they sit.
    assert TREE not in body["providers"]
    assert body["any"]["here"] == ["deck-a1x-1", "ghost-site", "site-a", "site-b"]
    assert body["any"]["below"] == ["area-a1", "proj", "site-a", "unit-a1x"]
    assert body["any"]["unplaced"] == ["ghost-site"]


def test_rollup_reads_no_spine_when_the_index_places_everything(tmp_path):
    scope = _Scope(tmp_path)
    _seed(scope)
    # Drop the deeper build and the unplaced site: every geometry subject is in the index.
    for sub in ("deck-a1x-1", "ghost-site"):
        for f in (scope.root / "assets" / COLLECTION / sub).rglob("*"):
            if f.is_file():
                f.unlink()
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        body = _get(client)
    assert body["any"]["here"] == ["site-a", "site-b"]
    assert body["any"]["below"] == ["proj"]
    assert body["stats"]["documents"] == 1  # the collection index only


def test_rollup_is_cached_per_listing_and_moves_on_a_new_publish(scope_and_client):
    scope, client = scope_and_client
    first = _get(client)
    assert first["cached"] is False
    again = _get(client)
    assert again["cached"] is True
    assert again["index_token"] == first["index_token"]
    assert {k: v for k, v in again.items() if k != "cached"} == {k: v for k, v in first.items() if k != "cached"}

    cache_dir = scope.root / "_derived" / "assets" / "_geometry" / COLLECTION
    assert [p.name for p in cache_dir.iterdir()] == [f"{first['index_token']}.json"]

    # A new publish moves the token: recomputed, and the old entry is gone.
    scope.publish("unit-b1x", "20260905T100000Z", MEMBER, delivery="build")
    moved = _get(client)
    assert moved["cached"] is False
    assert moved["index_token"] != first["index_token"]
    assert moved["providers"][MEMBER]["here"] == ["deck-a1x-1", "unit-b1x"]
    assert "area-b1" in moved["providers"][MEMBER]["below"]
    assert [p.name for p in cache_dir.iterdir()] == [f"{moved['index_token']}.json"]


def test_rollup_cache_is_not_an_orphan_of_any_source(scope_and_client):
    _, client = scope_and_client
    _get(client)
    r = client.get("/api/scopes/user:me/assets/sources")
    assert r.status_code == 200, r.text
    assert r.json()["orphans"] == []
    files = client.get("/api/scopes/user:me/assets/files").json()
    assert files["unrecognised"] == []
    assert any(f.get("kind") == "geometry-rollup" for f in files["files"])


def test_rollup_newest_content_per_provider_speaks(scope_and_client):
    """A newer tree-only revision by the SAME provider does not take its geometry away -- the
    browser's content pick skips a `none` delivery -- but a newer content revision does decide."""
    scope, client = scope_and_client
    scope.publish("site-b", "20260906T100000Z", MESH, root="site-b", nodes=[_n("site-b", None)])
    assert "site-b" in _get(client)["providers"][MESH]["here"]


def test_rollup_unknown_collection_is_404(scope_and_client):
    _, client = scope_and_client
    r = client.get("/api/scopes/user:me/assets/geometry/nowhere")
    assert r.status_code == 404


def test_rollup_on_a_30k_row_collection(tmp_path):
    """Timing on a realistic shape: 200 sites in the index, each with its own ~150-row spine
    (30k rows), mesh at 160 sites and a member build deep inside five of them -- so every site
    spine is read."""
    scope = _Scope(tmp_path)
    sites = [f"site-{i:03d}" for i in range(200)]
    scope.publish(COLLECTION, R1, TREE, nodes=[_n("proj", None), *(_n(s, "proj") for s in sites)])
    builds = []
    for i, site in enumerate(sites):
        rows = [_n(site, None)]
        for a in range(5):
            area = f"{site}-a{a}"
            rows.append(_n(area, site))
            for u in range(29):
                rows.append(_n(f"{area}-u{u}", area))
        scope.publish(
            site, R2, MESH if i < 160 else TREE, delivery="mesh" if i < 160 else "none", root=site, nodes=rows
        )
        if i % 40 == 39:  # the last site among them: every spine is read before it is placed
            builds.append(f"{site}-a3-u17")
    for node in builds:
        scope.publish(node, R3, MEMBER, delivery="build")

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        t0 = time.perf_counter()
        body = _get(client)
        cold = time.perf_counter() - t0
        t0 = time.perf_counter()
        assert _get(client)["cached"] is True
        warm = time.perf_counter() - t0
    print(
        f"\ngeometry roll-up, {body['stats']['rows']} rows in {body['stats']['documents']} documents: "
        f"cold {cold * 1000:.0f} ms (computed {body['stats']['computed_ms']} ms), warm {warm * 1000:.0f} ms"
    )
    assert body["stats"]["rows"] >= 30_000
    assert len(body["providers"][MESH]["here"]) == 160
    assert body["providers"][MEMBER]["here"] == sorted(builds)
    assert body["providers"][MEMBER]["unplaced"] == []
    assert f"{builds[0].rsplit('-u', 1)[0]}" in body["providers"][MEMBER]["below"]
    assert cold < 15.0
    assert warm < 2.0
