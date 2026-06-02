"""Integration test for the /fea/manifest endpoint.

The bake itself runs in the worker container (the API container is
intentionally slim and lacks ada.fem). These tests cover the API's
half of the flow: cache hit, validation guards, and the enqueue
short-circuit when no NATS is configured. The full bake-and-poll
loop is exercised against a real worker in deployed environments.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import uuid

import pytest

# Same env shim as test_admin.py: importing ada.comms.rest.app
# evaluates a module-level create_app() that needs a writable storage
# root. Point it at a temp dir so import succeeds in CI sandboxes.
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


def _upload(client: TestClient, key: str, data: bytes) -> None:
    r = client.put(f"/api/scopes/shared/blobs/{key}", content=data)
    assert r.status_code in (200, 201), (key, r.status_code, r.text)


def _stage_manifest(tmp_path: pathlib.Path, source_key: str, manifest: dict) -> None:
    """Pre-populate a baked manifest in the test storage so the cache
    path can be exercised without spinning up a worker."""
    target = tmp_path / "shared" / f"_derived/{source_key}.fea/fea.manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest))


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_fea_manifest_returns_cached(app_client: TestClient, tmp_path: pathlib.Path):
    """When the manifest is already in storage the endpoint serves it
    directly with 200 — no enqueue, no worker round-trip."""

    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed-bytes")
    manifest = {
        "version": 1,
        "src": src,
        "mesh": {"url": "fea.mesh.glb", "n_points": 10, "n_cells": 5},
        "fields": [
            {
                "name_canonical": "DEPL",
                "name_native": "DEPL",
                "kind": "vector6",
                "support": "nodal",
                "components": ["DX", "DY", "DZ", "DRX", "DRY", "DRZ"],
                "blob": {
                    "url": "fea.DEPL.bin",
                    "header_bytes": 1024,
                    "stride_bytes": 240,
                    "dtype": "float32",
                    "byte_order": "little",
                },
                "n_steps": 1,
                "steps": [{"i": 0, "value": 0.0, "label": "DEPL"}],
                "scalar_range": {"DX": [0, 1], "magnitude": [0, 1]},
                "default_view": {"reduction": "magnitude", "colormap": "viridis"},
            }
        ],
    }
    _stage_manifest(tmp_path, src, manifest)

    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["src"] == src
    assert body["fields"][0]["name_canonical"] == "DEPL"


def test_fea_manifest_returns_503_when_queue_disabled(app_client: TestClient):
    """No cached manifest + no NATS configured → 503. The bake has to
    run in the worker; the API can't fall back to in-process baking
    because the slim API container lacks ada.fem."""

    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed-bytes")

    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 503, r.text


def test_fea_manifest_rejects_unsupported_extension(app_client: TestClient):
    """A non-FEA source (e.g. .glb) gets a 415 without the bake even
    starting — the picker UI shouldn't ask in the first place but
    the endpoint guards regardless."""

    src = f"models/{uuid.uuid4().hex}.glb"
    _upload(app_client, src, b"glTF\x02\x00\x00\x00")

    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 415, r.text


def test_fea_manifest_404s_on_missing_source(app_client: TestClient):
    src = f"models/{uuid.uuid4().hex}.rmed"
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 404, r.text
