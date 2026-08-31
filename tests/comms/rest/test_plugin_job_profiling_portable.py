"""The plugin-job profiling harness must not be POSIX-only.

The convert path does its resource accounting inside a forked child, and that
child only ever exists on POSIX — so `resource` being unavailable off Linux
costs nothing there. A plugin job is different: it runs IN-PROCESS in an
executor thread, so the harness runs wherever the worker runs. An unguarded
``import resource`` in it fails the JOB, not just the measurement, on a Windows
worker with ``profile_conversions`` on — which is precisely the configuration an
off-cluster capability worker runs in: a machine outside the cluster that joins
a capability pool because it holds something no pod can (a licensed CAD seat, a
device, a dataset), and which is far more likely to be Windows than any pod is.

So: a counter the platform cannot produce is omitted from the audit row, and the
job runs.
"""

import asyncio
import builtins
import pathlib
import re
import sys
import types

import pytest

import ada.plugins as plugins_mod
from ada.comms.rest import worker as worker_mod
from ada.comms.rest.queue import Job

# --- the reader ------------------------------------------------------------


def test_rusage_is_none_when_the_module_does_not_exist(monkeypatch):
    real_import = builtins.__import__

    def _no_resource(name, *args, **kwargs):
        if name == "resource":
            raise ModuleNotFoundError("No module named 'resource'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_resource)

    assert worker_mod._read_self_rusage() is None


def test_rusage_reads_real_counters_where_it_can():
    got = worker_mod._read_self_rusage()
    if got is None:
        pytest.skip("no resource module on this platform — the other tests cover that path")
    user, sys_, rss = got
    assert user >= 0.0 and sys_ >= 0.0
    assert rss >= 0


def test_resource_is_imported_in_exactly_one_guarded_place():
    """A regression guard with a specific failure in mind.

    The harness is easy to extend with another counter, and `import resource`
    at the top of a new block would reintroduce the crash without any test
    failing — the profiling path only runs when an admin toggles it on. So the
    invariant is stated directly: worker.py reaches for `resource` once, inside
    the helper that handles its absence.
    """
    src = pathlib.Path(worker_mod.__file__).read_text(encoding="utf-8")
    assert len(re.findall(r"^\s*import resource\b", src, re.MULTILINE)) == 1
    guarded = re.search(
        r"def _read_self_rusage\(.*?\n(.*?)\n\ndef ",
        src,
        re.DOTALL,
    )
    assert guarded and "import resource" in guarded.group(1)


# --- the harness -----------------------------------------------------------


class _FakeQueue:
    def __init__(self):
        self.updates: list[dict] = []

    async def update(self, job_id, **kw):
        self.updates.append({"job_id": job_id, **kw})


ENTRY_MODULE = "_test_portable_plugin"


def _install_plugin(monkeypatch, summary: dict) -> str:
    """Register a trivial entrypoint and make the worker resolve to it.

    ``plugin_backend_spec`` is imported INSIDE ``_run_plugin_job``, so it is
    patched on ``ada.plugins`` rather than on the worker module. The entrypoint
    lives in a synthetic module in ``sys.modules`` so ``import_module`` finds it
    without depending on how the test tree is laid out on disk.
    """
    mod = types.ModuleType(ENTRY_MODULE)

    def run(options, **kwargs):
        return summary

    mod.run = run
    monkeypatch.setitem(sys.modules, ENTRY_MODULE, mod)
    monkeypatch.setattr(
        plugins_mod,
        "plugin_backend_spec",
        lambda pid: {"job_entrypoint": f"{ENTRY_MODULE}:run"},
    )
    return f"{ENTRY_MODULE}:run"


@pytest.mark.asyncio
async def test_a_profiled_plugin_job_completes_without_rusage(monkeypatch, tmp_path):
    """The whole point: profiling on, `resource` absent, job still succeeds."""
    # Force the no-resource world regardless of the host platform, so this test
    # asserts the same thing on Linux CI as on a Windows worker.
    monkeypatch.setattr(worker_mod, "_read_self_rusage", lambda: None)

    audited: dict = {}

    async def _fake_audit_done(db_pool, job_id, status, error, started_at, **kw):
        audited["status"] = status
        audited["metrics"] = kw.get("metrics") or {}

    async def _fake_get_setting(pool, key):
        # profile_conversions on, no per-task filter => the harness runs.
        return "true" if key == "profile_conversions" else ""

    async def _never_cancelled(pool, job_id):
        return False

    monkeypatch.setattr(worker_mod, "_audit_done", _fake_audit_done)
    monkeypatch.setattr(worker_mod.db_module, "get_setting", _fake_get_setting)
    monkeypatch.setattr(worker_mod.db_module, "audit_is_cancelled", _never_cancelled)
    _install_plugin(monkeypatch, {"ok": True, "produced": "nothing"})

    stored: dict[str, bytes] = {}

    class _FakeStorage:
        async def put_bytes(self, scope, key, data, **kw):
            stored[key] = data

    queue = _FakeQueue()
    job = Job(
        job_id="job-1",
        source_key="synthetic/plugin",
        derived_key="_derived/summary.json.gz",
        status="running",
        target_format="plugin_job",
        conversion_options={"plugin_id": "demo", "options": {}},
    )

    await worker_mod._run_plugin_job(
        job=job,
        scope=object(),
        storage=_FakeStorage(),
        queue=queue,
        db_pool=object(),  # non-None so the profiling settings are read
        started_at=0.0,
    )

    assert audited["status"] == "done"
    # The summary was written; the job did its work.
    assert job.derived_key in stored


@pytest.mark.asyncio
async def test_cpu_counters_are_omitted_rather_than_zeroed(monkeypatch):
    """A zero would be indistinguishable from a job that burned no CPU.

    The audit panel divides summed CPU by summed duration to decide a task is
    "mostly waiting on IO"; a fabricated zero would make every Windows-worker
    run vote for that conclusion. An absent column does not vote.
    """
    monkeypatch.setattr(worker_mod, "_read_self_rusage", lambda: None)

    captured: dict = {}

    async def _fake_audit_done(db_pool, job_id, status, error, started_at, **kw):
        captured.update(kw.get("metrics") or {})

    async def _fake_get_setting(pool, key):
        return "true" if key == "profile_conversions" else ""

    async def _never_cancelled(pool, job_id):
        return False

    monkeypatch.setattr(worker_mod, "_audit_done", _fake_audit_done)
    monkeypatch.setattr(worker_mod.db_module, "get_setting", _fake_get_setting)
    monkeypatch.setattr(worker_mod.db_module, "audit_is_cancelled", _never_cancelled)
    _install_plugin(monkeypatch, {"ok": True})

    class _FakeStorage:
        async def put_bytes(self, scope, key, data, **kw):
            pass

    await worker_mod._run_plugin_job(
        job=Job(
            job_id="job-2",
            source_key="synthetic/plugin",
            derived_key="_derived/s.json.gz",
            status="running",
            target_format="plugin_job",
            conversion_options={"plugin_id": "demo", "options": {}},
        ),
        scope=object(),
        storage=_FakeStorage(),
        queue=_FakeQueue(),
        db_pool=object(),
        started_at=0.0,
    )

    assert "cpu_user_ms" not in captured
    assert "cpu_sys_ms" not in captured
    # The counters that ARE portable still land.
    assert "read_bytes" in captured and "write_bytes" in captured


def test_the_event_loop_is_not_required_for_the_reader():
    # Called from an executor thread in production, so it must not assume a
    # running loop.
    assert asyncio.get_event_loop_policy() is not None
    worker_mod._read_self_rusage()
