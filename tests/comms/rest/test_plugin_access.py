"""Scope-gated plugins (migration 056, ``ada.comms.rest.plugin_access``): a plugin that opts in is off
until an admin turns it on for a scope or for a user, and the server refuses its simulation runs and
its jobs where it is off."""

from __future__ import annotations

import contextlib
import os
import pathlib
import re
import tempfile
import uuid

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-plugin-access-"))

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest import plugin_access as pa  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.routes import plugin_access as routes  # noqa: E402
from ada.simulation import (  # noqa: E402
    register_simulation_provider,
    simulation_provider_specs,
)
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(not POSTGRES_URL, reason="ADA_TEST_POSTGRES_URL not set")

GATE = "robot-sim"
ME = "local-dev"  # the user a server without sign-in serves
SCOPE = "users/local-dev"


def _row(kind: str, principal: str, enabled: bool, gate: str = GATE) -> dict:
    return {"principal_kind": kind, "principal": principal, "plugin_id": gate, "enabled": enabled}


# --- the rule, pure ------------------------------------------------------------------------------


def test_a_gated_plugin_is_off_with_no_switch():
    assert pa.effective_enabled([], GATE, "shared", ME) is False


def test_a_scope_switch_alone_turns_it_on_in_that_scope_only():
    rows = [_row("scope", "shared", True)]
    assert pa.effective_enabled(rows, GATE, "shared", ME) is True
    assert pa.effective_enabled(rows, GATE, "projects/p1", ME) is False
    # Another plugin's switch says nothing about this one.
    assert pa.effective_enabled(rows, "other", "shared", ME) is False


def test_a_user_switch_alone_turns_it_on_in_every_scope():
    rows = [_row("user", ME, True)]
    for scope in ("shared", "projects/p1", "users/x"):
        assert pa.effective_enabled(rows, GATE, scope, ME) is True
    assert pa.effective_enabled(rows, GATE, "shared", "someone-else") is False
    assert pa.effective_enabled(rows, GATE, "shared", None) is False


def test_a_users_own_off_wins_over_the_scopes_on():
    rows = [_row("scope", "shared", True), _row("user", ME, False)]
    assert pa.effective_enabled(rows, GATE, "shared", ME) is False
    assert pa.effective_enabled(rows, GATE, "shared", "someone-else") is True
    # A scope's "off" row is the same as no row.
    assert pa.effective_enabled([_row("scope", "shared", False)], GATE, "shared", ME) is False


def test_the_view_says_what_decided():
    rows = [_row("scope", "shared", True), _row("user", ME, True, "b"), _row("user", ME, False, "c")]
    assert pa.access_view(rows, "shared", ME) == {
        GATE: {"enabled": True, "via": "scope"},
        "b": {"enabled": True, "via": "user"},
        "c": {"enabled": False, "via": "user"},
    }


def test_bindings_are_checked_and_gates_come_from_every_source():
    assert pa.parse_bindings(None) == {"simulation_providers": {}, "plugin_jobs": {}}
    parsed = pa.parse_bindings('{"simulation_providers": {"robot": "Robot-Sim"}}')
    assert parsed["simulation_providers"] == {"robot": GATE}
    assert pa.normalise_bindings('{"plugin_jobs": {"x": "g"}}') == '{"plugin_jobs": {"x": "g"}}'
    for bad in ("nope", "[]", '{"other": {}}', '{"plugin_jobs": []}', '{"plugin_jobs": {"x": "BAD ID"}}'):
        with pytest.raises(pa.BindingsError):
            pa.parse_bindings(bad)

    assert pa.gates_for({"access_gate": GATE}, "robot", "simulation_providers", parsed) == {GATE}
    assert pa.gates_for({}, "robot", "simulation_providers", parsed) == {GATE}  # the setting alone
    assert pa.gates_for({"access_gate": "a"}, "robot", "simulation_providers", parsed) == {"a", GATE}
    assert pa.gates_for({}, "other", "simulation_providers", parsed) == set()
    assert pa.gates_for(None, "robot", "plugin_jobs", parsed) == set()


def test_a_provider_declares_its_gate_on_its_spec():
    class Gated(FakeSimulationProvider):
        access_gate = "Robot-Sim"

    plugins.reset_registry()
    try:
        register_simulation_provider(Gated("gated-sim"))
        register_simulation_provider(FakeSimulationProvider("plain-sim"))
        specs = {s["id"]: s for s in simulation_provider_specs()}
        assert specs["gated-sim"]["access_gate"] == GATE
        assert "access_gate" not in specs["plain-sim"]
    finally:
        plugins.reset_registry()


def test_the_migration_is_the_next_one_and_keeps_one_row_per_principal_and_plugin():
    mig = pathlib.Path(pa.__file__).parent / "migrations"
    assert [p.name for p in mig.glob("056_*.sql")] == ["056_plugin_access.sql"]
    sql = (mig / "056_plugin_access.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS plugin_access" in sql
    assert re.search(
        r"UNIQUE INDEX IF NOT EXISTS plugin_access_identity\s+ON plugin_access \(principal_kind, principal, plugin_id\)",
        sql,
    )
    assert "principal_kind IN ('scope', 'user')" in sql


# --- the routes ----------------------------------------------------------------------------------


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


class _Switches:
    """``plugin_access`` and ``app_settings`` in memory, behind the db module's functions."""

    def __init__(self, monkeypatch) -> None:
        self.rows: list[dict] = []
        self.settings: dict[str, str] = {}
        self.audits: list[dict] = []

        async def rows_for(pool, *, scope, user_sub, plugin_ids=None):
            return [
                r
                for r in self.rows
                if (
                    (r["principal_kind"] == "scope" and r["principal"] == scope)
                    or (r["principal_kind"] == "user" and r["principal"] == user_sub)
                )
                and (plugin_ids is None or r["plugin_id"] in plugin_ids)
            ]

        async def set_access(pool, *, principal_kind, principal, plugin_id, enabled, granted_by=None, note=None):
            self.rows = [
                r
                for r in self.rows
                if (r["principal_kind"], r["principal"], r["plugin_id"]) != (principal_kind, principal, plugin_id)
            ]
            row = {**_row(principal_kind, principal, enabled, plugin_id), "granted_by": granted_by, "note": note}
            self.rows.append(row)
            return row

        async def clear(pool, *, principal_kind, principal, plugin_id):
            before = len(self.rows)
            self.rows = [
                r
                for r in self.rows
                if (r["principal_kind"], r["principal"], r["plugin_id"]) != (principal_kind, principal, plugin_id)
            ]
            return len(self.rows) < before

        async def listing(pool, **kw):
            return [r for r in self.rows if all(v is None or r[k] == v for k, v in kw.items())]

        async def get_setting(pool, key):
            return self.settings.get(key)

        async def set_setting(pool, key, value, updated_by=None):
            self.settings[key] = value

        async def insert_audit(pool, **kw):
            self.audits.append(kw)

        async def project_exists(pool, pid):
            return True

        monkeypatch.setattr(dbm, "plugin_access_rows_for", rows_for)
        monkeypatch.setattr(dbm, "set_plugin_access", set_access)
        monkeypatch.setattr(dbm, "clear_plugin_access", clear)
        monkeypatch.setattr(dbm, "list_plugin_access", listing)
        monkeypatch.setattr(dbm, "get_setting", get_setting)
        monkeypatch.setattr(dbm, "set_setting", set_setting)
        monkeypatch.setattr(dbm, "insert_audit", insert_audit)
        monkeypatch.setattr(dbm, "project_exists", project_exists)


class _GatedSim(FakeSimulationProvider):
    access_gate = GATE


@pytest.fixture
def gated(tmp_path, monkeypatch):
    plugins.reset_registry()
    register_simulation_provider(_GatedSim("gated-sim"))
    register_simulation_provider(FakeSimulationProvider("plain-sim"))
    (tmp_path / "store" / SCOPE).mkdir(parents=True)
    switches = _Switches(monkeypatch)
    with TestClient(create_app(_settings(tmp_path / "store"))) as c:
        c.app.state.db_pool = object()
        c.switches = switches
        yield c
    plugins.reset_registry()


def _start(c, provider: str, scope: str = "user:me"):
    return c.post(f"/api/scopes/{scope}/simulations", json={"provider": provider, "parameters": {"steps": 1}})


def _refused(r) -> bool:
    return r.status_code == 403 and r.json()["detail"]["code"] == pa.ACCESS_OFF_CODE


def test_a_gated_providers_run_is_refused_with_no_switch_and_others_are_not(gated):
    r = _start(gated, "gated-sim")
    assert _refused(r), r.text
    assert r.json()["detail"]["plugin"] == GATE and r.json()["detail"]["scope"] == SCOPE
    assert _start(gated, "plain-sim").status_code == 200
    # The listing says which gate a provider belongs to.
    listed = {p["id"]: p for p in gated.get("/api/simulation/providers").json()["providers"]}
    assert listed["gated-sim"]["access_gate"] == GATE and listed["plain-sim"]["access_gate"] is None


def test_a_scope_switch_alone_lets_the_run_start_in_that_scope(gated):
    gated.switches.rows.append(_row("scope", SCOPE, True))
    assert _start(gated, "gated-sim").status_code == 200
    assert _refused(_start(gated, "gated-sim", scope="shared"))


def test_a_user_switch_alone_lets_the_run_start_in_any_scope(gated):
    gated.switches.rows.append(_row("user", ME, True))
    assert _start(gated, "gated-sim").status_code == 200
    assert _start(gated, "gated-sim", scope="shared").status_code == 200


def test_a_users_off_refuses_where_the_scope_is_on(gated):
    gated.switches.rows += [_row("scope", SCOPE, True), _row("user", ME, False)]
    assert _refused(_start(gated, "gated-sim"))


def test_the_bindings_setting_gates_a_provider_that_declares_nothing(gated):
    gated.switches.settings[pa.BINDINGS_SETTING] = '{"simulation_providers": {"plain-sim": "other-gate"}}'
    r = _start(gated, "plain-sim")
    assert _refused(r) and r.json()["detail"]["plugin"] == "other-gate"


def test_an_unreadable_bindings_setting_leaves_the_declarations_in_force(gated):
    gated.switches.settings[pa.BINDINGS_SETTING] = "not json"
    assert _refused(_start(gated, "gated-sim"))
    assert _start(gated, "plain-sim").status_code == 200


def test_unreadable_switches_refuse_rather_than_allow(gated, monkeypatch):
    async def broken(pool, **kw):
        raise RuntimeError("database down")

    monkeypatch.setattr(dbm, "plugin_access_rows_for", broken)
    r = _start(gated, "gated-sim")
    assert r.status_code == 503 and r.json()["detail"]["code"] == pa.ACCESS_UNAVAILABLE_CODE
    assert _start(gated, "plain-sim").status_code == 200


def test_the_batch_start_is_refused_as_a_whole(gated):
    r = gated.post("/api/scopes/user:me/simulations/batch", json={"provider": "gated-sim", "cks": ["a"]})
    assert _refused(r), r.text


def test_without_a_database_nothing_is_gated(tmp_path):
    plugins.reset_registry()
    try:
        register_simulation_provider(_GatedSim("gated-sim"))
        (tmp_path / "store" / SCOPE).mkdir(parents=True)
        with TestClient(create_app(_settings(tmp_path / "store"))) as c:
            assert _start(c, "gated-sim").status_code == 200
            assert c.get("/api/scopes/user:me/plugin-access").json() == {
                "scope": SCOPE,
                "enforced": False,
                "enabled": [],
                "plugins": {},
            }
    finally:
        plugins.reset_registry()


def test_the_read_route_answers_for_the_caller_in_the_scope(gated):
    assert gated.get("/api/scopes/user:me/plugin-access").json() == {
        "scope": SCOPE,
        "enforced": True,
        "enabled": [],
        "plugins": {},
    }
    gated.switches.rows += [_row("scope", "shared", True), _row("user", ME, True, "b")]
    body = gated.get("/api/scopes/shared/plugin-access").json()
    assert body["enabled"] == ["b", GATE]
    assert body["plugins"][GATE] == {"enabled": True, "via": "scope"}
    assert gated.get("/api/scopes/user:me/plugin-access").json()["enabled"] == ["b"]


def test_a_gated_plugins_job_is_refused_where_it_is_off(gated):
    plugins.register_plugin_backend("gated-plug", access_gate=GATE)
    r = gated.post("/api/plugins/gated-plug/jobs?scope=shared", json={"options": {}})
    assert _refused(r), r.text
    gated.switches.rows.append(_row("scope", "shared", True))
    r = gated.post("/api/plugins/gated-plug/jobs?scope=shared", json={"options": {}})
    assert not _refused(r)


def test_the_admin_routes_set_clear_and_list_switches(gated):
    pid = str(uuid.uuid4())
    r = gated.put(
        "/api/admin/plugin-access",
        json={"principal_kind": "scope", "principal": f"projects/{pid}", "plugin_id": "Robot-Sim", "enabled": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["row"]["plugin_id"] == GATE and r.json()["row"]["granted_by"] == ME
    r = gated.put(
        "/api/admin/plugin-access",
        json={"principal_kind": "user", "principal": "someone", "plugin_id": GATE, "enabled": False, "note": "n"},
    )
    assert r.status_code == 200
    assert [a["action"] for a in gated.switches.audits] == ["plugin_access_set", "plugin_access_set"]
    assert gated.switches.audits[0]["key"] == f"scope:projects/{pid}:{GATE}"
    assert gated.switches.audits[1]["scope_kind"] == "user" and gated.switches.audits[1]["target_format"] == "off"

    body = gated.get("/api/admin/plugin-access").json()
    assert len(body["rows"]) == 2
    gates = {g["id"]: g["sources"] for g in body["gates"]}
    assert "simulation_provider:gated-sim" in gates[GATE] and "switch" in gates[GATE]
    assert len(gated.get("/api/admin/plugin-access?principal_kind=user").json()["rows"]) == 1

    ref = {"principal_kind": "user", "principal": "someone", "plugin_id": GATE}
    assert gated.request("DELETE", "/api/admin/plugin-access", json=ref).json() == {"removed": True}
    assert gated.request("DELETE", "/api/admin/plugin-access", json=ref).json() == {"removed": False}
    assert gated.switches.audits[-1]["action"] == "plugin_access_clear"


@pytest.mark.parametrize(
    "body",
    [
        {"principal_kind": "group", "principal": "x", "plugin_id": GATE, "enabled": True},
        {"principal_kind": "scope", "principal": "providers", "plugin_id": GATE, "enabled": True},
        {"principal_kind": "scope", "principal": "corpus/x", "plugin_id": GATE, "enabled": True},
        {"principal_kind": "scope", "principal": "projects/not-a-uuid", "plugin_id": GATE, "enabled": True},
        {"principal_kind": "scope", "principal": "project:abc", "plugin_id": GATE, "enabled": True},
        {"principal_kind": "user", "principal": " ", "plugin_id": GATE, "enabled": True},
        {"principal_kind": "user", "principal": "u", "plugin_id": "Not An Id", "enabled": True},
        {"principal_kind": "user", "principal": "u", "plugin_id": GATE, "enabled": "yes"},
        {"principal_kind": "user", "principal": "u", "plugin_id": GATE, "enabled": True, "note": 3},
    ],
)
def test_bad_admin_bodies_are_400(gated, body):
    assert gated.put("/api/admin/plugin-access", json=body).status_code == 400


def test_parse_ref_keeps_a_valid_scope_and_user():
    assert routes.parse_ref({"principal_kind": "scope", "principal": " shared ", "plugin_id": "g"}) == {
        "principal_kind": "scope",
        "principal": "shared",
        "plugin_id": "g",
    }
    with pytest.raises(HTTPException):
        routes.parse_ref({"principal_kind": "scope", "principal": "users/", "plugin_id": "g"})


def test_the_bindings_setting_is_checked_when_it_is_written(gated):
    url = f"/api/admin/settings/{pa.BINDINGS_SETTING}"
    assert gated.post(url, json={"value": "nope"}).status_code == 400
    r = gated.post(url, json={"value": '{"simulation_providers": {"robot": "Robot-Sim"}}'})
    assert r.status_code == 200 and r.json()["value"] == '{"simulation_providers": {"robot": "robot-sim"}}'


# --- live Postgres ------------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _pool():
    pool = await dbm.init_pool(POSTGRES_URL)
    try:
        yield pool
    finally:
        await dbm.close_pool(pool)


@needs_postgres
@pytest.mark.asyncio
async def test_switches_round_trip_and_the_table_refuses_what_it_must():
    from ada.comms.rest.db import plugin_access as store

    gate = f"zz-test-{uuid.uuid4().hex[:8]}"
    sub = f"user-{gate}"
    async with _pool() as pool:
        try:
            on = await store.set_plugin_access(
                pool, principal_kind="scope", principal="shared", plugin_id=gate, enabled=True, granted_by="admin"
            )
            again = await store.set_plugin_access(
                pool, principal_kind="scope", principal="shared", plugin_id=gate, enabled=False
            )
            assert again["id"] == on["id"] and again["enabled"] is False
            await store.set_plugin_access(pool, principal_kind="user", principal=sub, plugin_id=gate, enabled=True)
            rows = await store.plugin_access_rows_for(pool, scope="shared", user_sub=sub, plugin_ids=[gate])
            assert pa.effective_enabled(rows, gate, "shared", sub) is True
            assert pa.effective_enabled(rows, gate, "shared", "nobody") is False
            assert len(await store.list_plugin_access(pool, plugin_id=gate)) == 2
            for bad in (
                {"principal_kind": "scope", "principal": "providers"},
                {"principal_kind": "scope", "principal": "corpus/x"},
            ):
                with pytest.raises(Exception):
                    await store.set_plugin_access(pool, **bad, plugin_id=gate, enabled=True)
            with pytest.raises(Exception):
                await store.set_plugin_access(pool, principal_kind="user", principal=sub, plugin_id="BAD", enabled=True)
            assert await store.clear_plugin_access(pool, principal_kind="user", principal=sub, plugin_id=gate) is True
            assert await store.clear_plugin_access(pool, principal_kind="user", principal=sub, plugin_id=gate) is False
        finally:
            await pool.execute("DELETE FROM plugin_access WHERE plugin_id = $1", gate)
