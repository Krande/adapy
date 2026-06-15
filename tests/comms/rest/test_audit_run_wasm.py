"""WASM audit-run target (section F).

A run created with ``worker_pool="wasm"`` must: be accepted without NATS,
compute the same cell matrix the worker dispatcher would, expose it via
``GET /api/admin/audit/runs/{id}/cells``, and let the browser close cells
through the ``audit/local`` endpoints (carrying ``audit_run_id`` so the
run counters advance). Live-Postgres only (opt-in via
``ADA_TEST_POSTGRES_URL``) — audit runs are DB-backed.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.scope import Scope  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)


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
        database_url=POSTGRES_URL,
    )


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _storage(client: TestClient):
    for route in client.app.routes:
        ep = getattr(route, "endpoint", None)
        for c in getattr(ep, "__closure__", None) or ():
            v = c.cell_contents
            if v.__class__.__name__ == "Storage":
                return v
    raise RuntimeError("storage not found on app")


@needs_postgres
def test_wasm_run_lists_cells_and_closes_via_audit_local(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        # Stage a convertible source so the run has at least one cell.
        _run(_storage(client).put_bytes(Scope.shared(), "models/part.step", b"ISO-10303-21;"))

        r = client.post(
            "/api/admin/audit/runs",
            json={"scope": "shared", "worker_pool": "wasm"},
        )
        assert r.status_code == 202, r.text
        run = r.json()
        run_id = run["id"]

        # Background dispatch (run synchronously by TestClient) set the
        # total without enqueueing anything.
        rget = client.get(f"/api/admin/audit/runs/{run_id}")
        assert rget.status_code == 200
        total = rget.json()["run"]["total"]
        assert total >= 1

        rcells = client.get(f"/api/admin/audit/runs/{run_id}/cells")
        assert rcells.status_code == 200
        cells = rcells.json()["cells"]
        assert len(cells) == total
        assert all(c["done"] is False for c in cells)

        # Close the first cell through the browser (WASM) audit flow.
        cell = cells[0]
        rc = client.post(
            "/api/scopes/shared/audit/local",
            json={
                "key": cell["source_key"],
                "target_format": cell["target_format"],
                "audit_run_id": run_id,
                "image_tag": "wasm:test",
            },
        )
        assert rc.status_code == 201, rc.text
        job_id = rc.json()["job_id"]
        ru = client.post(
            f"/api/scopes/shared/audit/local/{job_id}",
            json={"status": "done", "duration_ms": 5, "write_bytes": 10},
        )
        assert ru.status_code == 200, ru.text

        # The closed cell now reads done=True (resume skips it) and the
        # run's ok counter advanced.
        rcells2 = client.get(f"/api/admin/audit/runs/{run_id}/cells")
        done = {(c["source_key"], c["target_format"]) for c in rcells2.json()["cells"] if c["done"]}
        assert (cell["source_key"], cell["target_format"]) in done

        rget2 = client.get(f"/api/admin/audit/runs/{run_id}")
        assert rget2.json()["run"]["ok"] == 1


@needs_postgres
def test_wasm_run_cells_404_for_unknown_run(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        r = client.get("/api/admin/audit/runs/not-a-real-run/cells")
        assert r.status_code == 404
