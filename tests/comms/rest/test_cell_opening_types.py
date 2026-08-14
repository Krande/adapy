"""Procedural cell-type / opening-type catalogs: the ``+ Cell`` and ``+ Opening``
pickers advertise engine/built-in types instead of the frontend hardcoding them.

Like the design-ruleset dropdown these are built-in + engine-advertised (no DB
table), so every test here runs without Postgres or a live worker: the endpoints
return the static ``adapy-default`` built-ins tagged ``origin="code"``, and the
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


# ── endpoints: built-in types served without a DB or a worker ─────────


def test_cell_types_builtin_without_db(app_client: TestClient):
    r = app_client.get("/api/scopes/shared/procedural-models/cell-types")
    assert r.status_code == 200, r.text
    types = {t["slug"]: t for t in r.json()["cell_types"]}
    assert "room" in types
    room = types["room"]
    assert room["origin"] == "code"
    assert room["size"] == [5.0, 5.0, 3.0]
    assert isinstance(room["metadata"], dict)


def test_opening_types_builtin_without_db(app_client: TestClient):
    r = app_client.get("/api/scopes/shared/procedural-models/opening-types")
    assert r.status_code == 200, r.text
    types = {t["slug"]: t for t in r.json()["opening_types"]}
    # Door, window + a generic "opening" (all valid TopoOpening.SUBTYPE values).
    assert set(types) >= {"door", "window", "opening"}
    assert types["door"]["subtype"] == "door"
    assert types["window"]["subtype"] == "window"
    assert types["opening"]["subtype"] == "opening"  # generic third option
    for t in types.values():
        assert t["origin"] == "code"
        assert len(t["size"]) == 3
        assert t["subtype"] in ("door", "window", "opening")


# ── the ada.topo_model registry the workers advertise ────────────────


def test_builtin_cell_opening_specs_are_announced():
    # The base worker advertises these from the registry (adapy-default).
    from ada.topo_model import procedural_cell_type_specs, procedural_opening_type_specs

    cells = {s["slug"]: s for s in procedural_cell_type_specs()}
    assert "room" in cells
    assert cells["room"]["size"] == [5.0, 5.0, 3.0]

    openings = {s["slug"]: s for s in procedural_opening_type_specs()}
    assert set(openings) >= {"door", "window"}
    assert openings["door"]["subtype"] == "door"


def test_register_procedural_cell_and_opening_types_are_idempotent():
    from ada.topo_model import (
        procedural_cell_type_specs,
        procedural_opening_type_specs,
        register_procedural_cell_type,
        register_procedural_opening_type,
    )

    register_procedural_cell_type("engine-bay", "Engine bay", (8.0, 6.0, 4.0), description="demo", metadata={"CRIT": 3})
    register_procedural_cell_type("engine-bay", "Engine bay", (8.0, 6.0, 4.0))  # replace, not dup
    bays = [s for s in procedural_cell_type_specs() if s["slug"] == "engine-bay"]
    assert len(bays) == 1
    assert bays[0]["size"] == [8.0, 6.0, 4.0]

    register_procedural_opening_type("hatch", "Hatch", "door", (0.9, 0.9, 0.3))
    hatches = [s for s in procedural_opening_type_specs() if s["slug"] == "hatch"]
    assert len(hatches) == 1
    assert hatches[0]["subtype"] == "door"

    with pytest.raises(ValueError):
        register_procedural_opening_type("bad", "Bad", "skylight", (1.0, 1.0, 1.0))  # type: ignore[arg-type]


def test_builtin_cell_opening_specs_match_slim_catalog():
    # The slim rest catalog mirrors the same built-ins so the dropdowns are never
    # empty without a worker; keep the two definitions in lock-step by slug/size.
    from ada.comms.rest.catalog import builtin_cell_specs, builtin_opening_specs

    cells = {s["slug"]: s for s in builtin_cell_specs()}
    assert cells["room"]["size"] == [5.0, 5.0, 3.0]
    openings = {s["slug"]: s for s in builtin_opening_specs()}
    assert openings["door"]["subtype"] == "door"
    assert openings["window"]["subtype"] == "window"
