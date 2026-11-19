"""A clash job over a saved GROUP whose members are provider-published nodes in the PROVIDER STORE.

The API routes read a provider dataset from the store when an admin granted the scope that dataset
and the store holds it (``routes/provider_store.py``). A worker job reads through a synchronous
facade bound to the job's scope; it must resolve its reads the same way, or a group of granted
provider nodes rebuilds nothing ("no published manifest") while the viewer shows every member.
Pinned here, on the worker's clash path (``formats/clash_isolation.py``), both the killable-child
facade and the in-thread one:

* a granted scope's group check reads its members from the store, and writes its result in the scope;
* an ungranted scope fails -- saying the member is provider data it holds no grant on;
* a contacts rebuild of a node part reads through the grant too;
* a job of the provider store itself is unchanged.

The grant table is faked the way ``test_provider_store_reads.py`` fakes it; nothing reaches a database.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-provider-members-"))

import pytest  # noqa: E402

from ada.assets.keys import asset_key  # noqa: E402
from ada.assets.manifest import (  # noqa: E402
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)
from ada.assets.projection import build_hierarchy  # noqa: E402
from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest import subprocess_convert  # noqa: E402
from ada.comms.rest.app import (  # noqa: E402,F401  (adacpp before numpy's BLAS, as elsewhere)
    create_app,
)
from ada.comms.rest.db.provider_grants import merge_access  # noqa: E402
from ada.comms.rest.formats import clash_check_group, clash_isolation  # noqa: E402
from ada.comms.rest.routes import provider_store as ps  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.subprocess_convert import IsolatedResult  # noqa: E402

PROVIDER = "line-reader"
COLLECTION = "fixture-yard"
REV = "20260901T000000Z"
SCOPE = Scope.user("member-1")
OPTIONS = {"include_plate_joints": False}
# Two nodes, one girder each; the second starts on the first: one joint between the two members.
NODES = {"zone-a": b"girder-a 0 0 0 2 0 0", "zone-b": b"girder-b 1 0 0 1 2 0"}


class _LinesConcepts:
    """The provider's reader: one beam per line of its published source."""

    def concepts(self, options, *, storage, scope=None, node=None):
        import ada

        part = ada.Part(node or "lines")
        for line in storage.get_bytes(options["source_key"]).decode().splitlines():
            name, *xyz = line.split()
            x0, y0, z0, x1, y1, z1 = (float(v) for v in xyz)
            part.add_beam(ada.Beam(name, (x0, y0, z0), (x1, y1, z1), "IPE200"))
        return part


def _write(root: pathlib.Path, key: str, data: bytes) -> None:
    dest = root / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _publish(root: pathlib.Path, subject: str, lines: bytes) -> None:
    spine = build_hierarchy(
        provider=PROVIDER,
        collection=COLLECTION,
        produced_at=REV,
        nodes=[{"id": subject, "parent": None, "label": subject, "kind": "item", "leaf": True}],
    )
    _write(root, asset_key(COLLECTION, subject, REV, HIERARCHY_FILENAME), spine.to_json())
    src = asset_key(COLLECTION, subject, REV, "lines.txt")
    _write(root, src, lines)
    manifest = AssetManifest(
        provider=PROVIDER,
        collection=COLLECTION,
        subject=subject,
        revision=REV,
        node=subject,
        produced_at=REV,
        published_at=REV,
        delivery="build",
        build=BuildSpec(capability="build-lines", options={"source_key": src}),
        artefacts=(
            ArtefactEntry(role="hierarchy", file=HIERARCHY_FILENAME, sha256="", size=1),
            ArtefactEntry(role="source", key=src, sha256="", size=len(lines)),
        ),
    )
    _write(root, asset_key(COLLECTION, subject, REV, MANIFEST_FILENAME), manifest.to_json())


def _node(subject: str) -> dict:
    return {
        "kind": "node",
        "provider": PROVIDER,
        "collection": COLLECTION,
        "subject": subject,
        "revision": REV,
        "node": None,
    }


@pytest.fixture(autouse=True)
def _reader_and_caches():
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts

    register_asset_concepts(PROVIDER, lambda: _LinesConcepts())
    ps.clear_store_cache()
    yield
    clear_asset_concepts()
    ps.clear_store_cache()


@pytest.fixture
def world(tmp_path):
    """Both nodes published to the PROVIDER STORE only; the member's own scope holds no copy."""
    from obstore.store import LocalStore

    from ada.comms.rest.storage import Storage

    for subject, lines in NODES.items():
        _publish(tmp_path / Scope.providers().prefix(), subject, lines)
    (tmp_path / SCOPE.prefix()).mkdir(parents=True)
    return Storage(LocalStore(str(tmp_path)), prefix="")


def _grants(monkeypatch, rows: list[dict]) -> None:
    """Fake the grant table, answered the way db/provider_grants.py answers."""

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

    monkeypatch.setattr(dbm, "list_provider_grants", list_grants)
    monkeypatch.setattr(dbm, "grant_access", access)


GEOMETRY_GRANT = {
    "target_scope": SCOPE.prefix(),
    "provider": PROVIDER,
    "collection": COLLECTION,
    "attributes": True,
    "geometry": True,
}


# ── the group check, on both facades ──────────────────────────────────


class _Queue:
    enabled = False

    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def update(self, job_id, **kw):
        self.updates.append(kw)


class _ThreadRunner:
    """Stands in for ``run_isolated``: runs the work in a thread against a child whose ``call``
    reaches the parent's real ``serve`` -- the killable path's storage, without a fork."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, fn, **kw):
        self.calls.append(kw)
        loop = asyncio.get_running_loop()
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix="fake-isolated-"))
        serve = kw["serve"]

        class _Child:
            def __init__(self):
                self.work_dir = work_dir

            def progress(self, stage, frac):
                pass

            def call(self, op, **args):
                try:
                    return asyncio.run_coroutine_threadsafe(serve(op, args), loop).result()
                except Exception as exc:  # the RPC re-raises by type name, as the real channel does
                    raise subprocess_convert._RPC_EXC_TYPES.get(type(exc).__name__, RuntimeError)(str(exc)) from None

        try:
            await loop.run_in_executor(None, fn, _Child())
        except Exception as exc:  # noqa: BLE001 - mirror the child's error.json
            return IsolatedResult(
                work_dir=None,
                error=f"{type(exc).__name__}: {exc}",
                traceback="tb",
                exit_code=2,
                signal_name=None,
                samples=[],
                final_metrics={},
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        return IsolatedResult(
            work_dir=work_dir, error=None, traceback=None, exit_code=0, signal_name=None, samples=[], final_metrics={}
        )


@pytest.fixture(params=["forked", "thread"])
def facade(request, monkeypatch):
    """Run the clash job on the killable-child facade (served by the parent) or the in-thread one."""
    forked = request.param == "forked"
    monkeypatch.setattr(clash_isolation, "_fork_available", lambda: forked)
    if forked:
        monkeypatch.setattr(subprocess_convert, "run_isolated", _ThreadRunner())

    async def _not_cancelled(_pool, _job_id):
        return False

    async def _no_timeout(_pool):
        return None

    async def _audit(*_a, **_k):
        return None

    monkeypatch.setattr(dbm, "audit_is_cancelled", _not_cancelled)
    monkeypatch.setattr(clash_check_group, "read_clash_timeout_s", _no_timeout)
    monkeypatch.setattr(clash_check_group, "_audit_done", _audit)
    monkeypatch.setattr(clash_isolation, "_audit_done", _audit)
    return request.param


def _group_job():
    from ada.clash.group import normalise_group
    from ada.comms.rest.queue import Job

    group = normalise_group(
        {"name": "g", "members": [{"target": _node(s), "element": None, "path": []} for s in NODES]}
    )
    return Job(
        job_id="job-group",
        source_key="group:tok",
        derived_key="_derived/clash/group/tok/o/result.json",
        status="queued",
        target_format="clash_check_group",
        conversion_options={"group": group, "group_token": "tok", "options": OPTIONS},
    )


async def _run_group_check(storage, scope) -> tuple[object, _Queue]:
    from ada.comms.rest.formats.registry import JobContext

    job = _group_job()
    queue = _Queue()
    ctx = JobContext(
        scope=scope, storage=storage, queue=queue, db_pool=object(), started_at=time.monotonic(), src_path=None
    )
    await clash_check_group.ClashCheckGroupHandler().run(job, ctx)
    return job, queue


@pytest.mark.asyncio
async def test_a_granted_scope_checks_a_group_of_provider_store_members(world, facade, monkeypatch):
    _grants(monkeypatch, [GEOMETRY_GRANT])
    job, queue = await _run_group_check(world, SCOPE)

    assert queue.updates[-1]["status"] == "done", queue.updates
    # The result is the scope's own: written there, never into the store.
    doc = json.loads(await world.get_bytes(SCOPE, job.derived_key))
    assert doc["counts"]["joints"] == 1
    assert doc["provenance"]["members_resolved"] == 2
    assert not await world.exists(Scope.providers(), job.derived_key)


@pytest.mark.asyncio
async def test_an_ungranted_scope_is_told_the_members_are_provider_data_it_has_no_grant_on(world, facade, monkeypatch):
    _grants(monkeypatch, [{**GEOMETRY_GRANT, "target_scope": "users/someone-else"}])
    job, queue = await _run_group_check(world, SCOPE)

    last = queue.updates[-1]
    assert last["status"] == "error", queue.updates
    assert "provider data" in last["error"] and "no geometry grant" in last["error"], last["error"]
    assert COLLECTION in last["error"]
    assert "no published manifest" not in last["error"]
    assert not await world.exists(SCOPE, job.derived_key)


@pytest.mark.asyncio
async def test_an_attributes_only_grant_reads_no_geometry(world, facade, monkeypatch):
    _grants(monkeypatch, [{**GEOMETRY_GRANT, "geometry": False}])
    _job, queue = await _run_group_check(world, SCOPE)
    assert queue.updates[-1]["status"] == "error"
    assert "no geometry grant" in queue.updates[-1]["error"]


@pytest.mark.asyncio
async def test_a_provider_store_job_reads_the_store_as_before(world, facade, monkeypatch):
    async def _never(*_a, **_k):
        raise AssertionError("a job of the provider store asks no grant")

    monkeypatch.setattr(dbm, "list_provider_grants", _never)
    monkeypatch.setattr(dbm, "grant_access", _never)
    job, queue = await _run_group_check(world, Scope.providers())
    assert queue.updates[-1]["status"] == "done", queue.updates
    assert await world.exists(Scope.providers(), job.derived_key)


# ── the facade itself: a contacts rebuild, a write, no pool ───────────


@pytest.mark.asyncio
async def test_a_contacts_rebuild_of_a_node_part_reads_through_the_grant(world, monkeypatch):
    from ada.comms.rest.formats.clash_contacts import load_target_model
    from ada.comms.rest.worker.source_nodes import _SyncStorageFacade

    _grants(monkeypatch, [GEOMETRY_GRANT])
    loop = asyncio.get_running_loop()
    facade = _SyncStorageFacade(world, SCOPE, loop, object())

    def _work():
        model = load_target_model(_node("zone-a"), facade)
        facade.put_bytes("_clash/out.json", b"{}")
        return model

    model = await loop.run_in_executor(None, _work)
    assert [b.name for b in model.get_all_physical_objects()] == ["girder-a"]
    # Reads resolved; writes did not.
    assert await world.exists(SCOPE, "_clash/out.json")
    assert not await world.exists(Scope.providers(), "_clash/out.json")


@pytest.mark.asyncio
async def test_without_a_pool_the_facade_reads_only_its_scope(world):
    from ada.comms.rest.formats.clash_contacts import load_target_model
    from ada.comms.rest.worker.source_nodes import _SyncStorageFacade

    loop = asyncio.get_running_loop()
    facade = _SyncStorageFacade(world, SCOPE, loop)

    def _work():
        return load_target_model(_node("zone-a"), facade)

    with pytest.raises(FileNotFoundError, match="no published manifest"):
        await loop.run_in_executor(None, _work)
