"""The deployment's default procedural engine (``ADA_DEFAULT_PROCEDURAL_ENGINE`` / the
``procedural_default_engine`` app setting): how it resolves, its fallback to the built-in when
the configured engine is not offered, and that every creation path records it while a stored
model keeps the engine it already has.

The API tests run on a server with no database and no queue (a local viewer), where an engine
a tool registers in-process (``raw_box_engine``) is offered as ``local:<slug>``.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import procedural_default_engine as pde  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
    load_settings,
)

BASE = "/api/scopes/shared/procedural-models"
ENGINES = "/api/scopes/shared/procedural-engines"
SPACE = {"NAME": "C1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}

_ENGINES = [
    {"id": "builtin:adapy-default", "slug": "adapy-default", "name": "adapy default"},
    {"id": "worker:example-engine", "slug": "example-engine", "name": "Example engine"},
]


def _settings(tmp_path: pathlib.Path, default_engine: str = "") -> Settings:
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
        default_procedural_engine=default_engine,
    )


def _client(tmp_path: pathlib.Path, default_engine: str = "") -> TestClient:
    return TestClient(create_app(_settings(tmp_path, default_engine)))


def _recorded_engine(client: TestClient, model_id: str) -> str:
    return next(m for m in client.get(BASE).json()["models"] if m["id"] == model_id)["engine"]


# ── resolution ───────────────────────────────────────────────────────


def test_nothing_configured_is_the_builtin():
    got = pde.resolve_default_engine("", "", _ENGINES)
    assert got["slug"] == "adapy-default" and got["id"] == "builtin:adapy-default"
    assert got["source"] == "builtin" and got["fallback"] is False and got["note"] is None


@pytest.mark.parametrize("configured", ["example-engine", "worker:example-engine"])
def test_a_configured_engine_resolves_by_slug_or_id(configured: str):
    got = pde.resolve_default_engine(configured, "env", _ENGINES)
    assert (got["id"], got["slug"]) == ("worker:example-engine", "example-engine")
    assert got["source"] == "env" and got["fallback"] is False and got["configured"] == configured


def test_an_engine_not_offered_falls_back_to_the_builtin_with_a_warning(monkeypatch: pytest.MonkeyPatch):
    warnings: list[str] = []
    monkeypatch.setattr(pde.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    pde._warned.discard("missing-engine")
    got = pde.resolve_default_engine("missing-engine", "setting", _ENGINES)
    pde.resolve_default_engine("missing-engine", "setting", _ENGINES)
    assert got["slug"] == "adapy-default" and got["fallback"] is True
    assert "missing-engine" in got["note"] and "procedural_default_engine setting" in got["note"]
    # Logged once, not on every listing.
    assert sum("missing-engine" in w for w in warnings) == 1


class _FakePool:
    def __init__(self, value):
        self.value = value

    async def fetchrow(self, _sql, key):
        assert key == pde.DEFAULT_ENGINE_SETTING
        return None if self.value is None else {"value": self.value}


@pytest.mark.parametrize(
    "pool, env, expected",
    [
        (None, "from-env", ("from-env", "env")),
        (_FakePool(None), "from-env", ("from-env", "env")),
        (_FakePool("  "), "from-env", ("from-env", "env")),
        (_FakePool("from-admin"), "from-env", ("from-admin", "setting")),
        (_FakePool(None), "", ("", "")),
    ],
)
def test_the_admin_setting_overrides_the_deployment_env(pool, env, expected):
    import asyncio

    assert asyncio.run(pde.configured_default_engine(pool, env)) == expected


def test_the_env_is_read_into_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
    monkeypatch.setenv("ADA_VIEWER_STORAGE_KIND", "local")
    monkeypatch.setenv("ADA_VIEWER_LOCAL_PATH", str(tmp_path))
    monkeypatch.setenv("ADA_DEFAULT_PROCEDURAL_ENGINE", " example-engine ")
    assert load_settings().default_procedural_engine == "example-engine"
    monkeypatch.delenv("ADA_DEFAULT_PROCEDURAL_ENGINE")
    assert load_settings().default_procedural_engine == ""


# ── the API ──────────────────────────────────────────────────────────


def test_unconfigured_creates_as_before(tmp_path: pathlib.Path):
    with _client(tmp_path) as client:
        listing = client.get(ENGINES).json()
        assert listing["default_engine"]["slug"] == "adapy-default"
        assert [e["slug"] for e in listing["procedural_engines"] if e["is_default"]] == ["adapy-default"]
        created = client.post(BASE, json={"name": "plain"}).json()
        # The column default, and a doc naming no engine (a shell's own default still applies).
        assert created["engine"] == "adapy-default" and "engine" not in created["doc"]


@pytest.mark.parametrize("configured", ["raw-box", "local:raw-box"])
def test_the_configured_default_is_listed_and_new_models_get_it(
    tmp_path: pathlib.Path, raw_box_engine: str, configured: str
):
    with _client(tmp_path, configured) as client:
        listing = client.get(ENGINES).json()
        default = listing["default_engine"]
        assert (default["id"], default["slug"], default["fallback"]) == (
            f"local:{raw_box_engine}",
            raw_box_engine,
            False,
        )
        assert [e["id"] for e in listing["procedural_engines"] if e["is_default"]] == [f"local:{raw_box_engine}"]

        created = client.post(BASE, json={"name": "new"}).json()
        assert created["engine"] == raw_box_engine and created["doc"]["engine"] == raw_box_engine
        assert client.get(f"{BASE}/{created['id']}").json()["doc"]["engine"] == raw_box_engine


def test_a_named_engine_wins_over_the_default(tmp_path: pathlib.Path, raw_box_engine: str):
    with _client(tmp_path, raw_box_engine) as client:
        created = client.post(BASE, json={"name": "named", "engine": "adapy-default"}).json()
        assert created["engine"] == "adapy-default" and created["doc"]["engine"] == "adapy-default"
        assert client.post(BASE, json={"name": "bad", "engine": 3}).status_code == 400


def test_a_default_not_offered_falls_back_and_never_breaks_a_create(tmp_path: pathlib.Path):
    pde._warned.discard("no-such-engine")
    with _client(tmp_path, "no-such-engine") as client:
        default = client.get(ENGINES).json()["default_engine"]
        assert default["slug"] == "adapy-default" and default["fallback"] is True
        assert "no-such-engine" in default["note"]
        r = client.post(BASE, json={"name": "still-works"})
        assert r.status_code == 201, r.text
        assert r.json()["engine"] == "adapy-default"


def test_a_commit_without_an_engine_keeps_the_recorded_one(tmp_path: pathlib.Path, raw_box_engine: str):
    with _client(tmp_path, raw_box_engine) as client:
        model_id = client.post(BASE, json={"name": "kept"}).json()["id"]
        r = client.put(f"{BASE}/{model_id}", json={"doc": {"spaces": [SPACE]}, "base_revision": 0})
        assert r.status_code == 200, r.text
        assert _recorded_engine(client, model_id) == raw_box_engine
        assert client.get(f"{BASE}/{model_id}").json()["doc"]["engine"] == raw_box_engine


def test_stored_models_keep_their_engine_when_the_default_changes(tmp_path: pathlib.Path, raw_box_engine: str):
    with _client(tmp_path) as client:
        model_id = client.post(BASE, json={"name": "older"}).json()["id"]
        client.put(f"{BASE}/{model_id}", json={"doc": {"spaces": [SPACE]}, "base_revision": 0})
    # The deployment now defaults to another engine: the stored model is not moved.
    with _client(tmp_path, raw_box_engine) as client:
        assert _recorded_engine(client, model_id) == "adapy-default"
        assert client.get(f"{BASE}/{model_id}").json()["doc"]["engine"] == "adapy-default"
        r = client.put(f"{BASE}/{model_id}", json={"doc": {"spaces": [SPACE]}, "base_revision": 1})
        assert r.status_code == 200, r.text
        assert _recorded_engine(client, model_id) == "adapy-default"
        # A new one does get the default.
        assert client.post(BASE, json={"name": "newer"}).json()["engine"] == raw_box_engine


def test_an_import_naming_no_engine_uses_the_default(tmp_path: pathlib.Path, raw_box_engine: str):
    """``/import-xlsx`` without an engine goes to the deployment's default: here one with no
    Excel format, so the request is refused for THAT engine (not silently the built-in)."""
    with _client(tmp_path, raw_box_engine) as client:
        r = client.post(
            f"{BASE}/import-xlsx", json={"source_key": "_procedural/_import/x/source.xlsx", "name": "imported"}
        )
        assert r.status_code == 400 and raw_box_engine in r.text, r.text


def test_settings_default_field_keeps_old_constructors_valid(tmp_path: pathlib.Path):
    s = dataclasses.replace(_settings(tmp_path))
    assert s.default_procedural_engine == ""
