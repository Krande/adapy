"""``POST /assets/build``, driven by the private-format fixture provider.

Modelled on ``test_asset_routes.py``'s harness: local-storage sandbox, auth disabled. The route
itself, the job transport (``LocalJobTransport``) and the summary validator are all owned by other
files in this change (see the task's "do not touch" list) -- this exercises the CONTRACT they
already implement: the claim is read off the node's manifest, the key is composed from identity, a
repeat answers from the store, a non-``build`` claim is refused, and an unknown node is a 404.

Any path that does not pre-seed a cached summary ends up enqueuing a background build, so the
trivial fixture builder is registered for the whole module -- the in-process job transport (no NATS
in this harness) needs something to resolve the capability to, or every non-cached POST 501s before
it can even compose a response. Each test asserts on the immediate response only; the real build
path, followed to completion, is ``tests/core/assets/test_fixture_builder.py``'s job.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-asset-build-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.core.assets.fixture_provider.builder import (  # noqa: E402
    register_fixture_builder,
)
from tests.core.assets.fixture_provider.provider import (  # noqa: E402
    BUILD_CAPABILITY,
    SOURCE_FILENAME,
    FakeStore,
    publish_fixture,
)

from ada.assets.build import build_fingerprint, derived_asset_key  # noqa: E402
from ada.assets.builders import clear_asset_builders  # noqa: E402
from ada.assets.keys import asset_key  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

COLLECTION = "fixture-a"


def _settings(tmp_path) -> Settings:
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
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


@pytest.fixture(autouse=True)
def _fixture_builder_registered():
    """Idempotent by origin (``ada.assets.builders``), so this never conflicts across tests --
    cleared afterwards so the process-global registry does not leak into other test modules."""
    clear_asset_builders()
    register_fixture_builder()
    yield
    clear_asset_builders()


@pytest.fixture
def client_and_revision(tmp_path):
    store = FakeStore()
    revision = publish_fixture(store)

    scope_root = tmp_path / "users" / "local-dev"
    for key, data in store.blobs.items():
        dest = scope_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client, revision, scope_root


def _build_url() -> str:
    return "/api/scopes/user:me/assets/build"


def _expected_derived_key(revision: str, node: str = "pump-b") -> str:
    """Reproduce exactly what the route composes, from public fixture facts alone -- the same
    identity a caller has no other way to predict, which is the point of pinning its shape here."""
    source_key = asset_key(COLLECTION, COLLECTION, revision, SOURCE_FILENAME)
    fingerprint = build_fingerprint(
        options={"ref": node, "source_key": source_key},
        fingerprint_inputs=("source_key", "ref"),
        node=node,
        hierarchy_source=revision,  # fixture manifests never set hierarchy_revision
    )
    return derived_asset_key(
        provider="fixture-lines",
        collection=COLLECTION,
        subject=node,
        revision=revision,
        node=node,
        fingerprint=fingerprint,
    )


def test_build_returns_a_derived_key_composed_from_identity(client_and_revision):
    client, revision, _ = client_and_revision
    r = client.post(_build_url(), json={"provider": "published", "collection": COLLECTION, "node": "pump-b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["derived_key"] == _expected_derived_key(revision)
    assert body["capability"] == BUILD_CAPABILITY
    assert body["provider"] == "fixture-lines"
    assert body["subject"] == "pump-b"
    assert body["node"] == "pump-b"
    assert body["revision"] == revision


def test_repeat_is_cached_once_the_summary_blob_exists(client_and_revision, tmp_path):
    client, revision, scope_root = client_and_revision
    derived_key = _expected_derived_key(revision)

    # Simulate a completed build: the route only checks existence for the cache short-circuit, so
    # writing any bytes at the key it would have composed is enough to answer from the store.
    dest = scope_root / derived_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b'{"schema":"ada.assets/build@1","ok":true}')

    r = client.post(_build_url(), json={"provider": "published", "collection": COLLECTION, "node": "pump-b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["derived_key"] == derived_key
    assert body["cached"] is True
    assert body["job_id"] is None


def test_a_mesh_claim_is_409(client_and_revision):
    client, _, _ = client_and_revision
    r = client.post(_build_url(), json={"provider": "published", "collection": COLLECTION, "node": "pump-a"})
    assert r.status_code == 409, r.text
    assert "mesh" in r.json()["detail"]


def test_an_unknown_node_is_404(client_and_revision):
    client, _, _ = client_and_revision
    r = client.post(_build_url(), json={"provider": "published", "collection": COLLECTION, "node": "not-a-node"})
    assert r.status_code == 404, r.text


def test_force_true_re_enqueues_even_when_a_summary_already_exists(client_and_revision, tmp_path):
    client, revision, scope_root = client_and_revision
    derived_key = _expected_derived_key(revision)
    dest = scope_root / derived_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b'{"schema":"ada.assets/build@1","ok":true}')

    r = client.post(
        _build_url(),
        json={"provider": "published", "collection": COLLECTION, "node": "pump-b", "force": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["derived_key"] == derived_key
    assert body["cached"] is False
    assert body["job_id"] is not None
