"""Browser-driven (WASM) conversion audit endpoints.

Always-run (no DB needed): the create endpoint 503s without a database
and the update endpoint rejects a non-``wasm-`` job id with 400 before
it ever touches the pool — so the routes are registered and their
input/infra guards behave.

Live-Postgres coverage of the full create→update lifecycle lives in
``test_db.py`` (opt-in via ``ADA_TEST_POSTGRES_URL``), exercising the
db helpers directly.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
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


def test_audit_local_create_requires_db(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))
    r = client.post(
        "/api/scopes/shared/audit/local",
        json={"key": "m.step", "target_format": "glb", "image_tag": "wasm:test"},
    )
    assert r.status_code == 503


def test_audit_local_update_rejects_non_wasm_job_id(tmp_path):
    client = TestClient(create_app(_settings(tmp_path)))
    r = client.post(
        "/api/scopes/shared/audit/local/not-a-wasm-id",
        json={"status": "done"},
    )
    # The wasm- prefix guard runs before the DB check, so a malformed id
    # is a clean 400 even on a deployment without a database.
    assert r.status_code == 400


@needs_postgres
def test_audit_local_http_lifecycle(tmp_path):
    # `with` runs the lifespan so the db pool is initialised + migrated.
    with TestClient(create_app(_settings(tmp_path, POSTGRES_URL))) as client:
        r = client.post(
            "/api/scopes/shared/audit/local",
            json={"key": "m.step", "target_format": "glb", "image_tag": "wasm:pyodide-0.27.7"},
        )
        assert r.status_code == 201, r.text
        job_id = r.json()["job_id"]
        assert job_id.startswith("wasm-")

        r2 = client.post(
            f"/api/scopes/shared/audit/local/{job_id}",
            json={
                "status": "done",
                "duration_ms": 1234,
                "read_bytes": 100,
                "write_bytes": 50,
                "peak_rss_kb": 4096,
                "metrics_samples": [{"ts": 0, "peak_rss_kb": 2048}],
            },
        )
        assert r2.status_code == 200, r2.text

        # Patching a wasm id that was never created → 404 (no owner row).
        r3 = client.post(
            "/api/scopes/shared/audit/local/wasm-never-created",
            json={"status": "done"},
        )
        assert r3.status_code == 404


@needs_postgres
def test_audit_local_create_rejects_run_id_for_unknown_run(tmp_path):
    # auth disabled → the default user is treated as admin, so the
    # admin-gate passes and we hit the "run not found" branch (404).
    with TestClient(create_app(_settings(tmp_path, POSTGRES_URL))) as client:
        r = client.post(
            "/api/scopes/shared/audit/local",
            json={"key": "m.step", "target_format": "glb", "audit_run_id": "no-such-run"},
        )
        assert r.status_code in (403, 404)
