"""Per-user and per-capability worker-pool access control (migration 057)."""

from __future__ import annotations

import os
import pathlib
import tempfile
from dataclasses import dataclass

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-worker-access-"))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest import worker_access as wa  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.auth import User, reset_request_user, set_request_user  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.job_transport import JobRequest, QueueJobTransport  # noqa: E402
from ada.comms.rest.queue import Job  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402


def _user(*, sub: str = "user-1", is_admin: bool = False) -> User:
    return User(
        sub=sub,
        email=f"{sub}@example.com",
        display_name=sub,
        groups=frozenset({"admins"} if is_admin else set()),
        is_admin=is_admin,
    )


def _settings(tmp_path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url="",
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


# ---------------------------------------------------------------------------
# Pure policy evaluation
# ---------------------------------------------------------------------------


def test_default_deny_for_non_admin():
    policy = wa.default_policy()
    assert wa.is_worker_allowed(_user(is_admin=False), policy, None) is False
    assert wa.is_worker_allowed(_user(is_admin=False), policy, "abaqus") is False


def test_admin_always_allowed():
    policy = wa.default_policy()
    assert wa.is_worker_allowed(_user(is_admin=True), policy, None) is True
    assert wa.is_worker_allowed(_user(is_admin=True), policy, "abaqus") is True


def test_scheduled_tick_without_user_always_allowed():
    policy = wa.default_policy()
    assert wa.is_worker_allowed(None, policy, None) is True
    assert wa.is_worker_allowed(None, policy, "abaqus") is True


def test_global_toggle_allows_all_capabilities():
    policy = wa.policy_from_row({"enabled": True, "allowed_capabilities": []})
    user = _user(is_admin=False)
    assert wa.is_worker_allowed(user, policy, None) is True
    assert wa.is_worker_allowed(user, policy, "abaqus") is True
    assert wa.is_worker_allowed(user, policy, "batch") is True


def test_per_capability_override_allows_only_listed():
    policy = wa.policy_from_row({"enabled": False, "allowed_capabilities": ["Abaqus", " mesh "]})
    user = _user(is_admin=False)
    assert wa.is_worker_allowed(user, policy, "abaqus") is True
    assert wa.is_worker_allowed(user, policy, "ABAQUS") is True
    assert wa.is_worker_allowed(user, policy, "mesh") is True
    assert wa.is_worker_allowed(user, policy, "batch") is False
    assert wa.is_worker_allowed(user, policy, None) is False


def test_serialize_me_worker_access():
    non_admin = _user(is_admin=False)
    admin = _user(is_admin=True)
    off = wa.policy_from_row({"enabled": False, "allowed_capabilities": ["abaqus"]})
    on = wa.policy_from_row({"enabled": True, "allowed_capabilities": []})

    assert wa.serialize_me_worker_access(non_admin, off) == {
        "enabled": False,
        "allowed_capabilities": ["abaqus"],
        "effective_all": False,
    }
    assert wa.serialize_me_worker_access(non_admin, on) == {
        "enabled": True,
        "allowed_capabilities": [],
        "effective_all": True,
    }
    assert wa.serialize_me_worker_access(admin, off) == {
        "enabled": False,
        "allowed_capabilities": ["abaqus"],
        "effective_all": True,
    }


# ---------------------------------------------------------------------------
# QueueJobTransport.submit() central guard
# ---------------------------------------------------------------------------


@dataclass
class _FakeQueue:
    enqueued: list[dict]

    async def enqueue(self, source_key: str, **kwargs) -> Job:
        self.enqueued.append({"source_key": source_key, **kwargs})
        return Job(
            job_id="job-1",
            source_key=source_key,
            derived_key=kwargs.get("derived_key") or "derived/out.glb",
            target_format=kwargs.get("target_format") or "glb",
            scope_kind=kwargs.get("scope_kind") or "shared",
            scope_id=kwargs.get("scope_id"),
            status="queued",
            stage="queued",
            progress=0.0,
            target_capability=kwargs.get("target_capability"),
        )


class _FakePool:
    """In-memory stub for user_worker_access lookups."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}


@pytest.mark.asyncio
async def test_queue_transport_submit_enforces_policy(monkeypatch):
    fake_pool = _FakePool()

    async def _fake_get(pool, user_sub: str) -> dict:
        return pool.rows.get(
            user_sub,
            {"enabled": False, "allowed_capabilities": [], "updated_by": None, "updated_at": None},
        )

    monkeypatch.setattr(db_module, "get_user_worker_access", _fake_get)

    queue = _FakeQueue(enqueued=[])
    transport = QueueJobTransport(queue, {}, pool=lambda: fake_pool)

    def _req(*, capability: str | None = None, user: User | None = None) -> JobRequest:
        return JobRequest(
            source_key="model.odb",
            target_format="sqlite",
            scope=Scope.shared(),
            feature="conversion",
            target_capability=capability,
            user=user,
        )

    non_admin = _user(sub="eng-1", is_admin=False)
    admin = _user(sub="adm-1", is_admin=True)

    # 1. Default deny for non-admin
    with pytest.raises(HTTPException) as exc:
        await transport.submit(_req(capability="abaqus", user=non_admin))
    assert exc.value.status_code == 403
    assert "abaqus" in exc.value.detail
    assert len(queue.enqueued) == 0

    # 2. Admin bypasses default deny
    res = await transport.submit(_req(capability="abaqus", user=admin))
    assert res.job_id == "job-1"
    assert len(queue.enqueued) == 1

    # 3. Scheduled background tick (user=None) bypasses default deny
    res = await transport.submit(_req(capability="abaqus", user=None))
    assert res.job_id == "job-1"
    assert len(queue.enqueued) == 2

    # 4. Per-capability override allows abaqus, denies batch
    fake_pool.rows["eng-1"] = {
        "enabled": False,
        "allowed_capabilities": ["abaqus"],
        "updated_by": "adm-1",
        "updated_at": None,
    }
    res = await transport.submit(_req(capability="abaqus", user=non_admin))
    assert res.job_id == "job-1"
    with pytest.raises(HTTPException) as exc:
        await transport.submit(_req(capability="batch", user=non_admin))
    assert exc.value.status_code == 403

    # 5. Global toggle allows all capabilities, and ContextVar request user is picked up automatically
    fake_pool.rows["eng-1"]["enabled"] = True
    tok = set_request_user(non_admin)
    try:
        res = await transport.submit(_req(capability="batch", user=None))
        assert res.job_id == "job-1"
    finally:
        reset_request_user(tok)


@dataclass
class _RoutingQueue(_FakeQueue):
    """A queue that routes capability-less jobs by extension, like JobQueue."""

    routes: dict[str, str] | None = None
    resolved: int = 0

    async def resolve_capability(self, source_key: str, target_format: str = "glb", conversion_options=None) -> str:
        self.resolved += 1
        ext = pathlib.PurePosixPath(source_key).suffix.lower()
        return (self.routes or {}).get(ext, "base")


@pytest.mark.asyncio
async def test_capability_less_bake_is_gated_on_the_pool_it_routes_to(monkeypatch):
    """A bake/convert carries no capability; the gate must judge the pool the queue
    routes it to, so an ``abaqus``-only grant admits an ``.odb`` bake and nothing else."""
    rows = {"eng-1": {"enabled": False, "allowed_capabilities": ["abaqus"], "updated_by": None, "updated_at": None}}

    async def _fake_get(pool, user_sub: str) -> dict:
        return rows.get(user_sub, {"enabled": False, "allowed_capabilities": []})

    monkeypatch.setattr(db_module, "get_user_worker_access", _fake_get)
    queue = _RoutingQueue(enqueued=[], routes={".odb": "abaqus"})
    transport = QueueJobTransport(queue, {}, pool=lambda: _FakePool())
    user = _user(sub="eng-1")

    def _bake(key: str) -> JobRequest:
        return JobRequest(
            source_key=key, target_format="fea_artefacts", scope=Scope.shared(), feature="bake", user=user
        )

    await transport.submit(_bake("results/job.odb"))
    # The resolved pool is handed to enqueue, so routing and the gate agree.
    assert queue.enqueued[-1]["target_capability"] == "abaqus"

    with pytest.raises(HTTPException) as exc:
        await transport.submit(_bake("model.step"))
    assert exc.value.status_code == 403
    assert "'base'" in exc.value.detail

    # Admins skip both the check and the extra registry lookup.
    before = queue.resolved
    await transport.submit(
        JobRequest(
            source_key="model.step",
            target_format="glb",
            scope=Scope.shared(),
            feature="conversion",
            user=_user(sub="adm", is_admin=True),
        )
    )
    assert queue.resolved == before
    assert queue.enqueued[-1]["target_capability"] is None


def test_browser_baked_sqlite_is_an_api_fea_source():
    """The browser bakes .sqlite and uploads the tree; the API must accept it."""
    from ada.comms.rest.converters.keys import is_fea_artefact_source

    assert is_fea_artefact_source("results/job.sqlite")
    assert not is_fea_artefact_source("results/job.odb")  # worker-advertised only


@pytest.mark.asyncio
async def test_fea_manifest_keeps_the_worker_access_403(monkeypatch):
    """The manifest route wrapped every submit error in a 503; a refusal must reach
    the client as the 403 it is, with the detail the UI shows."""
    from types import SimpleNamespace

    from ada.comms.rest.routes import fea as fea_routes

    async def _advertised(*_a, **_k):
        return {".odb"}

    async def _no_cache(*_a, **_k):
        return None

    async def _exists(*_a, **_k):
        return True

    async def _deny(req, **_k):
        raise HTTPException(status_code=403, detail="worker pool access is required for capability 'abaqus'")

    audits: list[dict] = []

    async def _audit(*_a, **kw):
        audits.append(kw)

    monkeypatch.setattr(fea_routes, "worker_advertised_exts", _advertised)
    monkeypatch.setattr(fea_routes, "_check_cached_manifest", _no_cache)
    ctx = SimpleNamespace(
        storage=SimpleNamespace(exists=_exists),
        queue=None,
        worker_registry={},
        jobs=SimpleNamespace(require=lambda _f: None, supports=lambda _f: True, submit=_deny),
        audit=_audit,
    )
    with pytest.raises(HTTPException) as exc:
        await fea_routes.api_scope_fea_manifest(
            request=None, key="job.odb", scope_obj=Scope.shared(), user=_user(), ctx=ctx
        )
    assert exc.value.status_code == 403
    assert "abaqus" in exc.value.detail


# ---------------------------------------------------------------------------
# REST routes: /api/me and /api/admin/users/{user_sub}/worker-access
# ---------------------------------------------------------------------------


def test_me_and_admin_worker_access_routes(tmp_path, monkeypatch):
    fake_pool = _FakePool()

    async def _fake_get(pool, user_sub: str) -> dict:
        return pool.rows.get(
            user_sub,
            {"enabled": False, "allowed_capabilities": [], "updated_by": None, "updated_at": None},
        )

    async def _fake_set(pool, user_sub: str, *, enabled: bool, allowed_capabilities=None, updated_by=None) -> dict:
        caps = sorted({str(c).strip().lower() for c in (allowed_capabilities or []) if str(c).strip()})
        row = {
            "enabled": bool(enabled),
            "allowed_capabilities": caps,
            "updated_by": updated_by,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        pool.rows[user_sub] = row
        return row

    async def _fake_upsert(*args, **kwargs):
        return None

    async def _fake_list_projects(*args, **kwargs):
        return []

    async def _fake_list_corpora(*args, **kwargs):
        return []

    monkeypatch.setattr(db_module, "get_user_worker_access", _fake_get)
    monkeypatch.setattr(db_module, "set_user_worker_access", _fake_set)
    monkeypatch.setattr(db_module, "upsert_user", _fake_upsert)
    monkeypatch.setattr(db_module, "list_user_projects", _fake_list_projects)
    monkeypatch.setattr(db_module, "list_corpora", _fake_list_corpora)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        app.state.db_pool = fake_pool

        # Admin updates non-admin user's worker access
        r = client.put(
            "/api/admin/users/eng-1/worker-access",
            json={"enabled": False, "allowed_capabilities": ["Abaqus", "abaqus"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_sub"] == "eng-1"
        assert body["enabled"] is False
        assert body["allowed_capabilities"] == ["abaqus"]
        assert body["updated_by"] == "local-dev"

        # GET returns stored policy
        r = client.get("/api/admin/users/eng-1/worker-access")
        assert r.status_code == 200
        assert r.json()["allowed_capabilities"] == ["abaqus"]

        # /api/me for non-admin reflects stored policy
        app.dependency_overrides[auth_module.current_user] = lambda: _user(sub="eng-1", is_admin=False)
        r = client.get("/api/me")
        assert r.status_code == 200
        assert r.json()["worker_access"] == {
            "enabled": False,
            "allowed_capabilities": ["abaqus"],
            "effective_all": False,
        }

        # Non-admin cannot update worker access
        r = client.put(
            "/api/admin/users/eng-1/worker-access",
            json={"enabled": True, "allowed_capabilities": []},
        )
        assert r.status_code == 403


def test_migration_057_exists_and_creates_table():
    mig = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "ada"
        / "comms"
        / "rest"
        / "migrations"
        / "057_user_worker_access.sql"
    )
    sql = mig.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS user_worker_access" in sql
    assert "allowed_capabilities" in sql
