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
    FIXTURE_PROVIDER_ID,
    SOURCE_FILENAME,
    FakeStore,
    publish_fixture,
)
from tests.core.assets.fixture_provider.second import (  # noqa: E402
    SECOND_BUILD_CAPABILITY,
    SECOND_PROVIDER_ID,
    SECOND_SOURCE_FILENAME,
    publish_second_claim,
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


@pytest.fixture
def two_providers(tmp_path):
    """``pump-b``: provider A's build claim, then provider B's on the SAME subject, newer."""
    store = FakeStore()
    rev_a = publish_fixture(store)
    rev_b = publish_second_claim(store, collection=COLLECTION, subject="pump-b")
    assert rev_b > rev_a

    scope_root = tmp_path / "users" / "local-dev"
    for key, data in store.blobs.items():
        dest = scope_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client, rev_a, rev_b, scope_root


def _expected_second_derived_key(revision: str, node: str = "pump-b") -> str:
    source_key = asset_key(COLLECTION, node, revision, SECOND_SOURCE_FILENAME)
    fingerprint = build_fingerprint(
        options={"outline_ref": node, "source_key": source_key},
        fingerprint_inputs=("source_key", "outline_ref"),
        node=node,
        hierarchy_source=revision,
    )
    return derived_asset_key(
        provider=SECOND_PROVIDER_ID,
        collection=COLLECTION,
        subject=node,
        revision=revision,
        node=node,
        fingerprint=fingerprint,
    )


def _seed_cached(scope_root, derived_key: str) -> None:
    """Only provider A's builder is registered here, so B's build is answered from a seeded
    summary rather than enqueued -- the route's key composition is what is under test."""
    dest = scope_root / derived_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b'{"schema":"ada.assets/build@1","ok":true}')


def test_build_published_takes_the_newest_revision_of_any_provider(two_providers):
    client, _, rev_b, scope_root = two_providers
    _seed_cached(scope_root, _expected_second_derived_key(rev_b))
    r = client.post(_build_url(), json={"provider": "published", "collection": COLLECTION, "node": "pump-b"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["provider"], body["revision"]) == (SECOND_PROVIDER_ID, rev_b)
    assert body["capability"] == SECOND_BUILD_CAPABILITY


def test_build_selects_by_provider_and_each_provider_gets_its_own_key(two_providers):
    client, rev_a, rev_b, scope_root = two_providers
    _seed_cached(scope_root, _expected_second_derived_key(rev_b))

    a = client.post(_build_url(), json={"provider": FIXTURE_PROVIDER_ID, "collection": COLLECTION, "node": "pump-b"})
    b = client.post(_build_url(), json={"provider": SECOND_PROVIDER_ID, "collection": COLLECTION, "node": "pump-b"})
    assert a.status_code == 200 and b.status_code == 200, (a.text, b.text)
    a, b = a.json(), b.json()

    assert (a["provider"], a["revision"], a["capability"]) == (FIXTURE_PROVIDER_ID, rev_a, BUILD_CAPABILITY)
    assert (b["provider"], b["revision"], b["capability"]) == (SECOND_PROVIDER_ID, rev_b, SECOND_BUILD_CAPABILITY)
    assert a["derived_key"] == _expected_derived_key(rev_a)
    assert b["derived_key"] == _expected_second_derived_key(rev_b)
    assert a["derived_key"] != b["derived_key"]
    # The provider is a segment of the derived key, so two providers never share a cache entry.
    assert f"/{FIXTURE_PROVIDER_ID}/" in a["derived_key"] and f"/{SECOND_PROVIDER_ID}/" in b["derived_key"]


def test_build_explicit_revision_by_another_provider_is_409(two_providers):
    client, rev_a, _, _ = two_providers
    r = client.post(
        _build_url(),
        json={"provider": SECOND_PROVIDER_ID, "collection": COLLECTION, "node": "pump-b", "revision": rev_a},
    )
    assert r.status_code == 409, r.text
    assert FIXTURE_PROVIDER_ID in r.json()["detail"]


def test_build_by_a_provider_that_never_published_the_subject_is_404(two_providers):
    client, _, _, _ = two_providers
    r = client.post(_build_url(), json={"provider": "nobody-here", "collection": COLLECTION, "node": "pump-b"})
    assert r.status_code == 404, r.text
    assert "nobody-here" in r.json()["detail"]


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
