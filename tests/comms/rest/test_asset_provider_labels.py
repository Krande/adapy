"""Provider display names over the routes: the scope's providers answer carries ``provider_labels``
resolved (admin alias for THIS scope > plugin spec > registration), and the admin setting is
validated where it is written.

Auth disabled, no database: the setting is served by a patched ``get_setting`` / ``set_setting``.
"""

import json
import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-labels-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.assets.provider_labels import PROVIDER_LABELS_SETTING  # noqa: E402
from ada.assets.registry import (  # noqa: E402
    clear_asset_providers,
    register_asset_provider,
)
from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.routes import assets as assets_routes  # noqa: E402


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


class _Provider:
    delivery_kinds = ("mesh",)

    def tree(self, *a, **k):  # pragma: no cover - never called
        return None


@pytest.fixture
def stored(monkeypatch):
    """The settings table, as a dict, behind patched get/set."""
    table: dict[str, str] = {}

    async def get_setting(_pool, key):
        return table.get(key)

    async def set_setting(_pool, key, value, *, updated_by):
        table[key] = value

    monkeypatch.setattr(db_module, "get_setting", get_setting)
    monkeypatch.setattr(db_module, "set_setting", set_setting)
    return table


@pytest.fixture
def client(tmp_path, monkeypatch, stored):
    async def specs(_ctx):
        return {
            "lines-plugin": {"asset_provider_id": "fixture-lines", "asset_provider_label": "Lines (spec)"},
            "mesher-plugin": {"asset_provider_id": "mesher"},
        }

    monkeypatch.setattr(assets_routes, "online_plugin_specs", specs)
    clear_asset_providers()
    register_asset_provider("builder", lambda: _Provider(), label="Builder (registered)")
    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        app.state.db_pool = object()
        yield c
    clear_asset_providers()


def _labels(client, scope: str) -> dict:
    r = client.get(f"/api/scopes/{scope}/assets/providers")
    assert r.status_code == 200, r.text
    return r.json()["provider_labels"]


def test_labels_without_aliases_come_from_the_providers(client):
    assert _labels(client, "shared") == {"fixture-lines": "Lines (spec)", "builder": "Builder (registered)"}


def test_an_alias_overrides_in_its_own_scope_only(client, stored):
    stored[PROVIDER_LABELS_SETTING] = json.dumps(
        {"shared": {"fixture-lines": "Lines here", "mesher": "Mesher here"}, "project:other": {"builder": "Elsewhere"}}
    )
    assert _labels(client, "shared") == {
        "fixture-lines": "Lines here",
        "mesher": "Mesher here",
        "builder": "Builder (registered)",
    }


def test_a_malformed_setting_falls_back_to_the_providers(client, stored):
    stored[PROVIDER_LABELS_SETTING] = "{not json"
    assert _labels(client, "shared")["fixture-lines"] == "Lines (spec)"


def test_a_personal_scope_gets_the_providers_own_labels(client, stored):
    stored[PROVIDER_LABELS_SETTING] = json.dumps({"shared": {"fixture-lines": "Lines here"}})
    assert _labels(client, "user:me")["fixture-lines"] == "Lines (spec)"


def test_the_admin_write_is_normalised(client, stored):
    r = client.post(
        f"/api/admin/settings/{PROVIDER_LABELS_SETTING}",
        json={"value": json.dumps({"shared": {"mesher": "  Mesher \t two "}, "project:x": {}})},
    )
    assert r.status_code == 200, r.text
    assert json.loads(stored[PROVIDER_LABELS_SETTING]) == {"shared": {"mesher": "Mesher two"}}
    assert _labels(client, "shared")["mesher"] == "Mesher two"


@pytest.mark.parametrize(
    "value",
    [
        json.dumps({"shared": {"mesher": "   "}}),
        json.dumps({"shared": {"mesher": "x" * 200}}),
        json.dumps({"shared": "mesher"}),
        "not json",
    ],
)
def test_the_admin_write_refuses_an_invalid_name(client, stored, value):
    r = client.post(f"/api/admin/settings/{PROVIDER_LABELS_SETTING}", json={"value": value})
    assert r.status_code == 400, r.text
    assert PROVIDER_LABELS_SETTING not in stored


def test_other_settings_are_not_validated_as_labels(client, stored):
    r = client.post("/api/admin/settings/public.something.else", json={"value": "   "})
    assert r.status_code == 200, r.text
    assert stored["public.something.else"] == "   "
