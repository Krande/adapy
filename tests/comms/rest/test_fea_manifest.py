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
from ada.comms.rest.converter import (  # noqa: E402
    EXPECTED_FEA_BAKE_VERSION,
    fea_manifest_stale_reason,
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
    """When a FRESH manifest is already in storage the endpoint serves it
    directly with 200 — no enqueue, no worker round-trip."""

    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed-bytes")
    manifest = {
        "version": 1,
        "bake_version": EXPECTED_FEA_BAKE_VERSION,
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


# ── Freshness ────────────────────────────────────────────────────────────────
#
# The cache is keyed by source NAME only, so freshness is the endpoint's whole
# defence against two silent-staleness modes: a deck re-solved and re-uploaded
# under the same name, and a bake made before the current bake output existed
# (property fields, node labels). fea_manifest_stale_reason decides; these pin
# both signals and the endpoint's serve-stale fallback when no worker exists.


def _head(iso: str | None) -> dict:
    return {"size": 1, "last_modified": iso, "e_tag": None}


def test_stale_reason_flags_an_old_or_unstamped_bake():
    assert fea_manifest_stale_reason({"bake_version": EXPECTED_FEA_BAKE_VERSION - 1}, None, None)
    # A pre-stamp manifest counts as 0 — every old deck re-bakes once.
    assert fea_manifest_stale_reason({}, None, None)
    assert fea_manifest_stale_reason({"bake_version": "not-a-number"}, None, None)
    assert fea_manifest_stale_reason({"bake_version": EXPECTED_FEA_BAKE_VERSION}, None, None) is None


def test_stale_reason_flags_a_source_newer_than_its_bake():
    fresh = {"bake_version": EXPECTED_FEA_BAKE_VERSION}
    older = "2026-09-01T10:00:00+00:00"
    newer = "2026-09-02T10:00:00+00:00"
    assert fea_manifest_stale_reason(fresh, _head(newer), _head(older)) == "source newer than bake"
    assert fea_manifest_stale_reason(fresh, _head(older), _head(newer)) is None
    # Missing or unparsable timestamps are INCONCLUSIVE, never stale: a
    # backend without timestamps must not churn every open into a re-bake.
    assert fea_manifest_stale_reason(fresh, _head(None), _head(older)) is None
    assert fea_manifest_stale_reason(fresh, _head("garbage"), _head(older)) is None
    # Mixed naive/aware timestamps compare as inconclusive too.
    assert fea_manifest_stale_reason(fresh, _head("2026-09-02T10:00:00"), _head(older)) is None


def test_fea_manifest_serves_stale_when_queue_disabled(app_client: TestClient, tmp_path: pathlib.Path):
    """A stale bake with no worker to rebuild it: serving the older results
    beats a 503, and the operator learns why from the log."""

    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed-bytes")
    manifest = {"version": 1, "src": src, "mesh": {}, "fields": []}  # unstamped → stale
    _stage_manifest(tmp_path, src, manifest)

    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200, r.text
    assert r.json()["src"] == src


def test_fea_manifest_treats_reuploaded_source_as_stale(app_client: TestClient, tmp_path: pathlib.Path):
    """Re-uploading a deck under the same name outdates its bake. With the
    queue disabled the stale bake still serves (previous test's rule); the
    staleness decision itself is what this pins, via the same code path the
    enqueue branch uses."""

    import os
    import time

    src = f"models/{uuid.uuid4().hex}.rmed"
    manifest = {
        "version": 1,
        "bake_version": EXPECTED_FEA_BAKE_VERSION,
        "src": src,
        "mesh": {},
        "fields": [],
    }
    _stage_manifest(tmp_path, src, manifest)
    # Date the bake firmly into the past, then upload the source: the source
    # object is now newer than its manifest, as after a re-solve.
    manifest_file = tmp_path / "shared" / f"_derived/{src}.fea/fea.manifest.json"
    past = time.time() - 3600
    os.utime(manifest_file, (past, past))
    _upload(app_client, src, b"re-solved-bytes")

    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    # Queue disabled → the stale manifest is served rather than 503; the
    # freshness verdict is covered directly:
    assert r.status_code == 200, r.text

    from datetime import datetime, timezone

    src_head = _head(datetime.now(tz=timezone.utc).isoformat())
    man_head = _head(datetime.fromtimestamp(past, tz=timezone.utc).isoformat())
    assert fea_manifest_stale_reason(manifest, src_head, man_head) == "source newer than bake"


def test_expected_bake_version_is_pinned_to_the_writer():
    """The API container cannot import ada.fem, so it carries a copy of the
    writer's FEA_BAKE_VERSION. This is the test that keeps them equal — a
    bake-output bump that forgets the API side would silently stop re-baking
    anything."""

    from ada.fem.results.artefacts import FEA_BAKE_VERSION

    assert EXPECTED_FEA_BAKE_VERSION == FEA_BAKE_VERSION
