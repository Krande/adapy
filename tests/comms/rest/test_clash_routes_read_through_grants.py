"""The clash and selection-export ROUTES read a group's members through the scope's provider grants.

A provider dataset published to the PROVIDER STORE is read by a granted scope from the store: the
viewer's asset routes do it, and so does the job (``test_clash_provider_store_members.py``). The
routes that price a check or an export resolve each node member's manifest BEFORE enqueueing
anything; reading the scope alone, they refused a member the viewer had just loaded with
"no manifest at assets/<collection>/<subject>/<revision>/asset.json". Pinned here:

* a granted scope resolves a group of store members (revision, provider, content token);
* a single published node resolves its source key and a content token from the store;
* the geometry-plan bridge and the geometry-state token read the store listing;
* an ungranted scope is told the member is provider data it holds no grant on;
* without a database (no grants at all) the scope is read alone, as before.
"""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-route-grants-"))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from ada.assets.keys import asset_key  # noqa: E402
from ada.clash.group import normalise_group  # noqa: E402
from ada.clash.options import ClashOptions  # noqa: E402
from ada.comms.rest.app import (  # noqa: E402,F401  (import order, as elsewhere)
    create_app,
)
from ada.comms.rest.routes import clash_check as cc  # noqa: E402
from ada.comms.rest.routes import provider_store as ps  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402

from .test_clash_provider_store_members import (  # noqa: E402
    COLLECTION,
    GEOMETRY_GRANT,
    NODES,
    PROVIDER,
    REV,
    SCOPE,
    _grants,
    _node,
    _publish,
)


@pytest.fixture(autouse=True)
def _caches():
    ps.clear_store_cache()
    yield
    ps.clear_store_cache()


@pytest.fixture
def world(tmp_path):
    from obstore.store import LocalStore

    from ada.comms.rest.storage import Storage

    for subject, lines in NODES.items():
        _publish(tmp_path / Scope.providers().prefix(), subject, lines)
    (tmp_path / SCOPE.prefix()).mkdir(parents=True)
    return Storage(LocalStore(str(tmp_path)), prefix="")


def _reads(storage, pool=object()):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=pool)))
    return cc._reads(SimpleNamespace(storage=storage), SCOPE, request)


def _group() -> dict:
    return normalise_group({"name": "g", "members": [{"target": _node(s), "element": None, "path": []} for s in NODES]})


@pytest.mark.asyncio
async def test_a_granted_scope_resolves_a_group_of_provider_store_members(world, monkeypatch):
    _grants(monkeypatch, [GEOMETRY_GRANT])
    resolved, content = await cc._resolve_group(_reads(world), _group())

    assert [m["target"]["revision"] for m in resolved["members"]] == [REV, REV]
    assert {m["target"]["provider"] for m in resolved["members"]} == {PROVIDER}
    assert content == {f"node:{COLLECTION}/{s}": REV for s in NODES}


@pytest.mark.asyncio
async def test_a_granted_scope_resolves_a_node_source_and_its_token_from_the_store(world, monkeypatch):
    _grants(monkeypatch, [GEOMETRY_GRANT])
    reads = _reads(world)
    source_key, manifest = await cc._asset_source_key(reads, COLLECTION, "zone-a", None)

    assert manifest.revision == REV
    assert source_key == asset_key(COLLECTION, "zone-a", REV, "lines.txt")
    # The token is the store copy's e_tag, not the key-only fallback a missing head gives.
    assert await cc._source_content_token(reads, source_key) != await cc._source_content_token(
        _reads(world, pool=None), source_key
    )


@pytest.mark.asyncio
async def test_the_geometry_state_and_plan_bridge_read_the_store(world, monkeypatch):
    import asyncio

    _grants(monkeypatch, [GEOMETRY_GRANT])
    reads = _reads(world)
    options = ClashOptions(geometry_provider="other")
    granted = await cc._geometry_options_token(reads, options, [COLLECTION])
    alone = await cc._geometry_options_token(_reads(world, pool=None), options, [COLLECTION])
    assert granted != alone  # the store's listing moved the token; an empty scope listing did not

    bridge = cc._ScopeStorageBridge(reads, asyncio.get_running_loop())
    manifest_key = asset_key(COLLECTION, "zone-a", REV, "asset.json")
    keys = await asyncio.to_thread(bridge.list_keys, f"assets/{COLLECTION}/")
    assert manifest_key in keys
    many = await asyncio.to_thread(bridge.get_many, [manifest_key, "assets/nope/x/y/asset.json"])
    assert many[manifest_key] and many["assets/nope/x/y/asset.json"] is None


@pytest.mark.asyncio
async def test_an_ungranted_scope_is_told_the_member_is_provider_data(world, monkeypatch):
    _grants(monkeypatch, [{**GEOMETRY_GRANT, "target_scope": "users/someone-else"}])
    with pytest.raises(HTTPException) as err:
        await cc._resolve_group(_reads(world), _group())
    assert err.value.status_code == 404
    assert "provider data" in err.value.detail and "no geometry grant" in err.value.detail


@pytest.mark.asyncio
async def test_without_a_database_the_scope_is_read_alone(world):
    with pytest.raises(HTTPException) as err:
        await cc._resolve_group(_reads(world, pool=None), _group())
    assert err.value.status_code == 404
    assert err.value.detail == f"no manifest at {asset_key(COLLECTION, 'zone-a', REV, 'asset.json')}"
