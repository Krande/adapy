"""The one queue in front of every job a queue-less server runs itself (``local_queue``).

FIFO under a cap, a position while waiting, cancel-while-queued never starts the job, a slot freed
however a job ends, limits that apply to the next job, the CPU budget handed to a job process as
its thread environment, and the settings route that sets them (stored without a database).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import local_jobs, local_queue
from ada.comms.rest.app import create_app
from ada.comms.rest.local_jobs import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_RUNNING,
    LocalJob,
)
from ada.comms.rest.local_queue import LocalJobQueue, limits_from, parse_cpu_budget

from .test_local_worker_jobs import (  # noqa: F401
    _settings,
    _start,
    _wait,
    fake_plugin,
    storage,
)


def _job(name: str) -> LocalJob:
    return LocalJob(job_id=name, plugin_id="kind", scope_kind="shared", scope_id=None, derived_key=f"_d/{name}")


def _run_in_queue(q: LocalJobQueue, job: LocalJob, started: list[str], gate: threading.Event, light=False):
    def _target():
        if not q.acquire(job, light=light):
            return
        try:
            started.append(job.job_id)
            gate.wait(10)
            job.status = STATUS_DONE
        finally:
            q.release(job)

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    return t


def _until(pred, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached")


@pytest.fixture
def restore_limits():
    before = local_queue.queue.limits
    yield
    local_queue.queue.configure(before)


# --------------------------------------------------------------------------
# The queue itself
# --------------------------------------------------------------------------


def test_jobs_start_in_order_and_never_beyond_the_cap():
    q = LocalJobQueue(limits_from(2))
    gates = {n: threading.Event() for n in "abcde"}
    jobs = {n: _job(n) for n in "abcde"}
    started: list[str] = []
    for n in "abcde":
        _run_in_queue(q, jobs[n], started, gates[n])
        time.sleep(0.05)  # arrival order
    _until(lambda: len(started) == 2)
    time.sleep(0.3)
    assert started == ["a", "b"]
    assert jobs["c"].stage == "queued (next)"
    assert jobs["d"].stage == "queued (1 ahead)"
    assert jobs["e"].stage == "queued (2 ahead)"
    gates["b"].set()
    _until(lambda: len(started) == 3)
    assert started == ["a", "b", "c"]
    assert jobs["e"].stage == "queued (1 ahead)"
    for g in gates.values():
        g.set()
    _until(lambda: len(started) == 5)
    assert started == ["a", "b", "c", "d", "e"]


def test_a_job_cancelled_while_queued_never_starts():
    q = LocalJobQueue(limits_from(1))
    gate = threading.Event()
    started: list[str] = []
    first, second = _job("first"), _job("second")
    _run_in_queue(q, first, started, gate)
    _until(lambda: started == ["first"])
    waiter = _run_in_queue(q, second, started, gate)
    _until(lambda: second.stage.startswith("queued"))
    second.cancel_event.set()
    q.wake()
    waiter.join(5)
    assert second.status == STATUS_CANCELLED and second.stage == "cancelled"
    gate.set()
    time.sleep(0.3)
    assert started == ["first"]
    assert q.snapshot() == {"running": [], "queued": []}


def test_a_new_cap_applies_to_the_next_job():
    q = LocalJobQueue(limits_from(1))
    gate = threading.Event()
    started: list[str] = []
    a, b = _job("a"), _job("b")
    _run_in_queue(q, a, started, gate)
    _run_in_queue(q, b, started, gate)
    _until(lambda: started == ["a"])
    time.sleep(0.3)
    assert started == ["a"]
    q.configure(limits_from(2))
    _until(lambda: started == ["a", "b"])
    gate.set()


def test_a_light_job_does_not_wait_behind_heavy_ones():
    q = LocalJobQueue(limits_from(1))
    gate = threading.Event()
    started: list[str] = []
    heavy, queued_heavy, light = _job("heavy"), _job("queued-heavy"), _job("light")
    _run_in_queue(q, heavy, started, gate)
    _until(lambda: started == ["heavy"])
    _run_in_queue(q, queued_heavy, started, gate)
    _run_in_queue(q, light, started, threading.Event(), light=True)
    _until(lambda: "light" in started)
    assert "queued-heavy" not in started
    gate.set()


def test_the_cpu_budget_is_divided_over_the_running_jobs(monkeypatch):
    monkeypatch.setattr(local_queue, "host_cores", lambda: 8)
    q = LocalJobQueue(limits_from(4, "4"))
    gate = threading.Event()
    started: list[str] = []
    a, b = _job("a"), _job("b")
    _run_in_queue(q, a, started, gate)
    _until(lambda: started == ["a"])
    _run_in_queue(q, b, started, gate)
    _until(lambda: started == ["a", "b"])
    assert a.threads == 4 and b.threads == 2
    gate.set()


@pytest.mark.parametrize(
    "raw,cores,expect",
    [(None, 8, None), ("", 8, None), ("50%", 8, 4.0), ("0.25", 8, 2.0), ("3", 8, 3.0), ("64", 8, 8.0)],
)
def test_a_cpu_budget_reads_as_cores_a_fraction_or_a_percentage(raw, cores, expect):
    assert parse_cpu_budget(raw, cores) == expect


@pytest.mark.parametrize("raw", ["0", "-1", "many", "150%"])
def test_a_malformed_cpu_budget_is_refused(raw):
    with pytest.raises(ValueError):
        parse_cpu_budget(raw, 8)


# --------------------------------------------------------------------------
# Job processes behind the queue
# --------------------------------------------------------------------------


pytestmark_posix = pytest.mark.skipif(sys.platform == "win32", reason="the crash case raises SIGABRT")


@pytestmark_posix
def test_a_crashed_job_frees_its_slot(fake_plugin, storage, restore_limits):  # noqa: F811
    local_queue.queue.configure(limits_from(1))
    crash = _start("fake_crash", storage)
    ok = _start("fake_ok", storage)
    assert _wait(crash).status == local_jobs.STATUS_ERROR
    assert _wait(ok).status == STATUS_DONE


def test_a_job_process_gets_its_share_of_the_cpu_budget(fake_plugin, storage, tmp_path, restore_limits):  # noqa: F811
    local_queue.queue.configure(limits_from(2, "2"))
    job = _wait(_start("fake_env", storage))
    assert job.status == STATUS_DONE, job.as_json()
    doc = json.loads((tmp_path / "store" / "shared" / "_derived" / "fake_env.out").read_bytes())
    share = str(min(2, local_queue.host_cores()))  # the budget is capped at the machine
    assert doc["OMP_NUM_THREADS"] == share
    assert doc["OPENBLAS_NUM_THREADS"] == share
    assert doc["MKL_NUM_THREADS"] == share
    assert doc["ADA_STEP_STREAM_WORKERS"] == share
    if sys.platform.startswith("linux"):
        assert len(doc["affinity"]) == int(share)


def test_a_thread_job_waits_its_turn_too(restore_limits):
    """The thread kinds go through the same queue: a plugin job behind a running one is queued."""
    local_queue.queue.configure(limits_from(1))
    gate = threading.Event()
    blocker = _job("blocker")
    started: list[str] = []
    _run_in_queue(local_queue.queue, blocker, started, gate)
    _until(lambda: started == ["blocker"])
    ran = threading.Event()
    job = _job("thread-kind")
    local_jobs._start_queued(job, ran.set, "test-thread-kind")
    _until(lambda: job.stage.startswith("queued"))
    assert not ran.is_set()
    gate.set()
    assert ran.wait(5)
    assert job.status == STATUS_RUNNING  # the target owns the verdict; the queue only admits


# --------------------------------------------------------------------------
# The settings route
# --------------------------------------------------------------------------


def test_the_limits_are_settings_stored_without_a_database(tmp_path, monkeypatch, restore_limits):
    monkeypatch.delenv("ADA_LOCAL_JOB_PROCESSES", raising=False)
    monkeypatch.delenv("ADA_LOCAL_JOB_CPU_BUDGET", raising=False)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/config").json()["localJobs"] is True
        r = client.put("/api/local/jobs/settings", json={"max_parallel": 3, "cpu_budget": "50%"})
        assert r.status_code == 200, r.text
        assert r.json()["max_parallel"] == 3 and r.json()["cpu_budget"] == "50%"
        assert local_queue.queue.limits.max_parallel == 3
        assert client.put("/api/local/jobs/settings", json={"max_parallel": 0}).status_code == 400
        assert client.put("/api/local/jobs/settings", json={"cpu_budget": "lots"}).status_code == 400
        assert client.get("/api/local/jobs").json()["max_parallel"] == 3
    stored = json.loads((tmp_path / "shared" / "_prefs" / "_server" / "local_jobs.json").read_text())
    assert stored == {"max_parallel": 3, "cpu_budget": "50%"}

    # A restart keeps them...
    local_queue.queue.configure(limits_from(1))
    with TestClient(create_app(_settings(tmp_path))):
        assert local_queue.queue.limits.max_parallel == 3
    # ...and a launch value wins for its launch, and is saved.
    monkeypatch.setenv("ADA_LOCAL_JOB_PROCESSES", "5")
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert local_queue.queue.limits.max_parallel == 5
        assert client.get("/api/local/jobs").json()["cpu_budget"] == "50%"
    stored = json.loads((tmp_path / "shared" / "_prefs" / "_server" / "local_jobs.json").read_text())
    assert stored["max_parallel"] == 5


def test_the_settings_file_is_not_served_by_the_blob_routes(tmp_path, restore_limits):
    with TestClient(create_app(_settings(tmp_path))) as client:
        client.put("/api/local/jobs/settings", json={"max_parallel": 2})
        r = client.get("/api/scopes/shared/blobs/_prefs/_server/local_jobs.json")
        assert r.status_code in (403, 404)
