"""GET /config.js — the runtime-config shim the SPA loads before its bundle.

These tests pin the one setting that has to be *absent* rather than empty when
unconfigured: the default UI shell. The frontend reads a missing
``window.ADA_UI_DEFAULT`` as "this deployment has no opinion" and keeps the
default baked into the bundle at build time, so emitting an empty string here
would silently change the behaviour of every image that predates this setting.

Same shape as test_public_settings.py — ``create_app`` with auth disabled and a
temp-dir local storage.
"""

from __future__ import annotations

import os
import tempfile

# Importing ada.comms.rest.app evaluates a module-level `create_app()` which
# materializes a local Storage. Point it at a temp dir so the import succeeds in
# environments without `./viewer-data`.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient

from ada.comms.rest.app import create_app
from ada.comms.rest.config import (
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
    load_settings,
)


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
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
    base.update(overrides)
    return Settings(**base)


def _config_js(tmp_path, **overrides) -> str:
    app = create_app(_settings(tmp_path, **overrides))
    with TestClient(app) as client:
        resp = client.get("/config.js")
    assert resp.status_code == 200
    return resp.text


def test_ui_default_is_emitted_when_configured(tmp_path):
    body = _config_js(tmp_path, ui_default="alt")
    assert 'window.ADA_UI_DEFAULT = "alt";' in body


def test_ui_default_is_omitted_when_unset(tmp_path):
    # Unset must not become `window.ADA_UI_DEFAULT = ""`: an empty string is a
    # value, and the frontend would have to decide what it means.
    body = _config_js(tmp_path)
    assert "ADA_UI_DEFAULT" not in body
    # The rest of the shim is unaffected — this is an additive setting.
    assert 'window.COMMS_MODE = "rest";' in body


def test_ui_default_is_json_encoded(tmp_path):
    # Same quoting rule as every other string in the shim: a value that
    # contains a quote must not break out of the JS literal.
    body = _config_js(tmp_path, ui_default='we"ird')
    assert 'window.ADA_UI_DEFAULT = "we\\"ird";' in body


def test_ui_default_reads_ada_viewer_ui_default(monkeypatch, tmp_path):
    monkeypatch.setenv("ADA_VIEWER_STORAGE_KIND", "local")
    monkeypatch.setenv("ADA_VIEWER_LOCAL_PATH", str(tmp_path))
    monkeypatch.setenv("ADA_VIEWER_UI_DEFAULT", "  alt  ")
    assert load_settings().ui_default == "alt"

    monkeypatch.delenv("ADA_VIEWER_UI_DEFAULT")
    assert load_settings().ui_default == ""
