"""The connection builder on a deployed studio (``routes/connections.py``, ``formats/component.py``
report mode): specs read from what the live workers' code registered, builds run on their pools,
answered in the shape ``ada dev connections`` answers in, and the admin's scope assignment."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from ada.comms.rest.routes import connections as routes  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402

WELD = {
    "slug": "pool_a.box_joint",
    "name": "pool_a.box_joint",
    "tags": ["box"],
    "priority": 10,
    "roles": {"name": "pool_a.box_joint", "tags": ["box"], "priority": 10, "roles": [{"role": "incoming"}]},
    "defaults": {"incoming": {"section": "BOX200x10"}},
    "module": "example_pool.connections.box_joint",
    "capability": "pool-a",
}
BUILTIN = {
    "slug": "builtin.girder_gusset",
    "name": "builtin.girder_gusset",
    "roles": {"name": "builtin.girder_gusset", "roles": []},
    "defaults": {},
    "capability": None,
}


class _Jobs:
    kind = "queue"

    def __init__(self, specs: dict, outcome: str = "done"):
        self.specs = specs
        self.outcome = outcome
        self.submitted = []

    async def advertised_specs(self, field, fallback_field=None):
        assert field == "connection_specs"
        return self.specs

    async def submit(self, req, *, before_dispatch=None):
        self.submitted.append(req)
        return SimpleNamespace(job_id="job-1", derived_key=req.derived_key)

    async def status(self, job_id):
        return SimpleNamespace(status=self.outcome, payload={"error": "boom" if self.outcome == "error" else None})


class _Storage:
    def __init__(self):
        self.blobs: dict[tuple[str, str], bytes] = {}

    async def get_bytes(self, scope, key):
        return self.blobs[(scope.prefix(), key)]

    def put_json(self, scope, key, doc):
        self.blobs[(scope.prefix(), key)] = json.dumps(doc).encode("utf-8")


class _Request:
    def __init__(self, body=None, setting=None):
        self._body = body or {}
        self.app = SimpleNamespace(state=SimpleNamespace(db_pool=None))
        self.setting = setting

    async def json(self):
        return self._body


@pytest.fixture
def no_setting(monkeypatch):
    async def _none(request):
        return getattr(request, "setting", None)

    monkeypatch.setattr(routes, "_setting", _none)


USER = SimpleNamespace(sub="alice")
PROJECT = Scope("project", "11111111-1111-1111-1111-111111111111")


def test_a_capability_the_admin_did_not_name_is_offered_everywhere():
    assert routes.scope_offers(None, PROJECT, "pool-a")
    assert routes.scope_offers({"other": ["shared"]}, PROJECT, "pool-a")
    # core's built-ins always are
    assert routes.scope_offers({"pool-a": []}, PROJECT, None)


def test_a_named_capability_is_offered_only_in_its_scopes():
    setting = {"pool-a": [PROJECT.wire()]}
    assert routes.scope_offers(setting, PROJECT, "pool-a")
    assert not routes.scope_offers(setting, Scope("shared"), "pool-a")
    assert not routes.scope_offers(setting, Scope.user("alice"), "pool-a")
    # `user` stands for every personal scope
    assert routes.scope_offers({"pool-a": ["user"]}, Scope.user("alice"), "pool-a")
    # an empty list offers it nowhere
    assert not routes.scope_offers({"pool-a": []}, PROJECT, "pool-a")


async def test_the_specs_are_the_workers_in_the_dev_librarys_shape(no_setting):
    ctx = SimpleNamespace(jobs=_Jobs({WELD["slug"]: WELD, BUILTIN["slug"]: BUILTIN}))
    resp = await routes.api_connections(request=_Request(), scope_obj=PROJECT, ctx=ctx)
    state = json.loads(resp.body)
    assert state["status"] == "ready" and state["error"] is None
    assert [s["name"] for s in state["specs"]] == ["builtin.girder_gusset", "pool_a.box_joint"]
    weld = state["specs"][1]
    assert weld["defaults"] == WELD["defaults"] and weld["schema"]["defaults"] == WELD["defaults"]
    assert weld["schema"]["roles"] == [{"role": "incoming"}]
    assert weld["capability"] == "pool-a" and weld["in_library"] is False and weld["source"] is None

    # The generation follows what is offered: same specs, same generation; a change, a new one.
    again = json.loads((await routes.api_connections(request=_Request(), scope_obj=PROJECT, ctx=ctx)).body)
    assert again["generation"] == state["generation"]
    ctx.jobs.specs = {WELD["slug"]: {**WELD, "defaults": {"incoming": {"section": "BOX300x10"}}}}
    changed = json.loads((await routes.api_connections(request=_Request(), scope_obj=PROJECT, ctx=ctx)).body)
    assert changed["generation"] != state["generation"]


async def test_the_admins_assignment_hides_a_pools_specs_elsewhere(no_setting):
    ctx = SimpleNamespace(jobs=_Jobs({WELD["slug"]: WELD, BUILTIN["slug"]: BUILTIN}))
    req = _Request(setting={"pool-a": [PROJECT.wire()]})
    shared = json.loads((await routes.api_connections(request=req, scope_obj=Scope("shared"), ctx=ctx)).body)
    assert [s["name"] for s in shared["specs"]] == ["builtin.girder_gusset"]
    project = json.loads((await routes.api_connections(request=req, scope_obj=PROJECT, ctx=ctx)).body)
    assert "pool_a.box_joint" in [s["name"] for s in project["specs"]]


async def test_a_build_runs_on_the_specs_pool_and_answers_with_its_report(no_setting, monkeypatch):
    monkeypatch.setattr(routes, "POLL_INTERVAL_S", 0)
    storage = _Storage()
    jobs = _Jobs({WELD["slug"]: WELD})
    ctx = SimpleNamespace(jobs=jobs, storage=storage)
    body = {"spec_name": "pool_a.box_joint", "inputs": {"incoming": {"section": "BOX300x10"}}}

    # What the worker leaves beside the GLB.
    async def _submit(req, *, before_dispatch=None):
        jobs.submitted.append(req)
        storage.put_json(
            req.scope,
            req.derived_key + routes.REPORT_SUFFIX,
            {"inputs": body["inputs"], "counts": {"welds": 4}, "log": [], "properties": {"members": [], "welds": []}},
        )
        return SimpleNamespace(job_id="job-1", derived_key=req.derived_key)

    jobs.submit = _submit
    resp = await routes.api_connections_build(request=_Request(body), scope_obj=PROJECT, ctx=ctx, user=USER)
    out = json.loads(resp.body)
    assert resp.status_code == 200, out
    [req] = jobs.submitted
    assert req.target_format == "component_build" and req.feature == "component_build"
    assert req.target_capability == "pool-a"
    assert req.conversion_options["mode"] == "report" and req.conversion_options["inputs"] == body["inputs"]
    # Built in the caller's own scope, as `ada dev connections` builds.
    assert req.scope == Scope.user("alice") and out["scope"] == routes.BUILD_SCOPE
    assert out["key"] == req.derived_key and out["key"].endswith(".glb")
    assert out["counts"] == {"welds": 4} and out["inputs"] == body["inputs"]


async def test_a_failed_build_answers_as_the_dev_route_does(no_setting, monkeypatch):
    monkeypatch.setattr(routes, "POLL_INTERVAL_S", 0)
    storage = _Storage()
    jobs = _Jobs({WELD["slug"]: WELD}, outcome="error")
    ctx = SimpleNamespace(jobs=jobs, storage=storage)

    async def _submit(req, *, before_dispatch=None):
        storage.put_json(
            req.scope,
            req.derived_key + routes.ERROR_SUFFIX,
            {"error": "the members do not meet", "traceback": "Traceback ...", "declined": True},
        )
        return SimpleNamespace(job_id="job-1", derived_key=req.derived_key)

    jobs.submit = _submit
    body = {"spec_name": "pool_a.box_joint", "inputs": {}}
    resp = await routes.api_connections_build(request=_Request(body), scope_obj=PROJECT, ctx=ctx, user=USER)
    assert resp.status_code == 422
    out = json.loads(resp.body)
    assert out["error"] == "the members do not meet" and out["declined"] is True and out["traceback"]


async def test_a_spec_no_online_pool_offers_in_this_scope_is_not_built(no_setting):
    from fastapi import HTTPException

    ctx = SimpleNamespace(jobs=_Jobs({WELD["slug"]: WELD}), storage=_Storage())
    req = _Request({"spec_name": "pool_a.box_joint"}, setting={"pool-a": ["shared"]})
    with pytest.raises(HTTPException) as e:
        await routes.api_connections_build(request=req, scope_obj=PROJECT, ctx=ctx, user=USER)
    assert e.value.status_code == 404
    assert ctx.jobs.submitted == []


# ── the worker side ──────────────────────────────────────────────────────────


class _Queue:
    def __init__(self):
        self.updates = []

    async def update(self, job_id, **kw):
        self.updates.append(kw)


class _WorkerStorage:
    def __init__(self):
        self.blobs = {}

    async def put_bytes(self, scope, key, data, content_encoding=None):
        self.blobs[key] = data


TEST_SPEC = "test_connections_routes.plate_on_box"


def _register_test_spec() -> None:
    """A spec of the shape a package registers: a plate landing on a box beam, no welds of its own."""
    from ada.api.connections import (
        ConnectionSpec,
        MemberCriteria,
        MemberKind,
        MemberRole,
        register_connection,
    )
    from ada.api.connections.spec import all_registered

    if any(r.spec.name == TEST_SPEC for r in all_registered()):
        return
    spec = ConnectionSpec(
        name=TEST_SPEC,
        roles=(
            MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.PLATE),
            MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"})),
        ),
    )

    @register_connection(spec)
    def build(incoming, landing, **_):
        return None


async def test_the_worker_builds_a_spec_with_the_dev_librarys_report(monkeypatch):
    from ada.comms.rest.formats import component

    async def _no_audit(*a, **k):
        return None

    monkeypatch.setattr(component, "_audit_done", _no_audit)
    _register_test_spec()
    job = SimpleNamespace(
        job_id="job-1",
        derived_key="_connections/x/abc.glb",
        conversion_options={"spec_name": TEST_SPEC, "inputs": {}, "mode": "report"},
    )
    queue, storage = _Queue(), _WorkerStorage()
    await component._run_component_build(
        job=job, scope=None, storage=storage, queue=queue, db_pool=None, started_at=0.0
    )
    assert queue.updates[-1]["status"] == "done", queue.updates
    assert storage.blobs[job.derived_key][:4] == b"glTF"
    report = json.loads(storage.blobs[job.derived_key + component.REPORT_SUFFIX])
    assert set(report) >= {"inputs", "log", "counts", "properties", "clash_run", "clash_note"}
    assert report["counts"]["beams"] >= 1


async def test_a_worker_build_that_fails_says_why_beside_the_key(monkeypatch):
    from ada.comms.rest.formats import component

    async def _no_audit(*a, **k):
        return None

    monkeypatch.setattr(component, "_audit_done", _no_audit)
    job = SimpleNamespace(
        job_id="job-1",
        derived_key="_connections/x/abc.glb",
        conversion_options={"spec_name": "no.such_spec", "inputs": {}, "mode": "report"},
    )
    queue, storage = _Queue(), _WorkerStorage()
    await component._run_component_build(
        job=job, scope=None, storage=storage, queue=queue, db_pool=None, started_at=0.0
    )
    assert queue.updates[-1]["status"] == "error"
    failure = json.loads(storage.blobs[job.derived_key + component.ERROR_SUFFIX])
    assert failure["error"] and "traceback" in failure and failure["declined"] is False


def test_the_heartbeat_carries_each_specs_defaults():
    pytest.importorskip("ada.clash.builtin_specs")
    from ada.comms.rest.worker.registration import _connection_specs_for_heartbeat

    specs = _connection_specs_for_heartbeat(["base"])
    assert specs, "core's built-ins are registered on every worker"
    for s in specs:
        assert isinstance(s["defaults"], dict) and "module" in s
