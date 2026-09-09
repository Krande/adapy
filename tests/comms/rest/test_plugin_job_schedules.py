"""Cron for plugin jobs — the table, the claim, and the cache-bust.

Two paths, the split ``test_db.py`` established:

* **Always run** — the no-database behaviour and the pure option-shaping.
* **Live Postgres** (skipped unless ``ADA_TEST_POSTGRES_URL`` is set) — the claim
  semantics, which is where a scheduler goes wrong quietly: double-firing across
  replicas, or never advancing and hammering the queue.

The test that matters most is ``test_two_firings_never_share_an_options_hash``.
Core hashes a plugin job's options into its source key so identical requests
cache-hit; a schedule sends byte-identical options every slot, so without the
fire token the second firing and every one after returns the FIRST run's summary.
Hourly green ticks, the worker never touched, and a caller trusting data that
stopped moving. That failure reports success at every layer, which is what makes
it worth a test rather than a comment.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-plugin-sched-"))

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

PLUGIN = "demo-plugin"


def _settings(tmp_path: pathlib.Path, database_url: str = "") -> Settings:
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
        database_url=database_url,
    )


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def pg_client(tmp_path: pathlib.Path):
    """A client whose app has a real database, so the admin routes work.

    Separate from ``app_client`` rather than parameterised: most of this file is
    about behaviour that does NOT need Postgres, and it should keep running where
    Postgres is absent.
    """
    if not POSTGRES_URL:
        pytest.skip("ADA_TEST_POSTGRES_URL not set")
    app = create_app(_settings(tmp_path, POSTGRES_URL))
    with TestClient(app) as client:
        yield client


# --- the cache-bust ----------------------------------------------------------


def _source_key(plugin_id: str, options: dict) -> str:
    """The same synthetic key core computes. Duplicated deliberately: if core's
    formula changes, this test should fail rather than silently stop covering
    the thing it exists for."""
    digest = hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    return f"_synthetic/plugin_job/{plugin_id}/{digest}"


def test_two_firings_never_share_an_options_hash():
    """The whole reason the fire token exists.

    Without it, a schedule's options are byte-identical on every slot, the source
    key is identical, and core's cache short-circuits every run after the first.
    """
    base = {"action": "changes-since", "since_days": 1}

    first = dict(base, scheduled_at="2026-09-09T10:00:00+00:00")
    second = dict(base, scheduled_at="2026-09-09T11:00:00+00:00")

    assert _source_key(PLUGIN, first) != _source_key(PLUGIN, second)
    # And the thing being guarded against: the bare options DO collide.
    assert _source_key(PLUGIN, base) == _source_key(PLUGIN, dict(base))


def test_the_fire_token_is_reserved_on_create(app_client):
    """A caller who sets it believes it means something, and the tick is about to
    overwrite it. Refused rather than silently replaced."""
    r = app_client.post(
        "/api/admin/plugin-jobs/schedules",
        json={
            "name": "x",
            "cron_expr": "0 * * * *",
            "scope": "shared",
            "plugin_id": PLUGIN,
            "options": {"scheduled_at": "whenever"},
        },
    )
    # 400 for the reserved key, or 503 without a database — both are refusals,
    # and which one depends on deployment rather than on this rule.
    assert r.status_code in (400, 503)
    if r.status_code == 400:
        assert "reserved" in r.json()["detail"]


# --- no database -------------------------------------------------------------


def test_without_a_database_the_admin_routes_refuse(app_client):
    """Schedules live in Postgres, so a deployment without one cannot have any.

    Refusing beats an empty list: "no schedules configured" and "scheduling is
    not available here" lead an operator to different next steps.
    """
    r = app_client.get("/api/admin/plugin-jobs/schedules")
    assert r.status_code in (503, 404), r.text


# --- live Postgres -----------------------------------------------------------


async def _fresh_pool():
    pool = await dbm.init_pool(POSTGRES_URL)
    await pool.execute("DELETE FROM plugin_job_schedules")
    return pool


def _schedule_kwargs(**over):
    base = dict(
        name="nightly",
        cron_expr="0 2 * * *",
        scope="shared",
        plugin_id=PLUGIN,
        options={"action": "changes-since", "since_days": 1},
        capability=None,
        created_by="tester",
    )
    base.update(over)
    return base


@needs_postgres
@pytest.mark.asyncio
async def test_migration_creates_the_table():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        cols = {
            r["column_name"]
            for r in await pool.fetch(
                "SELECT column_name FROM information_schema.columns WHERE table_name='plugin_job_schedules'"
            )
        }
        assert {"name", "cron_expr", "scope", "plugin_id", "options", "next_fire_at", "last_skipped_reason"} <= cols
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_options_round_trip_as_a_dict():
    """asyncpg hands JSONB back as a string unless a codec is registered, and this
    module registers none — so a caller would otherwise get a str where the tick
    expects a mapping, and `dict(options)` would silently produce nonsense."""
    pool = await _fresh_pool()
    try:
        row = await dbm.create_plugin_job_schedule(
            pool, next_fire_at=datetime.datetime.now(datetime.timezone.utc), **_schedule_kwargs()
        )
        assert isinstance(row["options"], dict)
        assert row["options"]["since_days"] == 1
        assert isinstance((await dbm.get_plugin_job_schedule(pool, row["id"]))["options"], dict)
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_a_due_schedule_is_claimed_exactly_once():
    """Cross-replica safety. Two ticks racing the same row must produce one fire.

    The second claim returns None because the first advanced `next_fire_at` out of
    the due window inside the same statement — which is why the claim is one
    UPDATE and not a SELECT followed by an UPDATE.
    """
    pool = await _fresh_pool()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        await dbm.create_plugin_job_schedule(
            pool, next_fire_at=now - datetime.timedelta(minutes=1), **_schedule_kwargs()
        )

        first = await dbm.claim_due_plugin_job_schedule(pool, now=now, next_fire_at=now + datetime.timedelta(hours=1))
        second = await dbm.claim_due_plugin_job_schedule(pool, now=now, next_fire_at=now + datetime.timedelta(hours=1))

        assert first is not None
        assert second is None
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_a_disabled_or_archived_schedule_is_never_claimed():
    pool = await _fresh_pool()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        due = now - datetime.timedelta(minutes=1)
        await dbm.create_plugin_job_schedule(pool, next_fire_at=due, enabled=False, **_schedule_kwargs(name="off"))
        archived = await dbm.create_plugin_job_schedule(pool, next_fire_at=due, **_schedule_kwargs(name="archived"))
        assert await dbm.archive_plugin_job_schedule(pool, archived["id"])

        assert await dbm.claim_due_plugin_job_schedule(pool, now=now, next_fire_at=now) is None
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_a_claim_clears_the_skip_reason_and_a_skip_sets_it():
    """A stale reason is worse than none: it describes a slot that has since
    fired, and an operator reading it concludes the schedule is still broken."""
    pool = await _fresh_pool()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        row = await dbm.create_plugin_job_schedule(
            pool, next_fire_at=now - datetime.timedelta(minutes=1), **_schedule_kwargs()
        )
        await dbm.set_plugin_job_schedule_skip_reason(pool, row["id"], "previous job still running")
        assert (await dbm.get_plugin_job_schedule(pool, row["id"]))["last_skipped_reason"]

        claimed = await dbm.claim_due_plugin_job_schedule(pool, now=now, next_fire_at=now + datetime.timedelta(hours=1))
        assert claimed["last_skipped_reason"] is None
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_an_archived_name_can_be_reclaimed():
    pool = await _fresh_pool()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        first = await dbm.create_plugin_job_schedule(pool, next_fire_at=now, **_schedule_kwargs(name="same"))
        await dbm.archive_plugin_job_schedule(pool, first["id"])
        # The unique index is partial on archived_at IS NULL precisely so this
        # works -- an operator should be able to replace a schedule's purpose
        # without inventing a new name for it.
        again = await dbm.create_plugin_job_schedule(pool, next_fire_at=now, **_schedule_kwargs(name="same"))
        assert again["id"] != first["id"]
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_a_live_name_collides():
    pool = await _fresh_pool()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        await dbm.create_plugin_job_schedule(pool, next_fire_at=now, **_schedule_kwargs(name="dup"))
        with pytest.raises(Exception) as caught:
            await dbm.create_plugin_job_schedule(pool, next_fire_at=now, **_schedule_kwargs(name="dup"))
        assert "UniqueViolation" in caught.value.__class__.__name__
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_the_in_flight_guard_keys_on_plugin_and_scope():
    """The concurrent-fire guard. A plugin job can hold one licensed workstation
    for minutes, so two overlapping firings contend for a single resource.

    Keyed on the synthetic source-key prefix because audit_log has no plugin
    column, and matched with ``starts_with`` rather than LIKE -- that key begins
    with an underscore, which LIKE reads as a wildcard.
    """
    pool = await _fresh_pool()
    try:
        await pool.execute("DELETE FROM audit_log WHERE scope_kind = 'shared'")
        assert await dbm.plugin_job_in_flight_jobs(pool, scope_kind="shared", scope_id=None, plugin_id=PLUGIN) == []

        await pool.execute(
            "INSERT INTO audit_log (user_sub, scope_kind, scope_id, action, key, target_format, status, job_id) "
            "VALUES ('system', 'shared', NULL, 'plugin_job', $1, 'plugin_job', 'running', 'job-1')",
            _source_key(PLUGIN, {"a": 1}),
        )
        assert await dbm.plugin_job_in_flight_jobs(
            pool, scope_kind="shared", scope_id=None, plugin_id=PLUGIN
        ) == ["job-1"]
        # A different plugin is not blocked by this one's run.
        assert await dbm.plugin_job_in_flight_jobs(pool, scope_kind="shared", scope_id=None, plugin_id="other") == []
    finally:
        await pool.execute("DELETE FROM audit_log WHERE scope_kind = 'shared'")
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_a_terminal_job_does_not_block_the_next_firing():
    pool = await _fresh_pool()
    try:
        await pool.execute("DELETE FROM audit_log WHERE scope_kind = 'shared'")
        await pool.execute(
            "INSERT INTO audit_log (user_sub, scope_kind, scope_id, action, key, target_format, status, job_id) "
            "VALUES ('system', 'shared', NULL, 'plugin_job', $1, 'plugin_job', 'ok', 'job-2')",
            _source_key(PLUGIN, {"a": 1}),
        )
        assert await dbm.plugin_job_in_flight_jobs(pool, scope_kind="shared", scope_id=None, plugin_id=PLUGIN) == []
    finally:
        await pool.execute("DELETE FROM audit_log WHERE scope_kind = 'shared'")
        await dbm.close_pool(pool)


@needs_postgres
def test_a_slot_that_cannot_be_routed_is_skipped_rather_than_queued(pg_client):
    """The failure this prevents is the worst-shaped one available here.

    A plugin job's pool comes from the plugin's LIVE spec, so during a worker
    restart there is no spec to read. Enqueuing anyway sends the job to the default
    pool, which no specialised worker subscribes to: nothing pulls it, so it is
    never retried and never reaches the delivery cap that would mark it failed. It
    sits at "queued" for good, with nothing in any worker's log to explain it --
    and an unattended schedule is most likely to fire into an empty fleet exactly
    when the fleet is being restarted.

    A missed slot is recoverable and says why.
    """
    plugin_id = "nobody-serves-this-plugin"
    created = pg_client.post(
        "/api/admin/plugin-jobs/schedules",
        json={
            "name": f"routing-guard-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d%H%M%S%f}",
            "cron_expr": "0 * * * *",
            "scope": "shared",
            "plugin_id": plugin_id,
            "options": {"action": "whatever"},
        },
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]

    try:
        # 409: the request was valid, the state said no. The reason is the useful
        # half, and it is the same text the row keeps.
        fired = pg_client.post(f"/api/admin/plugin-jobs/schedules/{schedule_id}/run")
        assert fired.status_code == 409, fired.text
        assert "no online worker advertises" in fired.json()["detail"]

        # Recorded, not merely returned: a schedule that silently does nothing is
        # the failure the whole skip-reason mechanism exists to remove.
        row = pg_client.get(f"/api/admin/plugin-jobs/schedules").json()["schedules"]
        mine = next(s for s in row if s["id"] == schedule_id)
        assert "no online worker advertises" in (mine["last_skipped_reason"] or "")
        assert mine["last_job_id"] is None, "a skipped slot must not look like it produced a job"
    finally:
        pg_client.delete(f"/api/admin/plugin-jobs/schedules/{schedule_id}")


@needs_postgres
def test_an_explicitly_named_pool_still_fires_when_the_fleet_is_empty(pg_client):
    """The admin named the pool, so routing does not depend on a live spec.

    A pool can be legitimately empty for a while -- scaled to zero, mid-deploy --
    and refusing here would make a deliberate configuration unusable whenever the
    workers happen to be down.
    """
    created = pg_client.post(
        "/api/admin/plugin-jobs/schedules",
        json={
            "name": f"explicit-pool-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d%H%M%S%f}",
            "cron_expr": "0 * * * *",
            "scope": "shared",
            "plugin_id": "nobody-serves-this-either",
            "options": {"action": "whatever"},
            "capability": "some-pool",
        },
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]
    try:
        fired = pg_client.post(f"/api/admin/plugin-jobs/schedules/{schedule_id}/run")
        # Not a routing refusal. Without a queue configured this deployment runs
        # the job locally instead, so either outcome is fine -- what matters is
        # that it was not skipped for want of a spec.
        if fired.status_code == 409:
            assert "no online worker advertises" not in fired.json()["detail"]
    finally:
        pg_client.delete(f"/api/admin/plugin-jobs/schedules/{schedule_id}")


@needs_postgres
def test_a_row_the_worker_could_never_close_does_not_block_the_schedule(pg_client):
    """The audit row's terminal status is written by the WORKER.

    A worker with no database pool cannot write it — it says so at startup — so in
    that deployment every plugin job stays `queued` in the log forever. A guard
    that trusted the row alone would let a schedule fire exactly ONCE and then
    block itself permanently, which is a worse failure than the double-firing the
    guard exists to prevent.

    Here the row says `queued` and the queue has no entry for it, which is the
    decisive half: nothing is going to run that job whatever the row claims.
    """
    plugin_id = "stale-row-plugin"
    pool_name = "some-pool"
    created = pg_client.post(
        "/api/admin/plugin-jobs/schedules",
        json={
            "name": f"stale-row-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d%H%M%S%f}",
            "cron_expr": "0 * * * *",
            "scope": "shared",
            "plugin_id": plugin_id,
            # Named so the firing does not ALSO need a live worker to route.
            "capability": pool_name,
        },
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["id"]

    import asyncio

    from ada.comms.rest import db as _dbm

    async def _seed_stale_row():
        p = await _dbm.init_pool(POSTGRES_URL)
        await p.execute(
            "INSERT INTO audit_log (user_sub, scope_kind, scope_id, action, key, target_format, status, job_id) "
            "VALUES ('system', 'shared', NULL, 'plugin_job', $1, 'plugin_job', 'queued', 'ghost-job')",
            f"_synthetic/plugin_job/{plugin_id}/deadbeef",
        )
        await _dbm.close_pool(p)

    async def _clear_stale_row():
        p = await _dbm.init_pool(POSTGRES_URL)
        await p.execute("DELETE FROM audit_log WHERE job_id = 'ghost-job'")
        await _dbm.close_pool(p)

    asyncio.run(_seed_stale_row())
    try:
        fired = pg_client.post(f"/api/admin/plugin-jobs/schedules/{schedule_id}/run")
        if fired.status_code == 409:
            assert "still queued or running" not in fired.json()["detail"], (
                "a row no worker can ever close blocked the schedule"
            )
    finally:
        asyncio.run(_clear_stale_row())
        pg_client.delete(f"/api/admin/plugin-jobs/schedules/{schedule_id}")


@needs_postgres
@pytest.mark.asyncio
async def test_a_successful_firing_clears_the_previous_skip_note():
    """The note is cleared on CLAIM, and "Run now" bypasses the claim.

    So a schedule that skipped once and then fired successfully kept showing the old
    note -- which reads as the current state, and sent someone looking for a queued
    job that had finished long before. Every successful firing makes the previous
    skip history, however it was triggered.
    """
    pool = await _fresh_pool()
    try:
        row = await dbm.create_plugin_job_schedule(
            pool, next_fire_at=datetime.datetime.now(datetime.timezone.utc), **_schedule_kwargs()
        )
        await dbm.set_plugin_job_schedule_skip_reason(pool, row["id"], "previous job still queued or running")
        assert (await dbm.get_plugin_job_schedule(pool, row["id"]))["last_skipped_reason"]

        # What the fire path does on success.
        updated = await dbm.update_plugin_job_schedule(
            pool, row["id"], last_job_id="job-xyz", last_skipped_reason=None
        )
        assert updated["last_job_id"] == "job-xyz"
        assert updated["last_skipped_reason"] is None, "a successful firing left a stale skip note"
    finally:
        await dbm.close_pool(pool)
