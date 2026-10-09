"""Per-token CLI bearer records: mint records, verify checks, admins list and revoke.

The always-on tests stub the DB functions; the live-Postgres test (skipped
unless ADA_TEST_POSTGRES_URL is set) runs the real queries and migration.
"""

from __future__ import annotations

import os
import tempfile
import time
import uuid
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import jwt
import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import auth as auth_module
from ada.comms.rest import db as dbm
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings

SECRET = "cli-token-test-secret-32-bytes-long"
POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)

ALICE = auth_module.User(
    sub="alice",
    email="alice@example.invalid",
    display_name="Alice",
    groups=frozenset({"g1"}),
    is_admin=True,
)


def _auth_config() -> AuthConfig:
    return AuthConfig(
        enabled=False,
        issuer="",
        client_id="",
        audience="",
        admin_group="",
        cli_token_secret=SECRET,
    )


def _settings(tmp_path, *, db_url: str = "") -> Settings:
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
        auth=_auth_config(),
        database_url=db_url,
    )


def _request(pool) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db_pool=pool)))


# ── mint ─────────────────────────────────────────────────────────────────────


def test_each_minted_token_has_its_own_jti():
    a, _ = auth_module.mint_cli_token(ALICE, _auth_config())
    b, _ = auth_module.mint_cli_token(ALICE, _auth_config())
    ja = jwt.decode(a, SECRET, algorithms=["HS256"], issuer="ada-viewer-cli")["jti"]
    jb = jwt.decode(b, SECRET, algorithms=["HS256"], issuer="ada-viewer-cli")["jti"]
    assert ja and jb and ja != jb


@pytest.mark.asyncio
async def test_record_stores_claims_and_the_tail_as_hint(monkeypatch):
    """The hint is the token's END: every token starts with the same JWT header,
    so a prefix could not tell two apart."""
    token, exp = auth_module.mint_cli_token(ALICE, _auth_config())
    other, _ = auth_module.mint_cli_token(ALICE, _auth_config())
    assert token[:20] == other[:20]

    inserted = {}

    async def fake_insert(pool, **kw):
        inserted.update(kw)

    monkeypatch.setattr(dbm, "insert_cli_token", fake_insert)
    record = await auth_module.record_cli_token(object(), token, label="laptop", issued_by="alice")

    assert record["hint"] == token[-8:]
    assert inserted["jti"] == record["jti"]
    assert inserted["sub"] == "alice"
    assert inserted["is_admin"] is True
    assert inserted["label"] == "laptop"
    assert inserted["issued_by"] == "alice"
    assert inserted["expires_at"] == exp
    assert token not in inserted.values()


@pytest.mark.asyncio
async def test_record_without_a_pool_stores_nothing(monkeypatch):
    async def boom(pool, **kw):
        raise AssertionError("must not insert without a pool")

    monkeypatch.setattr(dbm, "insert_cli_token", boom)
    token, _ = auth_module.mint_cli_token(ALICE, _auth_config())
    record = await auth_module.record_cli_token(None, token, label=None, issued_by=None)
    assert record["hint"] == token[-8:]


# ── verify ───────────────────────────────────────────────────────────────────


@pytest.fixture
def token_state(monkeypatch):
    """Stub the two lookups verification makes; returns the jti → state map."""
    states: dict[str, str] = {}
    checked: list[str] = []

    async def fake_get_setting(pool, key):
        return None

    async def fake_check(pool, jti):
        checked.append(jti)
        return states.get(jti, "unknown")

    monkeypatch.setattr(dbm, "get_setting", fake_get_setting)
    monkeypatch.setattr(dbm, "check_cli_token", fake_check)
    return SimpleNamespace(states=states, checked=checked)


def _jti(token: str) -> str:
    return jwt.decode(token, options={"verify_signature": False})["jti"]


@pytest.mark.asyncio
async def test_active_token_is_accepted(token_state):
    token, _ = auth_module.mint_cli_token(ALICE, _auth_config())
    token_state.states[_jti(token)] = "active"
    user = await auth_module._verify_cli_token(_request(object()), token, _auth_config())
    assert user.sub == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize("state, detail", [("revoked", "token revoked"), ("unknown", "token not recognised")])
async def test_revoked_or_unrecorded_token_is_refused(token_state, state, detail):
    token, _ = auth_module.mint_cli_token(ALICE, _auth_config())
    if state != "unknown":
        token_state.states[_jti(token)] = state
    with pytest.raises(auth_module.TokenError) as exc:
        await auth_module._verify_cli_token(_request(object()), token, _auth_config())
    assert exc.value.detail == detail


@pytest.mark.asyncio
async def test_token_from_before_tracking_is_still_accepted(token_state):
    """No jti means minted before migration 034: the cutoff alone governs it,
    so an existing CI bot keeps working through the upgrade."""
    now = int(time.time())
    legacy = jwt.encode(
        {"iss": "ada-viewer-cli", "sub": "ci:demo", "iat": now, "exp": now + 60},
        SECRET,
        algorithm="HS256",
    )
    user = await auth_module._verify_cli_token(_request(object()), legacy, _auth_config())
    assert user.sub == "ci:demo"
    assert token_state.checked == []


@pytest.mark.asyncio
async def test_revoke_all_also_marks_the_rows(monkeypatch):
    calls = {}

    async def fake_set_setting(pool, key, value, *, updated_by):
        calls["cutoff"] = (key, updated_by)

    async def fake_revoke_for_sub(pool, sub, *, revoked_by):
        calls["rows"] = (sub, revoked_by)
        return 2

    monkeypatch.setattr(dbm, "set_setting", fake_set_setting)
    monkeypatch.setattr(dbm, "revoke_cli_tokens_for_sub", fake_revoke_for_sub)
    await auth_module.revoke_cli_tokens(object(), ALICE, revoked_by="admin")
    assert calls["cutoff"] == ("cli_token_revoke_at:alice", "admin")
    assert calls["rows"] == ("alice", "admin")


# ── routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    calls: dict = {"inserted": [], "revoked": []}
    rows = {"j1": {"jti": "j1", "sub": "alice", "hint": "abcd1234"}}

    async def fake_insert(pool, **kw):
        calls["inserted"].append(kw)

    async def fake_list(pool, *, include_inactive=False, sub=None, limit=500):
        calls["listed"] = {"include_inactive": include_inactive, "sub": sub}
        return list(rows.values())

    async def fake_tracked_since(pool):
        return "2026-10-09T00:00:00+00:00"

    async def fake_revoke(pool, jti, *, revoked_by):
        calls["revoked"].append((jti, revoked_by))
        return {**rows[jti], "revoked_by": revoked_by} if jti in rows else None

    monkeypatch.setattr(dbm, "insert_cli_token", fake_insert)
    monkeypatch.setattr(dbm, "list_cli_tokens", fake_list)
    monkeypatch.setattr(dbm, "cli_tokens_tracked_since", fake_tracked_since)
    monkeypatch.setattr(dbm, "revoke_cli_token", fake_revoke)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        yield client, calls


def test_mint_records_the_token_with_its_label(admin_client):
    client, calls = admin_client
    r = client.post("/api/admin/auth/cli-token", json={"label": "  build laptop  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hint"] == body["token"][-8:]
    [row] = calls["inserted"]
    assert row["jti"] == body["jti"]
    assert row["label"] == "build laptop"
    assert row["issued_by"] == "local-dev"


def test_mint_without_a_body_still_works(admin_client):
    """The endpoint predates its body; existing callers POST nothing."""
    client, calls = admin_client
    r = client.post("/api/admin/auth/cli-token")
    assert r.status_code == 200, r.text
    assert calls["inserted"][0]["label"] is None


def test_mint_refuses_an_overlong_label(admin_client):
    client, calls = admin_client
    r = client.post("/api/admin/auth/cli-token", json={"label": "x" * 121})
    assert r.status_code == 400
    assert calls["inserted"] == []


def test_list_returns_tokens_and_when_tracking_started(admin_client):
    client, calls = admin_client
    r = client.get("/api/admin/auth/cli-tokens", params={"include_inactive": "true", "sub": "alice"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["jti"] for t in body["tokens"]] == ["j1"]
    assert body["tracked_since"] == "2026-10-09T00:00:00+00:00"
    assert calls["listed"] == {"include_inactive": True, "sub": "alice"}


def test_revoke_one_token(admin_client):
    client, calls = admin_client
    r = client.post("/api/admin/auth/cli-tokens/j1/revoke")
    assert r.status_code == 200, r.text
    assert r.json()["revoked_by"] == "local-dev"
    assert calls["revoked"] == [("j1", "local-dev")]


def test_revoke_unknown_token_is_404(admin_client):
    client, _ = admin_client
    r = client.post("/api/admin/auth/cli-tokens/nope/revoke")
    assert r.status_code == 404


def test_token_routes_are_admin_only(tmp_path, monkeypatch):
    monkeypatch.setattr(
        auth_module.User,
        "local_dev",
        classmethod(lambda cls: cls(sub="u", email="", display_name="U", groups=frozenset(), is_admin=False)),
    )
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/admin/auth/cli-tokens").status_code == 403
        assert client.post("/api/admin/auth/cli-tokens/j1/revoke").status_code == 403


# ── live Postgres ────────────────────────────────────────────────────────────


@needs_postgres
@pytest.mark.asyncio
async def test_token_lifecycle_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    sub = f"tok-{uuid.uuid4().hex[:12]}"
    user = auth_module.User(sub=sub, email="", display_name=sub, groups=frozenset(), is_admin=False)
    try:
        first, _ = auth_module.mint_cli_token(user, _auth_config())
        second, _ = auth_module.mint_cli_token(user, _auth_config())
        r1 = await auth_module.record_cli_token(pool, first, label="one", issued_by="admin")
        r2 = await auth_module.record_cli_token(pool, second, label="two", issued_by="admin")

        listed = await dbm.list_cli_tokens(pool, sub=sub)
        # Both were minted in the same second, so issued_at ties; compare as sets.
        assert {t["jti"] for t in listed} == {r1["jti"], r2["jti"]}
        assert {t["hint"] for t in listed} == {first[-8:], second[-8:]}
        assert await dbm.cli_tokens_tracked_since(pool) is not None

        assert await dbm.check_cli_token(pool, r1["jti"]) == "active"
        assert await dbm.check_cli_token(pool, "missing") == "unknown"

        row = await dbm.revoke_cli_token(pool, r1["jti"], revoked_by="admin")
        assert row["revoked_by"] == "admin" and row["revoked_at"]
        assert await dbm.check_cli_token(pool, r1["jti"]) == "revoked"
        assert await dbm.check_cli_token(pool, r2["jti"]) == "active"
        assert [t["jti"] for t in await dbm.list_cli_tokens(pool, sub=sub)] == [r2["jti"]]
        assert len(await dbm.list_cli_tokens(pool, sub=sub, include_inactive=True)) == 2

        assert await dbm.revoke_cli_tokens_for_sub(pool, sub, revoked_by="admin") == 1
        assert await dbm.check_cli_token(pool, r2["jti"]) == "revoked"
    finally:
        await pool.execute("DELETE FROM cli_tokens WHERE sub = $1", sub)
        await dbm.close_pool(pool)
