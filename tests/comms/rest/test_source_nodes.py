"""Source-node change tracking — the routes, the repository helpers, and the
worker-side recorders that write through them.

Two paths, the split ``test_db.py`` already uses:

* **Always run** — the no-database behaviour, through the in-process API. This
  is the part worth pinning hardest: a deployment without Postgres must REFUSE
  the question rather than answer it emptily. The worker-side REST recorder is
  here too, exercised against a stubbed transport, because none of what makes
  it correct (which recorder gets chosen, how it keys a scope, what it refuses
  to serialise) needs a database to check.
* **Live Postgres** (skipped unless ``ADA_TEST_POSTGRES_URL`` is set) — the
  upsert semantics and the write route's validation, which are where the
  subtle bugs are.
"""

from __future__ import annotations

import dataclasses
import datetime
import io
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


def test_the_write_route_refuses_without_a_database_too(app_client):
    """Symmetric with the read, and for the same reason.

    A writer that gets 2xx for a write nothing stored would carry on with its
    cursor advanced, and the rows it thought it had recorded would never exist.
    The deployment fact is reported ahead of any complaint about the body,
    because the body is not what is wrong.
    """
    r = app_client.post(
        "/api/scopes/shared/source-nodes",
        json={"source": SOURCE, "nodes": [{"node_ref": "a", "last_changed_at": "2024-05-01T12:00:00Z"}]},
    )
    assert r.status_code == 503
    assert "DATABASE_URL" in r.json()["detail"]


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


# --- the write route --------------------------------------------------------
#
# Validated against a STUBBED pool rather than a live one. Everything the route
# itself decides -- what it refuses, how it keys a scope, what reaches the
# repository -- is above the database, and gating it behind
# ADA_TEST_POSTGRES_URL would mean the whole route went unexercised in the
# environment that normally runs this suite. The live round trip below still
# needs a real database, and still says so.


@pytest.fixture
def write_client(tmp_path: pathlib.Path, monkeypatch):
    """A client whose app believes it has a pool, with the repository stubbed.

    Yields ``(client, calls)``; each recorded call is the kwargs the route
    passed to ``record_source_nodes``.
    """
    from ada.comms.rest import db as db_mod

    calls: list[dict] = []

    async def fake_record(pool, *, scope, source, nodes):
        calls.append({"scope": scope, "source": source, "nodes": nodes})
        return len(nodes)

    monkeypatch.setattr(db_mod, "record_source_nodes", fake_record)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        # The one thing the route reads to decide whether it can answer at all.
        client.app.state.db_pool = object()
        yield client, calls


def test_a_posted_node_reaches_the_repository_keyed_by_the_scope_prefix(write_client):
    """prefix(), never str(). The reader keys rows the same way, and a writer
    that disagreed would fill an invisible second half of the table -- the bug
    this feature already had once, in the other direction."""
    client, calls = write_client
    r = client.post(
        "/api/scopes/shared/source-nodes",
        json={
            "source": "pytest-src",
            "nodes": [{"node_ref": "x", "name": "root", "last_changed_at": "2024-05-01T12:00:00Z"}],
        },
    )
    assert r.status_code == 200
    assert r.json() == {"scope": "shared", "source": "pytest-src", "recorded": 1}

    (call,) = calls
    assert call["scope"] == "shared"
    node = call["nodes"][0]
    # Parsed into a real aware datetime, not passed through as a string -- the
    # column is timestamptz and asyncpg would not take the text.
    assert node["last_changed_at"] == datetime.datetime(2024, 5, 1, 12, 0, tzinfo=datetime.timezone.utc)
    assert node["parent_ref"] is None and node["name"] == "root"


def test_a_timestamp_without_an_offset_is_refused_by_the_route(write_client):
    """The validation worth having, because its failure is PERMANENT.

    ``last_changed_at`` only moves forward (GREATEST in the upsert). A writer
    posting local wall-clock read as UTC lands the row in the future, and no
    later correct observation can pull it back -- every consumer then believes
    its export is stale forever. Cheap to refuse, unfixable to accept.
    """
    client, calls = write_client
    r = client.post(
        "/api/scopes/shared/source-nodes",
        json={"source": "pytest-src", "nodes": [{"node_ref": "x", "last_changed_at": "2024-05-01T12:00:00"}]},
    )
    assert r.status_code == 400
    assert "offset" in r.json()["detail"]
    # And nothing partial was written on the way to refusing.
    assert calls == []


def test_the_write_route_rejects_a_malformed_body(write_client):
    client, calls = write_client
    bad = [
        ({"nodes": []}, "source"),
        ({"source": "s", "nodes": "no"}, "nodes"),
        ({"source": "s", "nodes": [{"last_changed_at": "2024-05-01T12:00:00Z"}]}, "node_ref"),
        ({"source": "s", "nodes": [{"node_ref": "x"}]}, "last_changed_at"),
        ({"source": "s", "nodes": [{"node_ref": "x", "last_changed_at": "not-a-time"}]}, "ISO-8601"),
    ]
    for body, expected in bad:
        r = client.post("/api/scopes/shared/source-nodes", json=body)
        assert r.status_code == 400, body
        assert expected in r.json()["detail"], body
    assert calls == []


def test_an_oversized_batch_is_refused_with_the_cap_named(write_client):
    """A roll-up is naturally thousands of rows, so the writer must be told to
    chunk rather than left to discover a proxy's limit."""
    client, _ = write_client
    nodes = [{"node_ref": f"n{i}", "last_changed_at": "2024-05-01T12:00:00Z"} for i in range(10001)]
    r = client.post("/api/scopes/shared/source-nodes", json={"source": "s", "nodes": nodes})
    assert r.status_code == 400
    assert "10000" in r.json()["detail"]


def test_an_empty_sweep_is_accepted_rather_than_refused(write_client):
    """ "Nothing changed" is a normal outcome. Making the writer special-case it
    invites the writer to skip the call, which loses the "I ran" signal."""
    client, _ = write_client
    r = client.post("/api/scopes/shared/source-nodes", json={"source": "pytest-src", "nodes": []})
    assert r.status_code == 200
    assert r.json()["recorded"] == 0


# --- the write route, against a real database -------------------------------


@pytest.fixture
def pg_client(tmp_path: pathlib.Path):
    if not POSTGRES_URL:
        pytest.skip("ADA_TEST_POSTGRES_URL not set")
    app = create_app(dataclasses.replace(_settings(tmp_path), database_url=POSTGRES_URL))
    with TestClient(app) as client:
        yield client


@needs_postgres
def test_posted_nodes_read_back_through_the_get(pg_client):
    """The round trip that neither side's own tests can see.

    The reader-vs-writer key bug this table already had was invisible to both
    halves in isolation, because each was self-consistent. A write posted
    through the API and read back through the API is the shape that catches it.
    """
    pg_client.post(
        "/api/scopes/shared/source-nodes",
        json={
            "source": "pytest-src",
            "nodes": [
                {"node_ref": "x", "name": "root", "last_changed_at": "2024-05-01T12:00:00Z"},
                {"node_ref": "y", "parent_ref": "x", "last_changed_at": "2024-05-01T13:00:00+02:00"},
            ],
        },
    ).raise_for_status()

    got = pg_client.get("/api/scopes/shared/source-nodes?source=pytest-src&refs=x,y,zz").json()
    assert {n["node_ref"] for n in got["nodes"]} == {"x", "y"}
    # A ref nobody recorded is named, not merely absent -- see the route.
    assert got["unknown"] == ["zz"]


# --- the worker-side recorders ----------------------------------------------
#
# Which recorder a worker offers a plugin, and what the REST one puts on the
# wire. No database and no server: the transport is stubbed, because what makes
# this correct is the choosing and the serialising, not the HTTP.


@pytest.fixture
def worker_mod():
    from ada.comms.rest import worker as wk

    return wk


@pytest.fixture
def shared_scope():
    from ada.comms.rest.scope import Scope

    return Scope.shared()


def _api_env(monkeypatch, url="https://viewer.example", token="tok"):
    monkeypatch.setenv("ADA_VIEWER_API_URL", url)
    monkeypatch.setenv("ADA_VIEWER_TOKEN", token)


def test_a_worker_with_no_pool_and_no_credentials_offers_no_recorder(worker_mod, shared_scope, monkeypatch):
    """And that stays a supported outcome, not an error.

    A plugin is written to handle the parameter being ABSENT -- it reports that
    it cannot record rather than failing at its first write -- so a worker
    configured for neither path must produce exactly that, not a recorder that
    throws.
    """
    monkeypatch.delenv("ADA_VIEWER_API_URL", raising=False)
    monkeypatch.delenv("ADA_VIEWER_TOKEN", raising=False)
    assert worker_mod._source_nodes_recorder(None, shared_scope, None) is None


def test_credentials_without_a_pool_select_the_rest_recorder(worker_mod, shared_scope, monkeypatch):
    _api_env(monkeypatch)
    rec = worker_mod._source_nodes_recorder(None, shared_scope, None)
    assert isinstance(rec, worker_mod._RestSourceNodesRecorder)


def test_a_pool_wins_over_credentials(worker_mod, shared_scope, monkeypatch):
    """The direct path where it exists. An in-cluster worker holds both, and
    routing its writes through HTTP back into the service it is part of would
    add a hop, a token and a failure mode for nothing."""
    _api_env(monkeypatch)
    rec = worker_mod._source_nodes_recorder(object(), shared_scope, None)
    assert isinstance(rec, worker_mod._SyncSourceNodesFacade)


def test_a_non_http_api_url_disables_recording_loudly(worker_mod, monkeypatch, caplog):
    _api_env(monkeypatch, url="viewer.example")

    # caplog's handler sits on the ROOT logger, and adapy's `ada` logger does
    # not propagate there -- so `caplog.at_level` alone records nothing even
    # while the warning is plainly on stderr. Attach to the logger that emits.
    from ada.config import logger as ada_logger

    ada_logger.addHandler(caplog.handler)
    try:
        assert worker_mod._rest_source_nodes_config() is None
    finally:
        ada_logger.removeHandler(caplog.handler)

    # Whoever set that variable meant to enable recording; falling back to
    # silence is the failure this path exists to remove.
    assert any("ADA_VIEWER_API_URL" in r.getMessage() for r in caplog.records)


def test_both_recorders_key_a_scope_the_same_way(worker_mod, shared_scope):
    """Two recorders writing the same table must agree on the key, or the
    second one writes an invisible half of it."""
    rest = worker_mod._RestSourceNodesRecorder("https://viewer.example", "tok", shared_scope)
    pool_facade = worker_mod._SyncSourceNodesFacade(object(), shared_scope, None)
    assert rest.scope == pool_facade.scope == shared_scope.prefix()


def test_the_client_refuses_a_naive_timestamp_before_it_reaches_the_wire(worker_mod):
    """Same rule as the route, enforced at the writer, where the node that
    caused it can still be named."""
    with pytest.raises(ValueError, match="timezone"):
        worker_mod._RestSourceNodesRecorder._node_json(
            {"node_ref": "x", "last_changed_at": datetime.datetime(2024, 5, 1, 12, 0)}
        )


def test_the_client_omits_absent_optional_fields(worker_mod):
    t = datetime.datetime(2024, 5, 1, 12, 0, tzinfo=datetime.timezone.utc)
    out = worker_mod._RestSourceNodesRecorder._node_json({"node_ref": "x", "last_changed_at": t, "name": None})
    assert out == {"node_ref": "x", "last_changed_at": t.isoformat()}


def test_a_large_roll_up_is_posted_in_chunks(worker_mod, shared_scope):
    """A roll-up stamps every node ABOVE a changed leaf, so the natural batch is
    thousands of rows -- one request for all of them is a proxy-sized surprise.
    The counts still sum to what was written."""
    rec = worker_mod._RestSourceNodesRecorder("https://viewer.example", "tok", shared_scope)
    t = datetime.datetime(2024, 5, 1, 12, 0, tzinfo=datetime.timezone.utc)
    nodes = [{"node_ref": f"n{i}", "last_changed_at": t} for i in range(5000)]

    sent = []

    def fake_request(url, payload=None):
        sent.append(payload)
        return {"recorded": len(payload["nodes"])}

    rec._request = fake_request
    assert rec.record("src", nodes) == 5000
    assert [len(p["nodes"]) for p in sent] == [2000, 2000, 1000]
    assert all(p["source"] == "src" for p in sent)


def test_reads_are_batched_by_url_length_not_by_count(worker_mod, shared_scope):
    """Refs travel in a query string, and proxies drop a long one long before
    the server would object -- so the budget is characters, not rows."""
    rec = worker_mod._RestSourceNodesRecorder("https://viewer.example", "tok", shared_scope)
    urls = []

    def fake_request(url, payload=None):
        urls.append(url)
        return {"nodes": [{"node_ref": "a", "last_changed_at": "2024-05-01T12:00:00+00:00"}]}

    rec._request = fake_request
    got = rec.get("src", [f"ref-{i:04d}-{'x' * 40}" for i in range(300)])

    assert len(urls) > 1
    assert all(len(u) < 4096 for u in urls)
    # And what comes back is the same type the pool facade returns, so a plugin
    # cannot tell the two recorders apart by what they hand it.
    assert all(isinstance(n, dbm.SourceNode) for n in got)
    assert got[0].last_changed_at.tzinfo is not None


def test_a_refused_request_is_not_retried_but_a_server_error_is(worker_mod, shared_scope, monkeypatch):
    """Retrying is safe (the write is an idempotent upsert) and worth it for a
    scheduled sweep over a link nobody controls -- but a 4xx will be just as
    wrong next time, and burning the budget on it only delays the message."""
    import urllib.error

    rec = worker_mod._RestSourceNodesRecorder("https://viewer.example", "tok", shared_scope)
    monkeypatch.setattr(worker_mod.time, "sleep", lambda *_: None)

    calls = {"n": 0}

    def raise_status(code):
        def _open(req, timeout=None):
            calls["n"] += 1
            raise urllib.error.HTTPError(req.full_url, code, "nope", {}, io.BytesIO(b"detail"))

        return _open

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", raise_status(400))
    with pytest.raises(RuntimeError, match="refused"):
        rec._request("https://viewer.example/api/scopes/shared/source-nodes", {"source": "s", "nodes": []})
    assert calls["n"] == 1

    calls["n"] = 0
    monkeypatch.setattr(urllib.request, "urlopen", raise_status(503))
    with pytest.raises(RuntimeError, match="503"):
        rec._request("https://viewer.example/api/scopes/shared/source-nodes", {"source": "s", "nodes": []})
    assert calls["n"] == worker_mod._RestSourceNodesRecorder._ATTEMPTS


def test_the_cursor_read_asks_for_no_source_and_parses_its_timestamps(worker_mod, shared_scope):
    """``sources()`` is the cursor a scanning writer needs BEFORE it knows any refs.

    ``get`` answers "is this node current", which needs refs the caller already
    holds. A sweep asking "how far did I get" has none, and without this it must
    re-read a fixed window every run — wasteful, and unsound, because a window is
    a guess about the gap between runs that a missed run makes wrong.
    """
    rec = worker_mod._RestSourceNodesRecorder("https://viewer.example", "tok", shared_scope)
    urls = []

    def fake_request(url, payload=None):
        urls.append(url)
        return {
            "sources": [
                {
                    "source": "e3d",
                    "nodes": 1234,
                    "last_changed_at": "2026-09-09T09:53:46+00:00",
                    "observed_at": "2026-09-09T09:53:47Z",
                }
            ]
        }

    rec._request = fake_request
    got = rec.sources()

    # No `source` and no `refs`: that is the shape the route answers with the
    # per-source summary rather than with nodes.
    assert urls == [rec._url()]
    assert "source=" not in urls[0] and "refs=" not in urls[0]

    assert got[0]["source"] == "e3d"
    assert got[0]["nodes"] == 1234
    # Parsed back to aware datetimes, like every other value these recorders
    # hand back — a plugin must not be able to tell the two apart by type.
    assert got[0]["last_changed_at"].tzinfo is not None
    assert got[0]["observed_at"].tzinfo is not None, "a trailing Z must parse too"


def test_an_unreadable_cursor_reads_as_absent_rather_than_failing_the_sweep(worker_mod, shared_scope):
    """None means "no cursor", and the caller then cold-starts.

    That is the safe direction: a cold start over-reads, where refusing the sweep
    loses the run entirely and a guessed timestamp could skip changes.
    """
    rec = worker_mod._RestSourceNodesRecorder("https://viewer.example", "tok", shared_scope)
    rec._request = lambda url, payload=None: {
        "sources": [{"source": "e3d", "nodes": 1, "last_changed_at": "not a timestamp", "observed_at": None}]
    }
    assert rec.sources()[0]["last_changed_at"] is None


def test_both_recorders_expose_the_same_cursor_surface(worker_mod, shared_scope):
    """The pool facade and the HTTP one are one surface with two backings. A
    method on only one of them is a plugin that works on a worker with a database
    and silently does less on a worker without one."""
    rest = worker_mod._RestSourceNodesRecorder("https://viewer.example", "tok", shared_scope)
    pool_facade = worker_mod._SyncSourceNodesFacade(object(), shared_scope, None)
    for name in ("record", "get", "sources", "scope"):
        assert hasattr(rest, name), f"REST recorder is missing {name}"
        assert hasattr(pool_facade, name), f"pool facade is missing {name}"
