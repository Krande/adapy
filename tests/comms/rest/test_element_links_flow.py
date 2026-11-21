"""Element links filed as their documents are written, end to end against the queue-less viewer: a
clash run's contacts (with the element keys and fingerprints its annotate resolved), a detail model's
connections and welds (with their ck), the subjects made of them, and a simulation run on a subject
(queued, then done), each under the elements it is about."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-element-links-"))

import pytest  # noqa: E402

clash = pytest.importorskip("adacpp.clash")
if not hasattr(clash, "find_glb_contacts"):  # pragma: no cover - adacpp without the GLB check
    pytest.skip("this adacpp has no find_glb_contacts", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

import ada  # noqa: E402
from ada import plugins  # noqa: E402
from ada.assoc.records import decode_sheet  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.simulation import register_simulation_provider  # noqa: E402
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
TARGET = {"kind": "file", "source_key": "models/frame.ifc"}
GLB_KEY = "_derived/models/frame.ifc.glb"
FILE = "file:models/frame.ifc"


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


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


@pytest.fixture(scope="module")
def frame_bytes(tmp_path_factory):
    """Two IPE300 girders, the second landing square on the first (the built-in gusset details it)."""
    tmp_path = tmp_path_factory.mktemp("frame")
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


def _wait(client, job_id, timeout_s=90.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


def _sheets(client, kind: str) -> list:
    out = []
    for path in sorted((client.scope_dir / "_assoc" / kind).glob("*.json")):
        raw = path.read_bytes()
        out.append(decode_sheet(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw))
    return out


def _rows(client, kind: str) -> list:
    return [r for _, _, rows in _sheets(client, kind) for r in rows]


def _run_on_worker(client) -> str:
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts", json={"target": TARGET, "glb_key": GLB_KEY})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    return r.json()["run_key"]


def _detail(client, run_key, **extra) -> dict:
    run = client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": run_key}).json()
    (c,) = run["contacts"]
    spec = c["applicable"][0]["spec"] if "spec" in c["applicable"][0] else c["applicable"][0]["name"]
    d = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail",
        json={"run_key": run_key, "contact_ids": [c["id"]], "spec": spec, **extra},
    )
    assert d.status_code == 200, d.text
    assert _wait(client, d.json()["job_id"])["status"] == "done"
    return d.json()


def test_a_clash_run_files_its_contacts_under_their_elements(client):
    run_key = _run_on_worker(client)
    rows = _rows(client, "contact")
    assert len(rows) == 2 and {r.doc_key for r in rows} == {run_key}
    keys = sorted(r.element_key for r in rows)
    assert all(k.startswith(f"{FILE}#p:") and k.endswith(("/G1", "/G2")) for k in keys), keys
    assert all(FILE in r.anc for r in rows)
    # The annotate fingerprinted the elements and wrote the run's element identities.
    assert all(r.fp for r in rows)
    assert {r.data["role"] for r in rows} == {"incoming", "landing"}
    run = client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": run_key}).json()
    assert [e["key"] for e in run["provenance"]["elements"]] and len(
        list((client.scope_dir / "_assoc" / "_state").glob("*"))
    ) == 1


def test_a_detail_files_connections_welds_and_subjects_and_a_run_on_a_subject_follows_it(client):
    run_key = _run_on_worker(client)
    d = _detail(client, run_key, make_subjects=True)
    # Settled when listed: its connections and welds are filed then.
    (model,) = client.get(f"/api/scopes/{SCOPE}/clash-detail/models").json()["models"]
    assert model["status"] == "done"
    conns = _rows(client, "connection")
    assert conns and {r.data["status"] for r in conns} == {"built"}
    (ck,) = {r.ck for r in conns}
    # One row per member, by the roles the builder recorded.
    assert len({r.element_key for r in conns}) == 2 and all(r.data["role"] for r in conns)
    assert all(r.data["detail_id"] == d["detail_id"] for r in conns)
    welds = _rows(client, "weld")
    assert welds and {r.ck for r in welds} == {ck}
    # The take-off itself names the ck.
    stats = json.loads(gzip.decompress((client.scope_dir / d["derived_key"]).read_bytes()))
    assert {c.get("ck") for c in stats["connections"]} == {ck}

    subject_key = f"_clash/subjects/{ck}.weld-assembly.json"
    assert (client.scope_dir / subject_key).exists()
    subjects = _rows(client, "subject")
    assert {r.doc_key for r in subjects} == {subject_key} and {r.element_key for r in subjects} == {
        r.element_key for r in conns
    }

    register_simulation_provider(FakeSimulationProvider("fake-sim", domain="robotics", accepts=("weld-assembly",)))
    r = client.post(
        f"/api/scopes/{SCOPE}/simulations",
        json={
            "provider": "fake-sim",
            "parameters": {"steps": 2},
            "subject": {"kind": "weld-assembly", "key": subject_key},
            "batch": {"id": "b-1", "label": "rack"},
        },
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run"]["run_id"]
    sims = _rows(client, "sim_run")
    assert sims and {r.ck for r in sims} == {ck} and {r.data["run_id"] for r in sims} == {run_id}
    assert {r.data["batch"]["id"] for r in sims} == {"b-1"}
    assert {r.element_key for r in sims} == {r.element_key for r in conns}

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        view = client.get(f"/api/scopes/{SCOPE}/simulations/{run_id}").json()["run"]
        if view["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert view["status"] == "done", view
    assert {r.data["status"] for r in _rows(client, "sim_run")} == {"done"}


def _query(client, **body) -> dict:
    r = client.post(f"/api/scopes/{SCOPE}/elements/links/query", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _ended(client, run_id) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        view = client.get(f"/api/scopes/{SCOPE}/simulations/{run_id}").json()["run"]
        if view["status"] in ("done", "error", "cancelled"):
            return view
        time.sleep(0.05)
    raise AssertionError(view)


def test_the_query_routes_reindex_subjects_and_batches(client):
    run_key = _run_on_worker(client)
    _detail(client, run_key)
    client.get(f"/api/scopes/{SCOPE}/clash-detail/models")  # settles it

    # Under the file: its contacts and its connections, grouped per ck / pair.
    page = _query(client, nodes=[{"key": FILE}])
    assert page["summary"]["kinds"]["contact"] == 2 and page["summary"]["kinds"]["connection"] == 2
    (ck,) = {g["ck"] for g in page["groups"] if g["ck"]}
    # A saved group's member names the same element by its path below the model's root row.
    groups = {
        "schema": "ada.viewer/groups@2",
        "groups": [
            {"id": "g-1", "name": "rack", "members": [{"target": TARGET, "element": "g1", "path": ["p", "g1"]}]}
        ],
    }
    _stage(client.scope_dir, "_groups/groups.json", json.dumps(groups).encode("utf-8"))
    by_group = _query(client, group_id="g-1", kinds=["connection"])
    assert by_group["summary"]["rows"] == 1 and by_group["keys"] == [f"{FILE}#p:P/G1"]
    one = client.get(f"/api/scopes/{SCOPE}/elements/{FILE}%23p:P%2FG1/links")
    assert one.status_code == 200 and one.json()["summary"]["rows"] >= 2, one.text
    conn = client.get(f"/api/scopes/{SCOPE}/connections/{ck}").json()
    assert len(conn["members"]) == 2 and len(conn["connections"]) == 2 and len(conn["contacts"]) == 2
    assert (
        client.post(f"/api/scopes/{SCOPE}/elements/links/query", json={"kinds": ["x"], "cks": [ck]}).status_code == 400
    )

    # A reindex from the documents gives the same rows.
    import shutil

    shutil.rmtree(client.scope_dir / "_assoc")
    report = client.post(f"/api/scopes/{SCOPE}/elements/links/reindex", json={"wait": True}).json()["report"]
    assert report["runs"] == 1 and report["details"] == 1 and not report["errors"], report
    assert _query(client, nodes=[{"key": FILE}])["summary"]["rows"] == page["summary"]["rows"]
    assert client.get(f"/api/scopes/{SCOPE}/elements/links/reindex").json()["indexed"] is True

    # The missing subject, made by rebuilding the run's members and the joint.
    made = client.post(f"/api/scopes/{SCOPE}/elements/subjects", json={"nodes": [{"key": FILE}]}).json()
    assert made["missing"] == [ck] and len(made["jobs"]) == 1
    assert _wait(client, made["jobs"][0]["job_id"])["status"] == "done"
    assert (client.scope_dir / f"_clash/subjects/{ck}.weld-assembly.json").exists()
    assert _query(client, cks=[ck], kinds=["subject"])["summary"]["rows"] == 2

    # One press: one run per connection that has none, in one batch; a second press starts nothing.
    register_simulation_provider(FakeSimulationProvider("fake-sim", domain="robotics", accepts=("weld-assembly",)))
    body = {"nodes": [{"key": FILE}], "provider": "fake-sim", "parameters": {"steps": 1}, "policy": "missing"}
    dry = client.post(f"/api/scopes/{SCOPE}/simulations/batch", json={**body, "dry_run": True}).json()
    assert [s["ck"] for s in dry["started"]] == [ck] and dry["started"][0]["run_id"] is None
    first = client.post(f"/api/scopes/{SCOPE}/simulations/batch", json=body).json()
    assert [s["ck"] for s in first["started"]] == [ck], first
    run_id = first["started"][0]["run_id"]
    again = client.post(f"/api/scopes/{SCOPE}/simulations/batch", json=body).json()
    assert not again["started"] and again["skipped"][0]["ck"] == ck
    assert _ended(client, run_id)["batch"]["id"] == first["batch"]["id"]
    sims = _query(client, cks=[ck], kinds=["sim_run"])
    assert sims["summary"]["sim_runs"] == {"done": 2} and sims["active"] is False
    # `failed` does not start one over a run that is done; `all` does.
    assert not client.post(f"/api/scopes/{SCOPE}/simulations/batch", json={**body, "policy": "failed"}).json()[
        "started"
    ]
    rerun = client.post(f"/api/scopes/{SCOPE}/simulations/batch", json={**body, "policy": "all"}).json()
    assert len(rerun["started"]) == 1, rerun
    assert rerun["started"][0]["run_id"] != run_id
