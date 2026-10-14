"""The convex-contact clash run routes (``/scopes/{scope}/clash-contacts...``), end to end against the
queue-less viewer (LocalJobTransport), like ``test_clash_group_routes.py``: a worker-style run over a
GLB, a browser-style run posted and annotated, listing and reading runs back, and detailing from a run.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-contacts-"))

import pytest  # noqa: E402

clash = pytest.importorskip("adacpp.clash")
if not hasattr(clash, "find_glb_contacts"):  # pragma: no cover - adacpp without the GLB check
    pytest.skip("this adacpp has no find_glb_contacts", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

import ada  # noqa: E402
from ada.clash.contacts import check_glb  # noqa: E402
from ada.clash.sidecar import parse_run  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
TARGET = {"kind": "file", "source_key": "models/frame.ifc"}
GLB_KEY = "_derived/models/frame.ifc.glb"


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


def _stage(scope_dir: Path, rel_key: str, data: bytes) -> None:
    dest = scope_dir / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


@pytest.fixture
def frame_bytes(tmp_path):
    """A source (IFC) and the GLB a viewer would load for it: two IPE300 girders, the second landing
    square on the first with its start end 2 mm into the first's flange (which the built-in gusset
    spec details: two I-section girders at 30..150 degrees)."""
    model = ada.Assembly("a") / (
        ada.Part("p")
        / [
            ada.Beam("g1", (0, 0, 2.5), (4.0, 0, 2.5), "IPE300"),
            ada.Beam("g2", (2.0, 0.073, 2.5), (2.0, 3.0, 2.5), "IPE300"),
        ]
    )
    ifc, glb = tmp_path / "frame.ifc", tmp_path / "frame.glb"
    model.to_ifc(ifc)
    model.to_gltf(glb)
    return ifc.read_bytes(), glb.read_bytes()


@pytest.fixture
def client(tmp_path, frame_bytes):
    scope_dir = tmp_path / "store" / SCOPE_DIR
    scope_dir.mkdir(parents=True)
    app = create_app(_settings(tmp_path / "store"))
    _stage(scope_dir, TARGET["source_key"], frame_bytes[0])
    _stage(scope_dir, GLB_KEY, frame_bytes[1])
    with TestClient(app) as c:
        c.scope_dir = scope_dir
        yield c


def _wait(client, job_id, timeout_s=60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


def _run_on_worker(client) -> str:
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts", json={"target": TARGET, "glb_key": GLB_KEY})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    return r.json()["run_key"]


def _read(client, key):
    r = client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": key})
    assert r.status_code == 200, r.text
    return parse_run(r.json())


def test_a_worker_run_is_stored_listed_and_read_back(client):
    run_key = _run_on_worker(client)
    assert run_key.startswith("_clash/")
    listing = client.get(f"/api/scopes/{SCOPE}/clash-contacts/runs", params={"target": json.dumps(TARGET)})
    assert listing.status_code == 200
    (line,) = listing.json()["runs"]
    assert line["key"] == run_key and line["engine"] == "worker" and line["contacts"] == 1

    run = _read(client, run_key)
    (c,) = run.contacts
    assert sorted(run.names(c)) == ["g1", "g2"]
    # The run's members bound to the members rebuilt from the source, so specs were resolved.
    assert run.provenance["applicability"] == {"resolved": 1, "unbound": 0}
    # Not listed as a user file.
    files = client.get(f"/api/scopes/{SCOPE}/files").json()
    assert not any(
        str(f.get("key", "")).startswith("_clash/") for f in files.get("files", files) if isinstance(f, dict)
    )


def test_a_browser_run_is_validated_stored_and_annotated(client, frame_bytes):
    run = check_glb(frame_bytes[1], target=TARGET, glb_key=GLB_KEY, engine="wasm")
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": run.to_dict()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["engine"] == "wasm"
    assert _wait(client, body["job_id"])["status"] == "done"
    annotated = _read(client, body["run_key"])
    assert annotated.provenance["applicability"] == {"resolved": 1, "unbound": 0}
    assert annotated.contacts[0].id == run.contacts[0].id
    # The same run again is a conflict, not a silent overwrite.
    assert client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": run.to_dict()}).status_code == 409


@pytest.mark.parametrize(
    "mutate, status, match",
    [
        (lambda d: d["contacts"][0].update(id="deadbeef0000"), 400, "not the id of its members"),
        (lambda d: d.update(target={"kind": "group"}), 400, "target kind"),
        (lambda d: d.update(schema="nope"), 400, "not a clash sidecar"),
    ],
)
def test_a_bad_browser_run_is_refused(client, frame_bytes, mutate, status, match):
    doc = check_glb(frame_bytes[1], target=TARGET, glb_key=GLB_KEY, engine="wasm").to_dict()
    mutate(doc)
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc})
    assert r.status_code == status and match in r.json()["detail"]


def test_requests_naming_nothing_real_are_refused(client):
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts", json={"target": TARGET, "glb_key": "_derived/nope.glb"})
    assert r.status_code == 404 and "convert the model first" in r.json()["detail"]
    assert client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": "models/frame.ifc"}).status_code == 400


def test_detail_from_a_run(client):
    run_key = _run_on_worker(client)
    (c,) = _read(client, run_key).contacts

    bad = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail",
        json={"run_key": run_key, "contact_ids": ["nope"], "spec": "builtin.girder_gusset"},
    )
    assert bad.status_code == 404 and "nope" in bad.json()["detail"]

    specs = [a["spec"] for a in c.applicable]
    assert "builtin.girder_gusset" in specs, specs
    d = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail", json={"run_key": run_key, "contact_ids": [c.id], "spec": specs[0]}
    )
    assert d.status_code == 200, d.text
    done = _wait(client, d.json()["job_id"])
    assert done["status"] == "done", done
    assert (client.scope_dir / d.json()["glb_key"]).exists()


def test_detail_refuses_a_run_whose_glb_changed(client, frame_bytes):
    run_key = _run_on_worker(client)
    (c,) = _read(client, run_key).contacts
    _stage(client.scope_dir, GLB_KEY, frame_bytes[1] + b"\0" * 8)
    d = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail",
        json={"run_key": run_key, "contact_ids": [c.id], "spec": "builtin.girder_gusset"},
    )
    assert d.status_code == 200
    done = _wait(client, d.json()["job_id"])
    assert done["status"] == "error" and "changed since it was checked" in done["error"]
