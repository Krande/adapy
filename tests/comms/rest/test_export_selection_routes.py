"""``POST /scopes/{scope}/export-selection`` -- a selection as a STEP/IFC download, driven by the
LOCAL (queue-less) job transport this harness runs under, the same way the clash-check routes are.

What is under test is the WIRING: the route names the model one way, refuses what cannot be
exported before anything is queued (a GLB, a mesh-delivered node), composes a derived key that a
repeat is answered from, and the job writes a file the blob route hands back as it is. Whether
the selection itself is found is ``test_selection_export.py``'s business.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-export-selection-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"


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


@pytest.fixture
def client_and_scope(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client, tmp_path / SCOPE_DIR


def _stage(scope_dir: Path, rel_key: str, data: bytes) -> str:
    dest = scope_dir / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel_key


def _two_deck_model():
    import ada

    return ada.Assembly("asm") / [
        ada.Part("deck_a")
        / [ada.Beam("bm1", (0, 0, 0), (5, 0, 0), "IPE300"), ada.Beam("bm3", (0, 0, 0), (0, 5, 0), "IPE300")],
        ada.Part("deck_b") / [ada.Beam("bm2", (0, 0, 3), (5, 0, 3), "IPE300")],
    ]


@pytest.fixture
def ifc_source_key(client_and_scope, tmp_path):
    _, scope_dir = client_and_scope
    ifc_path = tmp_path / "decks.ifc"
    _two_deck_model().to_ifc(ifc_path)
    return _stage(scope_dir, "models/decks.ifc", ifc_path.read_bytes())


def _wait_done(client, job_id, *, timeout_s=120.0) -> dict:
    if job_id is None:
        return {"status": "done"}
    status = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


def _export(client, body: dict) -> dict:
    r = client.post(f"/api/scopes/{SCOPE}/export-selection", json=body)
    assert r.status_code == 200, r.text
    status = _wait_done(client, r.json()["job_id"])
    assert status["status"] == "done", status
    return r.json()


def _download(client, key: str) -> bytes:
    r = client.get(f"/api/scopes/{SCOPE}/blobs/{key}")
    assert r.status_code == 200, r.text
    return r.content


# ── a file core reads ─────────────────────────────────────────────────


def test_a_selected_deck_downloads_as_an_ifc_holding_only_that_deck(client_and_scope, ifc_source_key, tmp_path):
    import ada

    client, _ = client_and_scope
    out = _export(client, {"source_key": ifc_source_key, "format": "ifc", "element": "deck_a", "path": ["deck_a"]})
    assert out["filename"] == "deck_a.ifc"

    dest = tmp_path / "downloaded.ifc"
    dest.write_bytes(_download(client, out["derived_key"]))
    names = sorted(o.name for o in ada.from_ifc(dest).get_all_physical_objects())
    assert names == ["bm1", "bm3"]


def test_a_selected_member_downloads_as_a_step(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    out = _export(client, {"source_key": ifc_source_key, "format": "step", "element": "bm2", "path": ["deck_b", "bm2"]})
    assert out["filename"] == "bm2.step"
    assert _download(client, out["derived_key"]).startswith(b"ISO-10303-21")


def test_the_whole_model_is_named_after_its_label(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    out = _export(client, {"source_key": ifc_source_key, "format": "ifc", "label": "decks"})
    assert out["filename"] == "decks.ifc"


def test_a_repeat_is_answered_from_the_store_with_no_job(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    body = {"source_key": ifc_source_key, "format": "ifc", "element": "deck_b", "path": ["deck_b"]}
    first = _export(client, body)
    second = client.post(f"/api/scopes/{SCOPE}/export-selection", json=body).json()
    assert second == {**first, "job_id": None, "cached": True}


def test_a_selection_the_model_does_not_hold_fails_with_the_reason(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    r = client.post(
        f"/api/scopes/{SCOPE}/export-selection",
        json={"source_key": ifc_source_key, "format": "ifc", "element": "nope", "path": ["nope"]},
    )
    assert r.status_code == 200, r.text
    status = _wait_done(client, r.json()["job_id"])
    assert status["status"] == "error"
    assert "no element named 'nope'" in status["error"]


def test_a_glb_is_refused_before_anything_is_queued(client_and_scope):
    client, scope_dir = client_and_scope
    key = _stage(scope_dir, "models/mesh.glb", b"glTF")
    r = client.post(f"/api/scopes/{SCOPE}/export-selection", json={"source_key": key, "format": "step"})
    assert r.status_code == 415
    assert "no model behind its tree" in r.json()["detail"]


def test_a_missing_source_is_404(client_and_scope):
    client, _ = client_and_scope
    r = client.post(f"/api/scopes/{SCOPE}/export-selection", json={"source_key": "models/gone.ifc", "format": "ifc"})
    assert r.status_code == 404


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"source_key": "models/a.ifc", "format": "obj"}, "'format' must be one of"),
        ({"format": "ifc"}, "is required"),
        ({"source_key": "models/a.ifc", "collection": "c", "subject": "s", "format": "ifc"}, "ONE way"),
        ({"source_key": "models/a.ifc", "format": "ifc", "path": ["x"]}, "'element' is required"),
        ({"source_key": "models/a.ifc", "format": "ifc", "element": "x", "path": ["x", "y"]}, "must end at"),
    ],
)
def test_a_malformed_request_is_400(client_and_scope, body, fragment):
    client, _ = client_and_scope
    r = client.post(f"/api/scopes/{SCOPE}/export-selection", json=body)
    assert r.status_code == 400
    assert fragment in r.json()["detail"]


# ── a published node, read by its provider ─────────────────────────────

LINES_PROVIDER = "fixture-lines"
LINES_COLLECTION = "lines-a"
LINES_SUBJECT = "unit-1"
LINES_REVISION = "20260928T000000Z"
LINES_SOURCE = b"\n".join([b"bm0 0 0 0 4 0 0", b"bm1 4 0 0 4 4 0"])


class _LinesConcepts:
    """Reads one member per line -- a format core has no reader for, so an export that succeeds
    can only have gone through the provider."""

    def concepts(self, options, *, storage, scope=None, node=None):
        import ada

        raw = storage.get_bytes(options["source_key"]).decode()
        part = ada.Part(node or "lines")
        for line in (ln for ln in raw.splitlines() if ln.strip()):
            name, x0, y0, z0, x1, y1, z1 = line.split()
            part.add_beam(
                ada.Beam(name, (float(x0), float(y0), float(z0)), (float(x1), float(y1), float(z1)), "IPE200")
            )
        return part


def _publish(scope_dir: Path, subject: str, *, delivery: str) -> None:
    from ada.assets.keys import asset_key
    from ada.assets.manifest import (
        MANIFEST_FILENAME,
        ArtefactEntry,
        AssetManifest,
        BuildSpec,
    )

    source_key = asset_key(LINES_COLLECTION, LINES_SUBJECT, LINES_REVISION, "lines.txt")
    _stage(scope_dir, source_key, LINES_SOURCE)
    manifest = AssetManifest(
        provider=LINES_PROVIDER,
        collection=LINES_COLLECTION,
        subject=subject,
        revision=LINES_REVISION,
        node=subject,
        produced_at="2026-09-28T00:00:00Z",
        published_at="2026-09-28T00:00:00Z",
        delivery=delivery,
        build=BuildSpec(capability="build-lines", options={"source_key": source_key}) if delivery == "build" else None,
        artefacts=(ArtefactEntry(role="source", key=source_key, sha256="", size=len(LINES_SOURCE)),),
    )
    _stage(scope_dir, asset_key(LINES_COLLECTION, subject, LINES_REVISION, MANIFEST_FILENAME), manifest.to_json())


@pytest.fixture
def published_lines_node(client_and_scope):
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts

    _, scope_dir = client_and_scope
    _publish(scope_dir, LINES_SUBJECT, delivery="build")
    register_asset_concepts(LINES_PROVIDER, lambda: _LinesConcepts())
    yield
    clear_asset_concepts()


def test_a_published_node_exports_through_its_provider(client_and_scope, published_lines_node, tmp_path):
    import ada

    client, _ = client_and_scope
    out = _export(
        client,
        {"collection": LINES_COLLECTION, "subject": LINES_SUBJECT, "format": "ifc", "label": LINES_SUBJECT},
    )
    dest = tmp_path / "node.ifc"
    dest.write_bytes(_download(client, out["derived_key"]))
    assert sorted(o.name for o in ada.from_ifc(dest).get_all_physical_objects()) == ["bm0", "bm1"]


def test_an_element_of_a_published_node_is_found_by_name(client_and_scope, published_lines_node):
    client, _ = client_and_scope
    out = _export(
        client,
        {"collection": LINES_COLLECTION, "subject": LINES_SUBJECT, "format": "step", "element": "bm1", "path": ["bm1"]},
    )
    assert out["filename"] == "bm1.step"
    assert _download(client, out["derived_key"]).startswith(b"ISO-10303-21")


def test_a_node_delivered_as_a_mesh_is_refused_before_anything_is_queued(client_and_scope):
    """A mesh provider hands over triangles, no objects -- there is no STEP in that, and an IFC
    would be a tessellated shell of what the user thinks they are downloading."""
    client, scope_dir = client_and_scope
    _publish(scope_dir, "mesh-1", delivery="mesh")
    r = client.post(
        f"/api/scopes/{SCOPE}/export-selection",
        json={"collection": LINES_COLLECTION, "subject": "mesh-1", "format": "ifc"},
    )
    assert r.status_code == 409
    assert "claims delivery 'mesh'" in r.json()["detail"]
    assert "no objects behind it" in r.json()["detail"]


def test_an_element_the_provider_does_not_name_fails_with_how_to_export_anyway(client_and_scope, published_lines_node):
    """A provider's GLB names are its own business; when its objects do not carry the tree's
    name, the job says so and points at the root row rather than exporting something else."""
    client, _ = client_and_scope
    r = client.post(
        f"/api/scopes/{SCOPE}/export-selection",
        json={
            "collection": LINES_COLLECTION,
            "subject": LINES_SUBJECT,
            "format": "ifc",
            "element": "zz",
            "path": ["zz"],
        },
    )
    status = _wait_done(client, r.json()["job_id"])
    assert status["status"] == "error"
    assert "root row" in status["error"]


def test_a_provider_the_caller_names_wrongly_is_refused(client_and_scope, published_lines_node):
    client, _ = client_and_scope
    r = client.post(
        f"/api/scopes/{SCOPE}/export-selection",
        json={"collection": LINES_COLLECTION, "subject": LINES_SUBJECT, "format": "ifc", "provider": "someone-else"},
    )
    assert r.status_code == 409


# ── the worker's handlers, through _process_one ──────────────────────────
#
# The tests above run the LOCAL engine. A cluster runs these two handlers instead; they share the
# leaf functions, so what is pinned here is only their half: the source-backed kind is handed the
# downloaded file and stores an identity-encoded download, the synthetic kind never downloads.


class _FakeQueue:
    def __init__(self, job) -> None:
        self.job = job
        self.updates: list[dict] = []

    async def get(self, job_id):
        return self.job if job_id == self.job.job_id else None

    async def update(self, job_id, **kw):
        self.updates.append(kw)


@pytest.mark.asyncio
async def test_the_worker_writes_a_selection_from_the_downloaded_source(tmp_path):
    from obstore.store import LocalStore

    from ada.comms.rest.queue import Job
    from ada.comms.rest.scope import Scope
    from ada.comms.rest.storage import Storage
    from ada.comms.rest.worker import _process_one

    store_dir = tmp_path / "store"
    store_dir.mkdir()
    storage = Storage(LocalStore(str(store_dir)), prefix="")
    src = tmp_path / "decks.ifc"
    _two_deck_model().to_ifc(src)
    await storage.put_bytes(Scope.shared(), "models/decks.ifc", src.read_bytes())

    job = Job(
        job_id="job-export",
        source_key="models/decks.ifc",
        derived_key="_derived/export/t/s/deck_b.step",
        status="queued",
        target_format="export_selection",
        conversion_options={
            "source_key": "models/decks.ifc",
            "format": "step",
            "element": "deck_b",
            "path": ["deck_b"],
        },
    )
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.updates[-1] == {"status": "done", "stage": "ready", "progress": 1.0, "error": None}
    data = await storage.get_bytes(Scope.shared(), job.derived_key)
    assert data.startswith(b"ISO-10303-21")


@pytest.mark.asyncio
async def test_the_worker_reads_a_published_node_without_downloading_anything(monkeypatch, tmp_path):
    from obstore.store import LocalStore

    from ada.comms.rest.queue import Job
    from ada.comms.rest.storage import Storage
    from ada.comms.rest.worker import _process_one

    async def _no_fetch(*a, **kw):
        raise AssertionError("a published node's export must not download a source")

    monkeypatch.setattr("ada.comms.rest.worker.process.fetch_source", _no_fetch)
    job = Job(
        job_id="job-export-asset",
        source_key="assets/c/s/20260928T000000Z/x.bin",
        derived_key="_derived/export/t/s/s.ifc",
        status="queued",
        target_format="export_selection_asset",
        conversion_options={"format": "ifc"},
    )
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, Storage(LocalStore(str(tmp_path)), prefix=""), None, None)
    assert queue.updates[-1]["status"] == "error"
    assert "needs 'collection' and 'subject'" in queue.updates[-1]["error"]
