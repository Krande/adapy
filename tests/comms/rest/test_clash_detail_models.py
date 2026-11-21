"""Detail models are kept and listed: ``GET /scopes/{scope}/clash-detail/models`` and
``DELETE .../clash-detail/models/{detail_id}`` (``ada.comms.rest.clash_details``).

Driven by the queue-less viewer (LocalJobTransport), like ``test_clash_check_routes.py``: every
detail run gets a directory of its own, so running the same selection again adds a model rather than
overwriting the last; the list says what each was made from, how it ended and what it holds; a
model is deleted with its files; the scope keeps its newest ``KEEP``; and the routes are
scope-checked like the clash-run listing.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-detail-models-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

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
SOURCE_KEY = "models/probe.ifc"
SPEC = "builtin.girder_gusset"


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


def _ifc_bytes(tmp_path: Path, *, second_girder: bool = True) -> bytes:
    """Two I-girders meeting square -- one joint ``builtin.girder_gusset`` details -- or the first
    alone, a source whose joint is gone."""
    import ada

    a = ada.Assembly("ClashTest")
    p = ada.Part("P")
    a.add_part(p)
    p.add_beam(ada.Beam("g1", (0, 0, 0), (2, 0, 0), "IPE200"))
    if second_girder:
        p.add_beam(ada.Beam("g2", (1, 0, 0), (1, 2, 0), "IPE200"))
    path = tmp_path / ("probe.ifc" if second_girder else "probe-one.ifc")
    a.to_ifc(path)
    return path.read_bytes()


def _stage(scope_dir: Path, rel_key: str, data: bytes) -> None:
    dest = scope_dir / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


@pytest.fixture
def client(tmp_path):
    scope_dir = tmp_path / "store" / SCOPE_DIR
    _stage(scope_dir, SOURCE_KEY, _ifc_bytes(tmp_path))
    app = create_app(_settings(tmp_path / "store"))
    with TestClient(app) as c:
        c.scope_dir = scope_dir
        c.tmp_path = tmp_path
        yield c


def _wait(client, job_id, *, timeout_s=60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


def _check(client) -> tuple[str, str]:
    """``(result_key, joint id)`` of a check over the staged source."""
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"source_key": SOURCE_KEY, "options": {"include_plate_joints": False}},
    )
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    raw = (client.scope_dir / r.json()["derived_key"]).read_bytes()
    doc = json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)
    return r.json()["derived_key"], doc["joints"][0]["id"]


def _detail(client, result_key: str, joint_id: str, *, wait: bool = True) -> dict:
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-detail",
        json={"result_key": result_key, "joint_ids": [joint_id], "spec": SPEC},
    )
    assert r.status_code == 200, r.text
    if wait:
        _wait(client, r.json()["job_id"])
    return r.json()


def _models(client) -> list[dict]:
    r = client.get(f"/api/scopes/{SCOPE}/clash-detail/models")
    assert r.status_code == 200, r.text
    return r.json()["models"]


def test_every_detail_run_is_a_model_of_its_own_listed_newest_first(client):
    result_key, jid = _check(client)
    first = _detail(client, result_key, jid)
    second = _detail(client, result_key, jid)

    # The same selection twice is two models, not one overwritten.
    assert first["glb_key"] != second["glb_key"]
    for body in (first, second):
        assert body["glb_key"].startswith(clash_details.DETAILS_PREFIX)
        assert (client.scope_dir / body["glb_key"]).exists()
        assert (client.scope_dir / body["derived_key"]).exists()

    models = _models(client)
    assert [m["detail_id"] for m in models] == [second["detail_id"], first["detail_id"]]
    m = models[0]
    assert m["status"] == "done" and m["error"] is None
    assert m["spec"] == SPEC and m["requested"] == 1
    assert m["counts"]["joints"] == 1 and m["counts"]["welds"] >= 1 and m["counts"]["skipped"] == 0
    assert m["counts"]["not_built"] == 0 and m["counts"]["weld_failures"] == 0
    assert m["built_by"] == {SPEC: 1}
    assert m["glb_key"] == second["glb_key"] and m["stats_key"] == second["derived_key"]
    assert m["source"] == {
        "kind": "result",
        "result_key": result_key,
        "source_key": SOURCE_KEY,
        "target": {"kind": "file", "source_key": SOURCE_KEY},
        "label": "probe.ifc",
    }
    assert m["label"] == f"{SPEC} · probe.ifc"
    assert m["created_at"].endswith("Z")
    # The settled summary is stored, so the next listing does not read the take-off again.
    record = json.loads(
        gzip.decompress((client.scope_dir / f"{clash_details.DETAILS_PREFIX}{m['detail_id']}/record.json").read_bytes())
    )
    assert record["status"] == "done" and record["counts"] == m["counts"]
    assert len(client.get(f"/api/scopes/{SCOPE}/clash-detail/models", params={"limit": 1}).json()["models"]) == 1


def test_detail_models_are_not_user_files_and_outlive_clearing_temp_files(client):
    result_key, jid = _check(client)
    body = _detail(client, result_key, jid)
    files = client.get(f"/api/scopes/{SCOPE}/files").json()
    rows = files.get("files", files) if isinstance(files, dict) else files
    assert not any(str(f.get("key", "")).startswith("_clash/") for f in rows if isinstance(f, dict))

    assert client.delete(f"/api/scopes/{SCOPE}/temp").status_code == 200
    assert (client.scope_dir / body["glb_key"]).exists()
    assert [m["detail_id"] for m in _models(client)] == [body["detail_id"]]


def test_a_failed_detail_run_is_listed_with_its_error(client):
    result_key, jid = _check(client)
    # The source loses the joint after the check: the route accepts the cached id, the job cannot
    # reproduce it.
    _stage(client.scope_dir, SOURCE_KEY, _ifc_bytes(client.tmp_path, second_girder=False))
    body = _detail(client, result_key, jid)
    (m,) = _models(client)
    assert m["detail_id"] == body["detail_id"]
    assert m["status"] == "error" and "not reproducible" in m["error"]
    assert m["counts"] is None


def test_a_detail_model_is_deleted_with_its_files(client):
    result_key, jid = _check(client)
    keep = _detail(client, result_key, jid)
    gone = _detail(client, result_key, jid)

    r = client.delete(f"/api/scopes/{SCOPE}/clash-detail/models/{gone['detail_id']}")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 3  # record, model and take-off
    assert not (client.scope_dir / f"{clash_details.DETAILS_PREFIX}{gone['detail_id']}").exists() or not any(
        (client.scope_dir / f"{clash_details.DETAILS_PREFIX}{gone['detail_id']}").iterdir()
    )
    assert [m["detail_id"] for m in _models(client)] == [keep["detail_id"]]

    assert client.delete(f"/api/scopes/{SCOPE}/clash-detail/models/{gone['detail_id']}").status_code == 404
    assert client.delete(f"/api/scopes/{SCOPE}/clash-detail/models/not-an-id").status_code == 400


def test_the_scope_keeps_its_newest_detail_models(client, monkeypatch):
    monkeypatch.setattr(clash_details, "KEEP", 2)
    result_key, jid = _check(client)
    runs = [_detail(client, result_key, jid) for _ in range(3)]
    assert [m["detail_id"] for m in _models(client)] == [runs[2]["detail_id"], runs[1]["detail_id"]]
    # The oldest went with its files.
    assert not (client.scope_dir / runs[0]["glb_key"]).exists()
    assert (client.scope_dir / runs[1]["glb_key"]).exists()


def test_the_routes_are_scope_checked(client, monkeypatch):
    from ada.comms.rest.routes import deps

    async def _never(*_a, **_k):
        return False

    monkeypatch.setattr(deps, "scope_can_access", _never)
    assert client.get(f"/api/scopes/{SCOPE}/clash-detail/models").status_code == 403
    assert client.delete(f"/api/scopes/{SCOPE}/clash-detail/models/20260101T000000000Z-0123abcd").status_code == 403


def test_an_empty_scope_lists_none(client):
    assert _models(client) == []


def test_ids_sort_as_they_were_made():
    from datetime import datetime, timezone

    a = clash_details.new_detail_id(datetime(2026, 1, 1, 0, 0, 0, 5000, tzinfo=timezone.utc))
    b = clash_details.new_detail_id(datetime(2026, 1, 1, 0, 0, 0, 6000, tzinfo=timezone.utc))
    assert clash_details.is_detail_id(a) and clash_details.is_detail_id(b) and a < b
    assert not clash_details.is_detail_id("../escape")
    with pytest.raises(ValueError):
        clash_details.detail_paths("../escape")


def test_a_take_off_summarises_as_joints_welds_and_plates():
    stats = {
        "joints": {"count": 2, "items": [{"welds": 3, "plates": 1}, {"welds": 2, "plates": 0}]},
        "built_by": {"a": 2},
        "skipped": ["j9: refused"],
    }
    # A take-off from before the structured records: its skipped lines are its joints not built.
    assert clash_details.summary_from_stats(stats) == {
        "counts": {
            "joints": 2,
            "welds": 5,
            "plates": 1,
            "skipped": 1,
            "not_built": 1,
            "weld_failures": 0,
            "duplicate_welds": 0,
        },
        "built_by": {"a": 2},
    }
    stats["not_built"] = [{"joint_id": "j9", "kind": "failed", "reason": "refused", "declined": []}]
    stats["weld_failures"] = [
        {"joint_id": "j1", "connection": "c1", "place": "flange", "weld_type": "fillet", "reason": "x"},
        {"joint_id": "j1", "connection": "c1", "place": "web", "weld_type": "fillet", "reason": "y"},
    ]
    counts = clash_details.summary_from_stats(stats)["counts"]
    assert counts["not_built"] == 1 and counts["weld_failures"] == 2


def test_the_listing_counts_joints_not_built_and_welds_failed(client):
    """A run's record, once its take-off is there, carries how many joints were not built and how
    many seams of the built ones failed -- what the Review tab's line says."""
    detail_id = clash_details.new_detail_id()
    paths = clash_details.detail_paths(detail_id)
    record = clash_details.make_record(
        paths,
        spec="provider.spec",
        capability=None,
        source={"kind": "result", "result_key": "r.json", "target": {"kind": "file"}, "label": "frame.ifc"},
        joint_ids=["j1", "j2", "j3"],
        options={},
        job_id=None,
    )
    stats = {
        "joints": {"count": 1, "items": [{"name": "c3", "welds": 2, "plates": 1}]},
        "built_by": {"provider.spec": 1},
        "skipped": ["j1: no applicable spec took it (provider.spec: not mine)", "j2: ValueError: boom"],
        "not_built": [
            {
                "joint_id": "j1",
                "kind": "declined",
                "reason": "not mine",
                "declined": [{"spec": "provider.spec", "reason": "not mine"}],
            },
            {"joint_id": "j2", "kind": "failed", "spec": "provider.spec", "reason": "ValueError: boom", "declined": []},
        ],
        "weld_failures": [
            {"joint_id": "j3", "connection": "c3", "place": "flange", "weld_type": "fillet", "reason": "no room"}
        ],
    }
    _stage(client.scope_dir, paths.record, json.dumps(record).encode("utf-8"))
    _stage(client.scope_dir, paths.stats, json.dumps(stats).encode("utf-8"))

    (m,) = _models(client)
    assert m["detail_id"] == detail_id and m["status"] == "done"
    assert m["counts"]["joints"] == 1
    assert m["counts"]["not_built"] == 2 and m["counts"]["weld_failures"] == 1 and m["counts"]["skipped"] == 2
