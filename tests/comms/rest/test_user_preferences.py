"""Per-user preference documents: ``/api/me/preferences/{key}``.

The route tests stub the DB functions; the live-Postgres test (skipped unless
ADA_TEST_POSTGRES_URL is set) runs the real queries and migration.
"""

from __future__ import annotations

import os
import tempfile
import uuid

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import db as dbm
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.routes import preferences as prefs_routes

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")


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
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


@pytest.fixture
def stored(monkeypatch):
    """Stub the three queries with a dict keyed by (sub, key)."""
    rows: dict[tuple[str, str], object] = {}

    async def get(pool, sub, key):
        return rows.get((sub, key))

    async def put(pool, sub, key, value):
        rows[(sub, key)] = value

    async def delete(pool, sub, key):
        return rows.pop((sub, key), None) is not None

    monkeypatch.setattr(dbm, "get_user_preference", get)
    monkeypatch.setattr(dbm, "set_user_preference", put)
    monkeypatch.setattr(dbm, "delete_user_preference", delete)
    return rows


def test_a_preference_round_trips_for_the_caller_only(tmp_path, stored):
    doc = {"v": 1, "docs": {"classic": {"floats": {"a": {"x": 1}}}}, "customisablePanels": True}
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        assert c.get("/api/me/preferences/layout").json() == {"key": "layout", "value": None}
        r = c.put("/api/me/preferences/layout", json={"value": doc})
        assert r.status_code == 200, r.text
        assert c.get("/api/me/preferences/layout").json() == {"key": "layout", "value": doc}
        # Stored under the token's user (auth off: the local dev user), not anything in the path.
        assert list(stored) == [("local-dev", "layout")]

        assert c.put("/api/me/preferences/layout", json={"value": None}).json() == {"key": "layout", "value": None}
        assert stored == {}
        c.put("/api/me/preferences/layout", json={"value": [1, 2]})
        assert c.delete("/api/me/preferences/layout").json() == {"removed": True}
        assert c.delete("/api/me/preferences/layout").json() == {"removed": False}


def test_bad_keys_and_bodies_are_refused(tmp_path, stored):
    with TestClient(create_app(_settings(tmp_path))) as c:
        c.app.state.db_pool = object()
        for key in ("-lead", "a%20b", "x" * 129):
            assert c.get(f"/api/me/preferences/{key}").status_code == 400, key
        assert c.put("/api/me/preferences/layout", json={"other": 1}).status_code == 400
        assert c.put("/api/me/preferences/layout", json=[1]).status_code == 400
        assert (
            c.put("/api/me/preferences/layout", content=b"not json", headers={"Content-Type": "application/json"})
        ).status_code == 400
        big = "x" * (prefs_routes.MAX_PREFERENCE_BYTES + 1)
        assert c.put("/api/me/preferences/layout", json={"value": big}).status_code == 413
        assert stored == {}


def test_without_a_database_the_routes_answer_503(tmp_path, stored):
    with TestClient(create_app(_settings(tmp_path))) as c:
        assert c.get("/api/me/preferences/layout").status_code == 503
        assert c.put("/api/me/preferences/layout", json={"value": 1}).status_code == 503
        assert c.delete("/api/me/preferences/layout").status_code == 503


class _RecordingConn:
    def __init__(self, log: list):
        self.log = log

    def transaction(self):
        log = self.log

        class _Tx:
            async def __aenter__(self):
                log.append("BEGIN")

            async def __aexit__(self, *exc):
                log.append("COMMIT")

        return _Tx()

    async def execute(self, sql, *args):
        self.log.append((" ".join(sql.split()), args))


class _RecordingPool:
    def __init__(self):
        self.log: list = []

    def acquire(self):
        conn = _RecordingConn(self.log)

        class _Acquire:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return None

        return _Acquire()


@pytest.mark.asyncio
async def test_saving_a_preference_first_makes_sure_the_user_row_exists():
    """The foreign key to ``users`` (deleted with the user) needs the row before the preference,
    in the same transaction: the placeholder insert ``add_project_member`` also makes."""
    pool = _RecordingPool()
    await dbm.set_user_preference(pool, "u1", "layout", {"v": 1})
    log = pool.log
    assert log[0] == "BEGIN" and log[-1] == "COMMIT"
    assert log[1] == ("INSERT INTO users (sub) VALUES ($1) ON CONFLICT (sub) DO NOTHING", ("u1",))
    assert log[2][0].startswith("INSERT INTO user_preferences") and log[2][1] == ("u1", "layout", '{"v": 1}')


@needs_postgres
@pytest.mark.asyncio
async def test_preferences_against_postgres():
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    sub = f"pref-{uuid.uuid4().hex[:12]}"
    other = f"{sub}-other"
    try:
        assert await dbm.get_user_preference(pool, sub, "layout") is None
        await dbm.set_user_preference(pool, sub, "layout", {"v": 1, "docs": {}})
        await dbm.set_user_preference(pool, other, "layout", {"v": 1, "docs": {"x": {}}})
        assert await dbm.get_user_preference(pool, sub, "layout") == {"v": 1, "docs": {}}
        await dbm.set_user_preference(pool, sub, "layout", {"v": 1, "customisablePanels": True, "docs": {}})
        assert (await dbm.get_user_preference(pool, sub, "layout"))["customisablePanels"] is True
        await dbm.set_user_preference(pool, sub, "scalar", "text")
        assert await dbm.get_user_preference(pool, sub, "scalar") == "text"
        assert await dbm.delete_user_preference(pool, sub, "layout") is True
        assert await dbm.delete_user_preference(pool, sub, "layout") is False
        assert await dbm.get_user_preference(pool, other, "layout") == {"v": 1, "docs": {"x": {}}}

        # Saving created a placeholder user; deleting the user deletes its preferences.
        assert await pool.fetchval("SELECT count(*) FROM users WHERE sub = $1", other) == 1
        await pool.execute("DELETE FROM users WHERE sub = $1", other)
        assert await dbm.get_user_preference(pool, other, "layout") is None
        assert await dbm.get_user_preference(pool, sub, "scalar") == "text"
    finally:
        await pool.execute("DELETE FROM users WHERE sub = ANY($1::text[])", [sub, other])
        await dbm.close_pool(pool)
