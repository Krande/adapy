"""``POST /scopes/{scope}/clash-check`` with ``{"group": ...}`` -- several sources checked as ONE
model -- and its detail hand-off, driven by the LOCAL (queue-less) transport like
``test_clash_check_routes.py``; the worker's two synthetic kinds (``clash_check_group`` /
``clash_detail_group``) are driven through ``_process_one`` at the bottom.

The fixture is two IFC files with ONE girder each, meeting at (1, 0, 0): neither file alone has a
joint, so any joint found is one BETWEEN sources -- the reason the group form exists.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-group-"))

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
OPTIONS = {"include_plate_joints": False}


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


def _one_girder_ifc(tmp_path: Path, name: str, beams: list[tuple]) -> bytes:
    import ada

    a = ada.Assembly(name)
    p = ada.Part(f"P{name}")
    a.add_part(p)
    for beam_name, n1, n2 in beams:
        p.add_beam(ada.Beam(beam_name, n1, n2, "IPE200"))
    path = tmp_path / f"{name}.ifc"
    a.to_ifc(path)
    return path.read_bytes()


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


@pytest.fixture
def two_files(client_and_scope, tmp_path):
    _, scope_dir = client_and_scope
    a = _stage(scope_dir, "models/a.ifc", _one_girder_ifc(tmp_path, "A", [("ga", (0, 0, 0), (2, 0, 0))]))
    b = _stage(scope_dir, "models/b.ifc", _one_girder_ifc(tmp_path, "B", [("gb", (1, 0, 0), (1, 2, 0))]))
    return a, b


def _file(key: str, element: str | None = None, path: list[str] | None = None) -> dict:
    return {"target": {"kind": "file", "source_key": key}, "element": element, "path": path or []}


def _wait_done(client, job_id, *, timeout_s=60.0) -> dict:
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


def _read_blob(client, key) -> dict:
    r = client.get(f"/api/scopes/{SCOPE}/blobs/{key}")
    assert r.status_code == 200, r.text
    raw = r.content
    if r.headers.get("content-encoding") != "gzip" and raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _post_group(client, members: list[dict], **extra):
    return client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"group": {"name": "Deck 3", "members": members}, "options": OPTIONS, **extra},
    )


def _run_group(client, members: list[dict]) -> tuple[dict, str]:
    r = _post_group(client, members)
    assert r.status_code == 200, r.text
    status = _wait_done(client, r.json()["job_id"])
    assert status["status"] == "done", status
    return _read_blob(client, r.json()["derived_key"]), r.json()["derived_key"]


# ── the check ─────────────────────────────────────────────────────────


def test_a_group_finds_the_joint_between_two_files(client_and_scope, two_files):
    client, _ = client_and_scope
    a, b = two_files
    doc, key = _run_group(client, [_file(a), _file(b)])

    assert key.startswith("_derived/clash/group/") and key.endswith("/result.json")
    assert doc["schema"] == "ada.clash/result@2"
    assert doc["source_key"].startswith("group:")
    assert doc["counts"]["joints"] == 1
    assert {m["name"] for m in doc["joints"][0]["members"]} == {"ga", "gb"}
    assert doc["provenance"]["group"]["name"] == "Deck 3"
    assert len(doc["provenance"]["group"]["members"]) == 2


def test_a_repeat_in_any_order_is_a_cache_hit_and_force_reruns(client_and_scope, two_files):
    client, _ = client_and_scope
    a, b = two_files
    _, key = _run_group(client, [_file(a), _file(b)])

    again = _post_group(client, [_file(b), _file(a), _file(a)])
    assert again.json() == {"job_id": None, "derived_key": key, "cached": True}

    forced = _post_group(client, [_file(a), _file(b)], force=True)
    assert forced.json()["cached"] is False and forced.json()["derived_key"] == key
    _wait_done(client, forced.json()["job_id"])


def test_re_uploading_a_member_moves_the_key(client_and_scope, two_files, tmp_path):
    client, scope_dir = client_and_scope
    a, b = two_files
    _, key = _run_group(client, [_file(a), _file(b)])
    _stage(scope_dir, b, _one_girder_ifc(tmp_path, "B2", [("gb", (1, 0, 0), (1, 3, 0))]))
    r = _post_group(client, [_file(a), _file(b)])
    assert r.status_code == 200, r.text
    assert r.json()["derived_key"] != key
    _wait_done(client, r.json()["job_id"])


def test_an_element_member_narrows_its_source(client_and_scope, two_files, tmp_path):
    client, scope_dir = client_and_scope
    a, _ = two_files
    # A second file holding the joining girder AND a far one in another part.
    import ada

    asm = ada.Assembly("C")
    near, far = ada.Part("Near"), ada.Part("Far")
    asm.add_part(near)
    asm.add_part(far)
    near.add_beam(ada.Beam("gc", (1, 0, 0), (1, 2, 0), "IPE200"))
    far.add_beam(ada.Beam("gfar", (50, 50, 0), (52, 50, 0), "IPE200"))
    asm.to_ifc(tmp_path / "C.ifc")
    c = _stage(scope_dir, "models/c.ifc", (tmp_path / "C.ifc").read_bytes())

    doc, _ = _run_group(client, [_file(a), _file(c, "Near", ["Near"]), _file(c, "missing", ["Far", "missing"])])
    assert doc["counts"]["beams"] == 2
    assert doc["counts"]["joints"] == 1
    assert any("'missing'" in w for w in doc["warnings"])


@pytest.mark.parametrize(
    "extra",
    [
        {"source_key": "models/a.ifc"},
        {"collection": "c", "subject": "s"},
        {"subject": "s"},
    ],
)
def test_a_group_mixed_with_another_form_is_refused(client_and_scope, two_files, extra):
    client, _ = client_and_scope
    r = _post_group(client, [_file(two_files[0])], **extra)
    assert r.status_code == 400, r.text
    assert "ONE way" in r.json()["detail"]


@pytest.mark.parametrize(
    "group,needle",
    [
        ({"name": "n", "members": []}, "non-empty"),
        ({"name": "n", "members": [{"target": {"kind": "folder"}}]}, "kind"),
        ("nope", "object"),
    ],
)
def test_a_bad_group_is_400_with_the_reason(client_and_scope, group, needle):
    client, _ = client_and_scope
    r = client.post(f"/api/scopes/{SCOPE}/clash-check", json={"group": group})
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]


# ── a published node beside a file ─────────────────────────────────────

LINES_PROVIDER = "fixture-lines-group"
LINES_COLLECTION = "lines-g"
LINES_SUBJECT = "unit-1"


class _LinesConcepts:
    """One member per line -- a format core has no reader for."""

    def concepts(self, options, *, storage, scope=None, node=None):
        import ada

        raw = storage.get_bytes(options["source_key"]).decode()
        part = ada.Part(node or "lines")
        for line in (ln for ln in raw.splitlines() if ln.strip()):
            name, *xyz = line.split()
            x0, y0, z0, x1, y1, z1 = (float(v) for v in xyz)
            part.add_beam(ada.Beam(name, (x0, y0, z0), (x1, y1, z1), "IPE200"))
        return part


def _publish(scope_dir: Path, revision: str, text: bytes) -> None:
    from ada.assets.keys import asset_key
    from ada.assets.manifest import (
        MANIFEST_FILENAME,
        ArtefactEntry,
        AssetManifest,
        BuildSpec,
    )

    source_key = asset_key(LINES_COLLECTION, LINES_SUBJECT, revision, "lines.txt")
    _stage(scope_dir, source_key, text)
    manifest = AssetManifest(
        provider=LINES_PROVIDER,
        collection=LINES_COLLECTION,
        subject=LINES_SUBJECT,
        revision=revision,
        node=LINES_SUBJECT,
        produced_at="2026-09-28T00:00:00Z",
        published_at="2026-09-28T00:00:00Z",
        delivery="build",
        build=BuildSpec(capability="build-lines", options={"source_key": source_key}),
        artefacts=(ArtefactEntry(role="source", key=source_key, sha256="", size=len(text)),),
    )
    _stage(scope_dir, asset_key(LINES_COLLECTION, LINES_SUBJECT, revision, MANIFEST_FILENAME), manifest.to_json())


@pytest.fixture
def lines_provider():
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts

    register_asset_concepts(LINES_PROVIDER, lambda: _LinesConcepts())
    yield
    clear_asset_concepts()


def _node(provider: str = LINES_PROVIDER) -> dict:
    return {
        "target": {
            "kind": "node",
            "provider": provider,
            "collection": LINES_COLLECTION,
            "subject": LINES_SUBJECT,
            "revision": None,
            "node": None,
        },
        "element": None,
        "path": [],
    }


def test_a_node_and_a_file_join_and_a_re_publish_moves_the_key(client_and_scope, two_files, lines_provider):
    client, scope_dir = client_and_scope
    a, _ = two_files
    _publish(scope_dir, "20260928T000000Z", b"gn 1 0 0 1 -2 0")

    doc, key = _run_group(client, [_file(a), _node()])
    assert doc["counts"]["joints"] == 1
    assert {m["name"] for m in doc["joints"][0]["members"]} == {"ga", "gn"}
    node_member = next(m for m in doc["provenance"]["group"]["members"] if m["target"]["kind"] == "node")
    # "latest" was resolved by the route and carried, so a later detail cannot drift onto a newer one.
    assert node_member["target"]["revision"] == "20260928T000000Z"

    _publish(scope_dir, "20261001T000000Z", b"gn 1 0 0 1 -3 0")
    r = _post_group(client, [_file(a), _node()])
    assert r.status_code == 200, r.text
    assert r.json()["derived_key"] != key
    _wait_done(client, r.json()["job_id"])


def test_a_node_member_naming_the_wrong_provider_is_refused(client_and_scope, two_files, lines_provider):
    client, scope_dir = client_and_scope
    _publish(scope_dir, "20260928T000000Z", b"gn 1 0 0 1 -2 0")
    r = _post_group(client, [_file(two_files[0]), _node("someone-else")])
    assert r.status_code == 409, r.text


def test_an_unpublished_node_member_is_404(client_and_scope, two_files, lines_provider):
    client, _ = client_and_scope
    r = _post_group(client, [_file(two_files[0]), _node()])
    assert r.status_code == 404, r.text


# ── detail ───────────────────────────────────────────────────────────


def test_detail_of_a_group_result_rebuilds_the_group_and_builds(client_and_scope, two_files):
    client, _ = client_and_scope
    a, b = two_files
    doc, result_key = _run_group(client, [_file(a), _file(b)])
    joint = doc["joints"][0]

    r = client.post(
        f"/api/scopes/{SCOPE}/clash-detail",
        json={"result_key": result_key, "joint_ids": [joint["id"]], "spec": "builtin.girder_gusset"},
    )
    assert r.status_code == 200, r.text
    status = _wait_done(client, r.json()["job_id"])
    assert status["status"] == "done", status
    stats = _read_blob(client, r.json()["derived_key"])
    assert stats["joints"]["count"] == 1


# ── saved groups live in a hidden scope blob ───────────────────────────


def test_saved_groups_round_trip_through_the_blob_route_and_stay_out_of_listings(client_and_scope):
    client, _ = client_and_scope
    body = json.dumps({"groups": [{"name": "Deck 3", "members": []}]}).encode()
    put = client.put(f"/api/scopes/{SCOPE}/blobs/_groups/groups.json", content=body)
    assert put.status_code == 201, put.text

    got = client.get(f"/api/scopes/{SCOPE}/blobs/_groups/groups.json")
    assert got.status_code == 200, got.text
    assert json.loads(got.content) == json.loads(body)

    listing = client.get(f"/api/scopes/{SCOPE}/files")
    assert listing.status_code == 200, listing.text
    assert "_groups/" not in listing.text


# ── the worker's synthetic kinds, through _process_one ─────────────────


class _FakeQueue:
    enabled = False  # no live pool: every non-builtin spec reports capability None

    def __init__(self, job) -> None:
        self.job = job
        self.updates: list[dict] = []

    async def get(self, job_id):
        return self.job if job_id == self.job.job_id else None

    async def update(self, job_id, **kw):
        self.updates.append(kw)


@pytest.mark.asyncio
async def test_the_worker_kinds_check_a_group_and_detail_it(tmp_path):
    from obstore.store import LocalStore

    from ada.clash.group import normalise_group
    from ada.comms.rest.queue import Job
    from ada.comms.rest.scope import Scope
    from ada.comms.rest.storage import Storage
    from ada.comms.rest.worker import _process_one

    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = Scope.shared()
    await storage.put_bytes(scope, "a.ifc", _one_girder_ifc(tmp_path, "A", [("ga", (0, 0, 0), (2, 0, 0))]))
    await storage.put_bytes(scope, "b.ifc", _one_girder_ifc(tmp_path, "B", [("gb", (1, 0, 0), (1, 2, 0))]))
    group = normalise_group({"name": "g", "members": [_file("a.ifc"), _file("b.ifc")]})

    check = Job(
        job_id="job-check",
        source_key="group:tok",
        derived_key="_derived/clash/group/tok/o/result.json",
        status="queued",
        target_format="clash_check_group",
        conversion_options={"group": group, "group_token": "tok", "options": OPTIONS},
    )
    queue = _FakeQueue(check)
    await _process_one(check.job_id, queue, storage, None, None)
    assert queue.updates[-1]["status"] == "done", queue.updates
    doc = json.loads(await storage.get_bytes(scope, check.derived_key))
    assert doc["source_key"] == "group:tok"
    assert doc["counts"]["joints"] == 1

    detail = Job(
        job_id="job-detail",
        source_key="group:tok",
        derived_key="_derived/clash/detail/x/result.stats.json",
        status="queued",
        target_format="clash_detail_group",
        conversion_options={
            "result_key": check.derived_key,
            "joint_ids": [doc["joints"][0]["id"]],
            "spec": "builtin.girder_gusset",
            "options": {},
            "glb_key": "_derived/clash/detail/x/model.glb",
        },
    )
    queue = _FakeQueue(detail)
    await _process_one(detail.job_id, queue, storage, None, None)
    assert queue.updates[-1]["status"] == "done", queue.updates
    stats = json.loads(await storage.get_bytes(scope, detail.derived_key))
    assert stats["joints"]["count"] == 1
