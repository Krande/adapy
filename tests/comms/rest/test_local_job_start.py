"""A local job process gets its job started, or says why it did not.

The contract pinned here (``local_runner`` / ``local_jobs._supervise``):

* what a job process starts -- a plugin's ``git`` call while it registers, a mesher, a solver --
  does not inherit the server's pipe as its stdin: it reads end-of-file at once. Inheriting it
  held a job before its first update (on Windows anything touching that stdin waits behind the
  runner's pending read), and the job read "starting" for as long as the server ran;
* a job process that is up but never reaches the pipeline (a plugin blocked at import) is stopped
  after ``ADA_LOCAL_JOB_START_TIMEOUT_S`` and the job is an error that says how far it got, with
  the process's last output. Its boot line alone does not count as the job starting.

Unlike ``test_local_worker_jobs`` these run on Windows too: that is where the stdin case bites.
"""

from __future__ import annotations

import os
import tempfile
import textwrap
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest

from ada.comms.rest import local_jobs
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage

#: Set in the job process only: the preloaded module then blocks at import, as a plugin stuck
#: in its registration does.
HANG_ENV = "ADAPY_TEST_FAKE_PLUGIN_HANGS"

FAKE_HANDLERS = textwrap.dedent(
    f"""
    import os
    import subprocess
    import sys
    import time

    from ada.comms.rest.formats import register
    from ada.comms.rest.formats.registry import SyntheticFormatHandler

    if os.environ.get({HANG_ENV!r}):
        print("fake plugin: blocked at import", file=sys.stderr, flush=True)
        time.sleep(600)


    class _ChildStdin(SyntheticFormatHandler):
        kind = "fake_child_stdin"

        async def run(self, job, ctx):
            # A child that does not name its stdin, the way a plugin calls git: it must get one
            # that ends, not the server's pipe.
            done = subprocess.run(
                [sys.executable, "-c", "import sys; sys.stdout.write(repr(sys.stdin.read()))"],
                stdout=subprocess.PIPE,
                timeout=30,
            )
            await ctx.storage.put_bytes(ctx.scope, job.derived_key, done.stdout)
            await ctx.queue.update(job.job_id, status="done", stage="ready", progress=1.0, error=None)


    register(_ChildStdin())
    """
)


@pytest.fixture
def fake_plugin(tmp_path, monkeypatch):
    mod_dir = tmp_path / "plugins"
    mod_dir.mkdir()
    (mod_dir / "adapy_test_fake_start_handlers.py").write_text(FAKE_HANDLERS, encoding="utf-8")
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setenv("ADA_WORKER_PRELOAD", "adapy_test_fake_start_handlers")
    monkeypatch.delenv(HANG_ENV, raising=False)
    import adapy_test_fake_start_handlers  # noqa: F401 -- the server registers it too, as the API's lifespan does

    yield


@pytest.fixture
def storage(tmp_path) -> Storage:
    root = tmp_path / "store"
    root.mkdir()
    return Storage.from_spec({"storage_kind": "local", "s3": None, "local": {"path": str(root), "prefix": ""}})


def _start(kind: str, storage: Storage) -> local_jobs.LocalJob:
    return local_jobs.start_worker_job(
        target_format=kind,
        source_key=f"_synthetic/{kind}",
        derived_key=f"_derived/{kind}.out",
        storage=storage,
        scope=Scope.shared(),
    )


def _wait(job: local_jobs.LocalJob, timeout: float = 120.0, until=None) -> local_jobs.LocalJob:
    until = until or (lambda j: j.status != local_jobs.STATUS_RUNNING)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if until(job):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job.job_id} never got there: {job.as_json()}")


def _gone(pid: int, timeout: float = 15.0) -> bool:
    import psutil

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.05)
    return False


def test_a_child_of_the_job_reads_end_of_file_not_the_servers_pipe(fake_plugin, storage, tmp_path):
    job = _wait(_start("fake_child_stdin", storage))
    assert job.status == local_jobs.STATUS_DONE, job.as_json()
    written = (tmp_path / "store" / "shared" / "_derived" / "fake_child_stdin.out").read_bytes()
    assert written.decode() == "''"


def test_a_job_process_stuck_before_the_pipeline_is_stopped_as_an_error(fake_plugin, storage, monkeypatch):
    pytest.importorskip("psutil")
    monkeypatch.setenv(HANG_ENV, "1")
    monkeypatch.setenv(local_jobs.ENV_START_TIMEOUT, "3")
    job = _start("fake_child_stdin", storage)
    # Up and loading plugins: the boot line is relayed, but it is not the job starting.
    _wait(job, timeout=60, until=lambda j: j.stage == "loading plugins" or j.status != local_jobs.STATUS_RUNNING)
    job = _wait(job, timeout=60)
    assert job.status == local_jobs.STATUS_ERROR, job.as_json()
    assert "did not start the job within 3s" in (job.error or "")
    assert "'loading plugins'" in (job.error or "")
    assert "fake plugin: blocked at import" in (job.error or "")
    assert job.pid is not None and _gone(job.pid)


@pytest.mark.parametrize(("raw", "expected"), [(None, 300.0), ("", 300.0), ("x", 300.0), ("-1", 300.0), ("45", 45.0)])
def test_the_start_timeout_comes_from_the_environment_or_the_default(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(local_jobs.ENV_START_TIMEOUT, raising=False)
    else:
        monkeypatch.setenv(local_jobs.ENV_START_TIMEOUT, raw)
    assert local_jobs._start_timeout() == expected
