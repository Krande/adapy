"""Integration test for the /fea/manifest endpoint.

Uploads a real RMED fixture into the test storage, hits the
endpoint, asserts the bake produced the expected artefact tree
under ``_derived/<src>.fea/``, and that subsequent requests hit
cache instead of rebaking.

Mirrors the create_app-with-local-storage pattern from
test_admin.py — the API process bakes synchronously today, so the
TestClient can drive the whole flow without a worker queue.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import tempfile
import uuid

import pytest

# Same env shim as test_admin.py: importing ada.comms.rest.app
# evaluates a module-level create_app() that needs a writable storage
# root. Point it at a temp dir so import succeeds in CI sandboxes.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault(
    "ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-")
)

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
    """PUT raw bytes to the shared scope under ``key``."""
    r = client.put(f"/api/scopes/shared/blobs/{key}", content=data)
    assert r.status_code in (200, 201), (key, r.status_code, r.text)


def _fixture_bytes(rel: str) -> bytes:
    here = pathlib.Path(__file__).resolve()
    files_root = here.parents[3] / "files"
    p = files_root / rel
    if not p.exists():
        pytest.skip(f"fixture not present: {rel}")
    return p.read_bytes()


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_fea_manifest_bakes_on_first_request(app_client: TestClient):
    """First request bakes; manifest body has expected shape and the
    artefact tree lands under _derived/<src>.fea/."""

    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(
        app_client,
        src,
        _fixture_bytes("fem_files/code_aster/Cantilever_CA_EIG_bm.rmed"),
    )

    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200, r.text
    manifest = r.json()
    assert manifest["version"] == 1
    assert manifest["src"] == src
    assert manifest["mesh"]["url"] == "fea.mesh.glb"
    assert manifest["mesh"]["n_points"] > 0
    assert manifest["fields"]
    for field in manifest["fields"]:
        assert field["support"] == "nodal"
        assert field["blob"]["url"].endswith(".bin")
        assert field["default_view"]["colormap"] == "viridis"

    # Each artefact landed in storage under the per-source prefix and
    # is fetchable via the regular /blobs endpoint, so the manifest's
    # blob URLs work without a separate FEA blob route.
    for filename in ["fea.manifest.json", "fea.mesh.glb"] + [
        f["blob"]["url"] for f in manifest["fields"]
    ]:
        b = app_client.get(f"/api/scopes/shared/blobs/_derived/{src}.fea/{filename}")
        assert b.status_code == 200, (filename, b.text)
        assert int(b.headers.get("content-length", "0")) > 0 or b.content


def test_fea_manifest_second_request_serves_cached(app_client: TestClient):
    """Two requests in a row should return the same manifest body. The
    second is served from cache (no re-bake) — the public assertion
    is just that the response is identical and stable."""

    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(
        app_client,
        src,
        _fixture_bytes("fem_files/code_aster/Cantilever_CA_EIG_bm.rmed"),
    )

    r1 = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r1.status_code == 200, r1.text
    r2 = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r2.status_code == 200, r2.text
    assert r1.json() == r2.json()


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


def test_fea_manifest_handles_sif_via_fearesult_adapter(app_client: TestClient):
    """SIF flows through the FEAResult adapter dispatch — same endpoint,
    no special-case wiring at the endpoint level."""

    src = f"models/{uuid.uuid4().hex}.sif"
    _upload(
        app_client,
        src,
        _fixture_bytes("fem_files/sesam/1EL_SHELL_R1.SIF"),
    )

    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200, r.text
    manifest = r.json()
    assert manifest["fields"]
