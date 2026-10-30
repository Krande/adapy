"""Admin "Clear all derived": one request purges every derived blob in the scope, sources stay."""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-purge-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402

from .test_admin_move_to_folder import _put, _settings  # noqa: E402


@pytest.fixture
def app_client(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def _keys(client: TestClient) -> set[str]:
    files = client.get("/api/admin/scopes/shared/files").json()["files"]
    out = set()
    for f in files:
        out.add(f["key"])
        out.update(d["key"] for d in f.get("derived", []))
    return out


def test_purge_removes_every_derived_blob_in_batches_and_keeps_sources(app_client: TestClient):
    _put(app_client, "models/a.step", b"step")
    _put(app_client, "models/b.rmed", b"rmed")
    # More than one 1000-key batch, plus an FEA tree.
    for i in range(2500):
        _put(app_client, f"_derived/models/a.step.{i:05d}.prof", b"p")
    _put(app_client, "_derived/models/a.step.glb", b"glb")
    _put(app_client, "_derived/models/b.rmed.fea/fea.manifest.json", b"{}")
    _put(app_client, "_derived/models/b.rmed.fea/fea.DEPL.bin", b"\0" * 8)

    r = app_client.delete("/api/admin/scopes/shared/derived")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 2503, "errors": []}
    keys = _keys(app_client)
    assert {"models/a.step", "models/b.rmed"} <= keys
    assert not any(k.startswith("_derived/") for k in keys)
    # Nothing left: a second purge is a no-op.
    assert app_client.delete("/api/admin/scopes/shared/derived").json() == {"deleted": 0, "errors": []}


def test_purge_of_one_source_leaves_a_lookalike_source_alone(app_client: TestClient):
    _put(app_client, "m/a.step", b"step")
    _put(app_client, "m/a.step.v2", b"step")
    _put(app_client, "_derived/m/a.step.glb", b"glb")
    _put(app_client, "_derived/m/a.step.0123abcd.prof", b"p")
    _put(app_client, "_derived/m/a.step.v2.glb", b"glb")  # also starts with "_derived/m/a.step."

    r = app_client.delete("/api/admin/scopes/shared/derived", params={"source": "m/a.step"})
    assert r.json() == {"deleted": 2, "errors": []}
    assert "_derived/m/a.step.v2.glb" in _keys(app_client)
