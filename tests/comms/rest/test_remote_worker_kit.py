"""The remote worker kit, end to end: a worker with no storage keys runs a simulation job by grant.

The bus is faked (a request is answered by the API's ``GrantService`` directly) and the API is a
FastAPI ``TestClient``; everything between is the real code:

* ``RemoteGrantStorage``: the six facade methods, a multipart upload with a part that fails once
  and is retried, an upload resumed by an earlier upload id, a refresh at two thirds of the grant;
* the worker's ``simulation_run`` handler in ``grants`` mode: the provider runs, its artefacts and
  record land in the scope, the job ends done; a job bound to another worker is left alone;
* the bootstrap route and its client, the preflight checks, the ``nats.conf`` entry;
* an nkey seed never reaches a log line, a check's message or an error.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import tempfile
import threading

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-remote-worker-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    GrantStorageConfig,
    LocalConfig,
    QueueConfig,
    Settings,
    WorkerGrantsConfig,
)
from ada.comms.rest.job_grants import GrantService, MemoryGrantBindings  # noqa: E402
from ada.comms.rest.queue import Job  # noqa: E402
from ada.comms.rest.remote_worker import (  # noqa: E402
    AUTH_VIOLATION,
    PERMISSION_VIOLATION,
    GrantClient,
    GrantModeStorage,
    GrantModeUnsupported,
    RemoteGrantStorage,
    active_grant_storage,
    check_seed,
    check_version_floor,
    classify_nats_error,
    fetch_bootstrap,
    nats_conf_entry,
    probe_grant,
    public_key_of_seed,
    render_nats_conf_entry,
)
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.worker import _process_one  # noqa: E402
from ada.simulation import register_simulation_provider  # noqa: E402
from ada.simulation.runs import (  # noqa: E402
    dump_run_record,
    new_run_id,
    new_run_record,
    parse_run_record,
    run_prefix,
    run_record_key,
)
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

SECRET = "remote-worker-test-secret-0123456789abcdef"
CFG = WorkerGrantsConfig(
    secret=SECRET,
    part_size=1024,
    small_object_max=2048,
    max_size=1_000_000,
    nats_public_url="wss://bus.example",
    min_ada_py="0.100.0",
    bootstrap_extra={"sim-fake-sim": {"partial_put_interval_s": 5}},
)
PROVIDER = "fake-sim"
CAP = "sim-fake-sim"
SCOPE = Scope.project("p1")
WORKER = "ws-01"


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


def _settings(path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(url=None, stream="s", subject="ada.viewer.jobs.convert", kv_bucket="k", durable="d"),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
        worker_grants=CFG,
    )


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def get(self, job_id):
        return self.jobs.get(job_id)


class _NoClose:
    """A TestClient the facade may 'close' without closing the app under test."""

    def __init__(self, client, *, fail_part_puts: int = 0) -> None:
        self._client = client
        self.fail_part_puts = fail_part_puts
        self.part_puts = 0

    def __getattr__(self, name):
        return getattr(self._client, name)

    def put(self, url, **kw):
        if "upload/part" in str(url):
            self.part_puts += 1
            if self.fail_part_puts:
                self.fail_part_puts -= 1
                import httpx

                return httpx.Response(503, json={"detail": "try again"})
        return self._client.put(url, **kw)

    def close(self) -> None:
        pass


class World:
    """The API under test, its grant service, a fake bus, and a loop thread a worker would have."""

    def __init__(self, client, service, jobs, root) -> None:
        self.client, self.service, self.jobs, self.root = client, service, jobs, root
        self.storage = service.storage
        self.requests: list[str] = []
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(5)

    async def request(self, subject: str, payload: bytes, timeout: float) -> bytes:
        """The bus: a request on a grant subject, answered by the API's responder."""
        self.requests.append(subject)
        return json.dumps(await self.service.answer(subject, payload)).encode()

    def on_loop(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def new_run(self, job_id="job-1", inputs=()) -> tuple[Job, dict]:
        record = new_run_record(
            run_id=new_run_id(),
            provider={"id": PROVIDER, "domain": "fea", "engine": "fake-solver"},
            parameters={"steps": 3},
            inputs=list(inputs),
            started_by={"id": "tester"},
        )
        key = run_record_key(record["run_id"])
        self.on_loop(self.storage.put_bytes(SCOPE, key, dump_run_record(record), content_encoding="gzip"))
        job = Job(
            job_id=job_id,
            source_key=key,
            derived_key=key,
            status="queued",
            target_format="simulation_run",
            scope_kind="project",
            scope_id="p1",
            conversion_options={"provider_id": PROVIDER, "run_id": record["run_id"]},
            target_capability=CAP,
        )
        self.jobs.jobs[job_id] = job
        return job, record

    def facade(self, job_id="job-1", **kw) -> tuple[RemoteGrantStorage, GrantClient]:
        grants = GrantClient(
            self.request, subject_prefix=CFG.subject_prefix, capability=CAP, worker_name=WORKER, job_id=job_id
        )
        http = _NoClose(self.client, **kw)
        return (
            RemoteGrantStorage(
                job_id, grants.token_provider(self.loop), client=http, small_object_max=2048, sleep=lambda s: None
            ),
            grants,
        )

    def stored(self, key: str) -> bytes:
        return (self.root / SCOPE.prefix() / key).read_bytes()


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    app = create_app(_settings(root))
    with TestClient(app) as client:
        jobs = FakeJobs()
        service = GrantService(CFG, app.state.rest.storage, jobs.get, MemoryGrantBindings())
        app.state.job_grants = service
        w = World(client, service, jobs, root)
        try:
            yield w
        finally:
            w.stop()


# ── the facade ───────────────────────────────────────────────────────────────────────────────────


def test_the_facade_reads_and_writes_what_the_job_may(world, tmp_path):
    job, record = world.new_run(inputs=["models/cell.json"])
    world.on_loop(world.storage.put_bytes(SCOPE, "models/cell.json", b'{"cell": 1}'))
    fs, _ = world.facade()
    run_id = record["run_id"]
    assert parse_run_record(fs.get_bytes(run_record_key(run_id)))["run_id"] == run_id
    assert fs.get_bytes("models/cell.json") == b'{"cell": 1}'
    with pytest.raises(FileNotFoundError):
        fs.get_bytes(f"{run_prefix(run_id)}cancel")  # what the runner's cancel poll reads as "not cancelled"
    with pytest.raises(PermissionError):
        fs.get_bytes("models/other.json")

    fs.put_bytes(f"{run_prefix(run_id)}artefacts/a.json", b"{}")
    fs.put_bytes(run_record_key(run_id), dump_run_record({**record, "stage": "x"}), content_encoding="gzip")
    assert world.stored(run_record_key(run_id))[:2] == b"\x1f\x8b"
    with pytest.raises(PermissionError):
        fs.put_bytes("models/cell.json", b"overwrite")
    assert sorted(fs.list_keys(run_prefix(run_id))) == [f"{run_prefix(run_id)}artefacts/a.json", run_record_key(run_id)]
    assert fs.provider_denial("models/cell.json") is None

    dest = tmp_path / "rec.json"
    fs.fetch_to_path(run_record_key(run_id), dest)  # gzip at rest undone on the way down
    assert json.loads(dest.read_text())["stage"] == "x"


def test_a_large_file_goes_up_in_parts_with_a_failed_part_retried(world, tmp_path):
    _, record = world.new_run()
    fs, _ = world.facade(fail_part_puts=1)
    src = tmp_path / "big.bin"
    data = os.urandom(3000)
    src.write_bytes(data)
    key = f"{run_prefix(record['run_id'])}artefacts/big.bin"
    out = fs.put_path(key, src)
    assert out["stored_bytes"] == 3000
    assert world.stored(key) == data
    assert fs._client.part_puts == 4  # three parts, one of them twice
    # And read back whole through get_bytes (above the API's small-object limit: via download-url).
    assert fs.get_bytes(key) == data


def test_bytes_above_the_small_limit_take_the_upload_path(world):
    _, record = world.new_run()
    fs, _ = world.facade()
    key = f"{run_prefix(record['run_id'])}artefacts/frames.bin"
    data = os.urandom(2500)
    fs.put_bytes(key, data)
    assert world.stored(key) == data


def test_an_upload_resumes_from_an_earlier_upload_id(world, tmp_path):
    _, record = world.new_run()
    fs, _ = world.facade()
    key = f"{run_prefix(record['run_id'])}artefacts/resume.bin"
    data = os.urandom(2100)
    src = tmp_path / "r.bin"
    src.write_bytes(data)
    # An earlier process started the upload and sent the first part, then stopped.
    plan = fs._api("POST", "upload", json={"key": key, "size": len(data)}).json()
    first = next(p for p in plan["parts"] if p["part_number"] == 1)
    fs._send(first["url"], data[:1024])
    http = fs._client
    http.part_puts = 0
    fs.upload_file(key, src, resume_upload_id=plan["upload_id"])
    assert world.stored(key) == data
    assert http.part_puts == 2  # parts 2 and 3 only


def test_the_grant_is_renewed_at_two_thirds_of_its_life(world):
    world.new_run()
    now = [1000.0]
    grants = GrantClient(
        world.request,
        subject_prefix=CFG.subject_prefix,
        capability=CAP,
        worker_name=WORKER,
        job_id="job-1",
        clock=lambda: now[0],
    )
    first = world.on_loop(grants.grant())
    assert world.on_loop(grants.grant()) is first and len(world.requests) == 1
    now[0] += CFG.ttl_s * 2 / 3 - 1
    assert world.on_loop(grants.grant()) is first
    now[0] += 2
    assert world.on_loop(grants.grant()) is not first and len(world.requests) == 2
    assert world.requests == [f"ada.viewer.grants.{CAP}.{WORKER}"] * 2
    assert "token" not in repr(first) and first.token not in repr(first)


def test_a_job_that_ended_gets_no_renewal(world):
    job, _ = world.new_run()
    fs, grants = world.facade()
    world.on_loop(grants.grant())
    job.status = "done"
    from ada.comms.rest.remote_worker import GrantRefusedError

    with pytest.raises(GrantRefusedError) as exc:
        world.on_loop(grants.grant(force=True))
    assert exc.value.code == "job_finished"


def test_a_refused_renewal_keeps_a_grant_that_still_has_life(world):
    from ada.comms.rest.remote_worker import GrantRefusedError

    job, _ = world.new_run()
    now = [1000.0]
    grants = GrantClient(
        world.request,
        subject_prefix=CFG.subject_prefix,
        capability=CAP,
        worker_name=WORKER,
        job_id="job-1",
        clock=lambda: now[0],
    )
    first = world.on_loop(grants.grant())
    job.status = "cancelled"  # the cancel route marks the job before the worker writes its last record
    now[0] += CFG.ttl_s * 0.8
    assert world.on_loop(grants.grant()) is first
    now[0] += CFG.ttl_s * 0.2
    with pytest.raises(GrantRefusedError):
        world.on_loop(grants.grant())


# ── the worker, in grants mode ───────────────────────────────────────────────────────────────────


class _WorkerQueue:
    """The worker's queue: the job in memory, and the bus's request."""

    def __init__(self, world: World, job: Job) -> None:
        self.world, self.job, self.updates = world, job, []

    async def get(self, job_id):
        return self.job if job_id == self.job.job_id else None

    async def update(self, job_id, **kw):
        self.updates.append(kw)
        for k, v in kw.items():
            setattr(self.job, k, v)
        return self.job

    async def request(self, subject, payload, timeout):
        return await self.world.request(subject, payload, timeout)


def _grant_mode(world: World) -> GrantModeStorage:
    cfg = GrantStorageConfig(api_url="http://testserver", worker_name=WORKER)
    return GrantModeStorage(cfg, small_object_max=2048, client_factory=lambda: _NoClose(world.client))


@pytest.mark.asyncio
async def test_a_simulation_run_by_grant_ends_done_with_its_artefacts_in_the_scope(world):
    seen = {}
    provider = FakeSimulationProvider(PROVIDER)
    provider.on_step = lambda i: seen.setdefault("facade", active_grant_storage(record["run_id"]))
    register_simulation_provider(provider)
    job, record = await asyncio.to_thread(world.new_run)
    queue = _WorkerQueue(world, job)

    await _process_one(job.job_id, queue, _grant_mode(world), None, None)

    final = queue.updates[-1]
    assert (final["status"], final["stage"], final["error"]) == ("done", "ready", None)
    stored = parse_run_record(
        await asyncio.to_thread(lambda: __import__("gzip").decompress(world.stored(run_record_key(record["run_id"]))))
    )
    assert stored["status"] == "done" and stored["job_id"] == job.job_id
    names = {a["name"] for a in stored["artefacts"]}
    assert {"timeline.json", "model.json", "log.txt"} <= names
    assert world.requests and all(s == f"ada.viewer.grants.{CAP}.{WORKER}" for s in world.requests)
    assert isinstance(seen.get("facade"), RemoteGrantStorage)
    assert active_grant_storage(record["run_id"]) is None  # released when the run ends


@pytest.mark.asyncio
async def test_a_run_bound_to_another_worker_is_left_alone(world):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    job, record = await asyncio.to_thread(world.new_run)
    await world.service.bindings.claim(job.job_id, "ws-02", CAP)
    queue = _WorkerQueue(world, job)
    storage = _grant_mode(world)
    storage.claim_wait_s, storage.claim_poll_s = 0.2, 0.05
    await _process_one(job.job_id, queue, storage, None, None)
    # Picked up (the loading tick), asked a few times while it waited, and then not touched.
    assert all(u.get("status") != "error" for u in queue.updates)
    assert queue.updates[-1]["status"] == "running"
    assert len(world.requests) >= 2


@pytest.mark.asyncio
async def test_a_run_whose_worker_stopped_is_taken_over_once_its_binding_lapses(world):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    job, record = await asyncio.to_thread(world.new_run)
    # Bound to a worker that last asked more than a grant lifetime ago (it crashed).
    world.service.bindings._held[job.job_id] = {"worker": "ws-02", "cap": CAP, "bound_at": 0.0, "seen_at": 0.0}
    queue = _WorkerQueue(world, job)
    await _process_one(job.job_id, queue, _grant_mode(world), None, None)
    assert queue.updates[-1]["status"] == "done"
    assert await world.service.bindings.holder(job.job_id) == WORKER


@pytest.mark.asyncio
async def test_other_job_kinds_fail_by_name_on_a_worker_without_storage(world):
    storage = _grant_mode(world)
    with pytest.raises(GrantModeUnsupported, match="no storage keys"):
        await storage.get_bytes(SCOPE, "x")


# ── bootstrap, preflight, nats.conf ──────────────────────────────────────────────────────────────


def test_the_bootstrap_is_public_and_says_how_to_join(world):
    doc = world.client.get("/api/workers/bootstrap").json()
    assert doc["nats"] == {"url": "wss://bus.example"}
    assert doc["grants"]["enabled"] is True and doc["grants"]["subject_prefix"] == "ada.viewer.grants"
    assert doc["grants"]["inbox_prefix"] == "_INBOX_<worker name>"
    assert doc["grants"]["multipart"] == {"part_size": 1024, "max_size": 1_000_000}
    assert doc["worker"] == {"min_ada_py": "0.100.0"}
    assert doc["capabilities"] == {"sim-fake-sim": {"partial_put_interval_s": 5}}
    assert SECRET not in json.dumps(doc)

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    boot = fetch_bootstrap("https://studio.example/", opener=lambda req, timeout: _Resp(json.dumps(doc).encode()))
    assert boot.nats_url == "wss://bus.example" and boot.part_size == 1024 and boot.min_ada_py == "0.100.0"
    assert boot.capability(CAP) == {"partial_put_interval_s": 5} and boot.capability("other") == {}
    assert boot.inbox_prefix(WORKER) == "_INBOX_ws-01"
    assert boot.grant_subject(CAP, WORKER) == "ada.viewer.grants.sim-fake-sim.ws-01"


def test_the_grant_probe_passes_on_no_such_job(world):
    ok = world.on_loop(
        probe_grant(world.request, subject_prefix="ada.viewer.grants", capability=CAP, worker_name=WORKER)
    )
    assert ok.ok, ok

    async def no_responders(subject, payload, timeout):
        from nats.errors import NoRespondersError

        raise NoRespondersError

    failed = world.on_loop(
        probe_grant(no_responders, subject_prefix="ada.viewer.grants", capability=CAP, worker_name=WORKER)
    )
    assert not failed.ok and "not enabled" in failed.detail

    async def silent(subject, payload, timeout):
        raise asyncio.TimeoutError

    timed_out = world.on_loop(
        probe_grant(silent, subject_prefix="ada.viewer.grants", capability=CAP, worker_name=WORKER)
    )
    assert not timed_out.ok and "permission" in timed_out.detail


def test_auth_and_permission_violations_are_told_apart():
    from nats.errors import AuthorizationError, Error

    assert classify_nats_error(AuthorizationError()) == AUTH_VIOLATION
    assert classify_nats_error("nats: 'Authorization Violation'") == AUTH_VIOLATION
    assert (
        classify_nats_error(Error("nats: 'Permissions Violation for Publish to \"ada.viewer.grants.x.y\"'"))
        == PERMISSION_VIOLATION
    )
    assert classify_nats_error(TimeoutError()) == "other"


def test_the_version_floor():
    assert check_version_floor("0.172.1", "0.170.0").ok
    assert not check_version_floor("0.160.0", "0.170.0").ok
    assert not check_version_floor("0.172.0rc1", "0.170.0").ok
    assert check_version_floor("0.1.0", None).ok
    assert not check_version_floor(None, "0.170.0").ok


def _seed_pair() -> tuple[str, str]:
    import nkeys

    seed = nkeys.encode_seed(os.urandom(32), nkeys.PREFIX_BYTE_USER).decode()
    return seed, public_key_of_seed(seed)


def test_the_nats_conf_entry_allows_exactly_its_own_subjects():
    _, public = _seed_pair()
    entry = nats_conf_entry(
        public_key=public, worker_name=WORKER, capabilities=["sim.fake-sim"], registry_bucket="ada-viewer-workers"
    )
    pub, sub = entry["permissions"]["publish"]["allow"], entry["permissions"]["subscribe"]["allow"]
    assert sub == ["_INBOX_ws-01.>", "ada.viewer.jobs.convert.sim-fake-sim"]
    assert "ada.viewer.grants.sim-fake-sim.ws-01" in pub
    assert "$JS.API.CONSUMER.MSG.NEXT.ADA_VIEWER_JOBS.ada-viewer-worker-sim-fake-sim" in pub
    assert "$KV.ada-viewer-workers.__meta_worker__ws-01" in pub
    assert not any(s.startswith("_INBOX.") or s == ">" or "ada-viewer-job-grants" in s for s in pub + sub)
    text = render_nats_conf_entry(entry, comment="remote worker ws-01")
    assert f'nkey: "{public}"' in text and '"_INBOX_ws-01.>"' in text and text.startswith("  # remote worker ws-01")


def test_a_seed_never_reaches_a_log_a_check_or_an_error(world, caplog):
    seed, public = _seed_pair()
    _, other_public = _seed_pair()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    ada_logger = logging.getLogger("ada")
    ada_logger.addHandler(handler)
    old_level = ada_logger.level
    ada_logger.setLevel(logging.DEBUG)
    texts: list[str] = []
    try:
        with caplog.at_level(logging.DEBUG):
            texts.append(str(check_seed(seed, public)))
            texts.append(str(check_seed(seed, other_public)))
            texts.append(str(check_seed(seed[:-3] + "AAA", public)))  # a damaged seed
            for bad in (seed, seed.lower()):
                try:
                    nats_conf_entry(public_key=bad, worker_name=WORKER, capabilities=[CAP])
                except ValueError as exc:
                    texts.append(str(exc))
            try:
                public_key_of_seed("SU" + seed[2:-1])
            except ValueError as exc:
                texts.append(str(exc))
            # The worker's NATS options with an inline seed, as the worker builds them.
            from ada.comms.rest.queue import JobQueue

            q = JobQueue(
                QueueConfig(
                    url="nats://x:4222",
                    stream="s",
                    subject="j",
                    kv_bucket="k",
                    durable="d",
                    nkey_seed=seed,
                    nkey_seed_file="/k.seed",
                )
            )
            opts = q._connect_options("adapy-worker-ws-01")
            assert "nkeys_seed" in opts  # the file wins; the inline seed is not echoed anywhere
    finally:
        ada_logger.removeHandler(handler)
        ada_logger.setLevel(old_level)
    assert check_seed(seed, public).ok and not check_seed(seed, other_public).ok
    everything = "\n".join([caplog.text, stream.getvalue(), *texts])
    assert seed not in everything
    assert seed[2:20] not in everything
    assert public in everything  # the public key is what the messages name instead
