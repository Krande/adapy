"""Hardening contract for the in-process plugin-job path (`local_jobs`).

When no queue is configured the API runs a plugin's backend job itself, in a
thread, instead of answering 503. That path is the one place where the API
imports and executes plugin code, so its failure modes are the API's failure
modes — and these tests pin the three that are not the plugin's own fault:

* a plugin backend that will not import must not take the API down with it,
  neither at startup (``ADA_WORKER_PRELOAD``) nor at request time
  (``job_entrypoint``);
* a job that never finishes must not hold its registry entry forever, because
  a never-evictable entry defeats the whole cap;
* a job that finishes must leave its summary where the caller was told to look
  for it, which is the ``derived_key`` it got back from the POST.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import tempfile
import threading
import time

# Importing ada.comms.rest.app evaluates a module-level `create_app()` which
# materializes a local Storage. Point it at a temp dir so the import succeeds in
# environments without `./viewer-data`.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import local_jobs
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.local_jobs import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_RUNNING,
    LocalJob,
    LocalJobRegistry,
)
from ada.plugins import register_plugin_backend


def _settings(tmp_path) -> Settings:
    """No queue URL — which is exactly the condition that routes a plugin job
    into `local_jobs` instead of onto NATS."""
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
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="",
        ),
        database_url="",
    )


def _job(job_id: str, status: str = STATUS_RUNNING, age_s: float = 0.0) -> LocalJob:
    job = LocalJob(
        job_id=job_id,
        plugin_id="test-plugin",
        scope_kind="shared",
        scope_id=None,
        derived_key=f"_derived/{job_id}.json",
        status=status,
    )
    job.started_at = time.time() - age_s
    return job


# --------------------------------------------------------------------------
# A bad plugin must not be able to stop the API from starting
# --------------------------------------------------------------------------


def test_an_unimportable_preload_module_does_not_stop_the_api(monkeypatch, tmp_path):
    """The worker dies on a failed preload on purpose — it exists because of it.

    The API does not: plugin jobs are one endpoint out of dozens, so an
    ImportError escaping the lifespan would trade "one capability is missing"
    for "the viewer is down", which is the worse of the two by a distance.
    """
    mod_dir = tmp_path / "badmods"
    mod_dir.mkdir()
    (mod_dir / "adapy_test_boom_plugin.py").write_text(
        "raise ImportError('a dependency this plugin needs is not installed')",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setenv("ADA_WORKER_PRELOAD", "adapy_test_boom_plugin")
    monkeypatch.delitem(sys.modules, "adapy_test_boom_plugin", raising=False)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        # Startup completed, and the rest of the API is untouched by the failure.
        assert client.get("/api/config").status_code == 200


# --------------------------------------------------------------------------
# A plugin whose entrypoint will not resolve answers, rather than 500-ing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plugin_id,entrypoint",
    [
        # Registered, but the module is not there at all.
        ("adapy-test-badmod", "adapy_test_missing_module:run"),
        # Module imports; the advertised callable is not in it.
        ("adapy-test-badattr", "json:no_such_callable"),
        # Not in "module:callable" form, so there is nothing to getattr.
        ("adapy-test-nocolon", "json"),
    ],
)
def test_an_unresolvable_entrypoint_is_a_501_not_a_500(tmp_path, plugin_id, entrypoint):
    """501 is the same answer an unregistered plugin gets, and for the same
    reason: this process cannot run that plugin. Letting the ImportError or
    AttributeError out instead produced a bare 500 naming neither the plugin
    nor the entrypoint — an unhandled exception, dressed as a server fault."""
    register_plugin_backend(plugin_id, job_entrypoint=entrypoint)
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(f"/api/plugins/{plugin_id}/jobs", json={"options": {}})
    assert r.status_code == 501, r.text
    detail = r.json()["detail"]
    assert plugin_id in detail and entrypoint in detail


def test_the_501_detail_does_not_echo_the_import_error_text(tmp_path):
    """The exception TEXT is the part that quotes module paths and filesystem
    layout back at whoever called the endpoint. The type is enough for the
    caller; the traceback belongs in the log."""
    register_plugin_backend("adapy-test-secretive", job_entrypoint="adapy_test_missing_module:run")
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post("/api/plugins/adapy-test-secretive/jobs", json={"options": {}})
    assert r.status_code == 501
    detail = r.json()["detail"]
    assert "ModuleNotFoundError" in detail
    assert "No module named" not in detail


# --------------------------------------------------------------------------
# The registry stays bounded even when nothing terminates
# --------------------------------------------------------------------------


def test_finished_jobs_are_evicted_oldest_first():
    reg = LocalJobRegistry()
    for i in range(reg.MAX_JOBS + 50):
        reg.add(_job(f"j{i}", status=STATUS_DONE))
    assert len(reg._jobs) == reg.MAX_JOBS
    assert reg.get("j0") is None
    assert reg.get(f"j{reg.MAX_JOBS + 49}") is not None


def test_a_job_that_never_finishes_is_declared_stuck_and_becomes_evictable():
    """The count cap only evicts terminal jobs, so before the age sweep a supply
    of jobs that never finish grew the registry without limit — the cap looked
    like a bound and was not one."""
    reg = LocalJobRegistry()
    for i in range(reg.MAX_JOBS + 50):
        reg.add(_job(f"stuck{i}", status=STATUS_RUNNING, age_s=reg.MAX_RUNTIME_S + 60))
    assert len(reg._jobs) == reg.MAX_JOBS


def test_a_stuck_job_reports_a_terminal_status_to_its_poller():
    """A poller has no other way out: the thread cannot be killed, so if the job
    never goes terminal the caller polls "running" until the process dies."""
    reg = LocalJobRegistry()
    job = _job("stuck", status=STATUS_RUNNING, age_s=reg.MAX_RUNTIME_S + 1)
    reg.add(job)

    seen = reg.get("stuck")
    assert seen is not None
    assert seen.status == STATUS_ERROR
    assert seen.as_json()["stage"] == "timeout"
    # ...and the cooperative signal is raised, so a plugin that watches it stops
    # burning CPU rather than running on unobserved.
    assert job.cancel_event.is_set()


def test_a_running_job_inside_the_limit_is_left_alone():
    reg = LocalJobRegistry()
    reg.add(_job("young", status=STATUS_RUNNING, age_s=5.0))
    for i in range(reg.MAX_JOBS + 10):
        reg.add(_job(f"done{i}", status=STATUS_DONE))
    still = reg.get("young")
    assert still is not None and still.status == STATUS_RUNNING


# --------------------------------------------------------------------------
# A finished job leaves its summary where the caller was told to look
# --------------------------------------------------------------------------


def _entrypoint(options, *, storage, scope, on_progress, derived_prefix, **_kw):
    on_progress("working", 0.5)
    return {"echoed": options.get("n"), "ok": True}


def test_a_completed_job_writes_its_summary_to_derived_key(tmp_path):
    """The POST hands back `derived_key` and the plugin's UI fetches the JSON
    from it once the job reports done — the same contract the worker fulfils by
    uploading the returned dict. In-process the return value used to stay in a
    field nothing serves, so the job succeeded and its answer was unreachable."""
    register_plugin_backend("adapy-test-echo", job_entrypoint=f"{__name__}:_entrypoint")
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        r = client.post("/api/plugins/adapy-test-echo/jobs", json={"options": {"n": 7}})
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        derived_key = r.json()["derived_key"]

        status = None
        for _ in range(200):
            status = client.get(f"/api/convert/{job_id}").json()
            if status["status"] != STATUS_RUNNING:
                break
            time.sleep(0.05)
        assert status["status"] == STATUS_DONE, status

        job = local_jobs.registry.get(job_id)
        assert job is not None and job.result == {"echoed": 7, "ok": True}

        # Fetched the way the plugin's UI fetches it, so the test pins the whole
        # round trip rather than the storage layout underneath it.
        blob_resp = client.get(f"/api/scopes/shared/blobs/{derived_key}")
        assert blob_resp.status_code == 200, blob_resp.text
        blob = blob_resp.content

    # httpx already unwraps Content-Encoding: gzip; be tolerant if it did not.
    if blob[:2] == b"\x1f\x8b":
        blob = gzip.decompress(blob)
    assert json.loads(blob) == {"echoed": 7, "ok": True}


def _slow_entrypoint(options, *, storage, scope, on_progress, derived_prefix, cancel_event, **_kw):
    _slow_entrypoint.started.set()
    _slow_entrypoint.release.wait(30)
    return {"late": True}


_slow_entrypoint.started = threading.Event()
_slow_entrypoint.release = threading.Event()


def test_a_job_swept_mid_run_is_not_resurrected_when_it_finally_returns(tmp_path, monkeypatch):
    """The sweep's verdict is one a poller may already have read. A late return
    from the abandoned call must not walk that back to `done` and hand out a
    result the caller was told did not exist."""
    monkeypatch.setattr(LocalJobRegistry, "MAX_RUNTIME_S", 0.5)
    register_plugin_backend("adapy-test-slow", job_entrypoint=f"{__name__}:_slow_entrypoint")
    _slow_entrypoint.started.clear()
    _slow_entrypoint.release.clear()

    app = create_app(_settings(tmp_path))
    try:
        with TestClient(app) as client:
            r = client.post("/api/plugins/adapy-test-slow/jobs", json={"options": {}})
            assert r.status_code == 200, r.text
            job_id = r.json()["job_id"]
            assert _slow_entrypoint.started.wait(10)

            time.sleep(0.7)  # past the patched MAX_RUNTIME_S
            swept = client.get(f"/api/convert/{job_id}").json()
            assert swept["status"] == STATUS_ERROR and swept["stage"] == "timeout"

            _slow_entrypoint.release.set()
            after = swept
            for _ in range(200):
                after = client.get(f"/api/convert/{job_id}").json()
                if after["status"] != STATUS_ERROR:
                    break
                time.sleep(0.05)
            assert after["status"] == STATUS_ERROR and after["stage"] == "timeout"
    finally:
        _slow_entrypoint.release.set()
