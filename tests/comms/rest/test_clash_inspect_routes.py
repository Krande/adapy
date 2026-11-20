"""Reading what the clash pipeline stored, as an agent asks for it (``ada.comms.rest.clash_inspect``):

* ``GET /clash-contacts/latest`` -- the newest run, of a target or of the scope;
* ``GET /clash-contacts/joints`` -- a run's clashes: members by name and role, centre, specs;
* ``GET /clash-detail/models/{detail_id}`` -- a detail model per joint: built (its connections and
  welds), declined or failed (and why), its failed welds, and its members from the run -- so a joint
  that is in no GLB can still be named and found;
* ``POST /clash-contacts/detail`` with ``run_key: "latest"``.

The documents are staged as the pipeline writes them; no job runs.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-inspect-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.clash.sidecar import CLASH_SIDECAR_SCHEMA, contact_id  # noqa: E402
from ada.comms.rest import clash_details  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
OLD_TARGET = {"kind": "file", "source_key": "models/old.ifc"}
TARGET = {"kind": "file", "source_key": "models/frame.ifc"}
MEMBERS = [{"id": "e1", "name": "column_1"}, {"id": "e2", "name": "brace_1"}, {"id": "e3", "name": "brace_2"}]
J_BUILT = contact_id(["e1", "e2"])
J_DECLINED = contact_id(["e1", "e3"])
J_OLD = contact_id(["e2", "e3"])


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


def _contact(i: int, j: int, centre_x: float, applicable=()) -> dict:
    return {
        "id": contact_id([MEMBERS[i]["id"], MEMBERS[j]["id"]]),
        "members": [i, j],
        "near_points": [[centre_x, 0, 0], [centre_x, 0, 0.002]],
        "normal": [0, 0, 1],
        "distance": -0.002,
        "contact_area": 0.01,
        "patches": [],
        "applicable": list(applicable),
        "roles": {"incoming": j, "landing": i},
    }


def _run(run_id: str, created_at: str, target: dict, contacts: list[dict]) -> dict:
    return {
        "schema": CLASH_SIDECAR_SCHEMA,
        "run_id": run_id,
        "created_at": created_at,
        "engine": "wasm",
        "target": target,
        "geometry": {"key": "_derived/models/frame.glb", "sha256": "0" * 64, "up_axis": "z"},
        "options": {"security_margin": 0.001, "min_contact_area": 0.0, "line_contact_thickness": 0.0},
        "members": MEMBERS,
        "contacts": contacts,
        "provenance": {},
    }


SPECS = [
    {"spec": "provider.low", "capability": None, "priority": 1},
    {"spec": "provider.high", "capability": None, "priority": 10},
]


@pytest.fixture
def client(tmp_path):
    store = tmp_path / "store"
    (store / SCOPE_DIR).mkdir(parents=True)
    app = create_app(_settings(store))
    with TestClient(app) as c:
        c.scope_dir = store / SCOPE_DIR
        # Two runs, on two targets: the older on `models/old.ifc`, the newer on `models/frame.ifc`.
        for run in (
            _run("a" * 32, "2026-01-01T00:00:00Z", OLD_TARGET, [_contact(1, 2, 5.0)]),
            _run("b" * 32, "2026-02-01T00:00:00Z", TARGET, [_contact(0, 1, 1.0, SPECS), _contact(0, 2, 2.0)]),
        ):
            r = c.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": run, "annotate": False})
            assert r.status_code == 200, r.text
            c.__dict__.setdefault("run_keys", []).append(r.json()["run_key"])
        yield c


def test_the_latest_run_of_the_scope_and_of_a_target(client):
    old_key, new_key = client.run_keys
    r = client.get(f"/api/scopes/{SCOPE}/clash-contacts/latest")
    assert r.status_code == 200 and r.json()["run"]["key"] == new_key
    r = client.get(f"/api/scopes/{SCOPE}/clash-contacts/latest", params={"target": json.dumps(OLD_TARGET)})
    assert r.json()["run"]["key"] == old_key
    none = {"kind": "file", "source_key": "models/none.ifc"}
    r = client.get(f"/api/scopes/{SCOPE}/clash-contacts/latest", params={"target": json.dumps(none)})
    assert r.status_code == 404


def test_a_runs_joints_name_their_members_and_specs(client):
    r = client.get(f"/api/scopes/{SCOPE}/clash-contacts/joints")  # key defaults to "latest"
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run"]["key"] == client.run_keys[1] and body["run"]["contacts"] == 2
    built = next(j for j in body["joints"] if j["id"] == J_BUILT)
    assert [(m["name"], m["role"]) for m in built["members"]] == [("column_1", "landing"), ("brace_1", "incoming")]
    assert built["centre"] == [1.0, 0.0, 0.001]
    # Highest priority first.
    assert [a["spec"] for a in built["applicable"]] == ["provider.high", "provider.low"]

    r = client.get(
        f"/api/scopes/{SCOPE}/clash-contacts/joints",
        params={"key": client.run_keys[1], "ids": f"{J_DECLINED},ffffffffffff"},
    )
    assert [j["id"] for j in r.json()["joints"]] == [J_DECLINED]
    assert r.json()["missing"] == ["ffffffffffff"]
    r = client.get(f"/api/scopes/{SCOPE}/clash-contacts/joints", params={"member": "brace_2"})
    assert [j["id"] for j in r.json()["joints"]] == [J_DECLINED]
    r = client.get(f"/api/scopes/{SCOPE}/clash-contacts/joints", params={"limit": 1})
    assert len(r.json()["joints"]) == 1 and r.json()["truncated"] is True


def _stage_detail(client, *, joint_ids, stats) -> str:
    paths = clash_details.detail_paths(clash_details.new_detail_id())
    record = clash_details.make_record(
        paths,
        spec="provider.high",
        capability=None,
        source={"kind": "run", "run_key": client.run_keys[1], "target": TARGET, "label": "frame.ifc"},
        joint_ids=joint_ids,
        options={},
        job_id=None,
    )
    record["status"] = "done"  # settled: the list reads it as it is
    base: Path = client.scope_dir / paths.prefix
    base.mkdir(parents=True)
    (base / "record.json").write_text(json.dumps(record))
    (base / "result.stats.json").write_text(json.dumps(stats))
    return paths.detail_id


STATS = {
    "joints": {"count": 1, "by_type": [], "items": [{"name": f"provider.high_{J_BUILT}", "welds": 1, "plates": 0}]},
    "built_by": {"provider.high": 1},
    "not_built": [
        {
            "joint_id": J_DECLINED,
            "kind": "declined",
            "reason": "takes an incoming at 60 to 120 degrees; this one is at 49.2",
            "declined": [{"spec": "provider.high", "reason": "takes an incoming at 60 to 120 degrees"}],
        }
    ],
    "weld_failures": [
        {"joint_id": J_BUILT, "connection": f"provider.high_{J_BUILT}", "place": "w_2", "weld_type": "", "reason": "x"}
    ],
    "connections": [
        {
            "joint_id": J_BUILT,
            "connection": f"provider.high_{J_BUILT}",
            "spec": "provider.high",
            "member_roles": {"landing": ["column_1"], "incoming": ["brace_1"]},
            "centre": [1.0, 0.0, 0.0],
            "plates": [],
            "welds": [{"name": "w_1", "weld_type": "fillet", "throat": 0.005, "length": 0.3, "metadata": {}}],
        }
    ],
}


def test_a_detail_model_per_joint_built_declined_and_not_asked_for(client):
    detail_id = _stage_detail(client, joint_ids=[J_BUILT, J_DECLINED], stats=STATS)
    r = client.get(f"/api/scopes/{SCOPE}/clash-detail/models/{detail_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run_found"] is True and body["has_connection_records"] is True
    built, declined = body["joints"]
    assert built["joint_id"] == J_BUILT and built["status"] == "built"
    assert built["connections"][0]["welds"][0]["throat"] == 0.005
    assert [w["place"] for w in built["weld_failures"]] == ["w_2"]

    # The declined joint is in no GLB: its reason, and its members from the run it was detailed from.
    assert declined["status"] == "declined" and declined["connections"] == []
    assert declined["not_built"]["reason"].startswith("takes an incoming")
    assert [m["name"] for m in declined["contact"]["members"]] == ["column_1", "brace_2"]
    assert declined["contact"]["centre"] == [2.0, 0.0, 0.001]

    r = client.get(f"/api/scopes/{SCOPE}/clash-detail/models/{detail_id}", params={"joint_ids": "0123456789ab"})
    (other,) = r.json()["joints"]
    assert other["status"] == "not_requested" and other["contact"] is None


def test_an_unknown_detail_model_is_a_404(client):
    assert client.get(f"/api/scopes/{SCOPE}/clash-detail/models/not-an-id").status_code == 400
    missing = clash_details.new_detail_id()
    assert client.get(f"/api/scopes/{SCOPE}/clash-detail/models/{missing}").status_code == 404


def test_detail_from_the_latest_run(client):
    # "latest" is the scope's newest run (frame.ifc): the older run's clash is not in it.
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail",
        json={"run_key": "latest", "contact_ids": [J_OLD], "spec": "provider.high"},
    )
    assert r.status_code == 404 and J_OLD in r.json()["detail"]
    # Of a target with no runs: 404 naming that.
    none = {"kind": "file", "source_key": "models/none.ifc"}
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-contacts/detail",
        json={"run_key": "latest", "target": none, "contact_ids": [J_OLD], "spec": "provider.high"},
    )
    assert r.status_code == 404 and "no clash run" in r.json()["detail"]
