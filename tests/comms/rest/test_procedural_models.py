"""Procedural cell models: API CRUD + revision concurrency + validation.

* No-DB path tests (always run): endpoints 503 without a database; the doc
  validator and key conventions work standalone.
* Live-Postgres tests (skipped unless ``ADA_TEST_POSTGRES_URL`` is set):
  migration 022 applies, CRUD round-trips, optimistic concurrency conflicts.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

import ada.comms.rest.db as dbm  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.converter import is_hidden_key  # noqa: E402
from ada.comms.rest.procedural import procedural_glb_key, validate_doc  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)


def _settings(tmp_path: pathlib.Path, database_url: str = "") -> Settings:
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
        database_url=database_url,
    )


# ── standalone helpers ───────────────────────────────────────────────


def test_procedural_glb_key_shape():
    key = procedural_glb_key("abc-123", 4)
    assert key == "_procedural/abc-123/r4.glb"
    assert is_hidden_key(key)


def test_validate_doc_normalizes():
    doc = {"spaces": [{"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}]}
    out = validate_doc(doc)
    assert out["spaces"][0]["NAME"] == "Cell1"
    assert out["spaces"][0]["DX"] == 5.0
    assert out["equipments"] == []


def test_validate_doc_roundtrips_blueprint_options():
    out = validate_doc({"spaces": [], "blueprint": {"reinforce_internal_walls": True}})
    assert out["blueprint"] == {"reinforce_internal_walls": True}
    with pytest.raises(ValueError):
        validate_doc({"spaces": [], "blueprint": "nope"})


def test_validate_doc_rejects_bad_input():
    with pytest.raises(ValueError):
        validate_doc({"spaces": [{"X": 0}]})  # NAME missing
    with pytest.raises(ValueError):
        validate_doc({"spaces": "nope"})


# ── no-DB API path ───────────────────────────────────────────────────


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_endpoints_503_without_db(app_client: TestClient):
    assert app_client.get("/api/scopes/shared/procedural-models").status_code == 503
    assert app_client.post("/api/scopes/shared/procedural-models", json={"name": "m"}).status_code == 503
    assert app_client.get("/api/scopes/shared/procedural-models/x").status_code == 503


def test_equipment_types_empty_without_queue(app_client: TestClient):
    r = app_client.get("/api/scopes/shared/procedural-models/equipment-types")
    assert r.status_code == 200
    assert r.json() == {"equipment_types": []}


# ── live-Postgres API path ───────────────────────────────────────────


@pytest.fixture
def pg_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path, database_url=POSTGRES_URL))
    with TestClient(app) as client:
        yield client


@needs_postgres
def test_crud_roundtrip_and_revision_conflict(pg_client: TestClient):
    # create
    r = pg_client.post("/api/scopes/shared/procedural-models", json={"name": "crud-model"})
    assert r.status_code == 201, r.text
    model = r.json()
    model_id = model["id"]
    assert model["revision"] == 0
    try:
        # duplicate name -> 409
        assert pg_client.post("/api/scopes/shared/procedural-models", json={"name": "crud-model"}).status_code == 409

        # listed
        listing = pg_client.get("/api/scopes/shared/procedural-models").json()["models"]
        assert any(m["id"] == model_id for m in listing)

        # commit a doc
        doc = {"spaces": [{"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}]}
        r = pg_client.put(f"/api/scopes/shared/procedural-models/{model_id}", json={"doc": doc, "base_revision": 0})
        assert r.status_code == 200, r.text
        assert r.json()["revision"] == 1

        # stale base_revision -> 409 with current_revision
        r = pg_client.put(f"/api/scopes/shared/procedural-models/{model_id}", json={"doc": doc, "base_revision": 0})
        assert r.status_code == 409
        assert r.json()["detail"]["current_revision"] == 1

        # invalid doc -> 422
        r = pg_client.put(
            f"/api/scopes/shared/procedural-models/{model_id}",
            json={"doc": {"spaces": [{"X": 1}]}, "base_revision": 1},
        )
        assert r.status_code == 422

        # fetch carries the committed doc
        got = pg_client.get(f"/api/scopes/shared/procedural-models/{model_id}").json()
        assert got["revision"] == 1
        assert got["doc"]["spaces"][0]["NAME"] == "Cell1"

        # wrong scope -> 404
        assert pg_client.get(f"/api/scopes/user:me/procedural-models/{model_id}").status_code == 404

        # compile without NATS -> 503 (no cached blob yet)
        r = pg_client.post(f"/api/scopes/shared/procedural-models/{model_id}/compile")
        assert r.status_code == 503
    finally:
        assert pg_client.delete(f"/api/scopes/shared/procedural-models/{model_id}").status_code == 200
    # archived models disappear from list + get
    assert pg_client.get(f"/api/scopes/shared/procedural-models/{model_id}").status_code == 404


@needs_postgres
def test_compile_cached_short_circuit(pg_client: TestClient, tmp_path: pathlib.Path):
    r = pg_client.post("/api/scopes/shared/procedural-models", json={"name": "cached-model"})
    assert r.status_code == 201, r.text
    model_id = r.json()["id"]
    try:
        # seed the derived blob for revision 0 in the app's local storage root
        key = procedural_glb_key(model_id, 0)
        target = tmp_path / "shared" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"glTF-fake")

        r = pg_client.post(f"/api/scopes/shared/procedural-models/{model_id}/compile")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["cached"] is True
        assert body["job_id"] is None
        assert body["derived_key"] == key
    finally:
        pg_client.delete(f"/api/scopes/shared/procedural-models/{model_id}")


@needs_postgres
@pytest.mark.asyncio
async def test_db_helpers_direct():
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    try:
        row = await dbm.create_procedural_model(
            pool, scope_kind="user", scope_id="test-sub", name="direct-model", created_by="test-sub"
        )
        assert row is not None and row["revision"] == 0
        model_id = row["id"]

        # same name other scope is fine
        row2 = await dbm.create_procedural_model(
            pool, scope_kind="user", scope_id="other-sub", name="direct-model", created_by="other-sub"
        )
        assert row2 is not None

        new_rev = await dbm.update_procedural_model_doc(pool, model_id, {"spaces": []}, 0)
        assert new_rev == 1
        assert await dbm.update_procedural_model_doc(pool, model_id, {"spaces": []}, 0) is None

        assert await dbm.archive_procedural_model(pool, model_id) is True
        assert await dbm.archive_procedural_model(pool, model_id) is False
        assert await dbm.get_procedural_model(pool, model_id) is None
        await dbm.archive_procedural_model(pool, row2["id"])
    finally:
        await pool.close()
