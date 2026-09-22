"""``POST /assets/publish``, ``GET /assets/staging`` and ``DELETE /assets/{collection}/{subject}/
{revision}``, driven end to end by the private-format fixture provider and publisher.

Modelled on ``test_asset_build_routes.py``'s harness: local-storage sandbox, auth disabled. The
routes themselves, the job transport (``LocalJobTransport``) and ``ada.assets.publish``/
``ada.assets.unpublish`` are all owned by other files in this change (see the task's "do not touch"
list) -- this exercises the CONTRACT they already implement: staging recovers from a plain listing,
a publish is core-stamped and manifests-last, and an unpublish is refused while a holder survives.

The fixture PUBLISHER (``tests/core/assets/fixture_provider/publisher.py``) is registered for the
whole module -- the in-process job transport (no NATS in this harness) needs something to resolve
``provider="fixture-lines"`` to, or every publish 501s before it can even derive a plan.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-asset-publish-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.core.assets.fixture_provider.provider import (  # noqa: E402
    FIXTURE_PROVIDER_ID,
)
from tests.core.assets.fixture_provider.publisher import (  # noqa: E402
    FixtureLinesPublisher,
    fixture_source_bytes,
    register_fixture_publisher,
)

from ada.assets.keys import asset_key, staging_prefix  # noqa: E402
from ada.assets.manifest import (  # noqa: E402
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
)
from ada.assets.publishers import asset_publisher, clear_asset_publishers  # noqa: E402
from ada.comms.rest import local_jobs  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

COLLECTION = "fixture-pub"
LOCAL_DEV_SUB = "local-dev"  # ada.comms.rest.auth.User.local_dev() -- the synthetic caller when auth is disabled


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
def _fixture_publisher_registered():
    """Idempotent by origin (``ada.assets.publishers``), same discipline as the builder's own
    fixture in ``test_asset_build_routes.py`` -- cleared afterwards so the process-global registry
    does not leak into other test modules."""
    clear_asset_publishers()
    register_fixture_publisher()
    yield
    clear_asset_publishers()


@pytest.fixture
def client(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        yield c, tmp_path


def _scope_root(tmp_path):
    return tmp_path / "users" / LOCAL_DEV_SUB


def _write(tmp_path, key: str, data: bytes) -> None:
    dest = _scope_root(tmp_path) / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _read(tmp_path, key: str) -> bytes:
    return (_scope_root(tmp_path) / key).read_bytes()


def _exists(tmp_path, key: str) -> bool:
    return (_scope_root(tmp_path) / key).exists()


def _stage(tmp_path, staging_id: str, filename: str = "source.jsonl") -> str:
    key = f"{staging_prefix(staging_id)}{filename}"
    _write(tmp_path, key, fixture_source_bytes())
    return key


def _publish_url() -> str:
    return "/api/scopes/user:me/assets/publish"


def _poll_done(client, job_id: str, *, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status.get("status") != local_jobs.STATUS_RUNNING:
            return status
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


def _blob_json(client, key: str) -> dict:
    r = client.get(f"/api/scopes/user:me/blobs/{key}")
    assert r.status_code == 200, r.text
    body = r.content
    if body[:2] == b"\x1f\x8b":  # tolerate gzip either way, as test_local_plugin_jobs.py does
        import gzip

        body = gzip.decompress(body)
    return json.loads(body)


# --------------------------------------------------------------------------------------------
# POST /assets/publish -- the ordinary staging_id path, run to completion.
# --------------------------------------------------------------------------------------------


def test_publish_with_staging_id_collects_everything_under_it_and_returns_the_job_shape(client):
    c, tmp_path = client
    _stage(tmp_path, "up1")

    r = c.post(_publish_url(), json={"provider": FIXTURE_PROVIDER_ID, "staging_id": "up1", "collection": COLLECTION})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"job_id", "derived_key", "dry_run"}
    assert body["dry_run"] is False
    assert body["job_id"]
    assert body["derived_key"]

    status = _poll_done(c, body["job_id"])
    assert status["status"] == local_jobs.STATUS_DONE, status


def test_a_real_publish_ends_with_manifests_present_written_last_and_change_published_by_stamped(client):
    c, tmp_path = client
    _stage(tmp_path, "up1")

    r = c.post(_publish_url(), json={"provider": FIXTURE_PROVIDER_ID, "staging_id": "up1", "collection": COLLECTION})
    assert r.status_code == 200, r.text
    body = r.json()
    status = _poll_done(c, body["job_id"])
    assert status["status"] == local_jobs.STATUS_DONE, status

    outcome = _blob_json(c, body["derived_key"])
    assert outcome["dry_run"] is False
    assert len(outcome["subjects"]) == 6  # site, unit-1, unit-2, pump-a, pump-b, tank-c
    revision = outcome["revision"]

    # Manifests present, on disk, for every subject this publish declared.
    for subject in outcome["subjects"]:
        manifest_key = asset_key(COLLECTION, subject, revision, MANIFEST_FILENAME)
        assert _exists(tmp_path, manifest_key), manifest_key

    # Written LAST: the collection-level manifest is the final key in the outcome's own order.
    collection_manifest_key = asset_key(COLLECTION, COLLECTION, revision, MANIFEST_FILENAME)
    assert outcome["written"][-1] == collection_manifest_key

    # change.published_by is CORE's stamp, from the authenticated (local-dev) caller, published_via
    # "user" -- exactly what `POST /assets/publish` hard-codes for a caller-initiated publish.
    # EVERY manifest of the publish carries it, collection-level included -- `apply_publish_plan`
    # stamps any write whose filename is `asset.json`, not just the node-level ones.
    manifest = json.loads(_read(tmp_path, collection_manifest_key))
    assert manifest["change"]["published_by"]["id"] == LOCAL_DEV_SUB
    assert manifest["change"]["published_via"] == "user"
    assert set(manifest["change"]) == {"published_by", "published_via"}  # nothing the fixture relays

    node_manifest_key = asset_key(COLLECTION, "pump-a", revision, MANIFEST_FILENAME)
    node_manifest = json.loads(_read(tmp_path, node_manifest_key))
    assert node_manifest["change"]["published_by"]["id"] == LOCAL_DEV_SUB
    assert node_manifest["change"]["published_via"] == "user"


def test_dry_run_publish_writes_nothing_to_disk_but_the_summary_says_so(client):
    c, tmp_path = client
    _stage(tmp_path, "up1")

    r = c.post(
        _publish_url(),
        json={"provider": FIXTURE_PROVIDER_ID, "staging_id": "up1", "collection": COLLECTION, "dry_run": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    status = _poll_done(c, body["job_id"])
    assert status["status"] == local_jobs.STATUS_DONE, status

    outcome = _blob_json(c, body["derived_key"])
    assert outcome["dry_run"] is True
    assert len(outcome["written"]) > 0  # it still REPORTS what it would have written ...
    for key in outcome["written"]:
        assert not _exists(tmp_path, key)  # ... none of which actually landed on disk


# --------------------------------------------------------------------------------------------
# POST /assets/publish -- the request-shape refusals.
# --------------------------------------------------------------------------------------------


def test_a_staged_map_pointing_outside_staging_is_400(client):
    c, _tmp_path = client
    outside_key = asset_key(COLLECTION, COLLECTION, "20260101T000000Z", "source.jsonl")
    r = c.post(_publish_url(), json={"provider": FIXTURE_PROVIDER_ID, "staged": {"source.jsonl": outside_key}})
    assert r.status_code == 400, r.text
    assert "outside" in r.json()["detail"]


def test_missing_staging_id_and_no_staged_map_is_400(client):
    c, _tmp_path = client
    r = c.post(_publish_url(), json={"provider": FIXTURE_PROVIDER_ID})
    assert r.status_code == 400, r.text


def test_a_staging_id_with_nothing_staged_under_it_is_404(client):
    c, _tmp_path = client
    r = c.post(_publish_url(), json={"provider": FIXTURE_PROVIDER_ID, "staging_id": "never-uploaded"})
    assert r.status_code == 404, r.text


# --------------------------------------------------------------------------------------------
# GET /assets/staging -- a listing, not a session.
# --------------------------------------------------------------------------------------------


def test_get_staging_groups_by_id_and_survives_a_reload(tmp_path):
    """Blobs are written directly to disk, never through the app -- the route must answer from
    the store alone, which is what "survives a reload" (the module docstring in routes/assets.py)
    actually means: no browser, no session, nothing remembered by this process."""
    _write(tmp_path, f"{staging_prefix('up1')}source.jsonl", b"one upload's bytes")
    _write(tmp_path, f"{staging_prefix('up2')}source.jsonl", b"a different upload's bytes, longer")

    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        r = c.get("/api/scopes/user:me/assets/staging")
        assert r.status_code == 200, r.text
        staged = {g["staging_id"]: g for g in r.json()["staged"]}

    assert set(staged) == {"up1", "up2"}
    assert staged["up1"]["files"][0]["file"] == "source.jsonl"
    assert staged["up1"]["size"] == len(b"one upload's bytes")
    assert staged["up2"]["size"] == len(b"a different upload's bytes, longer")


# --------------------------------------------------------------------------------------------
# DELETE /assets/{collection}/{subject}/{revision} -- the refcount-checked unpublish.
# --------------------------------------------------------------------------------------------


REVISION = "20260921T143001Z"


def _seed_collection_and_holder(tmp_path) -> str:
    """A collection-level revision holding the real source blob, and ONE node manifest at the
    same revision that references it by absolute ``key`` -- written straight to disk so this test
    exercises only the DELETE route, not a publish."""
    source_key = asset_key(COLLECTION, COLLECTION, REVISION, "source.jsonl")
    _write(tmp_path, source_key, b"the shared source bytes")
    collection_manifest = AssetManifest(
        provider=FIXTURE_PROVIDER_ID,
        collection=COLLECTION,
        subject=COLLECTION,
        revision=REVISION,
        node=None,
        produced_at="2026-09-21T14:29:00Z",
        published_at="2026-09-21T14:30:01Z",
        delivery="none",
        artefacts=(ArtefactEntry(role="source", file="source.jsonl", sha256="a" * 64, size=24),),
    )
    _write(tmp_path, asset_key(COLLECTION, COLLECTION, REVISION, MANIFEST_FILENAME), collection_manifest.to_json())

    holder_manifest = AssetManifest(
        provider=FIXTURE_PROVIDER_ID,
        collection=COLLECTION,
        subject="pump-a",
        revision=REVISION,
        node="pump-a",
        produced_at="2026-09-21T14:29:00Z",
        published_at="2026-09-21T14:30:01Z",
        delivery="none",
        artefacts=(ArtefactEntry(role="source", key=source_key, sha256="a" * 64, size=24),),
    )
    _write(tmp_path, asset_key(COLLECTION, "pump-a", REVISION, MANIFEST_FILENAME), holder_manifest.to_json())
    return source_key


def _delete_url(subject: str, revision: str = REVISION) -> str:
    return f"/api/scopes/user:me/assets/{COLLECTION}/{subject}/{revision}"


def test_delete_refuses_with_409_and_a_reason_while_a_holder_exists(client):
    c, tmp_path = client
    source_key = _seed_collection_and_holder(tmp_path)

    r = c.delete(_delete_url(COLLECTION))
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["reason"]
    assert "pump-a" in body["reason"] or any("pump-a" in h for h in body["held_by"])
    assert _exists(tmp_path, source_key)  # refused means untouched, not partially deleted


def test_delete_succeeds_once_the_holder_is_gone(client):
    c, tmp_path = client
    source_key = _seed_collection_and_holder(tmp_path)

    r = c.delete(_delete_url("pump-a"))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["deleted"]

    r = c.delete(_delete_url(COLLECTION))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted"]
    assert body["deleted"][0] == asset_key(COLLECTION, COLLECTION, REVISION, MANIFEST_FILENAME)  # manifest first
    assert not _exists(tmp_path, source_key)


def test_delete_of_an_absent_subject_revision_is_404(client):
    c, _tmp_path = client
    r = c.delete(_delete_url("no-such-subject"))
    assert r.status_code == 404, r.text
    assert r.json()["ok"] is False


def test_register_fixture_publisher_resolves_by_provider_id():
    # The autouse fixture already registered it once; a second registration (discovery plus an
    # explicit preload, say) is a no-op by origin, not a conflict -- the same idempotency
    # `ada.assets.registry` promises for tree providers.
    register_fixture_publisher()
    publisher = asset_publisher(FIXTURE_PROVIDER_ID)
    assert isinstance(publisher, FixtureLinesPublisher)


def test_published_via_is_read_from_the_caller_not_the_body():
    """Decision 6's first two trust levels. A scheduled firing arrives as the deployment's own
    identity, and recording that as a `user` publish would put a person's name on a revision
    nobody pushed -- so the field is derived from the authenticated principal, and a request body
    cannot influence it."""
    from ada.comms.rest.routes.assets import _published_via
    from ada.comms.rest.routes.deps import SystemUser

    class _Person:
        sub = "alice@example.invalid"

    assert _published_via(_Person()) == "user"
    assert _published_via(SystemUser()) == "service"
