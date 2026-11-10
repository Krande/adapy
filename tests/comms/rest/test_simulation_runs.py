"""Simulation providers and runs through the REST app and the worker's job handler.

Three views of the same seam (``ada.simulation``):

* the WORKER: the heartbeat advertises a registered provider, a pool without the provider refuses
  its job, and a ``simulation_run`` job runs the provider with the job's status following it;
* the QUEUE-LESS viewer (``LocalJobTransport``): a run end to end over the routes -- progress, a
  partial artefact fetched while the run is going, the final artefacts, the run record, cancel;
* the QUEUE path as far as it goes without a NATS server: the queue is faked the way the other
  route tests fake it, so what is checked is which pool a run is published to, what the provider
  listing says about live and stopped workers, and how a run reads when its job fails or is lost.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import pathlib
import tempfile
import threading
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-simulations-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from obstore.store import LocalStore  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.queue import Job, JobQueue  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.simulation_runs import providers_from_workers  # noqa: E402
from ada.comms.rest.storage import Storage  # noqa: E402
from ada.comms.rest.worker import _process_one  # noqa: E402
from ada.comms.rest.worker.registration import build_registration  # noqa: E402
from ada.comms.rest.worker.routing import misroute_reason  # noqa: E402
from ada.simulation import register_simulation_provider  # noqa: E402
from ada.simulation.runs import (  # noqa: E402
    cancel_key,
    dump_run_record,
    new_run_id,
    new_run_record,
    parse_run_record,
    run_record_key,
)
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
PROVIDER = "fake-sim"
CAPABILITY = "sim-fake-sim"


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


def _settings(tmp_path, *, queue_url: str | None = None) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=queue_url,
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


def _ungzip(raw: bytes) -> bytes:
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


# ══ the worker ═══════════════════════════════════════════════════════


class _StubQueue:
    """What ``build_registration`` needs of a queue (as in ``test_worker_runtime_capabilities``)."""

    def __init__(self) -> None:
        self.meta: dict[str, str] = {}
        self.registered: list[dict] = []

    async def set_meta(self, key: str, value: str) -> None:
        self.meta[key] = value

    async def get_meta(self, key: str):
        return self.meta.get(key)

    async def register_worker(self, worker_id: str, payload: dict) -> None:
        self.registered.append(payload)


def test_the_heartbeat_advertises_a_provider_and_whether_this_worker_serves_it(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base")
    register_simulation_provider(FakeSimulationProvider(PROVIDER, domain="fea", engine="fake-solver"))
    queue = _StubQueue()

    async def run():
        reg = await build_registration(queue)
        await reg.publish()
        # What the worker loop does with the capability the registration asked for.
        kept = reg.add_capabilities(plugins.requested_worker_capabilities())
        await reg.publish()
        return kept

    kept = asyncio.run(run())
    assert kept == [CAPABILITY]
    (before,), (after,) = (p["simulation_providers"] for p in queue.registered)
    assert (after["id"], after["domain"], after["engine"], after["capability"]) == (
        PROVIDER,
        "fea",
        "fake-solver",
        CAPABILITY,
    )
    assert after["title"] == "Fake simulation" and after["version"] == "1.0.0"
    assert after["kinds"] == [{"id": "count", "title": "Count steps", "description": ""}]
    assert after["parameters_schema"]["required"] == ["steps"]
    # Advertised from the first heartbeat, served only once the pool is subscribed.
    assert before["serving"] is False and after["serving"] is True
    assert CAPABILITY in queue.registered[-1]["capabilities"]


def test_a_withheld_capability_leaves_the_provider_advertised_and_not_served(monkeypatch):
    from ada.comms.rest.qualification import CAPABILITY_REQUIREMENTS_KEY

    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base")
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    queue = _StubQueue()
    queue.meta[CAPABILITY_REQUIREMENTS_KEY] = json.dumps({CAPABILITY: {"requires": {"a-package-nobody-has": ">=1"}}})

    async def run():
        reg = await build_registration(queue)
        reg.add_capabilities(plugins.requested_worker_capabilities())
        await reg.publish()

    asyncio.run(run())
    payload = queue.registered[-1]
    assert payload["simulation_providers"][0]["serving"] is False
    assert any(w["capability"] == CAPABILITY for w in payload["withheld"])
    # ...and the API lists it as offline, with the reason.
    (entry,) = providers_from_workers(
        [{**payload, "worker_id": "w1", "last_heartbeat": time.time()}], now=time.time(), stale_after_s=60
    )
    assert entry["online"] is False and "a-package-nobody-has" in entry["unavailable_reason"]


POOL = {"cap": "base", "source_ext_set": {".ifc"}, "ext_allow_set": None}


def test_a_pool_without_the_provider_refuses_the_job():
    reason = misroute_reason("simulation_run", "k", conversion_options={"provider_id": PROVIDER}, **POOL)
    assert reason is not None
    assert "misrouted" in reason and repr(PROVIDER) in reason and "not registered on this worker" in reason

    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    assert misroute_reason("simulation_run", "k", conversion_options={"provider_id": PROVIDER}, **POOL) is None
    # Another provider's job is still refused here.
    assert "'other'" in misroute_reason("simulation_run", "k", conversion_options={"provider_id": "other"}, **POOL)


class _FakeQueue:
    """A job in memory: ``update`` applies to it and is recorded, as the KV entry would be."""

    def __init__(self, job: Job) -> None:
        self.job = job
        self.updates: list[dict] = []

    async def get(self, job_id):
        return self.job if job_id == self.job.job_id else None

    async def update(self, job_id, **kw):
        self.updates.append(kw)
        for key, value in kw.items():
            setattr(self.job, key, value)
        return self.job


@pytest.fixture
def storage(tmp_path: pathlib.Path) -> Storage:
    return Storage(LocalStore(str(tmp_path)), prefix="")


async def _queued_run(storage: Storage, provider, parameters: dict) -> tuple[Job, dict]:
    spec = {"id": provider.id, "domain": provider.domain, "engine": provider.engine}
    record = new_run_record(
        run_id=new_run_id(), provider=spec, parameters=parameters, inputs=[], started_by={"id": "tester"}
    )
    key = run_record_key(record["run_id"])
    await storage.put_bytes(Scope.shared(), key, dump_run_record(record), content_encoding="gzip")
    job = Job(
        job_id="job-sim",
        source_key=key,
        derived_key=key,
        status="queued",
        target_format="simulation_run",
        conversion_options={"provider_id": provider.id, "run_id": record["run_id"]},
        target_capability=CAPABILITY,
    )
    return job, record


async def _stored(storage: Storage, record: dict) -> dict:
    return parse_run_record(await storage.get_bytes(Scope.shared(), run_record_key(record["run_id"])))


@pytest.mark.asyncio
async def test_a_simulation_run_job_follows_the_provider_and_ends_done(storage):
    provider = FakeSimulationProvider(PROVIDER)
    register_simulation_provider(provider)
    job, record = await _queued_run(storage, provider, {"steps": 3})
    queue = _FakeQueue(job)

    await _process_one(job.job_id, queue, storage, None, None)

    assert queue.updates[0] == {"status": "running", "stage": "loading", "progress": 0.05}
    final = queue.updates[-1]
    assert (final["status"], final["stage"], final["progress"], final["error"]) == ("done", "ready", 1.0, None)
    assert final["artefacts_ready"]["complete"] is True
    assert {a["name"] for a in final["artefacts_ready"]["artefacts"]} == {"timeline.json", "model.json", "log.txt"}
    # While it ran, the job said which artefacts were already in storage, the partial one first.
    live = [u["artefacts_ready"] for u in queue.updates[:-1] if "artefacts_ready" in u]
    assert live[0] == {"artefacts": [live[0]["artefacts"][0]], "complete": False}
    assert live[0]["artefacts"][0]["name"] == "timeline.json" and live[0]["artefacts"][0]["partial"] is True
    assert any(u.get("stage", "").startswith("step ") for u in queue.updates)

    stored = await _stored(storage, record)
    assert stored["status"] == "done" and stored["job_id"] == "job-sim"
    assert stored["summary"] == {"steps": 3, "inputs": {}}
    timeline = next(a for a in stored["artefacts"] if a["name"] == "timeline.json")
    assert json.loads(await storage.get_bytes(Scope.shared(), timeline["key"]))["steps"][-1] == {"step": 3}


@pytest.mark.asyncio
async def test_a_job_for_a_provider_this_worker_lacks_fails_by_name(storage):
    job, record = await _queued_run(storage, FakeSimulationProvider(PROVIDER), {"steps": 1})
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.updates[-1]["status"] == "error"
    assert f"simulation provider {PROVIDER!r} is not registered on this worker" in queue.updates[-1]["error"]
    stored = await _stored(storage, record)
    assert stored["status"] == "error" and "not registered on this worker" in stored["error"]


@pytest.mark.asyncio
async def test_a_failing_run_fails_the_job_with_the_providers_error(storage):
    provider = FakeSimulationProvider(PROVIDER)
    register_simulation_provider(provider)
    job, record = await _queued_run(storage, provider, {"steps": 3, "fail_at": 2})
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.updates[-1]["status"] == "error"
    assert queue.updates[-1]["error"] == "RuntimeError: step 2 failed on request"
    assert (await _stored(storage, record))["status"] == "error"


@pytest.mark.asyncio
async def test_a_run_cancelled_through_its_marker_ends_the_job_cancelled(storage, monkeypatch):
    monkeypatch.setattr("ada.simulation.runner._CANCEL_POLL_S", 0.0)
    loop = asyncio.get_running_loop()
    provider = FakeSimulationProvider(PROVIDER)
    register_simulation_provider(provider)
    job, record = await _queued_run(storage, provider, {"steps": 100})

    def cancel_at_two(index):
        if index == 2:  # what the cancel route does, from another process
            coro = storage.put_bytes(Scope.shared(), cancel_key(record["run_id"]), b"someone")
            asyncio.run_coroutine_threadsafe(coro, loop).result()

    provider.on_step = cancel_at_two
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.updates[-1]["status"] == "cancelled"
    stored = await _stored(storage, record)
    assert stored["status"] == "cancelled" and stored["progress"] == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_a_redelivered_job_for_a_finished_run_does_not_run_it_again(storage):
    calls = []

    class Counting(FakeSimulationProvider):
        def run(self, case, ctx):
            calls.append(case.run_id)
            return super().run(case, ctx)

    provider = Counting(PROVIDER)
    register_simulation_provider(provider)
    job, record = await _queued_run(storage, provider, {"steps": 1})
    await _process_one(job.job_id, _FakeQueue(job), storage, None, None)
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None, delivery_count=2)
    assert calls == [record["run_id"]]
    assert queue.updates[-1]["status"] == "done"


# ══ the queue-less viewer: end to end ════════════════════════════════


@pytest.fixture
def client(tmp_path):
    scope_dir = tmp_path / "store" / SCOPE_DIR
    scope_dir.mkdir(parents=True)
    app = create_app(_settings(tmp_path / "store"))
    with TestClient(app) as c:
        c.scope_dir = scope_dir
        yield c


def _start(client, parameters=None, **body):
    return client.post(
        f"/api/scopes/{SCOPE}/simulations",
        json={"provider": PROVIDER, "parameters": parameters or {"steps": 3}, **body},
    )


def _run(client, run_id) -> dict:
    r = client.get(f"/api/scopes/{SCOPE}/simulations/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()["run"]


def _wait_ended(client, run_id, timeout_s=30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    run = None
    while time.monotonic() < deadline:
        run = _run(client, run_id)
        if run["status"] in ("done", "error", "cancelled"):
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not end in {timeout_s}s: {run}")


def test_the_provider_listing_names_the_domains_and_the_local_providers(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    register_simulation_provider(FakeSimulationProvider("fake-fea", domain="fea"))
    body = client.get("/api/simulation/providers").json()
    assert [d["id"] for d in body["domains"]] == ["fea", "robotics", "cfd"]
    robotics = next(d for d in body["domains"] if d["id"] == "robotics")
    assert robotics["title"] == "Robotics"
    assert [r["id"] for r in robotics["artefact_roles"] if r["required"]] == ["model", "timeline"]
    # Grouped by domain, in the domains' order; a provider in this process is online.
    assert [(p["id"], p["domain"], p["online"]) for p in body["providers"]] == [
        ("fake-fea", "fea", True),
        (PROVIDER, "robotics", True),
    ]
    assert body["providers"][1]["capability"] == CAPABILITY
    assert body["providers"][1]["parameters_schema"]["properties"]["steps"]["type"] == "integer"

    only = client.get("/api/simulation/providers", params={"domain": "fea"}).json()["providers"]
    assert [p["id"] for p in only] == ["fake-fea"]
    assert client.get("/api/simulation/providers", params={"domain": "robot"}).status_code == 400


def test_a_run_end_to_end_with_a_partial_artefact_seen_before_the_end(client):
    at_step, release = threading.Event(), threading.Event()

    def on_step(index):
        if index == 2:
            at_step.set()
            assert release.wait(20)

    register_simulation_provider(FakeSimulationProvider(PROVIDER, on_step=on_step))
    (client.scope_dir / "models").mkdir()
    (client.scope_dir / "models" / "arm.glb").write_bytes(b"glb bytes")

    r = _start(client, {"steps": 4}, inputs=["models/arm.glb"], kind="count")
    assert r.status_code == 200, r.text
    started = r.json()["run"]
    run_id = started["run_id"]
    assert started["job_id"].startswith("local-") and started["status"] == "queued"
    assert started["provider"] == {"id": PROVIDER, "domain": "robotics", "engine": "fake", "version": "1.0.0"}
    assert started["parameters"] == {"steps": 4, "duration": 0.0, "label": "plain"}
    assert started["started_by"] == {"id": "local-dev", "display": "Local Dev"}
    assert started["kind"] == "count" and started["inputs"] == ["models/arm.glb"]

    try:
        assert at_step.wait(20), "the provider never reached step 2"
        # Mid-run: status and progress are the job's, and the partial artefact is already there.
        live = _run(client, run_id)
        assert live["status"] == "running" and live["finished_at"] is None
        assert live["progress"] == pytest.approx(0.5) and live["stage"] == "step 2/4"
        (partial,) = [a for a in live["artefacts"] if a["name"] == "timeline.json"]
        assert partial["partial"] is True and partial["role"] == "timeline" and partial["revision"] == 2
        blob = client.get(f"/api/scopes/{SCOPE}/blobs/{partial['key']}")
        assert blob.status_code == 200 and blob.json() == {"steps": [{"step": 1}, {"step": 2}]}
        # The job is the ordinary kind: the shared status route serves it too.
        job = client.get(f"/api/convert/{started['job_id']}").json()
        assert job["status"] == "running" and job["artefacts_ready"]["complete"] is False
    finally:
        release.set()

    done = _wait_ended(client, run_id)
    assert done["status"] == "done" and done["progress"] == 1.0 and done["error"] is None
    assert done["summary"] == {"steps": 4, "inputs": {"models/arm.glb": 9}}
    assert done["job_id"] == started["job_id"] and done["cancel_requested"] is False
    by_name = {a["name"]: a for a in done["artefacts"]}
    assert set(by_name) == {"timeline.json", "model.json", "log.txt"}
    assert not any(a["partial"] for a in done["artefacts"])
    assert client.get(f"/api/scopes/{SCOPE}/blobs/{by_name['model.json']['key']}").json()["steps"][-1] == {"step": 4}

    # The record is a file beside the artefacts, and it is what the listing reads.
    on_disk = client.scope_dir / "_simulations" / run_id
    record = json.loads(_ungzip((on_disk / "run.json").read_bytes()))
    assert record["status"] == "done" and record["started_at"] and record["finished_at"]
    assert sorted(p.name for p in (on_disk / "artefacts").iterdir()) == ["log.txt", "model.json", "timeline.json"]
    listed = client.get(f"/api/scopes/{SCOPE}/simulations").json()["runs"]
    assert [r["run_id"] for r in listed] == [run_id] and listed[0]["status"] == "done"


def test_runs_are_listed_newest_first_and_filtered(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    register_simulation_provider(FakeSimulationProvider("fake-fea", domain="fea"))
    ids = []
    for provider in (PROVIDER, "fake-fea", PROVIDER):
        r = client.post(f"/api/scopes/{SCOPE}/simulations", json={"provider": provider, "parameters": {"steps": 1}})
        assert r.status_code == 200, r.text
        ids.append(r.json()["run"]["run_id"])
        _wait_ended(client, ids[-1])
        time.sleep(0.003)
    url = f"/api/scopes/{SCOPE}/simulations"
    assert [r["run_id"] for r in client.get(url).json()["runs"]] == ids[::-1]
    assert [r["run_id"] for r in client.get(url, params={"provider": "fake-fea"}).json()["runs"]] == [ids[1]]
    assert [r["run_id"] for r in client.get(url, params={"domain": "robotics"}).json()["runs"]] == [ids[2], ids[0]]
    assert [r["run_id"] for r in client.get(url, params={"limit": 1}).json()["runs"]] == [ids[2]]
    # Another scope has none of them.
    assert client.get("/api/scopes/shared/simulations").json() == {"runs": []}
    assert client.get(f"/api/scopes/shared/simulations/{ids[0]}").status_code == 404


def test_parameters_are_checked_before_anything_is_queued(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    r = _start(client, {"steps": 0, "speed": 2})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.startswith("invalid parameters: ")
    assert "steps: must be at least 1" in detail and "speed: is not a parameter of this provider" in detail
    assert _start(client, {"steps": 1}, kind="orbit").status_code == 400
    assert _start(client, {"steps": 1}, inputs=["missing.glb"]).status_code == 404
    assert _start(client, {"steps": 1}, inputs="missing.glb").status_code == 400
    assert client.post(f"/api/scopes/{SCOPE}/simulations", json={"parameters": {}}).status_code == 400
    assert client.get(f"/api/scopes/{SCOPE}/simulations").json() == {"runs": []}


def test_a_provider_nobody_registered_is_a_clear_error_not_a_queued_run(client):
    r = _start(client)
    assert r.status_code == 404 and r.json()["detail"] == f"unknown simulation provider {PROVIDER!r}"
    assert client.get(f"/api/scopes/{SCOPE}/simulations").json() == {"runs": []}


def test_cancelling_a_running_run_stops_it(client):
    at_step, release = threading.Event(), threading.Event()

    def on_step(index):
        if index == 1:
            at_step.set()
            assert release.wait(20)

    register_simulation_provider(FakeSimulationProvider(PROVIDER, on_step=on_step))
    run_id = _start(client, {"steps": 500}).json()["run"]["run_id"]
    try:
        assert at_step.wait(20)
        r = client.post(f"/api/scopes/{SCOPE}/simulations/{run_id}/cancel")
        assert r.status_code == 200, r.text
        assert r.json()["cancelled"] is True
        assert r.json()["run"]["status"] == "running" and r.json()["run"]["cancel_requested"] is True
    finally:
        release.set()
    ended = _wait_ended(client, run_id)
    assert ended["status"] == "cancelled" and ended["cancel_requested"] is False
    assert ended["progress"] < 0.1
    # What it had stored is still listed. Cancelling again changes nothing.
    assert [a["name"] for a in ended["artefacts"] if a["role"] == "timeline"] == ["timeline.json"]
    again = client.post(f"/api/scopes/{SCOPE}/simulations/{run_id}/cancel").json()
    assert again["cancelled"] is False and again["run"]["status"] == "cancelled"
    assert client.post(f"/api/scopes/{SCOPE}/simulations/{'0' * 32}/cancel").status_code == 404
    assert client.get(f"/api/scopes/{SCOPE}/simulations/not-a-run-id").status_code == 404


def test_a_failed_run_reads_as_an_error_with_the_providers_message(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    run_id = _start(client, {"steps": 2, "fail_at": 1}).json()["run"]["run_id"]
    ended = _wait_ended(client, run_id)
    assert ended["status"] == "error" and ended["error"] == "RuntimeError: step 1 failed on request"


def test_a_run_whose_job_the_server_no_longer_knows_is_ended_not_left_running(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    run_id = _start(client, {"steps": 1}).json()["run"]["run_id"]
    _wait_ended(client, run_id)
    # The record of a run a restarted server was in the middle of: running, and its job gone.
    path = client.scope_dir / "_simulations" / run_id / "run.json"
    record = json.loads(_ungzip(path.read_bytes()))
    record.update(status="running", finished_at=None, job_id="local-gone")
    path.write_bytes(json.dumps(record).encode())
    run = _run(client, run_id)
    assert run["status"] == "error" and "lost before the run finished" in run["error"]
    assert json.loads(_ungzip(path.read_bytes()))["status"] == "error"


# ══ the queue path, with the queue faked ═════════════════════════════


def _worker(worker_id: str, *, age_s: float = 0.0, serving: bool = True, provider: str = PROVIDER, **spec) -> dict:
    described = {
        "slug": provider,
        "id": provider,
        "domain": "robotics",
        "engine": "fake",
        "capability": f"sim-{provider}",
        "serving": serving,
        **FakeSimulationProvider().describe().to_dict(),
        **spec,
    }
    return {
        "worker_id": worker_id,
        "last_heartbeat": time.time() - age_s,
        "capabilities": ["base", f"sim-{provider}"] if serving else ["base"],
        "simulation_providers": [described],
    }


def _queue_app(monkeypatch, tmp_path, workers: list[dict]):
    """An app whose queue is faked: no NATS, real routes (as ``test_capability_sharding_route``)."""
    enqueued: list[Job] = []
    published: list[str] = []
    jobs: dict[str, Job] = {}

    async def fake_connect(self, **kwargs):
        return None

    async def fake_list_workers(self):
        return workers

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
        published.append(job.job_id)

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
    return app, enqueued, published, jobs


def test_providers_come_from_the_worker_registry_with_whether_one_is_alive(monkeypatch, tmp_path):
    workers = [
        _worker("w1"),
        _worker("w2"),
        _worker("w3", age_s=600, provider="stopped-sim", domain="cfd", title="Stopped"),
        _worker("w4", serving=False, provider="unserved-sim", domain="fea"),
        _worker("w5", provider="other-kind", domain="thermal"),
    ]
    app, *_ = _queue_app(monkeypatch, tmp_path, workers)
    with TestClient(app) as c:
        providers = c.get("/api/simulation/providers").json()["providers"]
    assert [(p["id"], p["domain"], p["online"], p["workers"]) for p in providers] == [
        ("unserved-sim", "fea", False, 0),
        (PROVIDER, "robotics", True, 2),
        ("stopped-sim", "cfd", False, 0),
    ]
    # A kind this API does not have is not listed under anything.
    assert "other-kind" not in {p["id"] for p in providers}


def test_a_run_is_published_to_the_providers_pool_with_its_record_written_first(monkeypatch, tmp_path):
    app, enqueued, published, _ = _queue_app(monkeypatch, tmp_path, [_worker("w1")])
    with TestClient(app) as c:
        r = c.post("/api/scopes/shared/simulations", json={"provider": PROVIDER, "parameters": {"steps": 2}})
        assert r.status_code == 200, r.text
        run = r.json()["run"]
        (job,) = enqueued
        assert job.target_format == "simulation_run" and job.target_capability == CAPABILITY
        assert job.conversion_options == {"provider_id": PROVIDER, "run_id": run["run_id"]}
        assert job.derived_key == run_record_key(run["run_id"])
        assert published == [job.job_id] and run["job_id"] == job.job_id
        # The record a worker will read carries the job id and the validated parameters.
        stored = json.loads(_ungzip((tmp_path / "shared" / job.derived_key).read_bytes()))
        assert stored["job_id"] == job.job_id and stored["status"] == "queued"
        assert stored["parameters"] == {"steps": 2, "duration": 0.0, "label": "plain"}
        # Queued, and read as queued: nothing has picked it up.
        assert c.get(f"/api/scopes/shared/simulations/{run['run_id']}").json()["run"]["status"] == "queued"


def test_a_run_for_a_provider_no_live_worker_serves_is_refused_not_queued(monkeypatch, tmp_path):
    workers = [_worker("w1", age_s=600), _worker("w2", serving=False)]
    app, enqueued, _, _ = _queue_app(monkeypatch, tmp_path, workers)
    with TestClient(app) as c:
        r = c.post("/api/scopes/shared/simulations", json={"provider": PROVIDER, "parameters": {"steps": 2}})
        assert r.status_code == 409
        assert f"no live worker serves simulation provider {PROVIDER!r}" in r.json()["detail"]
        unknown = c.post("/api/scopes/shared/simulations", json={"provider": "nope", "parameters": {}})
        assert unknown.status_code == 404
        assert c.get("/api/scopes/shared/simulations").json() == {"runs": []}
    assert enqueued == []


def test_a_running_job_is_laid_over_the_record_and_a_failed_one_ends_it(monkeypatch, tmp_path):
    app, enqueued, _, jobs = _queue_app(monkeypatch, tmp_path, [_worker("w1")])
    with TestClient(app) as c:
        run_id = c.post(
            "/api/scopes/shared/simulations", json={"provider": PROVIDER, "parameters": {"steps": 2}}
        ).json()["run"]["run_id"]
        url = f"/api/scopes/shared/simulations/{run_id}"
        job = enqueued[0]
        partial = {"name": "timeline.json", "key": "k", "role": "timeline", "partial": True, "revision": 1}
        job.status, job.stage, job.progress = "running", "step 1/2", 0.5
        job.artefacts_ready = {"artefacts": [partial], "complete": False}
        live = c.get(url).json()["run"]
        assert (live["status"], live["stage"], live["progress"]) == ("running", "step 1/2", 0.5)
        assert live["artefacts"] == [partial]

        # The pool it reached did not have the provider: the worker failed the job as misrouted.
        job.status, job.error = "error", "misrouted: pool capability 'base' cannot run this job"
        failed = c.get(url).json()["run"]
        assert failed["status"] == "error" and failed["error"].startswith("misrouted: ")
        assert failed["finished_at"]

        # A second run, whose job the queue has dropped (a worker died holding it).
        second = c.post(
            "/api/scopes/shared/simulations", json={"provider": PROVIDER, "parameters": {"steps": 2}}
        ).json()["run"]
        del jobs[second["job_id"]]
        lost = c.get(f"/api/scopes/shared/simulations/{second['run_id']}").json()["run"]
        assert lost["status"] == "error" and "lost before the run finished" in lost["error"]


def test_cancelling_a_queued_run_ends_it_and_marks_it_for_a_late_worker(monkeypatch, tmp_path):
    app, enqueued, _, jobs = _queue_app(monkeypatch, tmp_path, [_worker("w1")])
    with TestClient(app) as c:
        run_id = c.post(
            "/api/scopes/shared/simulations", json={"provider": PROVIDER, "parameters": {"steps": 2}}
        ).json()["run"]["run_id"]
        r = c.post(f"/api/scopes/shared/simulations/{run_id}/cancel")
        assert r.status_code == 200 and r.json()["cancelled"] is True
        assert r.json()["run"]["status"] == "cancelled"
        assert (tmp_path / "shared" / cancel_key(run_id)).exists()
        assert jobs[enqueued[0].job_id].status == "cancelled"
        assert c.get(f"/api/scopes/shared/simulations/{run_id}").json()["run"]["status"] == "cancelled"
