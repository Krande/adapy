"""Job grants, the API side: the NATS responder, the token, and what a grant allows.

* the responder refuses each thing it should, by code: a probe, a job that is not there, one that
  has ended, a plugin job, a capability that is not the job's (computed from the provider, not the
  registry), a second worker for a bound job, a malformed subject or body;
* binding: the first worker to ask is bound, its refresh is granted again, a second is refused;
* the token: HS256 with its own issuer; another job's token, a token past its expiry, a CLI token
  or a token signed with another secret are refused;
* what a grant allows is recomputed from the run record: reads of the record, inputs, cancel
  marker and declared read prefixes; writes under the run's own directory only;
* the settings: opt-in, the worker's automatic choice of ``grants``, and the inbox prefix.
"""

from __future__ import annotations

import asyncio
import json
import time

import jwt
import pytest
from obstore.store import LocalStore

from ada import plugins
from ada.comms.rest.config import (
    QueueConfig,
    WorkerGrantsConfig,
    load_worker_grants_config,
    worker_storage_kind,
)
from ada.comms.rest.job_grants import (
    GRANT_ISSUER,
    PLUGIN_JOBS_REFUSAL,
    GrantRefused,
    GrantService,
    GrantTokenError,
    MemoryGrantBindings,
    job_access,
    mint_grant,
    safe_key,
    verify_grant,
)
from ada.comms.rest.queue import Job, JobQueue
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage
from ada.simulation import declare_storage_read_prefixes
from ada.simulation.runs import (
    cancel_key,
    dump_run_record,
    new_run_id,
    new_run_record,
    run_prefix,
    run_record_key,
)

SECRET = "job-grant-test-secret-0123456789abcdef0123"
CFG = WorkerGrantsConfig(secret=SECRET)
PROVIDER = "fake-sim"
CAP = "sim-fake-sim"
PREFIX = "ada.viewer.grants"


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def get(self, job_id: str):
        return self.jobs.get(job_id)


@pytest.fixture
def storage(tmp_path) -> Storage:
    return Storage(LocalStore(str(tmp_path)), prefix="")


async def _run_job(
    storage: Storage,
    jobs: FakeJobs,
    *,
    job_id: str = "job-1",
    provider: str = PROVIDER,
    inputs=("models/cell.json",),
    status: str = "queued",
    scope: Scope | None = None,
    target_capability: str | None = CAP,
) -> tuple[Job, dict]:
    scope = scope or Scope.project("p1")
    record = new_run_record(
        run_id=new_run_id(),
        provider={"id": provider, "domain": "robotics", "engine": "fake"},
        parameters={},
        inputs=list(inputs),
        started_by={"id": "tester"},
    )
    key = run_record_key(record["run_id"])
    await storage.put_bytes(scope, key, dump_run_record(record), content_encoding="gzip")
    job = Job(
        job_id=job_id,
        source_key=key,
        derived_key=key,
        status=status,
        target_format="simulation_run",
        scope_kind=scope.kind,
        scope_id=scope.id,
        conversion_options={"provider_id": provider, "run_id": record["run_id"]},
        target_capability=target_capability,
    )
    jobs.jobs[job_id] = job
    return job, record


def _service(storage, jobs, cfg=CFG) -> GrantService:
    return GrantService(cfg, storage, jobs.get, MemoryGrantBindings())


def _ask(service: GrantService, job_id: str, *, cap: str = CAP, worker: str = "ws-01") -> dict:
    return asyncio.run(service.answer(f"{PREFIX}.{cap}.{worker}", json.dumps({"job_id": job_id}).encode()))


# ── the responder ────────────────────────────────────────────────────────────────────────────────


def test_a_probe_is_answered_no_such_job_without_a_lookup(storage):
    class Exploding(FakeJobs):
        async def get(self, job_id):  # pragma: no cover - must not be called
            raise AssertionError("the probe looked a job up")

    reply = _ask(_service(storage, Exploding()), "probe")
    assert reply == {"ok": False, "error": "no_such_job", "detail": "no such job (probe)", "probe": True}


def test_a_job_that_does_not_exist_is_refused(storage):
    assert _ask(_service(storage, FakeJobs()), "nope")["error"] == "no_such_job"


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_a_job_that_has_ended_gets_no_grant(storage, status):
    jobs = FakeJobs()
    asyncio.run(_run_job(storage, jobs, status=status))
    reply = _ask(_service(storage, jobs), "job-1")
    assert reply["ok"] is False and reply["error"] == "job_finished"


def test_a_plugin_job_is_refused_with_the_reason(storage):
    jobs = FakeJobs()
    jobs.jobs["pj"] = Job(
        job_id="pj",
        source_key="",
        derived_key="_derived/plugin_jobs/x/out.json",
        status="queued",
        target_format="plugin_job",
        conversion_options={"plugin_id": "report"},
        target_capability="report",
    )
    reply = _ask(_service(storage, jobs), "pj", cap="report")
    assert reply["error"] == "kind_not_granted"
    assert reply["detail"] == PLUGIN_JOBS_REFUSAL
    assert "registry" in reply["detail"]


def test_a_conversion_job_is_refused(storage):
    jobs = FakeJobs()
    jobs.jobs["c"] = Job(job_id="c", source_key="a.ifc", derived_key="_derived/a.ifc.glb", status="queued")
    assert _ask(_service(storage, jobs), "c", cap="base")["error"] == "kind_not_granted"


def test_the_capability_is_computed_from_the_provider_not_taken_from_the_job(storage):
    jobs = FakeJobs()
    # The job says another capability (as a registry row written by anyone could have made it).
    asyncio.run(_run_job(storage, jobs, target_capability="sim-other"))
    service = _service(storage, jobs)
    assert _ask(service, "job-1", cap="sim-other")["error"] == "capability_mismatch"
    assert _ask(service, "job-1", cap=CAP)["ok"] is True


def test_a_record_naming_another_provider_is_refused(storage):
    jobs = FakeJobs()
    job, _ = asyncio.run(_run_job(storage, jobs))
    job.conversion_options = {**job.conversion_options, "provider_id": "other"}
    assert _ask(_service(storage, jobs), "job-1", cap="sim-other")["error"] == "kind_not_granted"


def test_a_run_without_a_record_is_refused(storage):
    jobs = FakeJobs()
    job, record = asyncio.run(_run_job(storage, jobs))
    asyncio.run(storage.delete(Scope.project("p1"), run_record_key(record["run_id"])))
    assert _ask(_service(storage, jobs), "job-1")["error"] == "run_record_missing"


def test_the_first_worker_is_bound_and_a_second_is_refused(storage):
    jobs = FakeJobs()
    asyncio.run(_run_job(storage, jobs))
    service = _service(storage, jobs)
    first = _ask(service, "job-1", worker="ws-01")
    assert first["ok"] is True and first["worker"] == "ws-01"
    # Its refresh is the same request, and is granted again.
    assert _ask(service, "job-1", worker="ws-01")["ok"] is True
    second = _ask(service, "job-1", worker="ws-02")
    assert second["ok"] is False and second["error"] == "claimed_by_another_worker"
    assert asyncio.run(service.bindings.holder("job-1")) == "ws-01"


def test_a_binding_whose_holder_stopped_asking_is_taken_over():
    now = [1000.0]
    bindings = MemoryGrantBindings(clock=lambda: now[0])

    async def run():
        assert await bindings.claim("j", "ws-01", CAP, stale_after_s=900) == "ws-01"
        now[0] += 600
        assert await bindings.claim("j", "ws-02", CAP, stale_after_s=900) == "ws-01"  # still alive
        assert await bindings.claim("j", "ws-01", CAP, stale_after_s=900) == "ws-01"  # its refresh
        now[0] += 899
        assert await bindings.claim("j", "ws-02", CAP, stale_after_s=900) == "ws-01"
        now[0] += 2
        assert await bindings.claim("j", "ws-02", CAP, stale_after_s=900) == "ws-02"  # lapsed: taken over
        assert await bindings.claim("j", "ws-01", CAP, stale_after_s=900) == "ws-02"
        return await bindings.holder("j")

    assert asyncio.run(run()) == "ws-02"


class _FakeKv:
    """Compare-and-set KV, as nats-py's: ``create`` fails on an existing key, ``update`` on a stale
    revision."""

    def __init__(self) -> None:
        self.data: dict[str, tuple[bytes, int]] = {}

    async def get(self, key):
        from types import SimpleNamespace

        from nats.js.errors import KeyNotFoundError

        if key not in self.data:
            raise KeyNotFoundError
        value, rev = self.data[key]
        return SimpleNamespace(value=value, revision=rev)

    async def create(self, key, value):
        from nats.js.errors import KeyWrongLastSequenceError

        if key in self.data:
            raise KeyWrongLastSequenceError
        self.data[key] = (value, 1)

    async def update(self, key, value, last):
        from nats.js.errors import KeyWrongLastSequenceError

        if self.data.get(key, (None, 0))[1] != last:
            raise KeyWrongLastSequenceError
        self.data[key] = (value, last + 1)


def test_kv_bindings_bind_once_refresh_and_take_over_a_lapsed_binding():
    from ada.comms.rest.job_grants import KvGrantBindings

    now = [1000.0]
    bindings = KvGrantBindings(_FakeKv(), clock=lambda: now[0])

    async def run():
        assert await bindings.claim("j", "ws-01", CAP, stale_after_s=900) == "ws-01"
        assert await bindings.claim("j", "ws-02", CAP, stale_after_s=900) == "ws-01"
        now[0] += 1000
        assert await bindings.claim("j", "ws-02", CAP, stale_after_s=900) == "ws-02"
        return await bindings.holder("j"), await bindings.holder("other")

    assert asyncio.run(run()) == ("ws-02", None)


@pytest.mark.parametrize(
    "subject, body",
    [
        (f"{PREFIX}.{CAP}", b'{"job_id": "job-1"}'),  # no worker token
        (f"{PREFIX}.{CAP}.ws.01", b'{"job_id": "job-1"}'),  # a deeper subject
        (f"other.prefix.{CAP}.ws-01", b'{"job_id": "job-1"}'),
        (f"{PREFIX}.{CAP}.ws-01", b"not json"),
        (f"{PREFIX}.{CAP}.ws-01", b'{"job": "job-1"}'),
    ],
)
def test_a_malformed_request_is_refused(storage, subject, body):
    reply = asyncio.run(_service(storage, FakeJobs()).answer(subject, body))
    assert reply["ok"] is False and reply["error"] == "bad_request"


def test_a_grant_carries_its_claims_and_nothing_else(storage):
    jobs = FakeJobs()
    asyncio.run(_run_job(storage, jobs))
    reply = _ask(_service(storage, jobs), "job-1")
    claims = jwt.decode(reply["token"], SECRET, algorithms=["HS256"], issuer=GRANT_ISSUER)
    assert set(claims) == {"iss", "job_id", "scope", "cap", "worker", "iat", "exp"}
    assert (claims["job_id"], claims["scope"], claims["cap"], claims["worker"]) == (
        "job-1",
        "projects/p1",
        CAP,
        "ws-01",
    )
    assert claims["exp"] - claims["iat"] == 15 * 60
    assert reply["ttl_s"] == 900 and reply["storage_path"] == "/api/jobs/job-1/storage"


class _Responder:
    """What ``GrantService.start`` asks of a queue, and a message to answer."""

    def __init__(self) -> None:
        self.subscribed = None

    async def subscribe(self, subject, *, queue, cb):
        self.subscribed = (subject, queue, cb)
        return self


class _Msg:
    def __init__(self, subject: str, data: bytes) -> None:
        self.subject, self.data, self.reply = subject, data, None

    async def respond(self, data: bytes) -> None:
        self.reply = json.loads(data)


def test_the_responder_joins_a_queue_group_and_answers_on_the_reply(storage):
    jobs = FakeJobs()
    asyncio.run(_run_job(storage, jobs))
    service = _service(storage, jobs)
    queue = _Responder()

    async def run():
        await service.start(queue)
        subject, group, cb = queue.subscribed
        assert (subject, group) == (f"{PREFIX}.*.*", "ada-viewer-grants")
        msg = _Msg(f"{PREFIX}.{CAP}.ws-01", b'{"job_id": "job-1"}')
        await cb(msg)
        return msg.reply

    assert asyncio.run(run())["ok"] is True


# ── the token ────────────────────────────────────────────────────────────────────────────────────


def test_a_token_round_trips_and_expires():
    token, exp = mint_grant(CFG, job_id="j", scope="shared", cap=CAP, worker="w", now=1000)
    claims = verify_grant(CFG, token, now=1000 + 60)
    assert (claims.job_id, claims.worker, claims.exp) == ("j", "w", exp)
    with pytest.raises(GrantTokenError, match="expired"):
        verify_grant(CFG, token, now=exp + 1)
    # And against the wall clock, a token minted in the past is expired.
    old, _ = mint_grant(CFG, job_id="j", scope="shared", cap=CAP, worker="w", now=time.time() - 3600)
    with pytest.raises(GrantTokenError, match="expired"):
        verify_grant(CFG, old)


def test_tokens_that_are_not_grants_are_refused():
    other_secret, _ = mint_grant(
        WorkerGrantsConfig(secret="another-secret-0123456789abcdef01234567"), job_id="j", scope="s", cap=CAP, worker="w"
    )
    with pytest.raises(GrantTokenError):
        verify_grant(CFG, other_secret)
    cli_like = jwt.encode(
        {"iss": "ada-viewer-cli", "sub": "u", "iat": int(time.time()), "exp": int(time.time()) + 60},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(GrantTokenError):
        verify_grant(CFG, cli_like)
    with pytest.raises(GrantTokenError, match="disabled"):
        verify_grant(WorkerGrantsConfig(secret=""), other_secret)


def test_a_grant_for_one_job_is_refused_on_another(storage):
    jobs = FakeJobs()
    asyncio.run(_run_job(storage, jobs, job_id="job-1"))
    asyncio.run(_run_job(storage, jobs, job_id="job-2"))
    service = _service(storage, jobs)
    token = _ask(service, "job-1")["token"]
    asyncio.run(service.authorise(token, "job-1"))
    with pytest.raises(GrantRefused, match="another job"):
        asyncio.run(service.authorise(token, "job-2"))


def test_a_grant_is_refused_once_the_job_is_bound_elsewhere(storage):
    jobs = FakeJobs()
    asyncio.run(_run_job(storage, jobs))
    service = _service(storage, jobs)
    # A token minted for a worker the job is not bound to (another replica's memory, a forged name).
    token, _ = mint_grant(CFG, job_id="job-1", scope="projects/p1", cap=CAP, worker="ws-09")
    _ask(service, "job-1", worker="ws-01")
    with pytest.raises(GrantRefused) as exc:
        asyncio.run(service.authorise(token, "job-1"))
    assert exc.value.code == "claimed_by_another_worker"


def test_a_grant_with_a_scope_the_job_does_not_have_is_refused(storage):
    jobs = FakeJobs()
    asyncio.run(_run_job(storage, jobs))
    service = _service(storage, jobs)
    _ask(service, "job-1")
    token, _ = mint_grant(CFG, job_id="job-1", scope="projects/other", cap=CAP, worker="ws-01")
    with pytest.raises(GrantRefused, match="no longer matches"):
        asyncio.run(service.authorise(token, "job-1"))


# ── what a grant allows ──────────────────────────────────────────────────────────────────────────


def test_access_is_the_record_its_inputs_the_cancel_marker_and_the_run_directory(storage):
    jobs = FakeJobs()
    job, record = asyncio.run(_run_job(storage, jobs, inputs=("models/cell.json", "models/robot.urdf")))
    access = asyncio.run(job_access(job, storage, CFG))
    run_id = record["run_id"]
    assert access.read_keys == {run_record_key(run_id), cancel_key(run_id), "models/cell.json", "models/robot.urdf"}
    assert access.write_prefixes == (run_prefix(run_id),)
    assert access.can_write(f"{run_prefix(run_id)}artefacts/model.glb")
    assert not access.can_write("models/cell.json")
    assert not access.can_write(f"_simulations/{new_run_id()}/run.json")  # another run's directory
    assert not access.can_read("models/other.json")
    assert not access.can_list("models/") and access.can_list(run_prefix(run_id))
    assert not access.can_list("")


def test_declared_and_configured_read_prefixes_are_granted_and_checked(storage):
    declare_storage_read_prefixes(PROVIDER, ["assets/robots/", "_simulations/", "../up", ""])
    cfg = WorkerGrantsConfig(secret=SECRET, read_prefixes={PROVIDER: ["assets/tools"], "other": ["assets/x/"]})
    jobs = FakeJobs()
    job, _ = asyncio.run(_run_job(storage, jobs))
    access = asyncio.run(job_access(job, storage, cfg))
    # An internal namespace, a path out and the whole scope are dropped; a missing slash is added.
    assert access.read_prefixes == ("assets/robots/", "assets/tools/")
    assert access.can_read("assets/robots/arm/arm.urdf") and access.can_list("assets/robots/arm/")
    assert not access.can_write("assets/robots/arm/arm.urdf")
    assert not access.can_read("assets/x/y")


def test_the_record_is_read_on_every_check_so_a_new_input_is_seen(storage):
    jobs = FakeJobs()
    job, record = asyncio.run(_run_job(storage, jobs, inputs=()))
    assert not asyncio.run(job_access(job, storage, CFG)).can_read("models/late.json")
    record["inputs"] = ["models/late.json"]
    asyncio.run(storage.put_bytes(Scope.project("p1"), run_record_key(record["run_id"]), dump_run_record(record)))
    assert asyncio.run(job_access(job, storage, CFG)).can_read("models/late.json")


@pytest.mark.parametrize("bad", ["../x", "a/../b", "a//b", "a\\b", "", "/", "a/./b", "a\x00b"])
def test_keys_that_could_leave_the_scope_are_refused(bad):
    with pytest.raises(ValueError):
        safe_key(bad)


def test_a_prefix_may_end_in_a_slash():
    assert safe_key("assets/robots/", prefix=True) == "assets/robots/"
    assert safe_key("/assets/x") == "assets/x"


# ── settings ─────────────────────────────────────────────────────────────────────────────────────


def test_grants_are_off_without_a_secret(monkeypatch):
    for name in ("ADA_VIEWER_WORKER_GRANT_SECRET", "ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA"):
        monkeypatch.delenv(name, raising=False)
    assert load_worker_grants_config().enabled is False
    monkeypatch.setenv("ADA_VIEWER_WORKER_GRANT_SECRET", "s3cret\n")
    monkeypatch.setenv("ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA", '{"sim-x": {"limit": 3}}')
    monkeypatch.setenv("ADA_VIEWER_NATS_PUBLIC_URL", "wss://bus.example")
    cfg = load_worker_grants_config()
    assert cfg.enabled and cfg.secret == "s3cret"
    assert cfg.bootstrap_extra == {"sim-x": {"limit": 3}} and cfg.nats_public_url == "wss://bus.example"


@pytest.mark.parametrize("raw", ["[1]", "not json"])
def test_a_bootstrap_block_that_is_not_an_object_is_refused(monkeypatch, raw):
    monkeypatch.setenv("ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA", raw)
    with pytest.raises(ValueError, match="ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA"):
        load_worker_grants_config()


@pytest.mark.parametrize(
    "env, kind",
    [
        ({}, "local"),
        ({"ADA_VIEWER_API_URL": "https://studio.example"}, "grants"),
        ({"ADA_VIEWER_API_URL": "https://studio.example", "ADA_VIEWER_S3_BUCKET": "b"}, "local"),
        ({"ADA_VIEWER_API_URL": "https://studio.example", "ADA_VIEWER_LOCAL_PATH": "/data"}, "local"),
        ({"ADA_VIEWER_API_URL": "https://studio.example", "ADA_VIEWER_STORAGE_KIND": "s3"}, "s3"),
        ({"ADA_VIEWER_STORAGE_KIND": "grants"}, "grants"),
    ],
)
def test_a_worker_picks_grants_only_with_an_api_and_no_storage_of_its_own(env, kind):
    assert worker_storage_kind(env) == kind


def test_the_api_never_picks_grants_by_itself(monkeypatch, tmp_path):
    from ada.comms.rest.config import load_settings

    monkeypatch.delenv("ADA_VIEWER_STORAGE_KIND", raising=False)
    monkeypatch.delenv("ADA_VIEWER_S3_BUCKET", raising=False)
    monkeypatch.delenv("ADA_VIEWER_LOCAL_PATH", raising=False)
    monkeypatch.setenv("ADA_VIEWER_API_URL", "https://studio.example")
    monkeypatch.setenv("ADA_VIEWER_WORKER_NAME", "ws-01")
    assert load_settings().storage_kind == "local"
    worker = load_settings(role="worker")
    assert worker.storage_kind == "grants"
    assert (worker.grant_storage.api_url, worker.grant_storage.worker_name) == ("https://studio.example", "ws-01")


def test_a_worker_name_that_is_not_one_subject_token_is_refused(monkeypatch):
    from ada.comms.rest.config import worker_name_from_env

    monkeypatch.setenv("ADA_VIEWER_WORKER_NAME", "ws.01")
    with pytest.raises(ValueError, match="subject token"):
        worker_name_from_env()


def test_the_inbox_prefix_reaches_nats_connect_only_when_set():
    base = dict(url="nats://x:4222", stream="s", subject="j", kv_bucket="k", durable="d")
    assert "inbox_prefix" not in JobQueue(QueueConfig(**base))._connect_options(None)
    opts = JobQueue(QueueConfig(**base, inbox_prefix="_INBOX_ws-01"))._connect_options(None)
    assert opts["inbox_prefix"] == "_INBOX_ws-01"
