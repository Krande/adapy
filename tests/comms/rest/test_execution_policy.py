"""Execution policy: which engine runs each operation (migration 058)."""

from __future__ import annotations

import os
import pathlib
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-execution-policy-"))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest import execution_policy as ep  # noqa: E402
from ada.comms.rest import worker_access as wa  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.auth import User  # noqa: E402
from ada.comms.rest.job_transport import JobRequest, QueueJobTransport  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402

from .test_worker_access import _FakeQueue, _settings  # noqa: E402


def _user(*, sub: str = "user-1", is_admin: bool = False) -> User:
    return User(
        sub=sub,
        email=f"{sub}@example.com",
        display_name=sub,
        groups=frozenset({"admins"} if is_admin else set()),
        is_admin=is_admin,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_defaults_are_todays_routing():
    d = ep.default_policy()
    assert set(d) == set(ep.OPERATIONS)
    for op in ("convert", "read"):
        assert d[op]["*"] == ["pyodide", "worker"]
        for ext in (".ifc", ".step", ".stp"):
            assert d[op][ext] == ["native", "pyodide", "worker"]
    for op in ("fea_bake", "procedural_compile", "plugin_job", "procedural_io", "asset_build", "core_job"):
        assert d[op] == {"*": ["pyodide", "worker"]}
    # A copy: mutating it leaves the module default alone.
    d["convert"]["*"].append("native")
    assert ep.DEFAULT_POLICY["convert"]["*"] == ["pyodide", "worker"]


def test_validate_normalises():
    doc = ep.validate_policy(
        {
            "convert": {".IFC": ["Native", " worker "], "*": ["pyodide"]},
            "plugin_job": {"Example-Plugin": ["worker"]},
        }
    )
    assert doc == {
        "convert": {".ifc": ["native", "worker"], "*": ["pyodide"]},
        "plugin_job": {"example-plugin": ["worker"]},
    }
    assert ep.validate_policy(None) == {}
    assert ep.validate_policy({}) == {}


@pytest.mark.parametrize(
    "doc, fragment",
    [
        ([], "JSON object"),
        ({"teleport": {"*": ["worker"]}}, "unknown operation"),
        ({"convert": ["worker"]}, "keyed by qualifier"),
        ({"convert": {"*": []}}, "non-empty"),
        ({"convert": {"*": "worker"}}, "non-empty"),
        ({"convert": {"*": ["gpu"]}}, "unknown engine"),
        ({"convert": {"*": ["worker", "worker"]}}, "listed twice"),
        ({"convert": {"ifc": ["worker"]}}, "extension"),
        ({"convert": {".ifc": ["worker"], ".IFC": ["native"]}}, "more than once"),
        ({"plugin_job": {"bad id": ["worker"]}}, "plugin id"),
        ({"procedural_compile": {"": ["worker"]}}, "engine slug"),
        ({"procedural_io": {"bad slug": ["worker"]}}, "engine slug"),
        ({"asset_build": {"bad capability": ["worker"]}}, "build capability"),
        ({"core_job": {"clash": ["worker"]}}, "one of clash_check"),
    ],
)
def test_validate_rejects(doc, fragment):
    with pytest.raises(ep.ExecutionPolicyError) as exc:
        ep.validate_policy(doc)
    assert fragment in str(exc.value)


def test_complete_merge_and_lookup():
    # A stored global document keeps defaults for what it does not name, gets a "*"
    # row where it has none, and can drop a default qualifier row.
    stored = {"read": {".ifc": ["worker"]}, "plugin_job": {"*": ["worker"]}}
    full = ep.complete_policy(stored)
    assert full["read"] == {".ifc": ["worker"], "*": ["pyodide", "worker"]}
    assert ".step" not in full["read"]
    assert full["plugin_job"] == {"*": ["worker"]}
    assert full["convert"] == ep.DEFAULT_POLICY["convert"]

    # An override replaces single rows.
    eff = ep.effective_policy(stored, {"read": {"*": ["pyodide"]}, "fea_bake": {".rmed": ["worker"]}})
    assert eff["read"] == {".ifc": ["worker"], "*": ["pyodide"]}
    assert eff["fea_bake"] == {"*": ["pyodide", "worker"], ".rmed": ["worker"]}

    assert ep.lookup(eff, "read", ".IFC") == ["worker"]
    assert ep.lookup(eff, "read", ".obj") == ["pyodide"]
    assert ep.lookup(eff, "read", None) == ["pyodide"]
    assert ep.lookup(eff, "nothing", "*") == []
    assert ep.effective_policy(None, None) == ep.default_policy()


def test_stored_policy_that_no_longer_validates_reads_as_empty():
    assert ep.stored_policy({"convert": {"*": ["quantum"]}}) == {}
    assert ep.stored_policy({"convert": {"*": ["worker"]}}) == {"convert": {"*": ["worker"]}}


def test_job_operation_mapping():
    assert ep.job_operation("conversion", source_key="a/b/Model.IFC") == ("convert", ".ifc")
    assert ep.job_operation("conversion", source_key="m.step", requested="read") == ("read", ".step")
    assert ep.job_operation("conversion", source_key="noext") == ("convert", "*")
    assert ep.job_operation("bake", source_key="r.rmed") == ("fea_bake", ".rmed")
    assert ep.job_operation("procedural_build", conversion_options={"engine": "Echo"}) == (
        "procedural_compile",
        "echo",
    )
    assert ep.job_operation("procedural_build", conversion_options={}) == ("procedural_compile", "*")
    assert ep.job_operation("plugin_jobs", plugin_id="example-plugin") == ("plugin_job", "example-plugin")
    assert ep.job_operation("plugin_jobs", conversion_options={"plugin_id": "example-plugin"}) == (
        "plugin_job",
        "example-plugin",
    )
    # A procedural model's workbook export / import, relocations and IFC/Genie export.
    for feature in ("procedural_export", "procedural_import", "procedural_relocations"):
        assert ep.job_operation(feature, conversion_options={"engine": "Wheel-Engine"}) == (
            "procedural_io",
            "wheel-engine",
        )
        assert ep.job_operation(feature, conversion_options={"engine": None}) == ("procedural_io", "*")
    # Core work the browser runs first: one operation, the job family as the qualifier.
    for feature, family in (
        ("clash_check", "clash_check"),
        ("clash_detail", "clash_detail"),
        ("component_build", "component_build"),
        ("export_selection", "export_selection"),
        ("bbox_inference", "equipment_bbox"),
        ("utilities", "utility"),
    ):
        assert ep.job_operation(feature, source_key="x.ifc") == ("core_job", family)
    assert ep.validate_policy({"core_job": {"Clash_Detail": ["worker"]}}) == {"core_job": {"clash_detail": ["worker"]}}
    # An asset node build: the capability of its claim is the qualifier.
    assert ep.job_operation("asset_build", conversion_options={"capability": "Asset-Build-X"}) == (
        "asset_build",
        "asset-build-x",
    )
    assert ep.job_operation("asset_build", source_key="x") == ("asset_build", "*")
    # Not an operation the policy covers.
    assert ep.job_operation("export_search", source_key="x") is None


def test_operation_header_is_read():
    assert ep.requested_operation({"x-ada-operation": " Read "}) == "read"
    assert ep.requested_operation({"x-ada-operation": "launch"}) is None
    assert ep.requested_operation({}) is None


# ---------------------------------------------------------------------------
# Enforcement in QueueJobTransport.submit
# ---------------------------------------------------------------------------


@pytest.fixture
def policy_db(monkeypatch):
    """In-memory global/override rows; every user has global worker access, so only
    the execution policy can refuse."""
    state: dict = {"global": None, "users": {}}

    async def _get_global(pool):
        return {"policy": state["global"], "updated_by": None, "updated_at": None} if state["global"] else None

    async def _get_user(pool, sub):
        doc = state["users"].get(sub)
        return {"policy": doc, "updated_by": None, "updated_at": None} if doc else None

    async def _access(pool, sub):
        return {"enabled": True, "allowed_capabilities": []}

    monkeypatch.setattr(db_module, "get_execution_policy", _get_global)
    monkeypatch.setattr(db_module, "get_user_execution_policy", _get_user)
    monkeypatch.setattr(db_module, "get_user_worker_access", _access)
    return state


@pytest.fixture
def chose_worker():
    tok = wa.set_explicit_worker_choice(True)
    try:
        yield
    finally:
        wa.reset_explicit_worker_choice(tok)


def _conv(key: str, user: User | None) -> JobRequest:
    return JobRequest(source_key=key, target_format="glb", scope=Scope.shared(), feature="conversion", user=user)


@pytest.mark.asyncio
async def test_explicit_worker_choice_outside_the_policy_is_refused(policy_db, chose_worker):
    policy_db["global"] = {"convert": {".ifc": ["native", "pyodide"]}}
    queue = _FakeQueue(enqueued=[])
    transport = QueueJobTransport(queue, {}, pool=lambda: object())
    user = _user(sub="eng-1")

    with pytest.raises(HTTPException) as exc:
        await transport.submit(_conv("m.ifc", user))
    assert exc.value.status_code == 403
    assert "convert (.ifc)" in exc.value.detail
    assert "native, pyodide" in exc.value.detail
    assert queue.enqueued == []

    # Another extension falls back to the "*" row, which still lists the worker.
    await transport.submit(_conv("m.obj", user))
    assert len(queue.enqueued) == 1

    # A per-user override re-admits the worker for that user only.
    policy_db["users"]["eng-1"] = {"convert": {".ifc": ["worker"]}}
    await transport.submit(_conv("m.ifc", user))
    assert len(queue.enqueued) == 2
    with pytest.raises(HTTPException):
        await transport.submit(_conv("m.ifc", _user(sub="eng-2")))


@pytest.mark.asyncio
async def test_read_and_convert_are_judged_separately(policy_db, chose_worker):
    policy_db["global"] = {"read": {"*": ["pyodide"]}}
    transport = QueueJobTransport(_FakeQueue(enqueued=[]), {}, pool=lambda: object())
    user = _user()
    await transport.submit(_conv("m.obj", user))  # convert: the default "*" lists the worker
    tok = ep.set_requested_operation("read")
    try:
        with pytest.raises(HTTPException) as exc:
            await transport.submit(_conv("m.obj", user))
        assert "read (.obj)" in exc.value.detail
    finally:
        ep.reset_requested_operation(tok)


@pytest.mark.asyncio
async def test_bake_compile_and_plugin_jobs_map_to_their_operation(policy_db, chose_worker):
    policy_db["global"] = {
        "fea_bake": {".rmed": ["pyodide"]},
        "procedural_compile": {"echo": ["pyodide"]},
        "plugin_job": {"example-plugin": ["pyodide"]},
    }
    transport = QueueJobTransport(_FakeQueue(enqueued=[]), {}, pool=lambda: object())
    user = _user()
    refused = [
        JobRequest(source_key="r.rmed", target_format="fea_artefacts", scope=Scope.shared(), feature="bake", user=user),
        JobRequest(
            source_key="procedural/m/1",
            target_format="procedural_build",
            scope=Scope.shared(),
            feature="procedural_build",
            conversion_options={"engine": "echo"},
            user=user,
        ),
        JobRequest(
            source_key="in.json",
            target_format="plugin_job",
            scope=Scope.shared(),
            feature="plugin_jobs",
            plugin_id="example-plugin",
            user=user,
        ),
    ]
    for req in refused:
        with pytest.raises(HTTPException) as exc:
            await transport.submit(req)
        assert exc.value.status_code == 403
    # Other qualifiers keep the default "*" rows.
    await transport.submit(
        JobRequest(source_key="r.sif", target_format="fea_artefacts", scope=Scope.shared(), feature="bake", user=user)
    )


@pytest.mark.asyncio
async def test_procedural_io_jobs_are_judged_as_procedural_io(policy_db, chose_worker):
    """An explicit worker choice for an Excel export / import, a relocation proposal or an
    IFC/Genie export is refused when ``procedural_io`` does not list the worker; the
    engine slug is the qualifier, the "*" row the rest."""
    policy_db["global"] = {"procedural_io": {"*": ["pyodide"], "wheel-engine": ["pyodide", "worker"]}}
    queue = _FakeQueue(enqueued=[])
    transport = QueueJobTransport(queue, {}, pool=lambda: object())
    user = _user()

    def _req(feature: str, target: str, engine: str | None) -> JobRequest:
        return JobRequest(
            source_key=f"_synthetic/procedural/{target}/m/1",
            target_format=target,
            scope=Scope.shared(),
            feature=feature,
            conversion_options={"model_id": "m", "revision": 1, "engine": engine},
            user=user,
        )

    for feature, target in (
        ("procedural_export", "procedural_export_xlsx"),
        ("procedural_export", "procedural_export_model"),
        ("procedural_import", "procedural_import_xlsx"),
        ("procedural_relocations", "procedural_relocations"),
    ):
        with pytest.raises(HTTPException) as exc:
            await transport.submit(_req(feature, target, None))
        assert exc.value.status_code == 403
        assert "procedural_io (*)" in exc.value.detail
    assert queue.enqueued == []
    await transport.submit(_req("procedural_export", "procedural_export_xlsx", "wheel-engine"))
    assert len(queue.enqueued) == 1


@pytest.mark.asyncio
async def test_unjudged_submissions_pass(policy_db):
    """No X-Ada-Run-On header (no browser path), no user (a scheduled tick), or a
    feature the policy does not cover: never refused by the execution policy."""
    policy_db["global"] = {"convert": {"*": ["pyodide"]}}
    queue = _FakeQueue(enqueued=[])
    transport = QueueJobTransport(queue, {}, pool=lambda: object())
    await transport.submit(_conv("m.obj", _user()))  # not an explicit choice
    tok = wa.set_explicit_worker_choice(True)
    try:
        await transport.submit(_conv("m.obj", None))  # scheduled
        await transport.submit(
            JobRequest(
                source_key="m.json", target_format="xlsx", scope=Scope.shared(), feature="utilities", user=_user()
            )
        )
    finally:
        wa.reset_explicit_worker_choice(tok)
    assert len(queue.enqueued) == 3


@pytest.mark.asyncio
async def test_without_a_database_the_defaults_apply(chose_worker, monkeypatch):
    async def _access(pool, sub):
        return {"enabled": True, "allowed_capabilities": []}

    monkeypatch.setattr(db_module, "get_user_worker_access", _access)
    queue = _FakeQueue(enqueued=[])
    transport = QueueJobTransport(queue, {}, pool=None)
    await transport.submit(_conv("m.ifc", _user()))
    assert len(queue.enqueued) == 1


@pytest.mark.asyncio
async def test_an_unreadable_policy_falls_back_to_the_defaults(chose_worker, monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_module, "get_execution_policy", _boom)
    await ep.require_execution_policy(object(), _user(), explicit=True, feature="conversion", source_key="m.ifc")


# ---------------------------------------------------------------------------
# Routes: /api/admin/execution-policy, /api/admin/users/{sub}/execution-policy, /api/me
# ---------------------------------------------------------------------------


@pytest.fixture
def route_db(monkeypatch):
    state: dict = {"global": None, "users": {}}

    def _row(doc):
        return {"policy": doc, "updated_by": "local-dev", "updated_at": "2026-01-01T00:00:00+00:00"}

    async def _get_global(pool):
        return _row(state["global"]) if state["global"] is not None else None

    async def _set_global(pool, policy, *, updated_by=None):
        state["global"] = policy
        return _row(policy)

    async def _del_global(pool):
        state["global"] = None
        return True

    async def _get_user(pool, sub):
        return _row(state["users"][sub]) if sub in state["users"] else None

    async def _set_user(pool, sub, policy, *, updated_by=None):
        state["users"][sub] = policy
        return _row(policy)

    async def _del_user(pool, sub):
        return state["users"].pop(sub, None) is not None

    async def _noop(*_a, **_k):
        return None

    async def _empty(*_a, **_k):
        return []

    async def _access(pool, sub):
        return {"enabled": False, "allowed_capabilities": [], "updated_by": None, "updated_at": None}

    for name, fn in {
        "get_execution_policy": _get_global,
        "set_execution_policy": _set_global,
        "delete_execution_policy": _del_global,
        "get_user_execution_policy": _get_user,
        "set_user_execution_policy": _set_user,
        "delete_user_execution_policy": _del_user,
        "upsert_user": _noop,
        "list_user_projects": _empty,
        "list_corpora": _empty,
        "get_user_worker_access": _access,
    }.items():
        monkeypatch.setattr(db_module, name, fn)
    return state


def test_admin_execution_policy_routes(tmp_path, route_db):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        # No database: the defaults, read-only.
        r = client.get("/api/admin/execution-policy")
        assert r.status_code == 200
        body = r.json()
        assert body["policy"] == ep.default_policy()
        assert body["stored"] is False
        assert body["operations"] == list(ep.OPERATIONS)
        assert body["engines"] == list(ep.ENGINES)
        assert client.get("/api/me").json()["execution_policy"] == ep.default_policy()
        r = client.put("/api/admin/execution-policy", json={"policy": {"convert": {"*": ["worker"]}}})
        assert r.status_code == 503

        app.state.db_pool = object()

        r = client.put("/api/admin/execution-policy", json={"policy": {"convert": {"*": ["worker", "worker"]}}})
        assert r.status_code == 422
        assert "listed twice" in r.json()["detail"]

        r = client.put(
            "/api/admin/execution-policy",
            json={"policy": {"convert": {"*": ["Worker"], ".ifc": ["native", "worker"]}}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["stored"] is True
        assert body["policy"]["convert"] == {"*": ["worker"], ".ifc": ["native", "worker"]}
        assert body["policy"]["read"] == ep.DEFAULT_POLICY["read"]
        assert route_db["global"] == {"convert": {"*": ["worker"], ".ifc": ["native", "worker"]}}

        # Per-user override.
        r = client.put("/api/admin/users/eng-1/execution-policy", json={"policy": {"convert": {".ifc": ["pyodide"]}}})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["override"] == {"convert": {".ifc": ["pyodide"]}}
        assert body["effective"]["convert"] == {"*": ["worker"], ".ifc": ["pyodide"]}
        r = client.get("/api/admin/users/eng-1/execution-policy")
        assert r.json()["override"] == {"convert": {".ifc": ["pyodide"]}}
        r = client.get("/api/admin/users/eng-2/execution-policy")
        assert r.json()["override"] == {}

        # /api/me carries the effective policy.
        app.dependency_overrides[auth_module.current_user] = lambda: _user(sub="eng-1")
        me = client.get("/api/me").json()
        assert me["execution_policy"]["convert"] == {"*": ["worker"], ".ifc": ["pyodide"]}
        # Non-admins cannot change either.
        assert client.put("/api/admin/execution-policy", json={"policy": None}).status_code == 403
        assert client.put("/api/admin/users/eng-1/execution-policy", json={"policy": {}}).status_code == 403
        app.dependency_overrides.clear()

        # An empty override clears it; a null global resets to the defaults.
        r = client.put("/api/admin/users/eng-1/execution-policy", json={"policy": {}})
        assert r.status_code == 200
        assert r.json()["override"] == {}
        assert "eng-1" not in route_db["users"]
        r = client.put("/api/admin/execution-policy", json={"policy": None})
        assert r.json()["stored"] is False
        assert r.json()["policy"] == ep.default_policy()


def test_the_local_audit_row_records_the_engine_and_its_fallbacks():
    from ada.comms.rest.routes.audit_local import execution_metrics

    assert execution_metrics(None) is None
    assert execution_metrics({"engine": ""}) is None
    assert execution_metrics({"engine": "native", "fallbacks": []}) == {"execution_engine": "native"}
    assert execution_metrics(
        {"engine": "pyodide", "fallbacks": [{"engine": "native", "reason": "module missing"}, "junk"]}
    ) == {
        "execution_engine": "pyodide",
        "fallback_from": "native",
        "fallback_reason": "native: module missing",
    }
    long = execution_metrics({"engine": "pyodide", "fallbacks": [{"engine": "native", "reason": "x" * 5000}]})
    assert len(long["fallback_reason"]) <= 512


def test_migration_058_creates_both_tables():
    mig = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "ada"
        / "comms"
        / "rest"
        / "migrations"
        / "058_execution_policy.sql"
    )
    sql = mig.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS execution_policy" in sql
    assert "CREATE TABLE IF NOT EXISTS user_execution_policy" in sql
    assert "REFERENCES users(sub) ON DELETE CASCADE" in sql
