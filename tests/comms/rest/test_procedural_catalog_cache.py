"""Catalog-aware compile cache: the equipment + system CATALOGS are live compile
inputs that a procedural model's own revision does NOT capture. Editing a placed
equipment type (moving its ports, changing bbox/mass, re-linking CAD) or a system
template must yield a FRESH compile — but leaves the revision-stamped derived key
unchanged, so a naive ``exists(derived_key)`` short-circuit would serve the stale
artifact.

The fix binds each compiled artifact to a ``.catfp`` sidecar recording the catalog
fingerprint it was built from; the compile endpoint compares the live fingerprint
against that sidecar and rebuilds on a mismatch (or a missing sidecar). These tests
drive the real endpoint with the DB + queue stubbed at the boundary (no pg / NATS),
writing blobs through an independent Storage over the same local path the app uses.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.procedural import (  # noqa: E402
    procedural_catalog_fp_key,
    procedural_detailing_glb_key,
)
from ada.comms.rest.queue import JobQueue  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.storage import Storage  # noqa: E402


def _settings(tmp_path: pathlib.Path) -> Settings:
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
        database_url="",
    )


def test_catalog_fp_key_is_sidecar_of_derived_key():
    # The sidecar is a plain ``.catfp`` sibling — one rule covers every variant.
    assert procedural_catalog_fp_key("_procedural/m1/r2.glb") == "_procedural/m1/r2.glb.catfp"
    assert procedural_catalog_fp_key("_procedural/m1/r2.det-ext-detail.glb") == (
        "_procedural/m1/r2.det-ext-detail.glb.catfp"
    )
    assert procedural_catalog_fp_key("_procedural/m1/r3_box.ifc") == "_procedural/m1/r3_box.ifc.catfp"


def _wire(monkeypatch, *, doc: dict, live_fp: str):
    """Stub the DB (model row + catalog fingerprint) + queue enqueue. Returns the
    ``calls`` list the fake enqueue appends to."""
    monkeypatch.setattr(JobQueue, "enabled", property(lambda self: True))
    calls: list[dict] = []

    async def _fake_enqueue(self, source_key, target_format="glb", **kw):
        calls.append({"source_key": source_key, "target_format": target_format, **kw})
        return SimpleNamespace(job_id=f"job-{target_format}")

    async def _fake_list_workers(self):
        return []

    async def _fake_connect(self, **kwargs):
        return None

    fake_row = {
        "id": "m1",
        "revision": 2,
        "scope_kind": "shared",
        "scope_id": None,
        "engine": None,
        "name": "M",
        "doc": doc,
    }

    async def _fake_get_model(pool, model_id):
        return fake_row

    async def _fake_fp(pool, *, scope_kind, scope_id):
        return live_fp

    monkeypatch.setattr(JobQueue, "enqueue", _fake_enqueue)
    monkeypatch.setattr(JobQueue, "list_workers", _fake_list_workers)
    monkeypatch.setattr(JobQueue, "connect", _fake_connect)
    monkeypatch.setattr(db_module, "get_procedural_model", _fake_get_model)
    monkeypatch.setattr(db_module, "get_catalog_fingerprint", _fake_fp)
    return calls


def _seed_cached_glb(settings: Settings, sidecar_fp: str | None):
    """Pre-write the r2 sim GLB (and optionally its catfp sidecar) into the same
    local storage the app reads, so the compile endpoint sees a cached artifact."""
    storage = Storage.from_settings(settings)
    scope = Scope.shared()
    derived_key = procedural_detailing_glb_key("m1", 2, None, None, "sim", {})

    async def _seed():
        await storage.put_bytes(scope, derived_key, b"GLB-CACHED", content_encoding="gzip")
        if sidecar_fp is not None:
            await storage.put_bytes(scope, procedural_catalog_fp_key(derived_key), sidecar_fp.encode("utf-8"))

    asyncio.run(_seed())
    return derived_key


def test_compile_rebuilds_when_catalog_changed(monkeypatch, tmp_path: pathlib.Path):
    # A model that PLACES catalog equipment, with a cached GLB whose sidecar records
    # an OLD catalog fingerprint. The live catalog differs -> the endpoint must NOT
    # serve the cache; it enqueues a forced rebuild.
    settings = _settings(tmp_path)
    doc = {"equipments": [{"NAME": "E1", "DESCRIPTION": "pump-a"}], "systems": []}
    calls = _wire(monkeypatch, doc=doc, live_fp="FP_NEW")
    _seed_cached_glb(settings, sidecar_fp="FP_OLD")

    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        r = client.post("/api/scopes/shared/procedural-models/m1/compile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cached"] is False
    assert len(calls) == 1
    job = calls[0]
    # Forced past the worker's own redelivery short-circuit, and the live fingerprint
    # travels to the worker so it can (re)stamp the sidecar after the rebuild.
    assert job["force_rebuild"] is True
    assert job["conversion_options"]["catalog_fingerprint"] == "FP_NEW"


def test_compile_serves_cache_when_catalog_unchanged(monkeypatch, tmp_path: pathlib.Path):
    # Same model, but the cached GLB's sidecar matches the live fingerprint -> cache
    # is honoured, no rebuild enqueued.
    settings = _settings(tmp_path)
    doc = {"equipments": [{"NAME": "E1", "DESCRIPTION": "pump-a"}], "systems": []}
    calls = _wire(monkeypatch, doc=doc, live_fp="FP_SAME")
    _seed_cached_glb(settings, sidecar_fp="FP_SAME")

    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        r = client.post("/api/scopes/shared/procedural-models/m1/compile")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cached"] is True
    assert calls == []


def test_compile_rebuilds_when_sidecar_missing(monkeypatch, tmp_path: pathlib.Path):
    # A cached GLB from BEFORE this feature (no sidecar) is treated as stale for a
    # catalog-bearing model, so the cache heals itself on the next compile.
    settings = _settings(tmp_path)
    doc = {"equipments": [{"NAME": "E1", "DESCRIPTION": "pump-a"}], "systems": []}
    calls = _wire(monkeypatch, doc=doc, live_fp="FP_NEW")
    _seed_cached_glb(settings, sidecar_fp=None)

    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        r = client.post("/api/scopes/shared/procedural-models/m1/compile")
    assert r.status_code == 200, r.text
    assert r.json()["cached"] is False
    assert len(calls) == 1
    assert calls[0]["force_rebuild"] is True


def test_compile_catalog_independent_model_ignores_fingerprint(monkeypatch, tmp_path: pathlib.Path):
    # A model with NO equipment/systems has no catalog dependency: the fingerprint
    # helper is never consulted and a cached GLB (no sidecar) short-circuits as
    # before — byte-identical cache behaviour to pre-feature.
    settings = _settings(tmp_path)
    doc = {"equipments": [], "systems": []}

    fp_calls: list[int] = []

    async def _fp_should_not_run(pool, *, scope_kind, scope_id):
        fp_calls.append(1)
        return "SHOULD_NOT_BE_USED"

    calls = _wire(monkeypatch, doc=doc, live_fp="unused")
    monkeypatch.setattr(db_module, "get_catalog_fingerprint", _fp_should_not_run)
    _seed_cached_glb(settings, sidecar_fp=None)

    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        r = client.post("/api/scopes/shared/procedural-models/m1/compile")
    assert r.status_code == 200, r.text
    assert r.json()["cached"] is True
    assert calls == []
    # A catalog-independent model must not even query the catalog fingerprint.
    assert fp_calls == []
