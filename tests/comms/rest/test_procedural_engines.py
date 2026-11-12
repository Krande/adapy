"""Procedural-engine registry: manifest validation + API CRUD + built-in union.

* No-DB tests (always run): the manifest validator; endpoints 503 without a DB;
  the built-in ``adapy-default`` engine is always listed.
* Live-Postgres tests (skipped unless ``ADA_TEST_POSTGRES_URL`` is set):
  migration 025 applies, CRUD round-trips, revision conflict, built-in is
  read-only, reserved slug + invalid manifest rejected.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.catalog import validate_engine_doc  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

_BUILTIN_ID = "builtin:adapy-default"


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


# ── manifest validation (no DB) ──────────────────────────────────────


def test_validate_engine_doc_builtin_default():
    out = validate_engine_doc({"kind": "builtin"})
    assert out["kind"] == "builtin"
    assert out["repo_url"] is None and out["pyodide_deps"] == []


def test_validate_engine_doc_wheel_roundtrips():
    doc = {
        "kind": "wheel",
        "repo_url": "git@example.com:me/engine.git",
        "ref": "v1.2.3",
        "entrypoint": "engine.compile:run",
        "pyodide_deps": ["shapely"],
        "deploy_key_secret": "engine-deploy-key",
    }
    out = validate_engine_doc(doc)
    assert out["entrypoint"] == "engine.compile:run"
    assert out["pyodide_deps"] == ["shapely"]


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "wheel"},  # missing repo_url + entrypoint
        {"kind": "server", "repo_url": "g"},  # missing entrypoint
        {"kind": "wheel", "repo_url": "g", "entrypoint": "nocolon"},  # bad entrypoint
        {"kind": "nonsense"},  # bad kind
        "not-an-object",
    ],
)
def test_validate_engine_doc_rejects(bad):
    with pytest.raises(ValueError):
        validate_engine_doc(bad)


# ── no-DB API path ───────────────────────────────────────────────────


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_engines_without_a_db(app_client: TestClient):
    """No database: the built-ins are listed (nothing can be registered as a row)."""
    engines = app_client.get("/api/scopes/shared/procedural-engines").json()["procedural_engines"]
    assert [e["slug"] for e in engines[:2]] == ["adapy-default", "echo"]
    assert app_client.post("/api/scopes/shared/procedural-engines", json={"name": "e"}).status_code == 503


@pytest.fixture
def local_engine():
    """An engine a tool registers in this process (as a preloaded package does)."""
    from ada.topo_model import engine_catalog

    engine_catalog.register_procedural_engine_capabilities(
        "test-local-engine",
        name="Test local engine",
        description="echo, registered in-process",
        entrypoint="ada.topo_model.echo_engine:compile_doc",
    )
    yield "test-local-engine"
    engine_catalog._ENGINE_CAPABILITY_REGISTRY.pop("test-local-engine", None)


def test_an_engine_registered_in_process_is_offered_and_compiles(app_client: TestClient, local_engine: str):
    """A local viewer's tool registers its engine at import; the queue-less server lists
    it (origin "local") and compiles with it in-process."""
    import time

    engines = app_client.get("/api/scopes/shared/procedural-engines").json()["procedural_engines"]
    mine = [e for e in engines if e["slug"] == local_engine]
    assert len(mine) == 1 and mine[0]["origin"] == "local" and mine[0]["id"] == f"local:{local_engine}"
    got = app_client.get(f"/api/scopes/shared/procedural-engines/local:{local_engine}").json()
    assert got["doc"] == {"kind": "server", "entrypoint": "ada.topo_model.echo_engine:compile_doc"}
    resolved = app_client.get(f"/api/scopes/shared/procedural-engines/local:{local_engine}/resolve").json()
    assert resolved["ready"] is False and resolved["kind"] == "server"

    base = "/api/scopes/shared/procedural-models"
    model_id = app_client.post(base, json={"name": "with-local-engine"}).json()["id"]
    doc = {"spaces": [{"NAME": "C1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}], "engine": local_engine}
    app_client.put(f"{base}/{model_id}", json={"doc": doc, "base_revision": 0})
    job_id = app_client.post(f"{base}/{model_id}/compile").json()["job_id"]
    deadline = time.monotonic() + 120
    while (status := app_client.get(f"/api/convert/{job_id}").json())["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert status["status"] == "done", status


# ── live-Postgres API path ───────────────────────────────────────────


@pytest.fixture
def pg_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path, database_url=POSTGRES_URL))
    with TestClient(app) as client:
        yield client


@needs_postgres
def test_list_includes_builtin(pg_client: TestClient):
    r = pg_client.get("/api/scopes/shared/procedural-engines")
    assert r.status_code == 200, r.text
    engines = r.json()["procedural_engines"]
    builtin = engines[0]
    assert builtin["slug"] == "adapy-default"
    assert builtin["origin"] == "builtin"


@needs_postgres
def test_builtin_is_read_only(pg_client: TestClient):
    assert (
        pg_client.put(
            f"/api/scopes/shared/procedural-engines/{_BUILTIN_ID}",
            json={"name": "x", "doc": {"kind": "builtin"}, "base_revision": 0},
        ).status_code
        == 403
    )
    assert pg_client.delete(f"/api/scopes/shared/procedural-engines/{_BUILTIN_ID}").status_code == 403
    r = pg_client.get(f"/api/scopes/shared/procedural-engines/{_BUILTIN_ID}")
    assert r.status_code == 200 and r.json()["slug"] == "adapy-default"


@needs_postgres
def test_crud_roundtrip_and_revision_conflict(pg_client: TestClient):
    r = pg_client.post("/api/scopes/shared/procedural-engines", json={"name": "My Engine"})
    assert r.status_code == 201, r.text
    engine = r.json()
    eid = engine["id"]
    assert engine["slug"] == "my-engine" and engine["revision"] == 0
    try:
        # reserved slug rejected
        assert (
            pg_client.post(
                "/api/scopes/shared/procedural-engines", json={"name": "x", "slug": "adapy-default"}
            ).status_code
            == 409
        )
        # duplicate slug -> 409
        assert pg_client.post("/api/scopes/shared/procedural-engines", json={"name": "My Engine"}).status_code == 409
        # update with a valid wheel manifest
        doc = {
            "kind": "wheel",
            "repo_url": "git@example.com:me/engine.git",
            "ref": "v1",
            "entrypoint": "engine:compile",
        }
        up = pg_client.put(
            f"/api/scopes/shared/procedural-engines/{eid}",
            json={"name": "My Engine", "doc": doc, "base_revision": 0},
        )
        assert up.status_code == 200, up.text
        assert up.json()["revision"] == 1
        # stale base_revision -> 409
        assert (
            pg_client.put(
                f"/api/scopes/shared/procedural-engines/{eid}",
                json={"name": "My Engine", "doc": doc, "base_revision": 0},
            ).status_code
            == 409
        )
        # invalid manifest -> 422
        assert (
            pg_client.put(
                f"/api/scopes/shared/procedural-engines/{eid}",
                json={"name": "My Engine", "doc": {"kind": "wheel"}, "base_revision": 1},
            ).status_code
            == 422
        )
        # get reflects the committed manifest
        got = pg_client.get(f"/api/scopes/shared/procedural-engines/{eid}").json()
        assert got["doc"]["entrypoint"] == "engine:compile"
    finally:
        pg_client.delete(f"/api/scopes/shared/procedural-engines/{eid}")
