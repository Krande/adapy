"""A saved GROUP's convex-contact run through the ``/clash-contacts`` routes: measured in a browser over
several GLBs (``ada.clash.sidecar``'s group runs), posted, annotated against one rebuilt model per part,
listed under the group id, read back and detailed, each member rebuilt from its own model.

Unlike ``test_clash_contacts_routes.py`` this needs no adacpp: nothing here measures, the run is
written as the browser would post it.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-contacts-group-"))

import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import ada  # noqa: E402
from ada.clash.sidecar import contact_id, group_geometry_sha256, parse_run  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings  # noqa: E402

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


def _stage(scope_dir: Path, rel_key: str, data: bytes) -> None:
    dest = scope_dir / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _wait(client, job_id, timeout_s=60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


FILE_A = {"kind": "file", "source_key": "models/a.ifc"}
FILE_B = {"kind": "file", "source_key": "models/b.ifc"}
TARGET = {"kind": "group", "group_id": "g1"}


@pytest.fixture
def client(tmp_path):
    """Two models, one girder each: g2 (in b.ifc) lands square on g1 (in a.ifc), 2 mm into its flange."""
    scope_dir = tmp_path / "store" / SCOPE_DIR
    scope_dir.mkdir(parents=True)
    for key, beam in (
        (FILE_A["source_key"], ada.Beam("g1", (0, 0, 2.5), (4.0, 0, 2.5), "IPE300")),
        (FILE_B["source_key"], ada.Beam("g2", (2.0, 0.073, 2.5), (2.0, 3.0, 2.5), "IPE300")),
    ):
        path = tmp_path / key.replace("/", "_")
        (ada.Assembly("a") / (ada.Part("p") / [beam])).to_ifc(path)
        _stage(scope_dir, key, path.read_bytes())
    app = create_app(_settings(tmp_path / "store"))
    with TestClient(app) as c:
        c.scope_dir = scope_dir
        yield c


def _ungzip(raw: bytes) -> bytes:
    import gzip

    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def _group_run(run_id: str = "r1") -> dict:
    """The run a browser posts: one member from each model -- both element id "1", told apart by part."""
    parts = [
        {"key": "_derived/models/a.ifc.glb", "sha256": "ab" * 32, "source": FILE_A},
        {"key": "_derived/models/b.ifc.glb", "sha256": "cd" * 32, "source": FILE_B},
    ]
    patch = {
        "normal": [0.0, 1.0, 0.0],
        "points1": [[1.95, 0.075, 2.4], [2.05, 0.075, 2.4], [2.05, 0.075, 2.6], [1.95, 0.075, 2.6]],
        "points2": [[1.95, 0.073, 2.4], [2.05, 0.073, 2.4], [2.05, 0.073, 2.6], [1.95, 0.073, 2.6]],
    }
    return {
        "schema": "ada.clash/contacts@1",
        "run_id": run_id,
        "created_at": "2026-10-16T12:00:00Z",
        "engine": "wasm",
        "checker": "convex-contact",
        "target": TARGET,
        "geometry": {"key": "group:g1", "sha256": group_geometry_sha256(parts), "up_axis": "z", "parts": parts},
        "options": {"security_margin": 0.01, "min_contact_area": 1e-6, "line_contact_thickness": 1e-3},
        "members": [
            {"id": "0:1", "name": "g1", "element": "1", "part": 0},
            {"id": "1:1", "name": "g2", "element": "1", "part": 1},
        ],
        "contacts": [
            {
                "id": contact_id(["0:1", "1:1"]),
                "members": [0, 1],
                "near_points": [[2.0, 0.075, 2.5], [2.0, 0.073, 2.5]],
                "normal": [0.0, 1.0, 0.0],
                "distance": -0.002,
                "contact_area": 0.02,
                "patches": [patch],
            }
        ],
        "provenance": {},
        "group": {
            "id": "g1",
            "name": "Deck 3",
            "members": [
                {"target": FILE_A, "element": None, "path": []},
                {"target": FILE_B, "element": None, "path": []},
            ],
        },
    }


def test_a_group_run_is_stored_annotated_per_part_and_listed_under_the_group(client):
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": _group_run()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_key"].startswith("_clash/")
    assert _wait(client, body["job_id"])["status"] == "done"

    got = client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": body["run_key"]})
    assert got.status_code == 200, got.text
    run = parse_run(got.json())
    # Each member bound in its own part's rebuilt model: g1 from a.ifc, g2 from b.ifc.
    assert run.provenance["applicability"] == {"resolved": 1, "unbound": 0}
    assert run.group["name"] == "Deck 3" and [m["part"] for m in run.members] == [0, 1]
    # And which member comes in: g2's start end is at the contact, so g2 (member 1) lands on g1.
    assert run.contacts[0].roles == {"incoming": 1, "landing": 0}
    assert got.json()["contacts"][0]["roles"] == {"incoming": 1, "landing": 0}
    assert run.provenance["roles"] == {"members": 1, "mesh": 0, "unresolved": 0}

    listing = client.get(f"/api/scopes/{SCOPE}/clash-contacts/runs", params={"target": json.dumps(TARGET)})
    assert listing.status_code == 200
    assert [line["key"] for line in listing.json()["runs"]] == [body["run_key"]]


def test_a_group_run_whose_part_does_not_rebuild_is_still_stored(client):
    doc = _group_run()
    doc["geometry"]["parts"][1]["source"] = {"kind": "file", "source_key": "models/gone.ifc"}
    doc["geometry"]["sha256"] = group_geometry_sha256(doc["geometry"]["parts"])
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    run = parse_run(client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": r.json()["run_key"]}).json())
    assert run.provenance["applicability"] == {"resolved": 0, "unbound": 1}


def test_a_group_run_whose_models_do_not_rebuild_gets_its_roles_from_the_meshes(client, monkeypatch):
    """A model this process cannot read into members (here: neither exists) still has its GLB, and
    the roles are read off the members' meshes there."""
    import numpy as np

    from ada.clash import contacts

    def box(lo, hi):
        return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])

    asked = []

    def fake_vertices(run, get_bytes, read_members=None):
        asked.append([p["key"] for p in run.parts])
        # g1 along x through the contact; g2 along y, its end face at y = 0.073.
        return {0: box((0, -0.075, 2.35), (4.0, 0.075, 2.65)), 1: box((1.925, 0.073, 2.35), (2.075, 3.0, 2.65))}

    monkeypatch.setattr(contacts, "member_vertices", fake_vertices)
    doc = _group_run()
    for n, part in enumerate(doc["geometry"]["parts"]):
        part["source"] = {"kind": "file", "source_key": f"models/gone-{n}.ifc"}
    doc["geometry"]["sha256"] = group_geometry_sha256(doc["geometry"]["parts"])
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    run = parse_run(client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": r.json()["run_key"]}).json())
    assert asked == [["_derived/models/a.ifc.glb", "_derived/models/b.ifc.glb"]]
    assert "no model of this group rebuilt" in run.provenance["applicability"]["error"]
    assert run.contacts[0].roles == {"incoming": 1, "landing": 0}
    assert run.provenance["roles"] == {"members": 0, "mesh": 1, "unresolved": 0}


def _staged_group_run(client, run_id: str = "r1") -> dict:
    """A group run whose part GLBs are stored with the sha256 the run records, so a detail accepts it."""
    import hashlib

    doc = _group_run(run_id)
    for n, part in enumerate(doc["geometry"]["parts"]):
        data = f"glb {n}".encode()
        _stage(client.scope_dir, part["key"], data)
        part["sha256"] = hashlib.sha256(data).hexdigest()
    doc["geometry"]["sha256"] = group_geometry_sha256(doc["geometry"]["parts"])
    return doc


def test_a_group_run_is_detailed_with_each_member_from_its_own_model(client):
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": _staged_group_run(client)})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    key = r.json()["run_key"]
    run = parse_run(client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": key}).json())
    (clash,) = run.contacts
    assert clash.applicable, "the annotate offered no spec to detail with"
    spec = clash.applicable[0]["spec"]

    d = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail",
        json={"run_key": key, "contact_ids": [clash.id], "spec": spec},
    )
    assert d.status_code == 200, d.text
    status = _wait(client, d.json()["job_id"])
    assert status["status"] == "done", status
    stats = json.loads(_ungzip((client.scope_dir / d.json()["derived_key"]).read_bytes()))
    assert sum(stats["built_by"].values()) == 1 and not stats.get("skipped")


def test_a_group_run_whose_geometry_changed_is_not_detailed(client):
    doc = _staged_group_run(client)
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc, "annotate": False})
    assert r.status_code == 200, r.text
    _stage(client.scope_dir, doc["geometry"]["parts"][1]["key"], b"re-converted")
    d = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail",
        json={"run_key": r.json()["run_key"], "contact_ids": [doc["contacts"][0]["id"]], "spec": "builtin.girder_gusset"},
    )
    assert d.status_code == 200, d.text
    status = _wait(client, d.json()["job_id"])
    assert status["status"] == "error" and "_derived/models/b.ifc.glb" in status["error"], status


def test_a_group_run_is_not_checked_on_a_worker(client):
    w = client.post(f"/api/scopes/{SCOPE}/clash-contacts", json={"target": TARGET, "glb_key": "x.glb"})
    assert w.status_code == 400 and "runs in the browser" in w.json()["detail"]


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["target"].pop("group_id"), "group_id"),
        (lambda d: d["target"].update(extra="x"), "normal form"),
        (lambda d: d["members"][1].update(id="1"), "'<part>:<element id>'"),
    ],
)
def test_a_bad_group_run_is_refused(client, mutate, match):
    doc = _group_run()
    doc["target"] = dict(doc["target"])
    mutate(doc)
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc})
    assert r.status_code == 400 and match in r.json()["detail"], r.text
