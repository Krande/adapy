"""Who may ENQUEUE a plugin job, as opposed to who may reach the scope it writes.

Scope access answers a different question. A plugin job can drive a licensed
workstation, a device, or a pool of one for minutes at a time, and some of those
are for admins even among users who can legitimately read the scope the result
lands in. Before this, ``POST /api/plugins/{id}/jobs`` asked only
``scope_can_access``, so an admin-only button was an affordance and not a
permission — anyone authenticated could send the same body from a console.

The gate is OR-ed across three sources, and the OR is the point: a source can
only ever TIGHTEN. The reason is that the obvious single source fails OPEN. A
plugin spec is advertised BY A WORKER, so a worker on an older build advertises
no flag, and a gate reading only that would disappear precisely when a
deployment is least uniform — looking protected while being open. The
deployment-side setting cannot be influenced by any worker, so it holds when the
advertisement does not.
"""

from __future__ import annotations

import contextlib
import os
import tempfile

# Importing ada.comms.rest.app evaluates a module-level `create_app()` which
# materializes a local Storage. Point it at a temp dir so the import succeeds in
# environments without `./viewer-data`.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

import ada.comms.rest.auth as _auth
import ada.comms.rest.db as _db
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.plugins import register_plugin_backend, reset_registry

GATE_SETTING = "admin.plugin_jobs.require_admin"


def _settings(tmp_path) -> Settings:
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


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def as_user(monkeypatch):
    """Run the next client as a NON-admin.

    With auth disabled every request is ``User.local_dev()``, which is an admin —
    convenient for local work and useless for testing a gate, so this swaps it.
    """

    monkeypatch.setattr(
        _auth.User,
        "local_dev",
        classmethod(
            lambda cls: cls(sub="viewer", email="v@x", display_name="V", groups=frozenset(), is_admin=False)
        ),
    )


def _entrypoint(options, *, storage, scope, on_progress, derived_prefix, **_kw):
    return {"ok": True}


@contextlib.contextmanager
def _client(tmp_path, *, setting: str | None = None):
    """A test client, optionally with the deployment gate setting present.

    ``database_url`` is empty, so ``app.state.db_pool`` is None and the settings
    lookup is skipped entirely. Handing it a sentinel pool plus a stubbed
    ``get_setting`` is what exercises the configured path without a database.

    The pool is installed AFTER the client context is entered, because that is
    what runs the lifespan — which sets ``db_pool`` itself and would otherwise
    overwrite the sentinel between assignment and first request.
    """
    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:
        if setting is not None:
            app.state.db_pool = object()

            async def _get_setting(_pool, key):
                return setting if key == GATE_SETTING else None

            _db.get_setting = _get_setting
        yield client


@pytest.fixture(autouse=True)
def _restore_get_setting():
    original = _db.get_setting
    yield
    _db.get_setting = original


# --- the declaration -------------------------------------------------------


def test_a_plugin_that_declares_requires_admin_refuses_a_normal_user(tmp_path, as_user):
    register_plugin_backend("adapy-test-gated", job_entrypoint=f"{__name__}:_entrypoint", requires_admin=True)

    with _client(tmp_path) as client:
        r = client.post("/api/plugins/adapy-test-gated/jobs", json={"options": {}})

    assert r.status_code == 403, r.text
    # Named, because "forbidden" alone sends the reader to look at scope access.
    assert "adapy-test-gated" in r.json()["detail"]
    assert "administrators" in r.json()["detail"]


def test_the_same_plugin_runs_for_an_admin(tmp_path):
    register_plugin_backend("adapy-test-gated", job_entrypoint=f"{__name__}:_entrypoint", requires_admin=True)

    with _client(tmp_path) as client:
        r = client.post("/api/plugins/adapy-test-gated/jobs", json={"options": {}})

    assert r.status_code == 200, r.text
    assert r.json()["job_id"]


def test_a_plugin_that_declares_nothing_is_unchanged_for_a_normal_user(tmp_path, as_user):
    """The gate is opt-in. Every existing plugin job must behave as before."""
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    with _client(tmp_path) as client:
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 200, r.text


# --- the deployment setting, which is the half a worker cannot influence ----


def test_the_setting_gates_a_plugin_that_declared_nothing(tmp_path, as_user):
    """THE REGRESSION THIS EXISTS FOR.

    A worker on a build predating ``requires_admin`` advertises no flag. If the
    advertisement were the only source, this request would be allowed and the
    deployment would look protected while being open.
    """
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    with _client(tmp_path, setting='["adapy-test-open"]') as client:
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 403, r.text


def test_the_setting_leaves_plugins_it_does_not_name_alone(tmp_path, as_user):
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    with _client(tmp_path, setting='["some-other-plugin"]') as client:
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 200, r.text


def test_a_hand_typed_comma_list_is_honoured(tmp_path, as_user):
    """Someone will type this into a settings box. Rejecting it would mean
    failing closed on a value whose intent is unambiguous."""
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    with _client(tmp_path, setting="adapy-test-open, another-one") as client:
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 403, r.text


def test_an_unreadable_setting_gates_everything_rather_than_nothing(tmp_path, as_user):
    """Fails CLOSED. A malformed value must not quietly remove the gate: an
    over-tight gate announces itself the moment someone tries to use it, and an
    absent one announces nothing at all."""
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    with _client(tmp_path, setting='{"not": "a list"}') as client:
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 403, r.text


def test_an_empty_setting_gates_nothing(tmp_path, as_user):
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    with _client(tmp_path, setting="   ") as client:
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 200, r.text


def test_a_settings_read_that_raises_gates_rather_than_500s(tmp_path, as_user):
    """Two failures in one: a gate must not become a server fault, and an error
    must not resolve to "allowed". A pool that cannot serve the lookup is an
    outage, and an outage silently removing a restriction is the worse outcome.
    """
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:

        class _BrokenPool:
            pass  # no fetchrow -- exactly the shape that used to raise

        app.state.db_pool = _BrokenPool()
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 403, r.text


def test_an_admin_still_gets_through_a_broken_settings_read(tmp_path):
    """Failing closed must not mean failing shut. The gate is admin-only, not
    nobody-at-all, or a database blip would take the capability away entirely."""
    register_plugin_backend("adapy-test-open", job_entrypoint=f"{__name__}:_entrypoint")

    app = create_app(_settings(tmp_path))
    with TestClient(app, raise_server_exceptions=False) as client:

        class _BrokenPool:
            pass

        app.state.db_pool = _BrokenPool()
        r = client.post("/api/plugins/adapy-test-open/jobs", json={"options": {}})

    assert r.status_code == 200, r.text


# --- what the UI is told ---------------------------------------------------


def test_the_listing_reports_the_effective_gate_not_just_the_declaration(tmp_path, monkeypatch):
    """A UI hiding its button on the declaration alone would offer an action the
    API then refuses, which reads as a broken button rather than a permission."""
    from ada.comms.rest import catalog

    monkeypatch.setattr(
        catalog,
        "builtin_plugin_specs",
        lambda: [{"slug": "adapy-test-open", "id": "adapy-test-open", "name": "Open"}],
    )

    with _client(tmp_path, setting='["adapy-test-open"]') as client:
        body = client.get("/api/plugins").json()

    spec = next(p for p in body["plugins"] if p["slug"] == "adapy-test-open")
    assert spec["requires_admin"] is True
