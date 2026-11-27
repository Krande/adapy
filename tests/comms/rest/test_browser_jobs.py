"""Core jobs the browser runs (``ada.comms.rest.browser_jobs``): the claim, its outputs, its end.

A route's job submitted with ``X-Ada-Run-On: browser`` is kept as a claim and published to no pool;
the route answers without a job id; the browser takes it, stores its outputs where the route said
they go, and completes it -- or hands the same job record to the worker. Run over HTTP against an
app whose transport is a queue (an in-memory one), with a real clash result made by core.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import tempfile
import time
import uuid

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-browser-jobs-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import browser_jobs as bj  # noqa: E402
from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.job_transport import JobRequest, QueueJobTransport  # noqa: E402
from ada.comms.rest.queue import Job  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402

from .test_worker_access import _settings  # noqa: E402


class _MemQueue:
    """The part of ``JobQueue`` the transport and these routes use, in memory."""

    enabled = True

    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.published: list[str] = []

    async def enqueue(self, source_key, target_format="glb", *, derived_key=None, publish=True, run_on=None, **kw):
        now = time.time()
        job = Job(
            job_id=uuid.uuid4().hex,
            source_key=source_key,
            derived_key=derived_key or f"_derived/{source_key}.{target_format}",
            status="queued",
            target_format=target_format,
            stage="browser" if run_on == "browser" else "queued",
            created_at=now,
            updated_at=now,
            scope_kind=kw.get("scope_kind") or "shared",
            scope_id=kw.get("scope_id"),
            conversion_options=kw.get("conversion_options"),
            target_capability=kw.get("target_capability"),
            run_on=run_on,
        )
        self.jobs[job.job_id] = job
        if publish:
            await self.publish(job)
        return job

    async def publish(self, job: Job) -> None:
        self.published.append(job.job_id)

    async def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    async def update(self, job_id: str, **fields) -> Job | None:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        for k, v in fields.items():
            setattr(job, k, v)
        return job

    async def resolve_capability(self, *a, **k) -> str:
        return "base"

    async def list_workers(self) -> list:
        return []


@pytest.fixture
def client(tmp_path: pathlib.Path):
    store = tmp_path / "store"
    store.mkdir()
    app = create_app(_settings(store))
    with TestClient(app) as c:
        queue = _MemQueue()
        rest = app.state.rest
        app.state.rest = dataclasses.replace(rest, queue=queue, jobs=QueueJobTransport(queue, {}, pool=None))
        c.mem_queue = queue  # type: ignore[attr-defined]
        yield c


# ---------------------------------------------------------------------------
# Pure contract
# ---------------------------------------------------------------------------


def test_header_and_families():
    assert bj.requests_browser({"x-ada-run-on": " Browser "})
    assert not bj.requests_browser({"x-ada-run-on": "worker"})
    assert bj.family_of("clash_check_group") == "clash_check"
    assert bj.family_of("clash_contacts", {"mode": "annotate"}) == "clash_check"
    assert bj.family_of("clash_contacts", {"mode": "detail"}) == "clash_detail"
    assert bj.family_of("equipment_bbox") == "equipment_bbox"
    # The node / group selection export writes attributes and relations read from the database.
    assert not bj.claimable("export_selection_asset")
    assert not bj.claimable("glb")
    from ada.comms.rest.wasm_jobs import KINDS

    assert set(bj.BROWSER_KINDS) == set(KINDS)


def test_outputs_go_where_the_route_said():
    claim = {
        "kind": "clash_contacts",
        "derived_key": "_derived/clash/detail/x/stats.json",
        "conversion_options": {"mode": "detail", "glb_key": "_derived/clash/detail/x/model.glb"},
    }
    assert bj.output_target(claim, "glb") == ("_derived/clash/detail/x/model.glb", "gzip", "blob")
    assert bj.output_target(claim, "stats") == ("_derived/clash/detail/x/stats.json", "gzip", "blob")
    with pytest.raises(bj.OutputError):
        bj.output_target(claim, "run")
    util = {"kind": "utility", "derived_key": "_derived/m.glb.diff.viewops.json", "conversion_options": {}}
    assert bj.output_target(util, "overlay:_overlays/abc.removed.glb")[0] == "_overlays/abc.removed.glb"
    for bad in ("overlay:assets/x.glb", "overlay:_overlays/../x.glb", "overlay:_overlays/x.json"):
        with pytest.raises(bj.OutputError):
            bj.output_target(util, bad)
    comp = {"kind": "component_build", "derived_key": "_c/s/d.glb", "conversion_options": {"mode": "report"}}
    assert bj.output_target(comp, "report")[0] == "_c/s/d.glb.report.json"
    with pytest.raises(bj.OutputError):
        bj.validate_output(comp, "data", b"{}")  # a report build's data is the GLB
    with pytest.raises(bj.OutputError):
        bj.validate_output({"kind": "equipment_bbox", "conversion_options": {}}, "bbox", b'{"lx": 1}')


def test_job_fields():
    class _S:
        job_id = "j1"
        run_on = None
        payload: dict = {}

    assert bj.job_fields(_S()) == {"job_id": "j1"}
    claimed = _S()
    claimed.run_on = "browser"
    claimed.payload = {
        "job_id": "j1",
        "target_format": "clash_check",
        "source_key": "m.ifc",
        "derived_key": "_derived/clash/a/b/result.json",
        "scope_kind": "user",
        "scope_id": "u1",
        "conversion_options": {"source_key": "m.ifc", "options": {}},
    }
    fields = bj.job_fields(claimed)
    assert fields["job_id"] is None
    assert fields["browser_job"]["id"] == "j1"
    assert fields["browser_job"]["family"] == "clash_check"
    assert fields["browser_job"]["scope"] == "user:me"


# ---------------------------------------------------------------------------
# The transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_browser_choice_is_a_claim_published_to_no_pool(monkeypatch):
    async def _no_worker_policy(pool, sub):
        return {"policy": {"core_job": {"*": ["pyodide"]}}, "updated_by": None, "updated_at": None}

    monkeypatch.setattr(db_module, "get_execution_policy", _no_worker_policy)
    queue = _MemQueue()
    transport = QueueJobTransport(queue, {}, pool=lambda: object())
    req = JobRequest(
        source_key="m.ifc",
        target_format="clash_check",
        scope=Scope.shared(),
        feature="clash_check",
        derived_key="_derived/clash/a/b/result.json",
        conversion_options={"source_key": "m.ifc", "options": {}},
    )
    tok = bj.set_browser_choice(True)
    try:
        submitted = await transport.submit(req)
        # A kind the browser does not run goes to the pool as before.
        other = await transport.submit(
            JobRequest(source_key="m.ifc", target_format="glb", scope=Scope.shared(), feature="conversion")
        )
    finally:
        bj.reset_browser_choice(tok)
    assert submitted.run_on == "browser"
    assert queue.jobs[submitted.job_id].run_on == "browser"
    assert queue.published == [other.job_id]


# ---------------------------------------------------------------------------
# Over HTTP: a clash check claimed, run (here, as the browser would), stored, completed
# ---------------------------------------------------------------------------


def _beam_model():
    import ada

    a = ada.Assembly("A")
    p = ada.Part("P")
    a.add_part(p)
    p.add_beam(ada.Beam("g1", (0, 0, 0), (2, 0, 0), "IPE200"))
    p.add_beam(ada.Beam("g2", (1, 0, 0), (1, 2, 0), "IPE200"))
    return a


def test_clash_check_claimed_and_completed_in_the_browser(client):
    up = client.put("/api/scopes/shared/blobs/models/m.ifc", content=b"ISO-10303-21; stand-in")
    assert up.status_code in (200, 201), up.text
    r = client.post(
        "/api/scopes/shared/clash-check",
        json={"source_key": "models/m.ifc", "options": {}},
        headers={"X-Ada-Run-On": "browser"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] is None and body["cached"] is False
    claim = body["browser_job"]
    assert claim["kind"] == "clash_check" and claim["derived_key"] == body["derived_key"]
    assert client.mem_queue.published == []

    start = client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/start")
    assert start.status_code == 200, start.text
    assert client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/start").status_code == 409

    # What the browser's run would produce: core's result over the same members.
    from ada.clash import ClashOptions, run_clash_check

    result = run_clash_check(_beam_model(), source_key="models/m.ifc", options=ClashOptions())
    doc = result.to_json()
    doc = doc.encode("utf-8") if isinstance(doc, str) else doc
    wrong = json.loads(doc)
    wrong["source_key"] = "models/other.ifc"
    bad = client.put(f"/api/scopes/shared/browser-jobs/{claim['id']}/outputs/result", content=json.dumps(wrong))
    assert bad.status_code == 400 and "not 'models/m.ifc'" in bad.json()["detail"]
    assert client.put(f"/api/scopes/shared/browser-jobs/{claim['id']}/outputs/glb", content=b"glTF").status_code == 400
    ok = client.put(f"/api/scopes/shared/browser-jobs/{claim['id']}/outputs/result", content=doc)
    assert ok.status_code == 200, ok.text
    assert ok.json()["key"] == body["derived_key"]

    done = client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/complete", json={"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done"
    status = client.get(f"/api/convert/{claim['id']}").json()
    assert status["status"] == "done" and status["run_on"] == "browser"
    # The result is where the check is read from, and a repeat is a cache hit.
    again = client.post("/api/scopes/shared/clash-check", json={"source_key": "models/m.ifc", "options": {}})
    assert again.json() == {"job_id": None, "derived_key": body["derived_key"], "cached": True}
    assert client.get(f"/api/scopes/shared/blobs/{body['derived_key']}").status_code == 200


def test_done_without_the_main_output_is_an_error(client):
    client.put("/api/scopes/shared/blobs/models/n.ifc", content=b"ISO-10303-21; stand-in")
    claim = client.post(
        "/api/scopes/shared/clash-check",
        json={"source_key": "models/n.ifc"},
        headers={"X-Ada-Run-On": "browser"},
    ).json()["browser_job"]
    client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/start")
    end = client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/complete", json={"status": "done"})
    assert end.json()["status"] == "error" and "without storing" in end.json()["error"]
    # Ended: nothing more is taken.
    assert client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/complete", json={"status": "done"}).status_code == 409


def test_to_worker_publishes_the_same_job(client):
    client.put("/api/scopes/shared/blobs/models/w.ifc", content=b"ISO-10303-21; stand-in")
    body = client.post(
        "/api/scopes/shared/clash-check",
        json={"source_key": "models/w.ifc"},
        headers={"X-Ada-Run-On": "browser"},
    ).json()
    claim = body["browser_job"]
    client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/start")
    handed = client.post(
        f"/api/scopes/shared/browser-jobs/{claim['id']}/to-worker", json={"reason": "no checker in the browser"}
    )
    assert handed.status_code == 200, handed.text
    assert handed.json()["job_id"] == claim["id"] and handed.json()["derived_key"] == body["derived_key"]
    assert client.mem_queue.published == [claim["id"]]
    job = client.mem_queue.jobs[claim["id"]]
    assert job.run_on == "worker" and job.status == "queued"
    # A worker's job is not a claim any more.
    assert client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/complete", json={"status": "done"}).status_code == 404


def test_a_claim_of_another_scope_is_not_found(client):
    client.put("/api/scopes/shared/blobs/models/s.ifc", content=b"ISO-10303-21; stand-in")
    claim = client.post(
        "/api/scopes/shared/clash-check",
        json={"source_key": "models/s.ifc"},
        headers={"X-Ada-Run-On": "browser"},
    ).json()["browser_job"]
    # A claim recorded in another scope is not one this scope can take.
    job = client.mem_queue.jobs[claim["id"]]
    job.scope_kind, job.scope_id = "project", "p1"
    assert client.post(f"/api/scopes/shared/browser-jobs/{claim['id']}/start").status_code == 404
    assert client.post("/api/scopes/shared/browser-jobs/nope/start").status_code == 404
