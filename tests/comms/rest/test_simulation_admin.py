"""Managing simulation runs after they were started (``simulation_admin``): moving runs between
batches, deleting runs one by one, many at once or as a whole batch -- who may, what goes, what
stays, and runs that are queued or running."""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import threading
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-sim-admin-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest import simulation_admin  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.auth import User  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.queue import Job, JobQueue  # noqa: E402
from ada.comms.rest.routes.deps import RestContext  # noqa: E402
from ada.comms.rest.simulation_runs import SETTLED  # noqa: E402
from ada.comms.rest.storage import Storage  # noqa: E402
from ada.simulation import register_simulation_provider  # noqa: E402
from ada.simulation.runs import cancel_key, deleted_key, run_record_key  # noqa: E402
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
PROVIDER = "fake-sim"
STRANGER = User(sub="stranger-1", email="s@x.invalid", display_name="Stranger", groups=frozenset(), is_admin=False)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    plugins.reset_registry()
    SETTLED.clear()
    simulation_admin._LAST_PURGE.clear()
    monkeypatch.setattr(simulation_admin, "_PURGE_EVERY_S", 0.0)
    yield
    plugins.reset_registry()
    SETTLED.clear()


def _settings(path, queue_url=None) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=queue_url, stream="ada", subject="ada.viewer.jobs.convert", kv_bucket="ada-viewer-jobs", durable="w"
        ),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


@pytest.fixture
def client(tmp_path):
    (tmp_path / "store" / SCOPE_DIR).mkdir(parents=True)
    with TestClient(create_app(_settings(tmp_path / "store"))) as c:
        c.root = tmp_path / "store"
        yield c


def _url(scope=SCOPE) -> str:
    return f"/api/scopes/{scope}/simulations"


def _start(client, scope=SCOPE, **body) -> dict:
    r = client.post(_url(scope), json={"provider": PROVIDER, "parameters": {"steps": 1}, **body})
    assert r.status_code == 200, r.text
    return r.json()["run"]


def _wait_ended(client, run_id, scope=SCOPE, timeout_s=60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = client.get(f"{_url(scope)}/{run_id}").json()["run"]
        if run["status"] in ("done", "error", "cancelled"):
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not end")


def _done_runs(client, n, scope=SCOPE, **body) -> list[str]:
    ids = []
    for _ in range(n):
        run = _start(client, scope, **body)
        _wait_ended(client, run["run_id"], scope)
        ids.append(run["run_id"])
        time.sleep(0.003)
    return ids


def _batches(client, scope=SCOPE) -> dict:
    r = client.get(f"{_url(scope)}/batches")
    assert r.status_code == 200, r.text
    return r.json()


def _ids(client, scope=SCOPE, **params) -> list[str]:
    r = client.get(_url(scope), params=params)
    assert r.status_code == 200, r.text
    return [run["run_id"] for run in r.json()["runs"]]


def _by_id(body) -> dict:
    return {r["run_id"]: r for r in body["results"]}


AUDITED: list[tuple[str, str | None, str | None]] = []


@pytest.fixture(autouse=True)
def _audit(monkeypatch):
    """The audit rows the routes write: ``(action, key, status)``."""
    AUDITED.clear()

    async def audit(self, request, user, scope, action, **kw):
        AUDITED.append((action, kw.get("key"), kw.get("status")))

    monkeypatch.setattr(RestContext, "audit", audit)
    yield


def _audit_actions(client) -> list[str]:
    return [a for a, _, _ in AUDITED]


# ── moving runs between batches ──────────────────────────────────────────────────────────────────


def test_runs_are_moved_into_a_new_batch_and_out_again(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    a, b, c = _done_runs(client, 3)
    assert _batches(client)["unbatched"] == 3

    r = client.post(f"{_url()}/batches/frame-a/runs", json={"run_ids": [a, b], "label": "Frame A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["batch"] == {"id": "frame-a", "label": "Frame A", "source": None}
    assert body["counts"] == {"moved": 2}
    listed = _batches(client)
    assert listed["unbatched"] == 1
    (line,) = listed["batches"]
    assert (line["id"], line["label"], line["count"], line["counts"]["done"]) == ("frame-a", "Frame A", 2, 2)
    assert set(_ids(client, batch="frame-a")) == {a, b} and _ids(client, batch="none") == [c]
    assert client.get(f"{_url()}/{a}").json()["run"]["batch"]["label"] == "Frame A"

    # Into the batch that now exists: its label is kept whatever is asked for; moving again is a no-op.
    again = client.post(f"{_url()}/batches/frame-a/runs", json={"run_ids": [a, c], "label": "Other"}).json()
    assert _by_id(again)[a]["status"] == "unchanged" and _by_id(again)[c]["status"] == "moved"
    assert again["batch"]["label"] == "Frame A"
    assert _batches(client)["batches"][0]["count"] == 3

    # Out again, to the root.
    out = client.request("DELETE", f"{_url()}/batches/frame-a/runs", json={"run_ids": [a, b]}).json()
    assert out["counts"] == {"removed": 2}
    assert _ids(client, batch="frame-a") == [c] and set(_ids(client, batch="none")) == {a, b}
    assert client.get(f"{_url()}/{a}").json()["run"]["batch"] is None
    gone = client.request("DELETE", f"{_url()}/batches/frame-a/runs", json={"run_ids": [a]}).json()
    assert _by_id(gone)[a]["status"] == "unchanged"
    assert "simulation_batch_move" in _audit_actions(client) and "simulation_batch_remove" in _audit_actions(client)


def test_a_run_in_another_batch_is_refused_unless_replace(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    (run_id,) = _done_runs(client, 1, batch={"id": "first", "label": "First"})
    refused = client.post(f"{_url()}/batches/second/runs", json={"run_ids": [run_id]}).json()
    assert _by_id(refused)[run_id]["status"] == "refused" and _by_id(refused)[run_id]["code"] == "other_batch"
    assert _ids(client, batch="first") == [run_id]

    moved = client.post(f"{_url()}/batches/second/runs", json={"run_ids": [run_id], "replace": True}).json()
    assert _by_id(moved)[run_id] == {"run_id": run_id, "status": "moved", "from": "first"}
    assert _ids(client, batch="first") == [] and _ids(client, batch="second") == [run_id]
    assert [b["id"] for b in _batches(client)["batches"]] == ["second"]


def test_bad_bodies_are_refused(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    (run_id,) = _done_runs(client, 1)
    url = f"{_url()}/batches/b1/runs"
    assert client.post(url, json={"run_ids": []}).status_code == 400
    assert client.post(url, json={"run_ids": ["not-a-run"]}).status_code == 400
    assert client.post(url, json={"run_ids": [run_id], "label": ""}).status_code == 400
    assert client.post(f"{_url()}/batches/has space/runs", json={"run_ids": [run_id]}).status_code == 400
    assert client.post(f"{_url()}/delete", json={"run_ids": "x"}).status_code == 400
    missing = client.post(url, json={"run_ids": ["0" * 32]}).json()
    assert _by_id(missing)["0" * 32]["code"] == "missing"


def test_in_the_shared_scope_only_the_starter_or_an_admin_moves_or_deletes_a_run(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    (theirs,) = _done_runs(client, 1, scope="shared")  # started by the local-dev admin
    client.app.dependency_overrides[auth_module.current_user] = lambda: STRANGER
    try:
        mine = _done_runs(client, 1, scope="shared")[0]
        moved = client.post(f"{_url('shared')}/batches/b/runs", json={"run_ids": [theirs, mine]}).json()
        assert _by_id(moved)[theirs]["code"] == "forbidden" and _by_id(moved)[mine]["status"] == "moved"
        r = client.delete(f"{_url('shared')}/{theirs}")
        assert r.status_code == 403
        bulk = client.post(f"{_url('shared')}/delete", json={"run_ids": [theirs, mine]}).json()
        assert _by_id(bulk)[theirs]["status"] == "failed" and _by_id(bulk)[theirs]["code"] == "forbidden"
        assert _by_id(bulk)[mine]["status"] == "deleted"
        # Nobody else's personal scope can be reached at all.
        assert client.delete(f"/api/scopes/user:someone-else/simulations/{theirs}").status_code in (400, 403, 404)
    finally:
        client.app.dependency_overrides.clear()
    assert _ids(client, scope="shared") == [theirs]
    # The admin may.
    assert client.delete(f"{_url('shared')}/{theirs}").status_code == 200
    assert _ids(client, scope="shared") == []


# ── deleting runs ────────────────────────────────────────────────────────────────────────────────


def test_deleting_a_run_removes_its_files_entries_and_derived_blobs_and_nothing_else(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    scope_dir = client.root / SCOPE_DIR
    (scope_dir / "models").mkdir()
    (scope_dir / "models" / "arm.glb").write_bytes(b"glb bytes")
    keep, gone = _done_runs(client, 2, batch="b1", inputs=["models/arm.glb"])
    run_dir = scope_dir / "_simulations" / gone
    assert (run_dir / "run.json").exists() and any((run_dir / "artefacts").iterdir())
    # What the convert cache made of one of its artefacts.
    derived = scope_dir / "_derived" / "_simulations" / gone / "artefacts" / "timeline.json.glb"
    derived.parent.mkdir(parents=True)
    derived.write_bytes(b"x" * 100)

    dry = client.post(f"{_url()}/delete", json={"run_ids": [gone], "dry": True}).json()
    assert dry["counts"] == {"deleted": 1} and dry["bytes"] > 100 and run_dir.exists()

    r = client.delete(f"{_url()}/{gone}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted" and r.json()["bytes"] == dry["bytes"]
    assert not run_dir.exists() or not any(run_dir.rglob("*.*"))
    assert not derived.exists()
    assert not (scope_dir / "_simulations" / "_batches" / "b1" / gone).exists()
    # Its input and the other run stay.
    assert (scope_dir / "models" / "arm.glb").read_bytes() == b"glb bytes"
    assert _ids(client, batch="b1") == [keep]
    assert _batches(client)["batches"][0]["count"] == 1
    assert client.get(f"{_url()}/{gone}").status_code == 404
    assert "simulation_delete" in _audit_actions(client)

    # Again: harmless, and says it was already gone.
    again = client.delete(f"{_url()}/{gone}")
    assert again.status_code == 200 and again.json()["already"] is True


def test_deleting_many_reports_each_and_tolerates_a_failure(client, monkeypatch):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    a, b, c = _done_runs(client, 3)
    real = Storage.delete_many

    async def flaky(self, scope, keys):
        # The artefacts of ``b`` cannot be deleted.
        bad = [k for k in keys if f"/{b}/artefacts/" in k]
        return bad + await real(self, scope, [k for k in keys if k not in bad])

    monkeypatch.setattr(Storage, "delete_many", flaky)
    body = client.post(f"{_url()}/delete", json={"run_ids": [a, b, c, "f" * 32]}).json()
    results = _by_id(body)
    assert results[a]["status"] == "deleted" and results[c]["status"] == "deleted"
    assert results["f" * 32] == {"run_id": "f" * 32, "status": "deleted", "already": True, "bytes": 0}
    assert results[b]["status"] == "failed" and results[b]["code"] == "storage"
    # Its record stays, so it is still listed and can be deleted again.
    assert _ids(client) == [b]
    monkeypatch.setattr(Storage, "delete_many", real)
    assert _by_id(client.post(f"{_url()}/delete", json={"run_ids": [b]}).json())[b]["status"] == "deleted"
    assert _ids(client) == []


def test_deleting_a_running_run_cancels_it_and_what_it_writes_after_is_removed(client, monkeypatch):
    monkeypatch.setattr("ada.simulation.runner._CANCEL_POLL_S", 0.0)
    at_step, release = threading.Event(), threading.Event()

    def on_step(index):
        if index == 1:
            at_step.set()
            assert release.wait(60)

    register_simulation_provider(FakeSimulationProvider(PROVIDER, on_step=on_step))
    run_id = _start(client, parameters={"steps": 50}, batch="b1")["run_id"]
    scope_dir = client.root / SCOPE_DIR
    try:
        assert at_step.wait(60)
        r = client.delete(f"{_url()}/{run_id}")
        assert r.status_code == 200, r.text
        assert r.json()["pending"] is True and r.json()["cancelled"] is True
        # Gone from every listing at once; its cancel marker stays for the worker.
        assert _ids(client) == [] and _batches(client) == {"batches": [], "unbatched": 0}
        assert (scope_dir / cancel_key(run_id)).exists() and (scope_dir / deleted_key(run_id)).exists()
    finally:
        release.set()
    # The worker stops and writes its last record: still not listed, and then removed.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and (scope_dir / deleted_key(run_id)).exists():
        assert _ids(client) == []
        time.sleep(0.05)
    assert not (scope_dir / deleted_key(run_id)).exists()
    assert not (scope_dir / run_record_key(run_id)).exists()
    assert client.get(f"{_url()}/{run_id}").status_code == 404


def test_a_running_run_is_not_moved_but_a_queued_one_is(monkeypatch, tmp_path):
    app, enqueued, jobs = _queue_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        queued = c.post(_url("shared"), json={"provider": PROVIDER, "parameters": {"steps": 2}}).json()["run"]
        running = c.post(_url("shared"), json={"provider": PROVIDER, "parameters": {"steps": 2}}).json()["run"]
        jobs[running["job_id"]].status = "running"
        body = c.post(
            f"{_url('shared')}/batches/b/runs", json={"run_ids": [queued["run_id"], running["run_id"]]}
        ).json()
        assert _by_id(body)[queued["run_id"]]["status"] == "moved"
        assert _by_id(body)[running["run_id"]]["code"] == "running"
        # The worker that picks the queued one up reads its new batch.
        stored = json.loads(gzip.decompress((tmp_path / "shared" / run_record_key(queued["run_id"])).read_bytes()))
        assert stored["batch"]["id"] == "b"


def test_deleting_a_queued_run_ends_it_and_a_late_worker_finds_nothing(monkeypatch, tmp_path):
    app, enqueued, jobs = _queue_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        run = c.post(_url("shared"), json={"provider": PROVIDER, "parameters": {"steps": 2}}).json()["run"]
        r = c.delete(f"{_url('shared')}/{run['run_id']}")
        assert r.status_code == 200, r.text
        assert r.json().get("cancelled") is True and not r.json().get("pending")
        assert jobs[run["job_id"]].status == "cancelled"
        assert not (tmp_path / "shared" / "_simulations" / run["run_id"]).exists() or not any(
            (tmp_path / "shared" / "_simulations" / run["run_id"]).rglob("*")
        )
        assert c.get(_url("shared")).json()["runs"] == []


def test_a_run_deleted_while_a_worker_runs_it_is_purged_once_the_worker_has_written_its_end(monkeypatch, tmp_path):
    app, enqueued, jobs = _queue_app(monkeypatch, tmp_path)
    with TestClient(app) as c:
        run = c.post(_url("shared"), json={"provider": PROVIDER, "parameters": {"steps": 2}}).json()["run"]
        jobs[run["job_id"]].status = "running"
        r = c.delete(f"{_url('shared')}/{run['run_id']}").json()
        assert r["pending"] is True
        root = tmp_path / "shared"
        record_path = root / run_record_key(run["run_id"])
        assert not record_path.exists() and (root / cancel_key(run["run_id"])).exists()
        # The worker writes on from the copy it had, then its end.
        stale = {**run, "status": "running"}
        stale.pop("cancel_requested", None)
        record_path.write_bytes(gzip.compress(json.dumps(stale).encode()))
        assert c.get(_url("shared")).json()["runs"] == []
        assert (root / deleted_key(run["run_id"])).exists()
        record_path.write_bytes(gzip.compress(json.dumps({**stale, "status": "cancelled"}).encode()))
        assert c.get(_url("shared")).json()["runs"] == []
        assert not (root / deleted_key(run["run_id"])).exists() and not record_path.exists()


# ── deleting a batch ─────────────────────────────────────────────────────────────────────────────


def test_deleting_a_batch_keeps_its_runs_at_the_root_or_deletes_them_too(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    kept = _done_runs(client, 2, batch={"id": "keep-me", "label": "Keep"})
    _done_runs(client, 2, batch={"id": "doom", "label": "Doom"})

    dry = client.delete(f"{_url()}/batches/doom", params={"runs": "true", "dry": "true"}).json()
    assert dry["batch"] == {"id": "doom", "deleted": False, "runs": 2} and dry["bytes"] > 0
    assert len(_ids(client, batch="doom")) == 2

    r = client.delete(f"{_url()}/batches/keep-me").json()
    assert r["batch"]["deleted"] is True and r["counts"] == {"removed": 2}
    assert set(_ids(client, batch="none")) == set(kept)
    assert client.get(f"{_url()}/{kept[0]}").json()["run"]["batch"] is None

    r = client.delete(f"{_url()}/batches/doom", params={"runs": "true"}).json()
    assert r["batch"]["deleted"] is True and r["counts"] == {"deleted": 2}
    assert set(_ids(client)) == set(kept)
    assert _batches(client) == {"batches": [], "unbatched": 2}
    assert "simulation_batch_delete" in _audit_actions(client)

    # A batch that is gone is deleted already.
    again = client.delete(f"{_url()}/batches/doom", params={"runs": "true"}).json()
    assert again["batch"] == {"id": "doom", "deleted": True, "runs": 0} and again["results"] == []


# ── the queue path, faked (as test_simulation_runs) ──────────────────────────────────────────────


def _worker() -> dict:
    described = {
        "slug": PROVIDER,
        "id": PROVIDER,
        "domain": "robotics",
        "engine": "fake",
        "capability": f"sim-{PROVIDER}",
        "serving": True,
        **FakeSimulationProvider().describe().to_dict(),
    }
    return {
        "worker_id": "w1",
        "last_heartbeat": time.time(),
        "capabilities": ["base", f"sim-{PROVIDER}"],
        "simulation_providers": [described],
    }


def _queue_app(monkeypatch, tmp_path):
    enqueued: list[Job] = []
    jobs: dict[str, Job] = {}

    async def fake_connect(self, **kwargs):
        return None

    async def fake_list_workers(self):
        return [_worker()]

    async def fake_enqueue(self, source_key, **kw):
        job = Job(
            job_id=f"job-{len(enqueued) + 1}",
            source_key=source_key,
            derived_key=kw.get("derived_key") or "",
            status="queued",
            stage="queued",
            target_format=kw.get("target_format") or "glb",
            conversion_options=kw.get("conversion_options"),
            target_capability=kw.get("target_capability"),
            scope_kind=kw.get("scope_kind") or "shared",
            scope_id=kw.get("scope_id"),
        )
        enqueued.append(job)
        jobs[job.job_id] = job
        return job

    async def fake_publish(self, job):
        return None

    async def fake_get(self, job_id):
        return jobs.get(job_id)

    async def fake_update(self, job_id, **fields):
        job = jobs.get(job_id)
        for key, value in fields.items():
            setattr(job, key, value)
        return job

    for name, fn in (
        ("connect", fake_connect),
        ("list_workers", fake_list_workers),
        ("enqueue", fake_enqueue),
        ("publish", fake_publish),
        ("get", fake_get),
        ("update", fake_update),
    ):
        monkeypatch.setattr(JobQueue, name, fn)
    app = create_app(_settings(tmp_path, queue_url="nats://test-not-dialled"))
    return app, enqueued, jobs
