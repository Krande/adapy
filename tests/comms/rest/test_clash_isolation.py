"""Clash jobs run killably: the forked-child machinery (``subprocess_convert.run_isolated``) and the
clash handlers' use of it (``formats/clash_isolation.py``).

Two halves, because the worker is Linux-only but this suite also runs on Windows:

* ``run_isolated`` itself forks, so its tests are POSIX-only (skipped where there is no fork);
* the handlers' side -- which path they take, what a cancel / timeout / crash outcome does to the
  queue entry, the audit row and the upload, and the parent-served storage the child reads
  through -- is driven with a stand-in runner that needs no fork, so it runs everywhere.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-isolation-"))

import pytest  # noqa: E402

# Imported for its side effect as much as anything: app creation pulls adacpp in before numpy's
# BLAS initialises, which this environment needs (see the other REST suites).
from ada.comms.rest.app import create_app  # noqa: E402,F401
from ada.comms.rest import subprocess_convert  # noqa: E402
from ada.comms.rest.formats import clash_isolation  # noqa: E402
from ada.comms.rest.subprocess_convert import (  # noqa: E402
    HAVE_POSIX_FORK,
    IsolatedResult,
    run_isolated,
)

needs_fork = pytest.mark.skipif(not HAVE_POSIX_FORK, reason="run_isolated forks (POSIX only)")

OPTIONS = {"include_plate_joints": False}


# ── run_isolated itself (POSIX) ────────────────────────────────────────


@needs_fork
@pytest.mark.asyncio
async def test_run_isolated_hands_back_the_work_dir_and_progress():
    seen: list[tuple[str, float]] = []

    async def on_progress(stage, frac):
        seen.append((stage, frac))

    def fn(child):
        child.progress("half", 0.5)
        (child.work_dir / "out.txt").write_text("hello")

    res = await run_isolated(fn, label="test job", on_progress=on_progress)
    try:
        assert res.ok and res.signal_name is None, res.error
        assert (res.work_dir / "out.txt").read_text() == "hello"
        assert ("half", 0.5) in seen
    finally:
        res.cleanup()
    assert res.work_dir is None


@needs_fork
@pytest.mark.asyncio
async def test_run_isolated_reports_the_childs_exception_in_parts():
    def fn(child):
        raise ValueError("no member resolved")

    res = await run_isolated(fn, label="test job")
    assert not res.ok and res.work_dir is None
    assert res.exit_code == 2 and res.signal_name is None
    assert res.error_type == "ValueError"
    assert res.error_message == "no member resolved"
    assert "Traceback" in (res.traceback or "")


@needs_fork
@pytest.mark.asyncio
async def test_run_isolated_cancel_reaps_a_running_child():
    async def cancel_check():
        return True

    def fn(child):
        time.sleep(60)

    started = time.monotonic()
    res = await run_isolated(fn, label="test job", cancel_check=cancel_check)
    assert res.cancelled and res.signal_name == "CANCELLED"
    assert res.work_dir is None
    # first poll ~3 s in, SIGTERM reaps a sleeper at once (5 s grace cap)
    assert time.monotonic() - started < 12.0


@needs_fork
@pytest.mark.asyncio
async def test_run_isolated_timeout_is_named():
    def fn(child):
        time.sleep(60)

    started = time.monotonic()
    res = await run_isolated(fn, label="clash check", timeout_s=1.0)
    assert res.signal_name == "TIMEOUT"
    assert "clash check" in res.error and "timeout" in res.error.lower()
    assert time.monotonic() - started < 10.0


@needs_fork
@pytest.mark.asyncio
async def test_run_isolated_serves_child_requests_from_the_parent():
    """The child's only way to storage: a request the parent serves (and an absence comes back as
    the FileNotFoundError a reader catches)."""
    blobs = {"a.txt": b"A" * 200_000}

    async def serve(op, args):
        if op == "get":
            if args["key"] not in blobs:
                raise FileNotFoundError(args["key"])
            pathlib.Path(args["dest"]).write_bytes(blobs[args["key"]])
            return args["dest"]
        raise ValueError(op)

    def fn(child):
        dest = child.work_dir / "got.bin"
        child.call("get", key="a.txt", dest=str(dest))
        try:
            child.call("get", key="missing", dest=str(dest) + ".x")
        except FileNotFoundError:
            (child.work_dir / "absent.flag").write_text("ok")

    res = await run_isolated(fn, label="test job", serve=serve)
    try:
        assert res.ok, res.error
        assert (res.work_dir / "got.bin").read_bytes() == blobs["a.txt"]
        assert (res.work_dir / "absent.flag").exists()
    finally:
        res.cleanup()


@needs_fork
@pytest.mark.asyncio
async def test_run_isolated_convert_still_wraps_it():
    """The conversion wrapper is a thin layer over run_isolated now -- same output contract."""

    def quick(src, source_key, target_format, on_progress, **_kw):
        on_progress("hello", 1.0)
        return b"ok"

    src = pathlib.Path(tempfile.mkdtemp()) / "x.bin"
    src.write_bytes(b"")
    res = await subprocess_convert.run_isolated_convert(quick, src, "x.bin", "glb")
    assert res.exit_code == 0 and res.out_path is not None
    assert res.out_path.read_bytes() == b"ok"
    res.cleanup_output()


# ── the handlers (any platform: a stand-in runner, no fork) ────────────


class _Queue:
    enabled = False  # no live pool: every non-builtin spec reports capability None

    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def update(self, job_id, **kw):
        self.updates.append(kw)


def _one_girder_ifc(tmp_path: pathlib.Path, name: str, beams: list[tuple]) -> bytes:
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
def group_job(tmp_path):
    """A storage with two one-girder IFCs, and a clash_check_group job over both."""
    from obstore.store import LocalStore

    from ada.clash.group import normalise_group
    from ada.comms.rest.queue import Job
    from ada.comms.rest.scope import Scope
    from ada.comms.rest.storage import Storage

    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = Scope.shared()

    async def _stage():
        await storage.put_bytes(scope, "a.ifc", _one_girder_ifc(tmp_path, "A", [("ga", (0, 0, 0), (2, 0, 0))]))
        await storage.put_bytes(scope, "b.ifc", _one_girder_ifc(tmp_path, "B", [("gb", (1, 0, 0), (1, 2, 0))]))

    asyncio.run(_stage())
    group = normalise_group(
        {
            "name": "g",
            "members": [
                {"target": {"kind": "file", "source_key": "a.ifc"}, "element": None, "path": []},
                {"target": {"kind": "file", "source_key": "b.ifc"}, "element": None, "path": []},
            ],
        }
    )
    job = Job(
        job_id="job-check",
        source_key="group:tok",
        derived_key="_derived/clash/group/tok/o/result.json",
        status="queued",
        target_format="clash_check_group",
        conversion_options={"group": group, "group_token": "tok", "options": OPTIONS},
    )
    return storage, scope, job


def _ctx(storage, scope, queue, *, db_pool=None, src_path=None):
    from ada.comms.rest.formats.registry import JobContext

    return JobContext(
        scope=scope, storage=storage, queue=queue, db_pool=db_pool, started_at=time.monotonic(), src_path=src_path
    )


class _FakeRunner:
    """Stands in for ``run_isolated``: records how it was called, and either runs ``fn`` in a
    thread against a child handle whose ``call`` reaches the real ``serve`` (so the parent-served
    storage is exercised without a fork) or returns a canned watchdog outcome."""

    def __init__(self, outcome: str = "run") -> None:
        self.outcome = outcome
        self.calls: list[dict] = []

    async def __call__(self, fn, **kw):
        self.calls.append(kw)
        if self.outcome != "run":
            canned = {
                "CANCELLED": dict(signal_name="CANCELLED", exit_code=-15, error=None),
                "TIMEOUT": dict(signal_name="TIMEOUT", exit_code=-15, error="clash check exceeded ..."),
                "OOM": dict(signal_name="OOM", exit_code=-9, error="clash check ran out of memory (exceeded ...)"),
                "SIGSEGV": dict(signal_name="SIGSEGV", exit_code=-11, error="clash check subprocess killed by SIGSEGV"),
            }[self.outcome]
            return IsolatedResult(work_dir=None, traceback=None, samples=[], final_metrics={}, **canned)

        loop = asyncio.get_running_loop()
        work_dir = pathlib.Path(tempfile.mkdtemp(prefix="fake-isolated-"))
        serve = kw.get("serve")
        on_progress = kw.get("on_progress")

        class _Child:
            def __init__(self):
                self.work_dir = work_dir

            def progress(self, stage, frac):
                if on_progress is not None:
                    asyncio.run_coroutine_threadsafe(on_progress(stage, frac), loop).result()

            def call(self, op, **args):
                assert serve is not None, "work asked for storage but the handler served none"
                return asyncio.run_coroutine_threadsafe(serve(op, args), loop).result()

        try:
            await loop.run_in_executor(None, fn, _Child())
        except Exception as exc:  # noqa: BLE001 - mirror the child's error.json
            return IsolatedResult(
                work_dir=None,
                error=f"{type(exc).__name__}: {exc}",
                traceback="tb",
                exit_code=2,
                signal_name=None,
                samples=[],
                final_metrics={},
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
        return IsolatedResult(
            work_dir=work_dir, error=None, traceback=None, exit_code=0, signal_name=None, samples=[], final_metrics={}
        )


@pytest.fixture
def forked(monkeypatch):
    """Make the handlers take the isolated path, through a ``_FakeRunner``; returns a setter."""
    monkeypatch.setattr(clash_isolation, "_fork_available", lambda: True)

    def _use(outcome: str = "run") -> _FakeRunner:
        runner = _FakeRunner(outcome)
        monkeypatch.setattr(subprocess_convert, "run_isolated", runner)
        return runner

    return _use


@pytest.fixture
def audit_calls(monkeypatch):
    calls: list[tuple] = []

    async def _rec(db_pool, job_id, status, error, started_at, traceback=None, metrics=None):
        calls.append((status, error))

    monkeypatch.setattr(clash_isolation, "_audit_done", _rec)
    from ada.comms.rest.formats import clash_check_group

    monkeypatch.setattr(clash_check_group, "_audit_done", _rec)
    return calls


@pytest.mark.asyncio
async def test_group_check_runs_isolated_and_reads_storage_through_the_parent(group_job, forked, audit_calls):
    from ada.comms.rest.formats.clash_check_group import ClashCheckGroupHandler

    storage, scope, job = group_job
    runner = forked("run")
    queue = _Queue()
    await ClashCheckGroupHandler().run(job, _ctx(storage, scope, queue))

    assert len(runner.calls) == 1, "the check must go through the isolated runner"
    assert runner.calls[0]["serve"] is not None and runner.calls[0]["cancel_check"] is not None
    assert queue.updates[-1]["status"] == "done", queue.updates
    doc = json.loads(await storage.get_bytes(scope, job.derived_key))
    assert doc["counts"]["joints"] == 1
    assert audit_calls == [("done", None)]


@pytest.mark.asyncio
async def test_a_cancelled_group_check_uploads_nothing_and_says_cancelled(group_job, forked, audit_calls):
    from ada.comms.rest.formats.clash_check_group import ClashCheckGroupHandler

    storage, scope, job = group_job
    forked("CANCELLED")
    queue = _Queue()
    await ClashCheckGroupHandler().run(job, _ctx(storage, scope, queue))

    assert queue.updates[-1]["status"] == "cancelled", queue.updates
    assert queue.updates[-1]["stage"] == "cancelled"
    assert not await storage.exists(scope, job.derived_key)
    # The audit row is already 'cancelled' (the cancel endpoint set it): left alone, as convert does.
    assert audit_calls == []


@pytest.mark.asyncio
async def test_a_timed_out_group_check_errors_with_the_minutes(group_job, forked, audit_calls, monkeypatch):
    from ada.comms.rest.formats import clash_check_group

    async def _thirty_minutes(_pool):
        return 1800.0

    monkeypatch.setattr(clash_check_group, "read_clash_timeout_s", _thirty_minutes)
    storage, scope, job = group_job
    runner = forked("TIMEOUT")
    queue = _Queue()
    await clash_check_group.ClashCheckGroupHandler().run(job, _ctx(storage, scope, queue))

    assert runner.calls[0]["timeout_s"] == 1800.0
    last = queue.updates[-1]
    assert last["status"] == "error"
    assert "timed out after 30 min" in last["error"]
    assert audit_calls and audit_calls[-1][0] == "error" and "timed out after 30 min" in audit_calls[-1][1]
    assert not await storage.exists(scope, job.derived_key)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome, needle", [("OOM", "out of memory"), ("SIGSEGV", "SIGSEGV")])
async def test_a_killed_group_check_errors_naming_why(group_job, forked, audit_calls, outcome, needle):
    from ada.comms.rest.formats.clash_check_group import ClashCheckGroupHandler

    storage, scope, job = group_job
    forked(outcome)
    queue = _Queue()
    await ClashCheckGroupHandler().run(job, _ctx(storage, scope, queue))

    assert queue.updates[-1]["status"] == "error"
    assert needle in queue.updates[-1]["error"]
    assert audit_calls[-1][0] == "error"


@pytest.mark.asyncio
async def test_an_exception_in_the_child_is_recorded_in_its_own_words_at_its_stage(tmp_path, forked, audit_calls):
    """A group none of whose members resolve fails in the child with GroupModelError; the row says
    what the in-thread handler always said (str(exc)), at the stage the work had reached."""
    from obstore.store import LocalStore

    from ada.clash.group import normalise_group
    from ada.comms.rest.formats.clash_check_group import ClashCheckGroupHandler
    from ada.comms.rest.queue import Job
    from ada.comms.rest.scope import Scope
    from ada.comms.rest.storage import Storage

    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = Scope.shared()
    group = normalise_group(
        {"name": "g", "members": [{"target": {"kind": "file", "source_key": "nope.ifc"}, "element": None}]}
    )
    job = Job(
        job_id="job-x",
        source_key="group:t",
        derived_key="_derived/clash/group/t/o/result.json",
        status="queued",
        target_format="clash_check_group",
        conversion_options={"group": group, "group_token": "t", "options": OPTIONS},
    )
    forked("run")
    queue = _Queue()
    await ClashCheckGroupHandler().run(job, _ctx(storage, scope, queue))

    last = queue.updates[-1]
    assert last["status"] == "error" and last["stage"] == "clash"
    assert last["error"].startswith("none of the 1 member(s)"), last["error"]


@pytest.mark.asyncio
async def test_the_source_check_runs_isolated_without_a_storage_channel(tmp_path, forked, monkeypatch):
    """``clash_check`` has its source on disk already: the child gets no storage at all."""
    from ada.comms.rest.formats import clash_check
    from ada.comms.rest.queue import Job
    from ada.comms.rest.scope import Scope

    monkeypatch.setattr(clash_check, "_audit_done", _noop_audit)
    monkeypatch.setattr(clash_isolation, "_audit_done", _noop_audit)

    src = tmp_path / "m.ifc"
    src.write_bytes(_one_girder_ifc(tmp_path, "A", [("ga", (0, 0, 0), (2, 0, 0)), ("gb", (1, 0, 0), (1, 2, 0))]))
    put: dict = {}

    class _Storage:
        async def put_bytes(self, scope, key, data, content_encoding=None):
            put[key] = data

    job = Job(
        job_id="job-src",
        source_key="m.ifc",
        derived_key="_derived/clash/m/o/result.json",
        status="queued",
        target_format="clash_check",
        conversion_options={"source_key": "m.ifc", "options": OPTIONS},
    )
    runner = forked("run")
    queue = _Queue()
    await clash_check.ClashCheckHandler().run(job, _ctx(_Storage(), Scope.shared(), queue, src_path=src))

    assert runner.calls[0]["serve"] is None
    assert queue.updates[-1]["status"] == "done", queue.updates
    doc = json.loads(put[job.derived_key])
    assert doc["counts"]["joints"] == 1
    assert doc["source_sha256"]


@pytest.mark.asyncio
async def test_without_fork_a_cancel_during_the_thread_run_still_stops_the_upload(group_job, monkeypatch):
    """No fork (Windows / a dev box): the work runs in a thread that cannot be killed, but a
    cancellation that landed meanwhile still keeps the result from being published."""
    from ada.comms.rest import db as db_module
    from ada.comms.rest.formats.clash_check_group import ClashCheckGroupHandler

    monkeypatch.setattr(clash_isolation, "_fork_available", lambda: False)

    async def _cancelled(_pool, _job_id):
        return True

    monkeypatch.setattr(db_module, "audit_is_cancelled", _cancelled)

    async def _no_timeout(_pool):
        return None

    from ada.comms.rest.formats import clash_check_group

    monkeypatch.setattr(clash_check_group, "read_clash_timeout_s", _no_timeout)
    storage, scope, job = group_job
    queue = _Queue()
    await ClashCheckGroupHandler().run(job, _ctx(storage, scope, queue, db_pool=object()))

    assert queue.updates[-1]["status"] == "cancelled", queue.updates
    assert not await storage.exists(scope, job.derived_key)


@pytest.mark.asyncio
async def test_group_detail_runs_isolated_and_uploads_both_outputs(group_job, forked, audit_calls, monkeypatch):
    from ada.comms.rest.formats import clash_detail
    from ada.comms.rest.formats.clash_check_group import ClashCheckGroupHandler
    from ada.comms.rest.queue import Job

    monkeypatch.setattr(clash_detail, "_audit_done", _noop_audit)
    storage, scope, job = group_job
    forked("run")
    await ClashCheckGroupHandler().run(job, _ctx(storage, scope, _Queue()))
    doc = json.loads(await storage.get_bytes(scope, job.derived_key))

    detail = Job(
        job_id="job-detail",
        source_key="group:tok",
        derived_key="_derived/clash/detail/x/result.stats.json",
        status="queued",
        target_format="clash_detail_group",
        conversion_options={
            "result_key": job.derived_key,
            "joint_ids": [doc["joints"][0]["id"]],
            "spec": "builtin.girder_gusset",
            "options": {},
            "glb_key": "_derived/clash/detail/x/model.glb",
        },
    )
    runner = forked("run")
    queue = _Queue()
    await clash_detail.ClashDetailGroupHandler().run(detail, _ctx(storage, scope, queue))

    assert runner.calls[0]["serve"] is not None and runner.calls[0]["label"] == "clash detail"
    assert queue.updates[-1]["status"] == "done", queue.updates
    stats = json.loads(await storage.get_bytes(scope, detail.derived_key))
    assert stats["joints"]["count"] == 1
    assert await storage.exists(scope, "_derived/clash/detail/x/model.glb")


async def _noop_audit(*_a, **_k):
    return None
