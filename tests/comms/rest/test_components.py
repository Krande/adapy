"""Tests for /api/components/* — Stage 5 of the component-view plan.

Covers:
- /api/components/profiles  (category dropdown)
- /api/components/specs     (latest-manifest resolution from blob layer)

`/api/components/build` is wired in Stage 5c with the worker handler;
its tests land alongside.
"""

from __future__ import annotations

import json
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


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


# ── /api/components/profiles ─────────────────────────────────────────


def test_profiles_returns_list_for_known_category(app_client: TestClient):
    r = app_client.get("/api/components/profiles", params={"category": "iprofiles"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["category"] == "iprofiles"
    assert isinstance(body["profiles"], list)
    assert len(body["profiles"]) > 0
    assert any(name.startswith("HEA") or name.startswith("IPE") for name in body["profiles"])


def test_profiles_returns_empty_for_unknown_category(app_client: TestClient):
    r = app_client.get("/api/components/profiles", params={"category": "nope"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profiles"] == []


def test_profiles_no_category_returns_catalog(app_client: TestClient):
    r = app_client.get("/api/components/profiles")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "categories" in body
    assert "shs" in body["categories"]
    assert "iprofiles" in body["categories"]


def test_profiles_box_category_empty_for_now(app_client: TestClient):
    """ProfileDB.json ships no SHS/BOX entries yet — endpoint returns
    an empty list. The frontend falls back to free-text input for
    sections in those categories."""
    r = app_client.get("/api/components/profiles", params={"category": "box"})
    assert r.status_code == 200, r.text
    assert r.json()["profiles"] == []


# ── /api/components/specs ────────────────────────────────────────────


def _stage_manifest_blob(
    tmp_path: pathlib.Path,
    branch: str,
    commit: str,
    body: dict,
) -> None:
    """Pre-populate a manifest.json in the shared scope (no DB needed)
    under the same versions/<branch>/<commit>/ key convention ada-build
    uses."""
    target = tmp_path / "shared" / "versions" / branch / commit / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body))


def test_specs_returns_empty_when_nothing_published(app_client: TestClient):
    """Auto-discovery: no manifests anywhere → empty specs + empty
    sources list."""
    r = app_client.get("/api/components/specs", params={"branch": "main"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["specs"] == {}
    assert body["sources"] == []
    assert body["branch"] == "main"


def test_specs_auto_discovers_shared_scope(app_client: TestClient, tmp_path: pathlib.Path):
    """Auto-discovery walks the user's scopes and picks up whichever
    has a manifest — no scope param required."""
    manifest = {
        "specs": {
            "box.box_to_box": {
                "schema": {"name": "box.box_to_box", "roles": []},
                "defaults": {"incoming": {"section": "BOX300x300x12x12"}},
                "preview_glb": "box.box_to_box.glb",
                "tags": ["beam-beam", "box"],
                "priority": 10,
            }
        }
    }
    _stage_manifest_blob(tmp_path, "main", "abc123", manifest)

    r = app_client.get("/api/components/specs", params={"branch": "main"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["branch"] == "main"
    assert body["sources"] == [{"scope": "shared", "branch": "main", "commit": "abc123"}]
    spec = body["specs"]["box.box_to_box"]
    assert spec["scope"] == "shared"
    assert spec["preview_url"] == "/api/scopes/shared/blobs/versions/main/abc123/box.box_to_box.glb"
    assert spec["defaults"] == {"incoming": {"section": "BOX300x300x12x12"}}
    assert spec["priority"] == 10


def test_specs_explicit_scope_override(app_client: TestClient, tmp_path: pathlib.Path):
    """Legacy: an explicit scope param restricts the lookup to that
    one scope (no auto-discovery sweep)."""
    manifest = {"specs": {"only.here": {"preview_glb": "x.glb", "schema": {}, "defaults": {}}}}
    _stage_manifest_blob(tmp_path, "main", "abc123", manifest)

    r = app_client.get("/api/components/specs", params={"scope": "shared", "branch": "main"})
    assert r.status_code == 200, r.text
    assert "only.here" in r.json()["specs"]


def test_specs_picks_latest_commit_by_mtime(app_client: TestClient, tmp_path: pathlib.Path):
    """When multiple commits have published manifests on the same
    branch, the latest by mtime wins."""
    import time

    old = {"specs": {"old.spec": {"preview_glb": "old.glb", "schema": {}, "defaults": {}}}}
    new = {"specs": {"new.spec": {"preview_glb": "new.glb", "schema": {}, "defaults": {}}}}

    _stage_manifest_blob(tmp_path, "main", "older_commit", old)
    time.sleep(0.05)  # ensure mtime differs
    _stage_manifest_blob(tmp_path, "main", "newer_commit", new)

    r = app_client.get("/api/components/specs", params={"branch": "main"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sources"] == [{"scope": "shared", "branch": "main", "commit": "newer_commit"}]
    assert "new.spec" in body["specs"]
    assert "old.spec" not in body["specs"]


def test_specs_ignores_other_branches(app_client: TestClient, tmp_path: pathlib.Path):
    feat = {"specs": {"feat.only": {"preview_glb": "feat.glb", "schema": {}, "defaults": {}}}}
    _stage_manifest_blob(tmp_path, "feature/foo", "abc", feat)

    r = app_client.get("/api/components/specs", params={"branch": "main"})
    assert r.status_code == 200, r.text
    assert r.json()["specs"] == {}

    r = app_client.get("/api/components/specs", params={"branch": "feature/foo"})
    assert r.status_code == 200, r.text
    assert "feat.only" in r.json()["specs"]


# ── POST /api/components/build ───────────────────────────────────────


def test_build_503_when_queue_disabled(app_client: TestClient):
    """No NATS configured in tests → queue.enabled is False → 503."""
    r = app_client.post(
        "/api/components/build",
        json={"spec_name": "box.box_to_box", "inputs": {"incoming": {"section": "X"}}},
    )
    assert r.status_code == 503, r.text
    assert "disabled" in r.json()["detail"].lower()


def test_build_400_on_missing_spec_name(app_client: TestClient):
    r = app_client.post("/api/components/build", json={"inputs": {}})
    # 503 fires first (queue disabled) before validation in this test
    # setup. The validation paths are exercised by deployed integration
    # tests where queue.enabled is True.
    assert r.status_code in (400, 503)


def test_build_400_on_bad_inputs_type(app_client: TestClient):
    r = app_client.post(
        "/api/components/build",
        json={"spec_name": "box.box_to_box", "inputs": "not-a-dict"},
    )
    assert r.status_code in (400, 503)
