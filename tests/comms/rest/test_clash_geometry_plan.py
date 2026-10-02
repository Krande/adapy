"""Asking for a check's geometry BEFORE the check: ``POST .../clash-check/geometry-plan``, the derived
key of a check through a geometry provider, and where such a check (and its detail) is routed.

The fixture is the one ``tests/core/clash/test_geometry_source.py`` uses, published into a real
scope through the local storage: one collection two providers publish into, each with its own node
ids for the same elements -- a MESH-ONLY provider (nothing to read) and a MEMBER-READER (its own
refs, and a build a reader turns into beams).

What is pinned:

* the plan answers matched / unmatched / unchanged with the SAME function the worker runs, over the
  async storage, from a process that has no modelling stack (the slim API);
* it is fast on a collection of a few hundred subjects (the timing is printed);
* a check through a geometry provider is a cache MISS once that provider publishes again, while a
  default check keeps the derived key it always had;
* such a check, and its detail, run only where the provider can be READ, or are refused by name.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-geometry-plan-"))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.assets.keys import asset_key  # noqa: E402
from ada.assets.manifest import (  # noqa: E402
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)
from ada.assets.projection import build_hierarchy  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

SRC = Path(__file__).resolve().parents[3] / "src"
SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
COLLECTION = "plant"
MESH = "mesh-only"
READER = "member-reader"
R1 = "20260901T000000Z"
R2 = "20260902T000000Z"
R3 = "20260903T000000Z"
LINES = b"\n".join([b"Z100-BEAMS2 0 0 0 2 0 0", b"Z100-BEAMS3 1 0 0 1 2 0", b"Z100-FAR 50 50 0 52 50 0"])


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


def _write(scope_dir: Path, key: str, data: bytes) -> None:
    dest = scope_dir / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _row(node_id, parent, label, provider=None, leaf=True) -> dict:
    row = {"id": node_id, "parent": parent, "label": label, "kind": "item", "leaf": leaf}
    if provider:
        row["provider"] = provider
    return row


def _publish(
    scope_dir: Path, *, provider, subject, revision, nodes, build_source=None, collection=COLLECTION, with_source=True
):
    spine = build_hierarchy(provider=provider, collection=collection, produced_at=revision, nodes=nodes)
    _write(scope_dir, asset_key(collection, subject, revision, HIERARCHY_FILENAME), spine.to_json())
    artefacts = [ArtefactEntry(role="hierarchy", file=HIERARCHY_FILENAME, sha256="", size=1)]
    build = None
    if build_source is not None:
        key = asset_key(collection, subject, revision, "lines.txt")
        _write(scope_dir, key, build_source)
        artefacts.append(ArtefactEntry(role="source", key=key, sha256="", size=len(build_source)))
        build = BuildSpec(capability="build-lines", options={"source_key": key})
    elif with_source:
        # Some mesh-only publishes name their export as a source artefact, though nothing can read
        # it; others carry none at all -- see the no-source test below.
        key = asset_key(collection, subject, revision, "export.bin")
        _write(scope_dir, key, b"opaque")
        artefacts.append(ArtefactEntry(role="source", key=key, sha256="", size=6))
    manifest = AssetManifest(
        provider=provider,
        collection=collection,
        subject=subject,
        revision=revision,
        node=None if subject == collection else subject,
        produced_at=revision,
        published_at=revision,
        delivery="build" if build else "mesh",
        build=build,
        artefacts=tuple(artefacts),
    )
    _write(scope_dir, asset_key(collection, subject, revision, MANIFEST_FILENAME), manifest.to_json())


def _publish_fixture(scope_dir: Path) -> None:
    _publish(
        scope_dir,
        provider=MESH,
        subject="fnzone",
        revision=R2,
        nodes=[
            _row("fnzone", None, "/Z100-ZONE", leaf=False),
            _row("fn01", "fnzone", "/Z100-BEAMS2"),
            _row("fn02", "fnzone", "/z100-beams3"),
            _row("fn04", "fnzone", "/LOST"),
        ],
    )
    _publish(
        scope_dir,
        provider=READER,
        subject="ref-100",
        revision=R1,
        build_source=LINES,
        nodes=[
            _row("ref-100", None, "/Z100-ZONE", leaf=False),
            _row("ref-101", "ref-100", "Z100-BEAMS2"),
            _row("ref-102", "ref-100", "Z100-BEAMS3"),
            _row("ref-105", "ref-100", "Z100-FAR"),
        ],
    )


class _LinesConcepts:
    """The member-reader's reader: one beam per line, named as its spine labels them."""

    def concepts(self, options, *, storage, scope=None, node=None):
        import ada

        part = ada.Part(node or "lines")
        for line in storage.get_bytes(options["source_key"]).decode().splitlines():
            name, *xyz = line.split()
            x0, y0, z0, x1, y1, z1 = (float(v) for v in xyz)
            part.add_beam(ada.Beam(name, (x0, y0, z0), (x1, y1, z1), "IPE200"))
        return part


@pytest.fixture
def client_and_scope(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        scope_dir = tmp_path / SCOPE_DIR
        _publish_fixture(scope_dir)
        yield client, scope_dir


@pytest.fixture
def reader():
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts

    register_asset_concepts(READER, lambda: _LinesConcepts())
    yield
    clear_asset_concepts()


def _mesh_member(element: str | None) -> dict:
    target = {
        "kind": "node",
        "provider": MESH,
        "collection": COLLECTION,
        "subject": "fnzone",
        "revision": R2,
        "node": None,
    }
    return {"target": target, "element": element, "path": [element] if element else []}


def _plan(client, body: dict):
    return client.post(f"/api/scopes/{SCOPE}/clash-check/geometry-plan", json=body)


def _wait_done(client, job_id, *, timeout_s=30.0) -> dict:
    if job_id is None:
        return {"status": "done"}
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


class _DirStorage:
    """The scope directory as the worker's synchronous facade -- for the parity check."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def list_keys(self, prefix: str = "") -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return [p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file()]

    def get_bytes(self, key: str) -> bytes:
        path = self.root / key
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()


# ── the plan ─────────────────────────────────────────────────────────


def test_a_group_plan_names_matched_unmatched_and_unchanged(client_and_scope):
    client, _ = client_and_scope
    own = {
        "target": {
            "kind": "node",
            "provider": READER,
            "collection": COLLECTION,
            "subject": "ref-100",
            "revision": R1,
            "node": "ref-100",
        },
        "element": "Z100-FAR",
        "path": [],
    }
    a_file = {"target": {"kind": "file", "source_key": "models/a.ifc"}, "element": None, "path": []}
    (Path(client_and_scope[1]) / "models").mkdir(parents=True, exist_ok=True)
    (Path(client_and_scope[1]) / "models" / "a.ifc").write_bytes(b"x")
    group = {"name": "Deck 3", "members": [_mesh_member("/Z100-BEAMS2"), _mesh_member("/LOST"), own, a_file]}

    r = _plan(client, {"group": group, "geometry_provider": READER})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["geometry_provider"] == READER

    (matched,) = plan["matched"]
    assert matched["from"]["element"] == "/Z100-BEAMS2"
    assert matched["to"]["target"]["subject"] == "ref-100" and matched["to"]["element"] == "Z100-BEAMS2"

    (unmatched,) = plan["unmatched"]
    assert unmatched["member"]["element"] == "/LOST"
    assert unmatched["label"] == "/LOST"
    assert "'/LOST'" in unmatched["reason"] and READER in unmatched["reason"]
    # The member as the CHECK resolves it: revision and provider filled in, so the panel can request
    # exactly the node it names.
    assert unmatched["member"]["target"]["provider"] == MESH and unmatched["member"]["target"]["revision"] == R2

    assert sorted(m["target"]["kind"] for m in plan["unchanged"]) == ["file", "node"]


def test_a_whole_node_plan_carries_the_label_its_provider_published(client_and_scope):
    client, scope_dir = client_and_scope
    # A zone the reader has no node for: its label is what a provider would be asked to find it by.
    _publish(scope_dir, provider=MESH, subject="fnorphan", revision=R2, nodes=[_row("fnorphan", None, "/Z100-ORPHAN")])
    r = _plan(client, {"collection": COLLECTION, "subject": "fnorphan", "geometry_provider": READER})
    assert r.status_code == 200, r.text
    (unmatched,) = r.json()["unmatched"]
    assert unmatched["label"] == "/Z100-ORPHAN"
    assert unmatched["member"]["element"] is None and unmatched["member"]["target"]["node"] == "fnorphan"


def test_a_node_plan_matches_the_whole_node_and_its_own_provider_is_unchanged(client_and_scope):
    client, _ = client_and_scope
    r = _plan(client, {"collection": COLLECTION, "subject": "fnzone", "geometry_provider": READER})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert not plan["unmatched"] and not plan["unchanged"]
    assert plan["matched"][0]["to"]["target"]["subject"] == "ref-100"

    own = _plan(client, {"collection": COLLECTION, "subject": "ref-100", "geometry_provider": READER}).json()
    assert own["matched"] == [] and own["unmatched"] == [] and len(own["unchanged"]) == 1


def test_the_plan_refuses_what_it_cannot_answer(client_and_scope):
    client, _ = client_and_scope
    assert _plan(client, {"collection": COLLECTION, "subject": "fnzone"}).status_code == 400
    both = {"collection": COLLECTION, "subject": "fnzone", "group": {"name": "g", "members": []}}
    assert _plan(client, {**both, "geometry_provider": READER}).status_code == 400
    assert _plan(client, {"geometry_provider": READER}).status_code == 400
    missing = _plan(client, {"collection": COLLECTION, "subject": "nope", "geometry_provider": READER})
    assert missing.status_code == 404


def test_the_plan_is_the_workers_remap_over_the_async_storage(client_and_scope):
    """Parity: the route's answer is what `remap_group` answers over the synchronous storage the
    worker hands it -- one implementation, two callers."""
    from ada.clash.geometry_source import plan_geometry
    from ada.clash.group import normalise_group

    client, scope_dir = client_and_scope
    group = normalise_group(
        {"name": "Deck 3", "members": [_mesh_member(e) for e in ("/Z100-BEAMS2", "/z100-beams3", "/LOST")]}
    )
    via_route = _plan(client, {"group": group, "geometry_provider": READER}).json()
    direct = plan_geometry(group, READER, storage=_DirStorage(scope_dir))
    assert via_route == json.loads(json.dumps(direct))
    assert len(via_route["matched"]) == 2 and len(via_route["unmatched"]) == 1


def test_the_plan_is_fast_on_a_collection_of_hundreds_of_subjects(tmp_path):
    """Real-ish size: 300 subjects per provider, 20 elements each (6000 named nodes a side), and a
    group of 300 members -- every subject's manifest and every reader spine is read once, in
    batches, through the async storage."""
    subjects, per = 300, 20
    app = create_app(_settings(tmp_path))
    scope_dir = tmp_path / SCOPE_DIR
    for s in range(subjects):
        mesh_nodes = [_row(f"fz{s}", None, f"/Z{s:04d}", leaf=False)]
        mesh_nodes += [_row(f"fz{s}-{i}", f"fz{s}", f"/E{s:04d}-{i:02d}") for i in range(per)]
        _publish(scope_dir, provider=MESH, subject=f"fz{s}", revision=R2, nodes=mesh_nodes, collection="big")
        reader_nodes = [_row(f"rz{s}", None, f"/Z{s:04d}", leaf=False)]
        reader_nodes += [_row(f"rz{s}-{i}", f"rz{s}", f"E{s:04d}-{i:02d}") for i in range(per)]
        _publish(
            scope_dir,
            provider=READER,
            subject=f"rz{s}",
            revision=R1,
            nodes=reader_nodes,
            build_source=LINES,
            collection="big",
        )
    members = [
        {
            "target": {
                "kind": "node",
                "provider": MESH,
                "collection": "big",
                "subject": f"fz{s}",
                "revision": R2,
                "node": None,
            },
            "element": f"/E{s:04d}-{s % per:02d}",
            "path": [],
        }
        for s in range(subjects)
    ]
    members[-1] = {**members[-1], "element": "/NOT-THERE"}
    with TestClient(app) as client:
        started = time.perf_counter()
        r = client.post(
            f"/api/scopes/{SCOPE}/clash-check/geometry-plan",
            json={"group": {"name": "big", "members": members}, "geometry_provider": READER},
        )
        elapsed = time.perf_counter() - started
    assert r.status_code == 200, r.text
    plan = r.json()
    assert len(plan["matched"]) == subjects - 1 and len(plan["unmatched"]) == 1
    print(
        f"\ngeometry-plan: {subjects} members over 2x{subjects} subjects ({2 * subjects * per} nodes) in {elapsed:.2f}s"
    )
    assert elapsed < 30, f"the plan took {elapsed:.1f}s"


def test_the_plan_runs_where_there_is_no_modelling_stack(client_and_scope):
    """The slim API image has no numpy and no reader. The plan's code is imported there with every
    heavy package blocked -- and the stub `ada` package the image ships in place of the real one."""
    _, scope_dir = client_and_scope
    script = r"""
import importlib.abc, json, sys, types
from pathlib import Path

HEAVY = {"numpy", "OCC", "ifcopenshell", "trimesh", "gmsh", "adacpp", "scipy", "pyquaternion"}
HEAVY_ADA = ("ada.api", "ada.cadit", "ada.fem", "ada.geom", "ada.occ", "ada.visit", "ada.core.vector_utils")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in HEAVY or name.startswith(HEAVY_ADA):
            raise ImportError(f"not in the slim image: {name}")
        return None

sys.meta_path.insert(0, Block())
pkg = types.ModuleType("ada")
pkg.__path__ = [sys.argv[1]]
sys.modules["ada"] = pkg

from ada.clash.geometry_source import plan_geometry

root = Path(sys.argv[2])

class Storage:
    def list_keys(self, prefix=""):
        base = root / prefix
        return [p.relative_to(root).as_posix() for p in base.rglob("*") if p.is_file()] if base.exists() else []
    def get_bytes(self, key):
        p = root / key
        if not p.is_file():
            raise FileNotFoundError(key)
        return p.read_bytes()

member = {"target": {"kind": "node", "provider": sys.argv[3], "collection": "plant", "subject": "fnzone",
          "revision": sys.argv[5], "node": None}, "element": "/Z100-BEAMS2", "path": []}
plan = plan_geometry({"name": "g", "members": [member]}, sys.argv[4], storage=Storage())
print(json.dumps({"matched": len(plan["matched"]), "unmatched": len(plan["unmatched"])}))
"""
    out = subprocess.run(
        [sys.executable, "-c", script, str(SRC / "ada"), str(scope_dir), MESH, READER, R2],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"matched": 1, "unmatched": 0}


# ── the derived key ──────────────────────────────────────────────────


def _check(client, body: dict) -> dict:
    r = client.post(f"/api/scopes/{SCOPE}/clash-check", json=body)
    assert r.status_code == 200, r.text
    _wait_done(client, r.json()["job_id"])
    return r.json()


def test_a_new_publish_by_the_geometry_provider_is_a_cache_miss(client_and_scope, reader):
    client, scope_dir = client_and_scope
    group = {"name": "Deck 3", "members": [_mesh_member("/Z100-BEAMS2"), _mesh_member("/LOST")]}
    body = {"group": group, "options": {"include_plate_joints": False, "geometry_provider": READER}}
    first = _check(client, body)
    assert _check(client, body) == {"job_id": None, "derived_key": first["derived_key"], "cached": True}

    # The reader publishes again (the node the check left out, say): the same request must not be
    # answered with the check that left it out.
    _publish(
        scope_dir,
        provider=READER,
        subject="ref-100",
        revision=R3,
        build_source=LINES,
        nodes=[_row("ref-100", None, "/Z100-ZONE", leaf=False), _row("ref-101", "ref-100", "Z100-BEAMS2")],
    )
    after = _check(client, body)
    assert after["cached"] is False and after["derived_key"] != first["derived_key"]

    # The node form, likewise.
    node_body = {"collection": COLLECTION, "subject": "fnzone", "options": {"geometry_provider": READER}}
    before_node = _check(client, node_body)
    _publish(
        scope_dir,
        provider=READER,
        subject="ref-200",
        revision=R3,
        build_source=LINES,
        nodes=[_row("ref-200", None, "X")],
    )
    assert _check(client, node_body)["derived_key"] != before_node["derived_key"]


def test_a_default_check_keeps_the_key_it_always_had(client_and_scope):
    """No geometry provider: the derived key is exactly the pre-existing formula, and another
    provider publishing into the collection does not move it."""
    from ada.clash.group import group_derived_prefix, group_token, normalise_group
    from ada.clash.options import ClashOptions
    from ada.comms.rest.routes.clash_check import _options_token

    client, scope_dir = client_and_scope
    options = {"include_plate_joints": False}
    group = {"name": "Deck 3", "members": [_mesh_member("/Z100-BEAMS2")]}
    key = _check(client, {"group": group, "options": options})["derived_key"]

    resolved = normalise_group(group)
    token = group_token(resolved["members"], {f"node:{COLLECTION}/fnzone": R2})
    assert key == f"{group_derived_prefix(token, _options_token(ClashOptions.from_dict(options)))}/result.json"

    _publish(
        scope_dir,
        provider=READER,
        subject="ref-300",
        revision=R3,
        build_source=LINES,
        nodes=[_row("ref-300", None, "Y")],
    )
    assert _check(client, {"group": group, "options": options})["derived_key"] == key


# ── routing ──────────────────────────────────────────────────────────


class _Jobs:
    kind = "queue"

    def __init__(self, readers: dict, checkers: dict | None = None, specs: dict | None = None) -> None:
        self._by_field = {
            "asset_concept_readers": readers,
            "clash_checkers": checkers or {},
            "connection_specs": specs or {},
        }

    async def advertised_specs(self, field, fallback_field=None):
        return self._by_field.get(field, {})


def _ctx(readers, checkers=None, specs=None):
    return SimpleNamespace(jobs=_Jobs(readers, checkers, specs))


def _reader_entry(*pools):
    return {
        "slug": READER,
        "id": READER,
        "readable": bool(pools),
        "readable_pools": list(pools),
        "union_fields": ["readable_pools"],
    }


@pytest.mark.asyncio
async def test_a_core_check_goes_to_a_pool_that_reads_the_provider():
    from ada.comms.rest.routes.clash_check import _route_with_reader

    ctx = _ctx({READER: _reader_entry("reader-pool")})
    assert await _route_with_reader(ctx, None, READER, needs="clash checker 'adapy'") == "reader-pool"
    # Read on the default pool too: that is where a core check already goes.
    ctx = _ctx({READER: _reader_entry(None, "reader-pool")})
    assert await _route_with_reader(ctx, None, READER, needs="x") is None
    # No geometry provider: nothing changes.
    assert await _route_with_reader(ctx, "any-pool", None, needs="x") == "any-pool"


@pytest.mark.asyncio
async def test_a_contributed_checker_runs_where_it_and_the_reader_both_are():
    from ada.comms.rest.routes.clash_check import _route_with_reader

    ctx = _ctx({READER: _reader_entry("checker-pool", "reader-pool")})
    assert await _route_with_reader(ctx, "checker-pool", READER, needs="clash checker 'c'") == "checker-pool"


@pytest.mark.asyncio
async def test_two_pools_that_cannot_both_be_satisfied_are_refused_by_name():
    from ada.comms.rest.routes.clash_check import _route_with_reader

    ctx = _ctx({READER: _reader_entry("reader-pool")})
    with pytest.raises(HTTPException) as err:
        await _route_with_reader(ctx, "checker-pool", READER, needs="clash checker 'c'")
    assert err.value.status_code == 409
    assert "'checker-pool'" in err.value.detail and "'reader-pool'" in err.value.detail and READER in err.value.detail


@pytest.mark.asyncio
async def test_a_provider_no_live_worker_reads_is_refused():
    from ada.comms.rest.routes.clash_check import _route_with_reader

    for readers in ({}, {READER: _reader_entry()}, {READER: {"slug": READER, "readable": False, "available": False}}):
        with pytest.raises(HTTPException) as err:
            await _route_with_reader(_ctx(readers), None, READER, needs="x")
        assert err.value.status_code == 409 and "no live worker can read" in err.value.detail


@pytest.mark.asyncio
async def test_a_worker_predating_readable_pools_speaks_for_its_own_pool():
    from ada.comms.rest.routes.clash_check import _route_with_reader

    old = {"slug": READER, "id": READER, "readable": True, "capability": "old-pool"}
    assert await _route_with_reader(_ctx({READER: old}), None, READER, needs="x") == "old-pool"


@pytest.mark.asyncio
async def test_a_detail_of_a_check_through_a_provider_runs_where_it_can_be_read():
    from ada.comms.rest.routes.clash_check import _detail_capability

    ctx = _ctx({READER: _reader_entry("reader-pool")})
    # A built-in spec and core's checker run anywhere: the reader decides.
    assert await _detail_capability(ctx, None, "builtin.girder_gusset", READER) == "reader-pool"
    assert await _detail_capability(ctx, None, "builtin.girder_gusset", None) is None
    # A contributed spec on another pool cannot be served by one job.
    specs = {"spec-x": {"slug": "spec-x", "capability": "spec-pool"}}
    with pytest.raises(HTTPException) as err:
        await _detail_capability(_ctx({READER: _reader_entry("reader-pool")}, specs=specs), None, "spec-x", READER)
    assert err.value.status_code == 409 and "'spec-x'" in err.value.detail


@pytest.mark.asyncio
async def test_the_listing_says_readable_when_any_live_worker_can_read():
    """Two workers advertise one provider; the FIRST cannot read it (a dependency missing) and the
    second can. The merged entry used to carry the first worker's verdict."""
    from ada.comms.rest.routes.clash_check import api_clash_geometry_providers
    from ada.comms.rest.routes.deps import live_worker_specs

    now = time.time()

    def worker(worker_id, readable, pool):
        return {
            "worker_id": worker_id,
            "last_heartbeat": now,
            "asset_concept_readers": [
                {
                    "id": READER,
                    "slug": READER,
                    "readable": readable,
                    "available": readable,
                    "capability": pool,
                    "readable_pools": [pool] if readable else [],
                    "union_fields": ["readable_pools"],
                }
            ],
        }

    class _Queue:
        enabled = True
        WORKER_STALE_AFTER_S = 60

        async def list_workers(self):
            return [worker("a-broken", False, "pool-a"), worker("b-fine", True, "pool-b")]

    class _QueueJobs:
        kind = "queue"

        async def advertised_specs(self, field, fallback_field=None):
            return await live_worker_specs(_Queue(), field, fallback_field)

    resp = await api_clash_geometry_providers(scope_obj=None, ctx=SimpleNamespace(jobs=_QueueJobs()))
    (entry,) = json.loads(resp.body)["providers"]
    assert entry["readable"] is True and entry["capability"] == "pool-b"
    assert "unavailable_reason" not in entry


def test_the_heartbeat_advertises_the_pools_that_can_read_a_provider_as_a_union_field():
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts
    from ada.comms.rest.worker.registration import _asset_concept_readers_for_heartbeat

    register_asset_concepts("member-reader", lambda: object())
    register_asset_concepts("missing-dep", lambda: object(), available=lambda: False)
    try:
        entries = {e["id"]: e for e in _asset_concept_readers_for_heartbeat(["base", "one"])}
        # Every pool this worker serves -- the default one (via `base`) as well as its own.
        assert entries["member-reader"]["readable_pools"] == [None, "one"]
        assert entries["missing-dep"]["readable_pools"] == []
        assert entries["member-reader"]["union_fields"] == ["readable_pools"]
        # A COMBINED worker (many pools in one process) reads the provider on all of them, so a
        # check whose checker lives on one of its named pools is routable -- it used to be refused
        # as needing two pools, because a multi-pool worker was attributed to the default pool only.
        combined = {e["id"]: e for e in _asset_concept_readers_for_heartbeat(["base", "checker-pool", "other"])}
        assert combined["member-reader"]["readable_pools"] == [None, "checker-pool", "other"]
        assert combined["member-reader"]["capability"] is None
    finally:
        clear_asset_concepts()


def test_a_combined_worker_routes_a_named_pool_checker_through_a_geometry_provider():
    from ada.comms.rest.routes.clash_check import _readable_pools

    entry = {"readable": True, "capability": None, "readable_pools": [None, "checker-pool", "other"]}
    assert "checker-pool" in _readable_pools(entry)
    assert None in _readable_pools(entry)


def test_a_member_whose_publish_has_no_source_artefact_is_planned_and_checked(client_and_scope, reader):
    """A mesh-only provider commonly publishes no source artefact at all -- its geometry is meshes
    fetched from elsewhere. With a geometry provider chosen the member is re-addressed before
    anything is read, so neither the plan nor the check may refuse it for lacking one (they did:
    409 "publishes no source artefact")."""
    client, scope_dir = client_and_scope
    _publish(
        scope_dir,
        provider=MESH,
        subject="fnbare",
        revision=R2,
        with_source=False,
        nodes=[_row("fnbare", None, "/Z100-ZONE", leaf=False), _row("fnb1", "fnbare", "/Z100-BEAMS2")],
    )
    member = {
        "target": {"kind": "node", "provider": MESH, "collection": COLLECTION, "subject": "fnbare", "revision": None, "node": None},
        "element": "/Z100-BEAMS2",
        "path": ["/Z100-BEAMS2"],
    }
    r = _plan(client, {"group": {"name": "g", "members": [member]}, "geometry_provider": READER})
    assert r.status_code == 200, r.text
    assert len(r.json()["matched"]) == 1

    r = _plan(client, {"collection": COLLECTION, "subject": "fnbare", "geometry_provider": READER})
    assert r.status_code == 200, r.text

    check = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"group": {"name": "g", "members": [member]}, "options": {"geometry_provider": READER}},
    )
    assert check.status_code == 200, check.text
