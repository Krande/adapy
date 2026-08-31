"""``POST /api/plugins/{id}/jobs`` routes a sharded plugin to the right pool.

The unit tests beside this one pin the token and the union. This one drives the
actual route, because the thing that breaks in production is not the normaliser
— it is the wiring: which capability ends up on the enqueued job, given a live
worker's advertisement and a request body.
"""

from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-shard-storage-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.queue import Job, JobQueue  # noqa: E402

PLUGIN = "cad-export"


def _settings(tmp_path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        # Non-empty so `queue.enabled` is True and the endpoint takes the NATS
        # path rather than the in-process one, which ignores capability.
        queue=QueueConfig(
            url="nats://test-not-dialled",
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="kv",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


def _worker(worker_id: str, spec: dict) -> dict:
    return {
        "worker_id": worker_id,
        "last_heartbeat": time.time(),
        "capabilities": ["cad"],
        "plugin_specs": [spec],
    }


def _spec(**over) -> dict:
    base = {
        "slug": PLUGIN,
        "id": PLUGIN,
        "name": "CAD export",
        "worker_capability": "cad",
        "job_entrypoint": "example:run",
    }
    base.update(over)
    return base


def _build(monkeypatch, tmp_path, workers: list[dict]):
    """An app whose queue is faked: no NATS, but a real route."""
    enqueued: list[Job] = []

    async def fake_connect(self, **kwargs):
        return None

    async def fake_list_workers(self):
        return workers

    async def fake_enqueue(self, source_key, **kw):
        job = Job(
            job_id="job-1",
            source_key=source_key,
            derived_key=kw.get("derived_key") or "",
            status="queued",
            target_format=kw.get("target_format") or "glb",
            target_capability=kw.get("target_capability"),
        )
        enqueued.append(job)
        return job

    async def fake_publish(self, job):
        # The route enqueues with `publish=False` and publishes after the audit
        # row lands (the fix for the stranded-"queued" race), so the fake has to
        # cover both halves or the real publish reaches a NATS that is not there.
        return None

    monkeypatch.setattr(JobQueue, "publish", fake_publish)
    monkeypatch.setattr(JobQueue, "connect", fake_connect)
    monkeypatch.setattr(JobQueue, "list_workers", fake_list_workers)
    monkeypatch.setattr(JobQueue, "enqueue", fake_enqueue)
    return create_app(_settings(tmp_path)), enqueued


def _post(app, options: dict, **body):
    with TestClient(app) as client:
        return client.post(f"/api/plugins/{PLUGIN}/jobs", json={"options": options, **body})


# --- routing ---------------------------------------------------------------


def test_without_capability_option_the_bare_pool_is_used(monkeypatch, tmp_path):
    app, enqueued = _build(monkeypatch, tmp_path, [_worker("w1", _spec())])

    assert _post(app, {"project": "alpha"}).status_code == 200

    # The plugin never declared a sharding option, so `project` is just an
    # opaque option — core must not invent routing from it.
    assert enqueued[0].target_capability == "cad"


def test_a_declared_option_shards_the_pool(monkeypatch, tmp_path):
    app, enqueued = _build(monkeypatch, tmp_path, [_worker("w1", _spec(capability_option="project"))])

    assert _post(app, {"project": "alpha"}).status_code == 200

    assert enqueued[0].target_capability == "cad-alpha"


def test_the_shard_value_is_normalised_into_a_subject_token(monkeypatch, tmp_path):
    app, enqueued = _build(monkeypatch, tmp_path, [_worker("w1", _spec(capability_option="project"))])

    assert _post(app, {"project": "SITE A/2"}).status_code == 200

    # A dot or space would make a subject nothing filters on; the job would sit
    # in the stream looking merely slow.
    cap = enqueued[0].target_capability
    assert cap == "cad-site-a-2"
    assert "." not in cap and " " not in cap


@pytest.mark.parametrize("value", ["", "   ", None])
def test_an_empty_shard_value_falls_back_to_the_bare_pool(monkeypatch, tmp_path, value):
    # Falling back rather than erroring is what lets one worker subscribe to
    # both `cad` and `cad-alpha` and serve unqualified requests too.
    app, enqueued = _build(monkeypatch, tmp_path, [_worker("w1", _spec(capability_option="project"))])

    assert _post(app, {"project": value}).status_code == 200

    assert enqueued[0].target_capability == "cad"


def test_a_missing_option_falls_back_to_the_bare_pool(monkeypatch, tmp_path):
    app, enqueued = _build(monkeypatch, tmp_path, [_worker("w1", _spec(capability_option="project"))])

    assert _post(app, {}).status_code == 200

    assert enqueued[0].target_capability == "cad"


def test_an_explicit_capability_in_the_body_still_wins(monkeypatch, tmp_path):
    app, enqueued = _build(monkeypatch, tmp_path, [_worker("w1", _spec(capability_option="project"))])

    assert _post(app, {"project": "alpha"}, capability="Other Pool").status_code == 200

    # And it is normalised the same way, so an override cannot name a subject
    # the worker side could never produce.
    assert enqueued[0].target_capability == "other-pool"


# --- the union, through the route -----------------------------------------


def test_two_workers_on_one_plugin_no_longer_erase_each_other(monkeypatch, tmp_path):
    """The reason the union exists.

    Two workers, one plugin, different shards. Before the union, whichever
    worker's row came back last supplied the whole spec, so the viewer could
    only ever see one project as available.
    """
    spec_a = _spec(capability_option="project", union_fields=["projects"], projects=["alpha"])
    spec_b = _spec(capability_option="project", union_fields=["projects"], projects=["beta"])
    app, _ = _build(monkeypatch, tmp_path, [_worker("w1", spec_a), _worker("w2", spec_b)])

    with TestClient(app) as client:
        plugins = client.get("/api/plugins").json()["plugins"]

    entry = next(p for p in plugins if p["slug"] == PLUGIN)
    assert sorted(entry["projects"]) == ["alpha", "beta"]


def test_the_advertised_spec_is_stable_across_requests(monkeypatch, tmp_path):
    """Deterministic, not last-writer-wins.

    Two workers disagreeing on a scalar previously resolved by KV listing
    order, so the advertised version could differ between two consecutive
    requests for no visible reason.
    """
    workers = [
        _worker("w2", _spec(version="2.0.0")),
        _worker("w1", _spec(version="1.0.0")),
    ]
    app, _ = _build(monkeypatch, tmp_path, workers)

    with TestClient(app) as client:
        first = client.get("/api/plugins").json()["plugins"]
        # Same rows, opposite order: the answer must not move.
        workers.reverse()
        second = client.get("/api/plugins").json()["plugins"]

    def version_of(payload):
        return next(p for p in payload if p["slug"] == PLUGIN)["version"]

    assert version_of(first) == version_of(second) == "1.0.0"  # worker id w1 sorts first


# --- every producer converges on one subject --------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("weld_gen", "ada.viewer.jobs.convert.weld_gen"),
        ("Fem Solver", "ada.viewer.jobs.convert.fem-solver"),
        ("abaqus", "ada.viewer.jobs.convert.abaqus"),
        (None, "ada.viewer.jobs.convert.base"),
    ],
)
def test_publish_normalises_whatever_producer_set_the_capability(raw, expected):
    """`target_capability` is set from a dozen call sites and only one of them
    goes through the plugin route. The worker derives its subscription with
    `capability_token`, so `publish` has to as well or a job from any other
    producer lands on a subject nothing is listening to — silently."""
    import asyncio

    published: list[str] = []

    class _JS:
        async def publish(self, subject, payload):
            published.append(subject)

    q = JobQueue(
        QueueConfig(url="nats://x", stream="ada", subject="ada.viewer.jobs.convert", kv_bucket="kv", durable="d")
    )
    q._js = _JS()
    job = Job(job_id="j", source_key="s", derived_key="d", status="queued", target_capability=raw)

    asyncio.run(q.publish(job))

    assert published == [expected]
