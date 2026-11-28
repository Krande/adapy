"""A build made in the browser: ``POST /assets/build`` with ``browser: true``, then
``POST /assets/build/result``.

Same harness as ``test_asset_build_routes.py`` (local storage, auth off, the fixture provider and
its builder). The browser's half runs here in-process -- ``ada.assets.wasm_build.run`` is what the
viewer's Pyodide worker calls -- against a storage facade that reads through the blob route, as the
worker's facade does, and keeps what the builder writes. Then the upload is framed the way the
viewer frames it (``ada.assets.build_upload``) and the server is asked to store it.
"""

from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-asset-build-browser-"))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from tests.comms.rest.test_asset_build_routes import COLLECTION, _settings  # noqa: E402
from tests.core.assets.fixture_provider.builder import (  # noqa: E402
    register_fixture_builder,
)
from tests.core.assets.fixture_provider.provider import (  # noqa: E402
    BUILD_CAPABILITY,
    FakeStore,
    publish_fixture,
)

from ada.assets import wasm_build  # noqa: E402
from ada.assets.build_upload import UploadedFile, encode_build_upload  # noqa: E402
from ada.assets.builders import clear_asset_builders  # noqa: E402
from ada.comms.rest import execution_policy as ep  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402

SCOPE = "user:me"
NODE = "pump-b"


@pytest.fixture(autouse=True)
def _fixture_builder_registered():
    clear_asset_builders()
    register_fixture_builder()
    yield
    clear_asset_builders()


@pytest.fixture
def client(tmp_path):
    store = FakeStore()
    publish_fixture(store)
    scope_root = tmp_path / "users" / "local-dev"
    for key, data in store.blobs.items():
        dest = scope_root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


class _BlobRouteStorage:
    """The viewer's facade: reads through the scope's blob route, keeps what the builder writes."""

    def __init__(self, client: TestClient):
        self.client = client
        self.written: dict[str, tuple[bytes, str | None]] = {}

    def get_bytes(self, key: str) -> bytes:
        if key in self.written:
            return self.written[key][0]
        r = self.client.get(f"/api/scopes/{SCOPE}/blobs/{key}")
        assert r.status_code == 200, (key, r.text)
        return r.content

    def put_bytes(self, key: str, data: bytes, content_encoding: str | None = None, **_) -> None:
        self.written[key] = (bytes(data), content_encoding)


def _plan(client: TestClient) -> dict:
    r = client.post(
        f"/api/scopes/{SCOPE}/assets/build",
        json={"provider": "published", "collection": COLLECTION, "node": NODE, "browser": True},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _browser_build(client: TestClient, plan: dict) -> tuple[dict, list[UploadedFile]]:
    storage = _BlobRouteStorage(client)
    summary = wasm_build.run(plan["build"], storage=storage)
    prefix = plan["build"]["derived_prefix"] + "/"
    files = [UploadedFile(name=k[len(prefix) :], data=d, content_encoding=e) for k, (d, e) in storage.written.items()]
    return summary, files


def _request_of(plan: dict) -> dict:
    return {
        "provider": "published",
        "collection": COLLECTION,
        "node": plan["node"],
        "subject": plan["subject"],
        "revision": plan["revision"],
    }


def _upload(client: TestClient, plan: dict, summary: dict, files: list[UploadedFile]):
    return client.post(
        f"/api/scopes/{SCOPE}/assets/build/result",
        content=encode_build_upload(_request_of(plan), summary, files),
        headers={"Content-Type": "application/octet-stream"},
    )


def test_a_browser_build_is_planned_not_enqueued(client):
    plan = _plan(client)
    assert plan["job_id"] is None
    assert plan["cached"] is False
    assert plan["execution"] == "browser"
    build = plan["build"]
    assert build["capability"] == BUILD_CAPABILITY
    assert plan["derived_key"] == f"{build['derived_prefix']}/summary.json"
    for field in ("provider", "subject", "revision", "node", "fingerprint"):
        assert build[field] == plan[field]


def test_the_browser_build_is_stored_where_every_reader_looks(client):
    plan = _plan(client)
    summary, files = _browser_build(client, plan)
    assert [f.name for f in files] == ["model.glb"]
    r = _upload(client, plan, summary, files)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stored"] is True and body["cached"] is False and body["job_id"] is None
    assert body["glb_key"] == summary["glb_key"]

    stored = client.get(f"/api/scopes/{SCOPE}/blobs/{plan['derived_key']}")
    assert stored.status_code == 200
    doc = json.loads(stored.content)
    assert doc["execution"] == "browser"
    assert doc["provenance"]["fingerprint"] == plan["fingerprint"]
    assert client.get(f"/api/scopes/{SCOPE}/blobs/{summary['glb_key']}").content == files[0].data

    # The next ask is a lookup, for anyone: no plan, no job.
    again = _plan(client)
    assert again["cached"] is True and again["job_id"] is None and "build" not in again
    # A second upload for the same key is not written over the first.
    r2 = _upload(client, plan, summary, files)
    assert r2.status_code == 200, r2.text
    assert r2.json()["stored"] is False and r2.json()["cached"] is True


def _tampered(summary: dict, **prov) -> dict:
    return {**summary, "provenance": {**summary["provenance"], **prov}}


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        # A summary that answers another request.
        (lambda s, f: (_tampered(s, fingerprint="0" * 16), f), "fingerprint"),
        (lambda s, f: (_tampered(s, node="pump-a"), f), "node"),
        # A model outside the prefix core composed.
        (lambda s, f: ({**s, "glb_key": "_derived/elsewhere/model.glb"}, f), "outside the prefix"),
        # The summary names a model the upload does not carry.
        (lambda s, f: (s, []), "not a file of the upload"),
        # Bytes that are not a GLB.
        (lambda s, f: (s, [UploadedFile("model.glb", b"not a glb at all, just bytes")]), "GLB"),
        # The GLB's own provenance disagreeing with the summary's.
        (lambda s, f: ({**s, "glb_size": 1}, f), "glb_size"),
        # A second model, and a file that is not JSON.
        (lambda s, f: (s, [*f, UploadedFile("other.glb", f[0].data)]), "one model"),
        (lambda s, f: (s, [*f, UploadedFile("notes.json", b"{not json")]), "not JSON"),
    ],
)
def test_an_upload_that_does_not_answer_the_request_is_refused(client, mutate, fragment):
    plan = _plan(client)
    summary, files = _browser_build(client, plan)
    bad_summary, bad_files = mutate(summary, files)
    r = _upload(client, plan, bad_summary, bad_files)
    assert r.status_code == 409, r.text
    assert fragment in r.json()["detail"]
    # Nothing landed: the node is still unbuilt.
    assert client.get(f"/api/scopes/{SCOPE}/blobs/{plan['derived_key']}").status_code == 404


def test_a_file_name_that_is_not_one_plain_name_is_a_400(client):
    plan = _plan(client)
    summary, files = _browser_build(client, plan)
    r = _upload(client, plan, summary, [UploadedFile("../model.glb", files[0].data)])
    assert r.status_code == 400, r.text
    assert "plain name" in r.json()["detail"]


def test_an_empty_build_is_stored_as_the_answer(client):
    plan = _plan(client)
    request = wasm_build.request_of(plan["build"])
    summary = wasm_build.empty_build_summary(request, "nothing this builder draws").to_dict()
    r = _upload(client, plan, summary, [])
    assert r.status_code == 200, r.text
    doc = json.loads(client.get(f"/api/scopes/{SCOPE}/blobs/{plan['derived_key']}").content)
    assert doc["empty"] is True and doc["ok"] is False


def test_a_truncated_frame_is_a_400(client):
    plan = _plan(client)
    summary, files = _browser_build(client, plan)
    body = encode_build_upload(_request_of(plan), summary, files)
    r = client.post(f"/api/scopes/{SCOPE}/assets/build/result", content=body[:-5])
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_the_policy_can_refuse_browser_builds(monkeypatch):
    async def _policy(pool, sub):
        return ep.effective_policy({"asset_build": {"*": ["pyodide", "worker"], "asset-build-x": ["worker"]}}, None)

    monkeypatch.setattr(ep, "load_effective_policy", _policy)
    await ep.require_browser_asset_build(object(), None, "asset-build-y")
    with pytest.raises(HTTPException) as exc:
        await ep.require_browser_asset_build(object(), None, "asset-build-x")
    assert exc.value.status_code == 403
    assert "asset-build-x" in exc.value.detail
