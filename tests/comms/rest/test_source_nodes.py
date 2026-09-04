"""Source-node change tracking — the route and the repository helpers.

Two paths, the split ``test_db.py`` already uses:

* **Always run** — the no-database behaviour, through the in-process API. This
  is the part worth pinning hardest: a deployment without Postgres must REFUSE
  the question rather than answer it emptily.
* **Live Postgres** (skipped unless ``ADA_TEST_POSTGRES_URL`` is set) — the
  upsert semantics, which are where the subtle bugs are.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-source-nodes-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)

SOURCE = "demo-cad"


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
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


# --- no database: refuse, do not answer emptily -----------------------------


def test_without_a_database_the_route_refuses_rather_than_returning_nothing(app_client):
    """The single most important behaviour here.

    An empty list and "nobody is recording changes" are indistinguishable to a
    consumer deciding whether its exported asset is still current — and reading
    the second as the first means serving stale geometry with confidence. So a
    deployment with no Postgres answers 503.
    """
    r = app_client.get("/api/scopes/shared/source-nodes")
    assert r.status_code == 503
    # The message has to name the cause, because the fix is a deployment change
    # and nothing in the response body would otherwise point at it.
    assert "DATABASE_URL" in r.json()["detail"]


def test_every_shape_of_the_route_refuses_without_a_database(app_client):
    for query in (
        "",
        f"?source={SOURCE}",
        f"?source={SOURCE}&refs=a,b",
        f"?source={SOURCE}&since=2024-01-01T00:00:00Z",
    ):
        r = app_client.get(f"/api/scopes/shared/source-nodes{query}")
        assert r.status_code == 503, query


# --- the scope key ----------------------------------------------------------


def test_the_scope_key_is_the_prefix_not_the_dataclass_repr():
    """Regression: `str(Scope)` is a repr, and a repr is not an identifier.

    Writer and reader would AGREE on the repr, so nothing would look broken --
    until someone adds a field to `Scope` or reorders it, at which point the key
    silently changes shape and every row already stored is orphaned with no
    error anywhere. Found by driving a real export through to a real REST read;
    neither side's own tests could see it, because each was self-consistent.
    """
    from ada.comms.rest.scope import Scope

    shared = Scope.shared()
    assert shared.prefix() == "shared"
    assert str(shared) != shared.prefix()

    project = Scope(kind="project", id="abc-123")
    assert project.prefix() == "projects/abc-123"
    # The repr embeds field names and quoting; the prefix is a path segment.
    assert "kind=" not in project.prefix()


# --- live Postgres ----------------------------------------------------------


async def _fresh_pool():
    pool = await dbm.init_pool(POSTGRES_URL)
    await pool.execute("DELETE FROM source_nodes WHERE scope = 'test-scope'")
    return pool


@needs_postgres
@pytest.mark.asyncio
async def test_migration_creates_the_table_and_its_identity_index():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        cols = {
            r["column_name"]
            for r in await pool.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'source_nodes'"
            )
        }
        assert {"scope", "source", "node_ref", "parent_ref", "last_changed_at", "observed_at"} <= cols
        idx = {
            r["indexname"] for r in await pool.fetch("SELECT indexname FROM pg_indexes WHERE tablename='source_nodes'")
        }
        assert "source_nodes_identity" in idx
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_record_then_read_back():
    pool = await _fresh_pool()
    try:
        t = datetime.datetime(2024, 5, 1, 12, 0, tzinfo=datetime.timezone.utc)
        written = await dbm.record_source_nodes(
            pool,
            scope="test-scope",
            source=SOURCE,
            nodes=[
                {"node_ref": "a", "parent_ref": None, "name": "root", "last_changed_at": t},
                {
                    "node_ref": "b",
                    "parent_ref": "a",
                    "name": "child",
                    "last_changed_at": t,
                    "last_changed_by": "someone",
                },
            ],
        )
        assert written == 2

        one = await dbm.get_source_node(pool, scope="test-scope", source=SOURCE, node_ref="b")
        assert one.parent_ref == "a"
        assert one.last_changed_by == "someone"

        many = await dbm.get_source_nodes(pool, scope="test-scope", source=SOURCE, node_refs=["a", "b", "nope"])
        assert {n.node_ref for n in many} == {"a", "b"}
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_last_changed_at_only_moves_forward():
    """The subtle one, and the reason the upsert uses GREATEST.

    A writer re-observing a node may hold an OLDER cursor than the row does — a
    backfill, a re-run over a wider window, two writers with different windows.
    Letting that overwrite a newer timestamp would report a stale asset as
    current, which is the exact failure this table exists to prevent.
    """
    pool = await _fresh_pool()
    try:
        new = datetime.datetime(2024, 5, 2, tzinfo=datetime.timezone.utc)
        old = datetime.datetime(2024, 5, 1, tzinfo=datetime.timezone.utc)

        await dbm.record_source_nodes(
            pool,
            scope="test-scope",
            source=SOURCE,
            nodes=[{"node_ref": "a", "last_changed_at": new, "last_changed_by": "newer"}],
        )
        await dbm.record_source_nodes(
            pool,
            scope="test-scope",
            source=SOURCE,
            nodes=[{"node_ref": "a", "last_changed_at": old, "last_changed_by": "older"}],
        )

        row = await dbm.get_source_node(pool, scope="test-scope", source=SOURCE, node_ref="a")
        assert row.last_changed_at == new
        # The author travels with the timestamp: keeping "older" beside a
        # timestamp it did not produce would be a row that contradicts itself.
        assert row.last_changed_by == "newer"
        # observed_at is NOT sticky -- it records the latest confirmation, which
        # is what separates "checked recently, unchanged" from "not heard from".
        assert row.observed_at > new
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_a_re_observation_updates_rather_than_accumulates():
    pool = await _fresh_pool()
    try:
        t = datetime.datetime(2024, 5, 1, tzinfo=datetime.timezone.utc)
        for _ in range(3):
            await dbm.record_source_nodes(
                pool, scope="test-scope", source=SOURCE, nodes=[{"node_ref": "a", "last_changed_at": t}]
            )
        n = await pool.fetchval("SELECT COUNT(*) FROM source_nodes WHERE scope='test-scope' AND node_ref='a'")
        # An hourly job must not turn into an append-only log of the same tree.
        assert n == 1
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_changed_since_is_newest_first_and_bounded():
    pool = await _fresh_pool()
    try:
        base = datetime.datetime(2024, 5, 1, tzinfo=datetime.timezone.utc)
        await dbm.record_source_nodes(
            pool,
            scope="test-scope",
            source=SOURCE,
            nodes=[{"node_ref": f"n{i}", "last_changed_at": base + datetime.timedelta(days=i)} for i in range(5)],
        )
        got = await dbm.list_source_nodes_changed_since(
            pool, scope="test-scope", source=SOURCE, since=base + datetime.timedelta(days=1)
        )
        assert [n.node_ref for n in got] == ["n4", "n3", "n2"]

        assert len(await dbm.list_source_nodes_changed_since(pool, scope="test-scope", source=SOURCE, limit=2)) == 2
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_sources_summary_is_per_scope():
    pool = await _fresh_pool()
    try:
        t = datetime.datetime(2024, 5, 1, tzinfo=datetime.timezone.utc)
        await dbm.record_source_nodes(
            pool, scope="test-scope", source=SOURCE, nodes=[{"node_ref": "a", "last_changed_at": t}]
        )
        await dbm.record_source_nodes(
            pool, scope="test-scope", source="other", nodes=[{"node_ref": "a", "last_changed_at": t}]
        )
        summary = {row["source"]: row["nodes"] for row in await dbm.list_source_node_sources(pool, scope="test-scope")}
        # Same ref in two sources is two rows, not a collision -- which is what
        # the `source` column in the identity index is for.
        assert summary == {SOURCE: 1, "other": 1}
    finally:
        await dbm.close_pool(pool)
