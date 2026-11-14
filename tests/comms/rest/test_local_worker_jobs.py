"""A queue-less server runs a worker's job kinds in a child process of its own environment.

The contract pinned here (``local_jobs.start_worker_job`` / ``local_runner``):

* the job runs through the worker's pipeline in ANOTHER process, which sees the plugins the
  server preloaded (``ADA_WORKER_PRELOAD``) and the server's ``sys.path``;
* its ``queue.update`` calls reach the job the polling route reads (progress, stage, verdict);
* a handler that raises is a job error with the handler's message;
* a process that crashes (a native fault) is a job error naming the signal, with the tail of its
  output -- and the server is still there to say so;
* cancel terminates the process;
* and end to end: a convert request on a server with no queue converts the file.
"""

from __future__ import annotations

import gzip
import os
import sys
import tempfile
import textwrap
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import local_jobs
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.job_transport import JobRequest, LocalJobTransport
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the crash case raises SIGABRT")

#: A plugin module the job process preloads: four synthetic job kinds, one per outcome.
FAKE_HANDLERS = textwrap.dedent(
    """
    import asyncio
    import os
    import sys

    from ada.comms.rest.formats import register
    from ada.comms.rest.formats.registry import SyntheticFormatHandler


    class _Ok(SyntheticFormatHandler):
        kind = "fake_ok"

        async def run(self, job, ctx):
            await ctx.queue.update(job.job_id, stage="halfway", progress=0.5)
            # Proof of which process ran it: the pid, next to the result.
            await ctx.storage.put_bytes(ctx.scope, job.derived_key, f"pid={os.getpid()}".encode())
            await ctx.queue.update(job.job_id, status="done", stage="ready", progress=1.0, error=None)


    class _Fail(SyntheticFormatHandler):
        kind = "fake_fail"

        async def run(self, job, ctx):
            raise ValueError("the deck has no elements")


    class _Crash(SyntheticFormatHandler):
        kind = "fake_crash"

        async def run(self, job, ctx):
            print("last words before the fault", file=sys.stderr, flush=True)
            os.abort()


    class _Slow(SyntheticFormatHandler):
        kind = "fake_slow"

        async def run(self, job, ctx):
            await ctx.queue.update(job.job_id, stage="spinning", progress=0.3)
            await asyncio.sleep(600)


    for h in (_Ok(), _Fail(), _Crash(), _Slow()):
        register(h)
    """
)


@pytest.fixture
def fake_plugin(tmp_path, monkeypatch):
    mod_dir = tmp_path / "plugins"
    mod_dir.mkdir()
    (mod_dir / "adapy_test_fake_handlers.py").write_text(FAKE_HANDLERS, encoding="utf-8")
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setenv("ADA_WORKER_PRELOAD", "adapy_test_fake_handlers")
    import adapy_test_fake_handlers  # noqa: F401 -- the server registers it too, as the API's lifespan does

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


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); it has exited all the same.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


def test_a_job_runs_in_another_process_and_reports_back(fake_plugin, storage, tmp_path):
    job = _wait(_start("fake_ok", storage))
    assert job.status == local_jobs.STATUS_DONE, job.as_json()
    assert job.stage == "ready" and job.progress == 1.0
    written = (tmp_path / "store" / "shared" / "_derived" / "fake_ok.out").read_bytes()
    pid = int(written.decode().split("=")[1])
    assert pid != os.getpid()
    assert pid == job.pid


def test_progress_reaches_the_job_while_it_runs_and_cancel_ends_the_process(fake_plugin, storage):
    job = _start("fake_slow", storage)
    _wait(job, until=lambda j: j.stage == "spinning")
    assert job.status == local_jobs.STATUS_RUNNING
    assert job.progress == pytest.approx(0.3)
    assert local_jobs.registry.cancel(job.job_id)
    _wait(job, timeout=30)
    assert job.status == local_jobs.STATUS_CANCELLED
    deadline = time.monotonic() + 10
    while _alive(job.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(job.pid)


def test_a_handler_that_raises_is_a_job_error_with_its_message(fake_plugin, storage):
    job = _wait(_start("fake_fail", storage))
    assert job.status == local_jobs.STATUS_ERROR
    assert "ValueError: the deck has no elements" in (job.error or "")


def test_a_crashed_process_is_a_job_error_with_its_last_output(fake_plugin, storage):
    job = _wait(_start("fake_crash", storage))
    assert job.status == local_jobs.STATUS_ERROR
    assert "SIGABRT" in (job.error or "")
    assert "last words before the fault" in (job.error or "")


def test_a_store_no_process_can_open_is_refused_before_anything_starts(fake_plugin):
    class _Opaque:
        def process_spec(self):
            return None

    with pytest.raises(LookupError, match="cannot be opened by a job process"):
        _start("fake_ok", _Opaque())


# --------------------------------------------------------------------------
# Through the transport and the routes
# --------------------------------------------------------------------------


def _settings(tmp_path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=None, stream="ada", subject="ada.viewer.jobs.convert", kv_bucket="ada-viewer-jobs", durable="w"
        ),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


async def test_a_conversion_this_process_cannot_do_is_a_501(monkeypatch, storage):
    """The deployment converts; this environment lacks the converter -- 501, naming the pair."""
    from ada.comms.rest import job_transport

    monkeypatch.setattr(
        job_transport, "local_advertisement", lambda: {"source_exts": [], "conversions": [], "utilities": []}
    )
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await LocalJobTransport(storage).submit(
            JobRequest(source_key="m/a.step", target_format="glb", scope=Scope.shared(), feature="conversion")
        )
    assert exc.value.status_code == 501
    assert ".step" in exc.value.detail and "glb" in exc.value.detail


#: A unit cube, as the smallest model every reader in the tests env can open.
CUBE_OBJ = b"""v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
f 1 2 3 4
f 5 6 7 8
f 1 2 6 5
f 2 3 7 6
f 3 4 8 7
f 4 1 5 8
"""


def test_a_queue_less_server_converts_a_file_itself(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        config = client.get("/api/config").json()
        assert config["convertEnabled"] is True
        offered = {row["from"]: row["to"] for row in config["conversionMatrix"]}
        assert "glb" in offered[".obj"]

        src = "models/cube.obj"
        (tmp_path / "shared" / "models").mkdir(parents=True)
        (tmp_path / "shared" / src).write_bytes(CUBE_OBJ)

        r = client.post("/api/scopes/shared/convert", json={"source_key": src, "target_format": "glb"})
        assert r.status_code == 202, r.text
        started = r.json()
        assert started["job_id"].startswith("local-")

        deadline = time.monotonic() + 180
        status: dict = {}
        while time.monotonic() < deadline:
            status = client.get(f"/api/convert/{started['job_id']}").json()
            if status["status"] != "running":
                break
            time.sleep(0.2)
        assert status["status"] == "done", status

        blob = (tmp_path / "shared" / started["derived_key"]).read_bytes()
        if blob[:2] == b"\x1f\x8b":
            blob = gzip.decompress(blob)
        assert blob[:4] == b"glTF"

        # The second ask is the cached answer, as behind a queue.
        again = client.post("/api/scopes/shared/convert", json={"source_key": src, "target_format": "glb"})
        assert again.status_code == 200 and again.json()["cached"] is True
