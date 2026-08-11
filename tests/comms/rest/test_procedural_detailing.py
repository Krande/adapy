"""Detailing-engine advertising + the compile ``detailing`` key routing.

Like the blueprints / design-ruleset dropdowns the detailing engines are
built-in + worker-advertised (no DB table), so the endpoint serves the static
built-ins (``none`` + ``adapy-default``) without Postgres or a live worker. The
compile ``detailing`` param composes into the derived blob key such that the
default (``none``) is BYTE-IDENTICAL to today's plain structural key.
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
from ada.comms.rest.procedural import (  # noqa: E402
    procedural_detail_glb_key,
    procedural_detailing_glb_key,
    procedural_glb_key,
    procedural_log_key,
    procedural_preview_glb_key,
    procedural_structural_ifc_key,
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


# ── endpoint: built-in detailing engines served without a DB/worker ──


def test_detailing_engines_builtin_without_db(app_client: TestClient):
    r = app_client.get("/api/scopes/shared/procedural-models/detailing-engines")
    assert r.status_code == 200, r.text
    engines = r.json()["detailing_engines"]
    slugs = [e["slug"] for e in engines]
    assert slugs == ["none", "adapy-default"]  # none first = the default
    for e in engines:
        assert e["origin"] == "code"
        assert e["name"]
        assert isinstance(e["joint_types"], list)
    adapy = next(e for e in engines if e["slug"] == "adapy-default")
    assert {"girder_gusset", "beam_column_endplate", "column_base_plate", "box_to_box"} <= {
        j["slug"] for j in adapy["joint_types"]
    }


def test_builtin_detailing_specs_match_registry():
    # The slim rest catalog mirrors the ada.topo_model registry so the dropdown is
    # never empty without a worker; keep the two definitions in lock-step by slug.
    from ada.comms.rest.catalog import builtin_detailing_engine_specs
    from ada.topo_model import detailing_engine_specs

    slim = {s["slug"] for s in builtin_detailing_engine_specs()}
    registry = {s["slug"] for s in detailing_engine_specs()}
    assert slim == registry == {"none", "adapy-default"}


# ── blob-key routing: "none" is byte-identical to the plain key ──────


def test_detailing_none_key_is_byte_identical_to_structural():
    # CRITICAL backward-compat: no detailing (None/"none") reduces EXACTLY to the
    # plain structural key, at every lod/engine combination.
    for detailing in (None, "none"):
        assert procedural_detailing_glb_key("m", 3, None, detailing, "sim") == procedural_glb_key("m", 3, None)
        assert procedural_detailing_glb_key("m", 3, None, detailing, "detail") == procedural_detail_glb_key("m", 3, None)
        assert procedural_detailing_glb_key("m", 3, "pm-engine", detailing, "sim") == procedural_glb_key(
            "m", 3, "pm-engine"
        )


def test_detailing_key_gets_det_suffix():
    key = procedural_detailing_glb_key("m", 3, None, "adapy-default", "sim")
    assert key == "_procedural/m/r3.det-adapy.glb"
    # Composes with lod + engine suffixes, all four combos distinct.
    assert procedural_detailing_glb_key("m", 3, None, "adapy-default", "detail") == "_procedural/m/r3_detail.det-adapy.glb"
    assert procedural_detailing_glb_key("m", 3, "echo", "adapy-default", "sim") == "_procedural/m/r3.echo.det-adapy.glb"
    # The .log sibling rule follows the key automatically.
    assert procedural_log_key(key) == "_procedural/m/r3.det-adapy.log"


def test_preview_key_gains_det_fragment():
    assert procedural_preview_glb_key("m", "abc", None, "sim", None) == "_procedural/m/preview/abc.glb"
    assert procedural_preview_glb_key("m", "abc", None, "sim", "adapy-default") == "_procedural/m/preview/abc.det-adapy.glb"


def test_structural_ifc_key():
    assert procedural_structural_ifc_key("m", 3) == "_procedural/m/r3.structural.ifc"
    assert procedural_structural_ifc_key("m", 3, "pm-engine") == "_procedural/m/r3.pm-engine.structural.ifc"
