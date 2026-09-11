"""The JobTransport contract: one shape for "run a job", two implementations.

These pin the three things the abstraction exists to guarantee, and nothing
about how either implementation gets its work done:

* both transports really do implement the same Protocol -- not merely
  ``isinstance``, which for a Protocol is a name check, but the same members
  with the same signatures, so a route written against one cannot fall over
  on the other;
* the in-process transport runs a plugin job END TO END through the same
  ``submit(JobRequest)`` the queue path uses, with the same request built from
  the same route;
* a feature a transport does not have raises the 503 whose text clients and
  the older tests already depend on.

The message strings are the load-bearing part of the last one. They were
inline in a dozen routes before this and are now in one table; a table is only
an improvement if what comes out of it is what came out of the routes.
"""

from __future__ import annotations

import gzip
import inspect
import json
import os
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-jt-storage-"))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.job_transport import (  # noqa: E402
    FEATURE_UNAVAILABLE_DETAIL,
    LOCAL_FEATURES,
    JobRequest,
    JobTransport,
    LocalJobTransport,
    QueueJobTransport,
)
from ada.comms.rest.local_jobs import STATUS_DONE, STATUS_RUNNING  # noqa: E402
from ada.comms.rest.queue import Job, JobQueue  # noqa: E402
from ada.plugins import register_plugin_backend  # noqa: E402

PLUGIN = "adapy-test-transport"


def _settings(tmp_path, *, queue_url: str | None) -> Settings:
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


def _entrypoint(options, *, storage, scope, on_progress, derived_prefix, **kw):
    on_progress("working", 0.5)
    return {"echoed": options.get("n"), "ok": True}


# --------------------------------------------------------------------------
# Both transports are the same shape
# --------------------------------------------------------------------------

#: Every member a route may reach for. Listed rather than derived so that
#: DROPPING one from the Protocol is a test failure too, not a silently
#: smaller contract.
CONTRACT = (
    "supports",
    "unavailable",
    "require",
    "submit",
    "inprocess",
    "status",
    "cancel",
    "capabilities",
    "advertised_specs",
    "local_specs",
    "worker_image_tag",
)


@pytest.mark.parametrize("impl", [QueueJobTransport, LocalJobTransport])
def test_both_transports_satisfy_the_protocol(impl):
    transport = QueueJobTransport(None, {}) if impl is QueueJobTransport else LocalJobTransport(None)
    assert isinstance(transport, JobTransport)
    assert transport.kind in ("queue", "local")


@pytest.mark.parametrize("name", CONTRACT)
def test_the_two_implementations_agree_on_every_signature(name):
    """isinstance against a Protocol only checks that the names exist. A route
    calls these with keywords, so the parameters have to line up too."""
    assert name in dir(JobTransport)
    want = inspect.signature(getattr(JobTransport, name))
    for impl in (QueueJobTransport, LocalJobTransport):
        assert inspect.signature(getattr(impl, name)) == want, f"{impl.__name__}.{name}"


def test_every_feature_has_a_message_and_only_plugin_jobs_runs_locally():
    # The Literal and the table are two lists that must not drift: a feature
    # with no message raises KeyError out of `unavailable`, which reads as a
    # 500 rather than as the 503 it was meant to be.
    from typing import get_args

    from ada.comms.rest.job_transport import TransportFeature

    assert set(get_args(TransportFeature)) == set(FEATURE_UNAVAILABLE_DETAIL)
    assert all(FEATURE_UNAVAILABLE_DETAIL.values())
    assert LOCAL_FEATURES == frozenset({"plugin_jobs"})


# --------------------------------------------------------------------------
# An unsupported feature raises the 503 the routes used to raise
# --------------------------------------------------------------------------

#: The exact detail strings, transcribed from the routes as they stood before
#: the transport existed. Duplicated here ON PURPOSE: a test that read them
#: back out of FEATURE_UNAVAILABLE_DETAIL would pass for any rewrite of the
#: table, which is the one thing this is guarding against.
PREEXISTING_503 = {
    "bake": "bake disabled (no NATS configured)",
    "bbox_inference": "bbox inference disabled (no NATS configured)",
    "component_build": "component build disabled (no NATS configured)",
    "conversion": "conversion disabled (no NATS configured)",
    "job_status_report": "no job queue configured",
    "procedural_build": "procedural build disabled (no NATS configured)",
    "procedural_export": "procedural export disabled (no NATS configured)",
    "procedural_import": "procedural import disabled (no NATS configured)",
    "procedural_relocations": "procedural relocations disabled (no NATS configured)",
    "result_meta": "result-meta disabled (no NATS configured)",
    "utilities": "utilities disabled (no NATS configured)",
    "worker_registry": "worker registry requires a NATS-backed queue",
}


@pytest.mark.parametrize("feature,detail", sorted(PREEXISTING_503.items()))
def test_an_unsupported_feature_raises_the_503_the_route_used_to(feature, detail):
    transport = LocalJobTransport(None)
    assert not transport.supports(feature)
    with pytest.raises(HTTPException) as exc:
        transport.require(feature)
    assert exc.value.status_code == 503
    assert exc.value.detail == detail


def test_a_supported_feature_does_not_raise():
    assert LocalJobTransport(None).require("plugin_jobs") is None
    for feature in FEATURE_UNAVAILABLE_DETAIL:
        assert QueueJobTransport(None, {}).require(feature) is None


async def test_submitting_an_unsupported_job_kind_refuses_it_by_feature():
    """The gate and the submit are the same check, so a route that forgets the
    gate still gets the right 503 rather than an AttributeError on a queue that
    is not there."""
    from ada.comms.rest.scope import Scope

    with pytest.raises(HTTPException) as exc:
        await LocalJobTransport(None).submit(
            JobRequest(source_key="x.step", target_format="glb", scope=Scope.shared(), feature="conversion")
        )
    assert exc.value.status_code == 503
    assert exc.value.detail == "conversion disabled (no NATS configured)"


# --------------------------------------------------------------------------
# The app picks the right one, and the plugin job runs either way
# --------------------------------------------------------------------------


def test_the_app_builds_the_transport_its_deployment_calls_for(tmp_path):
    assert create_app(_settings(tmp_path, queue_url=None)).state.rest.jobs.kind == "local"
    assert create_app(_settings(tmp_path, queue_url="nats://not-dialled")).state.rest.jobs.kind == "queue"


def _capture(monkeypatch, impl):
    """Record the JobRequest a route hands the transport, and let it through."""
    seen: list[JobRequest] = []
    original = impl.submit

    async def _spy(self, req, **kw):
        seen.append(req)
        return await original(self, req, **kw)

    monkeypatch.setattr(impl, "submit", _spy)
    return seen


def test_a_plugin_job_runs_end_to_end_through_the_transport(tmp_path, monkeypatch):
    """No queue: POST, poll, and read the summary back from the derived key --
    every hop the plugin's own UI makes, driven through `submit`."""
    register_plugin_backend(PLUGIN, job_entrypoint=f"{__name__}:_entrypoint")
    seen = _capture(monkeypatch, LocalJobTransport)

    app = create_app(_settings(tmp_path, queue_url=None))
    with TestClient(app) as client:
        r = client.post(f"/api/plugins/{PLUGIN}/jobs", json={"options": {"n": 7}})
        assert r.status_code == 200, r.text
        job_id, derived_key = r.json()["job_id"], r.json()["derived_key"]

        status = None
        for _ in range(200):
            status = client.get(f"/api/convert/{job_id}").json()
            if status["status"] != STATUS_RUNNING:
                break
            time.sleep(0.05)
        assert status["status"] == STATUS_DONE, status

        blob = client.get(f"/api/scopes/shared/blobs/{derived_key}")
        assert blob.status_code == 200, blob.text
        raw = blob.content
        if blob.headers.get("content-encoding") != "gzip":
            raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        assert json.loads(raw) == {"echoed": 7, "ok": True}

    assert len(seen) == 1
    assert (seen[0].feature, seen[0].target_format, seen[0].plugin_id) == ("plugin_jobs", "plugin_job", PLUGIN)


def test_the_queue_path_is_handed_the_same_request(tmp_path, monkeypatch):
    """Same route, same body, a queue behind it: the JobRequest the transport
    receives must not depend on which transport it is. That equality is the
    whole claim -- "the plugin sees the same contract either way"."""
    register_plugin_backend(PLUGIN, job_entrypoint=f"{__name__}:_entrypoint")

    async def _fake_enqueue(self, source_key, target_format="glb", **kw):
        return Job(
            job_id="job-queued",
            source_key=source_key,
            derived_key=kw.get("derived_key") or "",
            status="queued",
            target_format=target_format,
        )

    monkeypatch.setattr(JobQueue, "enqueue", _fake_enqueue)
    monkeypatch.setattr(JobQueue, "publish", lambda self, job: _noop())
    monkeypatch.setattr(JobQueue, "connect", lambda self, **kw: _noop())
    monkeypatch.setattr(JobQueue, "list_workers", lambda self: _empty())
    monkeypatch.setattr(JobQueue, "close", lambda self: _noop())

    local_seen = _capture(monkeypatch, LocalJobTransport)
    queue_seen = _capture(monkeypatch, QueueJobTransport)

    body = {"options": {"n": 7}}
    with TestClient(create_app(_settings(tmp_path, queue_url=None))) as client:
        assert client.post(f"/api/plugins/{PLUGIN}/jobs", json=body).status_code == 200
    with TestClient(create_app(_settings(tmp_path, queue_url="nats://not-dialled"))) as client:
        assert client.post(f"/api/plugins/{PLUGIN}/jobs", json=body).status_code == 200

    assert len(local_seen) == 1 and len(queue_seen) == 1
    assert local_seen[0] == queue_seen[0]


async def _noop():
    return None


async def _empty():
    return []
