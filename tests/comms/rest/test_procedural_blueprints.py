"""Procedural blueprint catalog: the cellbuilder's Blueprint dropdown advertises
the structural blueprints the SELECTED engine can build instead of the frontend
hardcoding a single one.

Like the design-ruleset / cell-type dropdowns these are built-in + engine-
advertised (no DB table), so every test here runs without Postgres or a live
worker: the endpoint returns the static ``adapy-default`` built-ins tagged
``origin="code"``, engine-scoped via the ``?engine=`` query param, and the
``ada.topo_model`` registry lets a capability engine register more.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)


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


# ── endpoint: built-in blueprints served without a DB or a worker ─────


def test_blueprints_default_engine_builtin_without_db(app_client: TestClient):
    r = app_client.get("/api/scopes/shared/procedural-models/blueprints?engine=adapy-default")
    assert r.status_code == 200, r.text
    blueprints = r.json()["blueprints"]
    slugs = [b["slug"] for b in blueprints]
    assert slugs == ["steel_stru", "none"]  # steel_stru first = the engine default
    for b in blueprints:
        assert b["origin"] == "code"
        assert b["name"]


def test_blueprints_default_engine_is_the_query_default(app_client: TestClient):
    # No ?engine= -> adapy-default, so the dropdown works before an engine is picked.
    r = app_client.get("/api/scopes/shared/procedural-models/blueprints")
    assert r.status_code == 200, r.text
    assert [b["slug"] for b in r.json()["blueprints"]] == ["steel_stru", "none"]


def test_blueprints_unknown_engine_falls_back_to_engine_default(app_client: TestClient):
    # An engine with no static built-ins and no live worker still yields a
    # non-empty dropdown so the compile can proceed.
    r = app_client.get("/api/scopes/shared/procedural-models/blueprints?engine=pm-engine")
    assert r.status_code == 200, r.text
    blueprints = r.json()["blueprints"]
    assert len(blueprints) == 1
    assert blueprints[0]["slug"] == "engine-default"


# ── the ada.topo_model registry the workers advertise ────────────────


def test_builtin_blueprint_specs_are_announced():
    # The base worker advertises these from the registry (adapy-default),
    # engine-scoped: each spec carries its engine.
    from ada.topo_model import procedural_blueprint_specs

    default = {s["slug"]: s for s in procedural_blueprint_specs("adapy-default")}
    assert set(default) == {"steel_stru", "none"}
    assert default["steel_stru"]["engine"] == "adapy-default"

    # The union (engine=None) is what the worker heartbeat advertises.
    union = procedural_blueprint_specs()
    assert all("engine" in s for s in union)
    assert {"steel_stru", "none"} <= {s["slug"] for s in union}


def test_register_procedural_blueprint_is_idempotent_and_engine_scoped():
    from ada.topo_model import procedural_blueprint_specs, register_procedural_blueprint

    register_procedural_blueprint("pm-engine", "hull", "Hull shell", description="demo")
    register_procedural_blueprint("pm-engine", "hull", "Hull shell")  # replace, not dup
    hulls = [s for s in procedural_blueprint_specs("pm-engine") if s["slug"] == "hull"]
    assert len(hulls) == 1
    assert hulls[0]["engine"] == "pm-engine"

    # It's scoped to its engine, not leaked into adapy-default.
    assert "hull" not in {s["slug"] for s in procedural_blueprint_specs("adapy-default")}


def test_builtin_blueprint_specs_match_slim_catalog():
    # The slim rest catalog mirrors the same built-ins so the dropdown is never
    # empty without a worker; keep the two definitions in lock-step by slug/name.
    from ada.comms.rest.catalog import builtin_procedural_blueprint_specs

    default = {s["slug"]: s for s in builtin_procedural_blueprint_specs("adapy-default")}
    assert set(default) == {"steel_stru", "none"}
    assert default["steel_stru"]["name"] == "Steel structure"
    # An engine with no static built-ins returns an empty list (endpoint then
    # falls back to the engine-default entry).
    assert builtin_procedural_blueprint_specs("pm-engine") == []
