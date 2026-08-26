"""Published dataset blobs under the ``assets/`` prefix.

Two coupled behaviours are pinned here, and the coupling is the point:

* the three write gates (direct PUT, presign, upload-complete) accept an
  ``assets/<collection>/<subject>/<revision>/<file>`` key whatever its
  extension, exactly as they already accept ``versions/``; and
* unlike ``versions/``, such a key stays *deletable* — by its publisher in
  a personal scope, and by a project member in a project scope.

If the write exemption were ever folded into ``is_versions_artefact_key``
the first half of that would still pass and the second half would not,
which is why the exemption is its own predicate.

Same env shim + LocalStore staging as test_user_storage_ops.py, and the
same non-admin principal via the User.local_dev monkeypatch.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
import uuid

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.converter import (  # noqa: E402
    HIDDEN_PREFIXES,
    PUBLISHED_ASSET_PREFIX,
    is_published_asset_key,
    is_versions_artefact_key,
)
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.storage import Storage  # noqa: E402

USER_SUB = "demo-user"
PROJECT_ID = str(uuid.UUID(int=0x5A55E75))

# One published revision: an index document and a packed dataset. Neither
# extension is an accepted conversion source, which is the whole reason the
# prefix needs an exemption.
ASSET_JSON = "assets/collection-a/subject-b/20260825T135553Z/assets.json"
ASSET_DB = "assets/collection-a/subject-b/20260825T135553Z/data.db"


def _settings(tmp_path: pathlib.Path) -> Settings:
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


@pytest.fixture
def app_client(tmp_path: pathlib.Path, monkeypatch):
    """API client whose principal is a NON-admin user with a fixed sub."""
    monkeypatch.setattr(
        auth_module.User,
        "local_dev",
        classmethod(
            lambda cls: cls(
                sub=USER_SUB,
                email="demo@x.invalid",
                display_name="Demo",
                groups=frozenset(),
                is_admin=False,
            )
        ),
    )
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def member_client(app_client: TestClient, monkeypatch):
    """The same non-admin principal, now a member of one project scope.

    ``scope.can_access`` resolves project membership through
    ``db.is_project_member`` behind a pool it only checks for None, so a
    sentinel pool plus a stubbed membership query gives a real project scope
    without a database. The scope id is UUID-shaped so
    ``_resolve_project_scope`` passes it straight through instead of trying a
    slug lookup.
    """

    async def _is_member(pool, project_id: str, sub: str) -> bool:
        return project_id == PROJECT_ID and sub == USER_SUB

    monkeypatch.setattr(db_module, "is_project_member", _is_member)
    app_client.app.state.db_pool = object()
    return app_client


@pytest.fixture
def presigning_client(app_client: TestClient, monkeypatch):
    """LocalStore cannot sign URLs and the presign route 503s before it reaches
    any key check, so stub just enough of the S3 surface that the route gets as
    far as the gates under test."""
    monkeypatch.setattr(Storage, "supports_presigned_uploads", property(lambda self: True))

    async def _fake_presign(self, scope, key, expires_in_seconds=3600):
        return f"https://object-store.invalid/{scope.prefix()}/{key}"

    monkeypatch.setattr(Storage, "presigned_put_url", _fake_presign)
    return app_client


def _storage(client: TestClient) -> Storage:
    storage = client.app.state.__dict__.get("_test_storage")
    if storage is None:
        for route in client.app.routes:
            ep = getattr(route, "endpoint", None)
            if ep is None:
                continue
            cells = getattr(ep, "__closure__", None)
            if not cells:
                continue
            for c in cells:
                v = c.cell_contents
                if v.__class__.__name__ == "Storage":
                    storage = v
                    break
            if storage is not None:
                break
        client.app.state._test_storage = storage
    return storage


def _put(client: TestClient, key: str, data: bytes, scope: Scope | None = None) -> None:
    """Stage a blob directly in storage, bypassing upload validation."""
    scope = scope or Scope.user(USER_SUB)
    asyncio.run(_storage(client).put_bytes(scope, key, data))


def _keys(client: TestClient, scope: Scope | None = None) -> set[str]:
    scope = scope or Scope.user(USER_SUB)
    files = asyncio.run(_storage(client).list(scope))
    return {f.key for f in files}


# ── the predicate itself ────────────────────────────────────────────


def test_predicates_are_disjoint_and_assets_are_not_hidden():
    assert is_published_asset_key(ASSET_JSON)
    assert is_published_asset_key("/" + ASSET_DB)  # leading slash tolerated
    assert not is_published_asset_key("versions/main/abc123/model.glb")
    assert not is_published_asset_key("models/assets/x.json")  # prefix, not substring
    assert not is_versions_artefact_key(ASSET_JSON)

    # A3: hiding the prefix would silently blank every consumer that projects a
    # hierarchy out of the file listing, since that listing is the only index of
    # the prefix. Keep it visible.
    assert PUBLISHED_ASSET_PREFIX not in HIDDEN_PREFIXES


# ── A1: the three write gates ───────────────────────────────────────


def test_put_accepts_published_asset_extensions(app_client: TestClient):
    """.json and .db are not accepted conversion sources; under assets/ they
    are stored blobs and must be taken anyway."""
    for key in (ASSET_JSON, ASSET_DB):
        r = app_client.put(f"/api/scopes/user:me/blobs/{key}", content=b"payload")
        assert r.status_code == 201, r.text
        assert r.json()["key"] == key
    assert {ASSET_JSON, ASSET_DB} <= _keys(app_client)


def test_put_exemption_is_prefix_scoped_not_a_blanket_lift(app_client: TestClient):
    """The same extensions outside assets/ (and outside versions/) still 415."""
    for key in (
        "notes/assets.json",
        "data/store.db",
        "assetsx/collection/subject/rev/assets.json",  # near-miss prefix
        "nested/assets/collection/subject/rev/assets.json",  # not at the root
    ):
        r = app_client.put(f"/api/scopes/user:me/blobs/{key}", content=b"payload")
        assert r.status_code == 415, f"{key}: {r.status_code} {r.text}"
    assert not _keys(app_client)


def test_derived_still_refused_on_direct_put(app_client: TestClient):
    r = app_client.put("/api/scopes/user:me/blobs/_derived/x.ifc.glb", content=b"x")
    assert r.status_code == 403, r.text


def test_presign_accepts_assets_and_still_refuses_derived(presigning_client: TestClient):
    r = presigning_client.post("/api/scopes/user:me/upload-url", json={"key": ASSET_DB})
    assert r.status_code == 200, r.text
    assert r.json()["key"] == ASSET_DB

    r = presigning_client.post("/api/scopes/user:me/upload-url", json={"key": "notes/assets.json"})
    assert r.status_code == 415, r.text

    r = presigning_client.post("/api/scopes/user:me/upload-url", json={"key": "_derived/x.ifc.glb"})
    assert r.status_code == 403, r.text


def test_upload_complete_accepts_assets_and_still_refuses_derived(app_client: TestClient):
    _put(app_client, ASSET_JSON, b"published")

    r = app_client.post("/api/scopes/user:me/upload-complete", json={"key": ASSET_JSON})
    assert r.status_code == 201, r.text
    assert r.json()["size"] == len(b"published")

    r = app_client.post("/api/scopes/user:me/upload-complete", json={"key": "notes/assets.json"})
    assert r.status_code == 415, r.text

    r = app_client.post("/api/scopes/user:me/upload-complete", json={"key": "_derived/x.ifc.glb"})
    assert r.status_code == 403, r.text


# ── A2: deletability ────────────────────────────────────────────────


def test_published_asset_is_deletable_in_personal_scope(app_client: TestClient):
    """Pins behaviour that holds *by omission*: ``_reject_protected_key`` knows
    only ``_derived/`` and ``versions/``, so an ``assets/`` key already deletes
    cleanly here. That is exactly why it needs a test — it would be lost the
    moment somebody tidied the write exemption into ``is_versions_artefact_key``
    or added the prefix to that predicate, and the loss would be silent. The
    ``versions/`` half of the assertion is the contrast: same scope, same user,
    still admin-managed."""
    _put(app_client, ASSET_JSON, b"published")
    _put(app_client, "versions/main/abc123/model.glb", b"ci")

    r = app_client.delete(f"/api/scopes/user:me/blobs/{ASSET_JSON}")
    assert r.status_code == 200, r.text
    assert ASSET_JSON in r.json()["deleted"]

    r = app_client.delete("/api/scopes/user:me/blobs/versions/main/abc123/model.glb")
    assert r.status_code == 400, r.text

    keys = _keys(app_client)
    assert ASSET_JSON not in keys
    assert "versions/main/abc123/model.glb" in keys


def test_published_asset_is_deletable_in_a_project_scope_by_a_member(member_client: TestClient):
    scope = Scope.project(PROJECT_ID)
    _put(member_client, ASSET_JSON, b"published", scope=scope)
    _put(member_client, "models/wall.ifc", b"regular", scope=scope)
    _put(member_client, "versions/main/abc123/model.glb", b"ci", scope=scope)

    r = member_client.delete(f"/api/scopes/project:{PROJECT_ID}/blobs/{ASSET_JSON}")
    assert r.status_code == 200, r.text
    assert ASSET_JSON in r.json()["deleted"]

    # Everything else in a project scope is still admin-managed.
    r = member_client.delete(f"/api/scopes/project:{PROJECT_ID}/blobs/models/wall.ifc")
    assert r.status_code == 403, r.text

    # versions/ is refused in a project scope too — the carve-out never applies
    # to it, so the personal-scope gate answers first.
    r = member_client.delete(f"/api/scopes/project:{PROJECT_ID}/blobs/versions/main/abc123/model.glb")
    assert r.status_code == 403, r.text

    keys = _keys(member_client, scope)
    assert ASSET_JSON not in keys
    assert {"models/wall.ifc", "versions/main/abc123/model.glb"} <= keys


def test_carve_out_does_not_reach_a_project_the_caller_is_not_in(member_client: TestClient):
    """The delete exemption rides on membership enforced upstream by
    _scope_from_path; a non-member never gets far enough to use it."""
    other = str(uuid.UUID(int=0xDEAD))
    _put(member_client, ASSET_JSON, b"published", scope=Scope.project(other))

    r = member_client.delete(f"/api/scopes/project:{other}/blobs/{ASSET_JSON}")
    assert r.status_code == 403, r.text
    assert ASSET_JSON in _keys(member_client, Scope.project(other))


def test_carve_out_is_delete_only_and_not_shared_scope(member_client: TestClient):
    """Rename / move-to-folder take a destination key, so they stay
    personal-scope-only; and the carve-out is for project scopes, not for the
    shared scope."""
    scope = Scope.project(PROJECT_ID)
    _put(member_client, ASSET_JSON, b"published", scope=scope)
    _put(member_client, ASSET_DB, b"published", scope=Scope.shared())

    r = member_client.post(
        f"/api/scopes/project:{PROJECT_ID}/keys/rename",
        json={
            "old_key": ASSET_JSON,
            "new_key": "assets/collection-a/subject-b/20260825T135553Z/other.json",
        },
    )
    assert r.status_code == 403, r.text

    r = member_client.post(
        f"/api/scopes/project:{PROJECT_ID}/keys/move-to-folder",
        json={"keys": [ASSET_JSON], "folder": "assets/collection-a/subject-b/20260826T090000Z"},
    )
    assert r.status_code == 403, r.text

    r = member_client.delete(f"/api/scopes/shared/blobs/{ASSET_DB}")
    assert r.status_code == 403, r.text

    assert ASSET_JSON in _keys(member_client, scope)
    assert ASSET_DB in _keys(member_client, Scope.shared())
