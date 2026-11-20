"""Driving the caller's own viewer (``/api/viewer/...``, ``ada.comms.rest.viewer_control``): the typed
vocabulary and its request body, and the switch that turns it all on (``ADA_VIEWER_CONTROL``).

A tab's session is its websocket; the round trips through one are in ``test_viewer_control_ws.py``.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-viewer-control-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.viewer_control import (  # noqa: E402
    ViewerHub,
    command_schema,
    parse_command,
)

FOCUS = {"action": "focus_joint", "joint_id": "0123456789ab"}


def _settings(tmp_path, *, viewer_control: str = "opt-in") -> Settings:
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
        viewer_control=viewer_control,
    )


def _app(tmp_path, viewer_control: str = "opt-in"):
    (tmp_path / "store").mkdir()
    return create_app(_settings(tmp_path / "store", viewer_control=viewer_control))


@pytest.fixture
def client(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        yield c


# ── the vocabulary ───────────────────────────────────────────────────────────────────────────────


def test_the_schema_has_one_branch_per_action_for_a_command_and_a_batch(client):
    r = client.get("/api/viewer/commands/schema")
    assert r.status_code == 200
    schema = r.json()
    assert schema == command_schema()
    assert {"command", "commands", "session_id", "wait_s"} <= set(schema["properties"])
    actions = {
        d["properties"]["action"]["const"] for d in schema["$defs"].values() if "action" in d.get("properties", {})
    }
    assert actions == {
        "focus_joint",
        "select",
        "clear_selection",
        "camera",
        "open_detail_model",
        "load_saved_group",
        "find_objects",
        "get_state",
    }


def test_commands_are_validated_and_filled_in():
    assert parse_command(FOCUS) == {
        "action": "focus_joint",
        "joint_id": "0123456789ab",
        "run_key": None,
        "scope": None,
        "frame": True,
        "reveal_panel": True,
    }
    assert parse_command({"action": "camera", "eye": [1, 2, 3], "target": [0, 0, 0]})["eye"] == [1.0, 2.0, 3.0]
    for bad in (
        {"action": "explode"},
        {"action": "focus_joint"},
        {"action": "select", "names": []},
        {"action": "camera"},
        {"action": "camera", "fit": "all", "view": "top"},
        {"action": "camera", "eye": [1, 2, 3]},
        {"action": "camera", "view": "sideways"},
        {"action": "get_state", "extra": 1},
    ):
        with pytest.raises(ValueError):
            parse_command(bad)


def test_a_request_is_one_command_or_one_batch(client):
    for body in (
        {},
        {"command": {"action": "camera", "fit": "everything"}},
        {"command": FOCUS, "commands": [FOCUS]},
        {"commands": []},
        {"commands": [FOCUS] * 21},
        {"commands": [FOCUS, {"action": "explode"}]},
        {"command": FOCUS, "wait_s": -1},
    ):
        r = client.post("/api/viewer/commands", json=body)
        assert r.status_code == 400, body
        assert r.json()["detail"]


def test_no_viewer_open_is_a_404(client):
    r = client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0})
    assert r.status_code == 404
    assert "no viewer open" in r.json()["detail"]
    assert client.get("/api/viewer/sessions").json() == {"sessions": []}
    assert client.get("/api/viewer/commands/0000").status_code == 404


# ── off by default ───────────────────────────────────────────────────────────────────────────────


def test_a_server_with_viewer_control_off_answers_404_and_tells_the_page(tmp_path):
    with TestClient(_app(tmp_path, viewer_control="off")) as c:
        for method, path in (
            ("get", "/api/viewer/sessions"),
            ("get", "/api/viewer/commands/schema"),
            ("post", "/api/viewer/commands"),
            ("post", "/api/viewer/ws-ticket"),
        ):
            r = getattr(c, method)(path)
            assert r.status_code == 404, (path, r.text)
            assert "ADA_VIEWER_CONTROL" in r.json()["detail"]
        assert "ADA_VIEWER_CONTROL" not in c.get("/config.js").text
        assert c.get("/api/config").json()["viewerControl"] == "off"


@pytest.mark.parametrize("mode", ["opt-in", "auto"])
def test_a_server_with_viewer_control_on_tells_the_page_its_mode(tmp_path, mode):
    with TestClient(_app(tmp_path, viewer_control=mode)) as c:
        assert f'window.ADA_VIEWER_CONTROL = "{mode}";' in c.get("/config.js").text
        assert c.get("/api/config").json()["viewerControl"] == mode
        assert c.get("/api/viewer/sessions").status_code == 200


@pytest.mark.parametrize(
    "raw, mode",
    [
        (None, "off"),
        ("", "off"),
        ("false", "off"),
        ("off", "off"),
        ("true", "opt-in"),
        ("opt-in", "opt-in"),
        ("on", "auto"),
        ("auto", "auto"),
        ("AUTO", "auto"),
    ],
)
def test_the_setting_is_read_from_the_environment(monkeypatch, raw, mode):
    from ada.comms.rest.config import load_settings

    monkeypatch.setenv("ADA_VIEWER_STORAGE_KIND", "local")
    if raw is None:
        monkeypatch.delenv("ADA_VIEWER_CONTROL", raising=False)
    else:
        monkeypatch.setenv("ADA_VIEWER_CONTROL", raw)
    settings = load_settings()
    assert settings.viewer_control == mode
    assert settings.viewer_control_enabled is (mode != "off")


def test_an_unknown_value_is_refused(monkeypatch):
    from ada.comms.rest.config import load_settings

    monkeypatch.setenv("ADA_VIEWER_STORAGE_KIND", "local")
    monkeypatch.setenv("ADA_VIEWER_CONTROL", "yes please")
    with pytest.raises(ValueError, match="ADA_VIEWER_CONTROL"):
        load_settings()


# ── the hub alone ────────────────────────────────────────────────────────────────────────────────


def test_a_ticket_is_its_users_once_and_only_for_a_while():
    now = [1000.0]
    hub = ViewerHub(clock=lambda: now[0])
    ticket = hub.mint_ticket("alice", {"title": "viewer"})
    assert hub.redeem_ticket(ticket) == ("alice", {"title": "viewer"})
    assert hub.redeem_ticket(ticket) is None
    late = hub.mint_ticket("alice")
    now[0] += 3600
    assert hub.redeem_ticket(late) is None
    assert hub.redeem_ticket("") is None


# ── saved groups ─────────────────────────────────────────────────────────────────────────────────


def test_load_saved_group_takes_an_id_or_a_name():
    assert parse_command({"action": "load_saved_group", "name": "Frame A"}) == {
        "action": "load_saved_group",
        "group_id": None,
        "name": "Frame A",
        "scope": None,
        "select": False,
        "frame": False,
    }
    for bad in ({"action": "load_saved_group"}, {"action": "load_saved_group", "name": "a", "group_id": "b"}):
        with pytest.raises(ValueError):
            parse_command(bad)
    assert parse_command({"action": "open_detail_model", "detail_id": "x"})["with_source"] is False


def test_the_scopes_saved_groups_in_brief(tmp_path):
    import json

    app = _app(tmp_path)
    groups_dir = tmp_path / "store" / "users" / "local-dev" / "_groups"
    with TestClient(app) as c:
        assert c.get("/api/scopes/user:me/saved-groups").json() == {"groups": []}
        groups_dir.mkdir(parents=True)
        doc = {
            "schema": "ada.viewer/groups@2",
            "groups": [
                {
                    "id": "g1",
                    "name": "Frame A",
                    "created_at": "2026-01-01T00:00:00Z",
                    "members": [
                        {"target": {"kind": "file", "source_key": "models/frame.ifc"}, "element": "column_1"},
                        {"target": {"kind": "file", "source_key": "models/frame.ifc"}, "element": "brace_1"},
                    ],
                },
                {"id": "g2", "name": "Deck", "created_at": "2026-01-01T00:00:00Z", "members": []},
            ],
        }
        (groups_dir / "groups.json").write_text(json.dumps(doc))
        groups = c.get("/api/scopes/user:me/saved-groups").json()["groups"]
        assert [(g["id"], g["name"], g["members"], g["sources"]) for g in groups] == [
            ("g1", "Frame A", 2, ["models/frame.ifc"]),
            ("g2", "Deck", 0, []),
        ]
        assert [
            g["id"] for g in c.get("/api/scopes/user:me/saved-groups", params={"name": "frame"}).json()["groups"]
        ] == ["g1"]
        (groups_dir / "groups.json").write_text(json.dumps({"schema": "something else"}))
        assert c.get("/api/scopes/user:me/saved-groups").status_code == 502
