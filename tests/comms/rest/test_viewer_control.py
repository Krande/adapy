"""Driving the caller's own viewer (``/api/viewer/...``, ``ada.comms.rest.viewer_control``).

A tab registers, long-polls for commands and answers them; an agent lists the caller's tabs, sends a
command from the typed vocabulary and reads the answers. A session belongs to the user who
registered it: another user can neither see, drive, poll nor answer for it.
"""

from __future__ import annotations

import os
import tempfile
import threading

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-viewer-control-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.auth import User  # noqa: E402
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


def _user(sub: str) -> User:
    return User(sub=sub, email=f"{sub}@example.invalid", display_name=sub, groups=frozenset(), is_admin=False)


ALICE, BOB = _user("alice"), _user("bob")


def _settings(tmp_path, *, viewer_control: bool = True) -> Settings:
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


@pytest.fixture
def app(tmp_path):
    (tmp_path / "store").mkdir()
    app = create_app(_settings(tmp_path / "store"))
    who = {"user": ALICE}
    app.dependency_overrides[auth_module.current_user] = lambda: who["user"]
    app.state.who = who
    return app


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def _as(client, user: User):
    client.app.state.who["user"] = user


def _register(client, user: User = ALICE, state: dict | None = None) -> str:
    _as(client, user)
    r = client.post(
        "/api/viewer/sessions", json={"client": {"title": "viewer"}, "state": state or {"scope": "user:me"}}
    )
    assert r.status_code == 200, r.text
    assert r.json()["poll_s"] > 0
    return r.json()["session_id"]


FOCUS = {"action": "focus_joint", "joint_id": "0123456789ab"}


# ── the vocabulary ───────────────────────────────────────────────────────────────────────────────


def test_the_schema_has_one_branch_per_action(client):
    r = client.get("/api/viewer/commands/schema")
    assert r.status_code == 200
    schema = r.json()
    assert schema == command_schema()
    actions = {
        d["properties"]["action"]["const"] for d in schema["$defs"].values() if "action" in d.get("properties", {})
    }
    assert actions == {
        "focus_joint",
        "select",
        "clear_selection",
        "camera",
        "open_detail_model",
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


def test_a_command_outside_the_vocabulary_is_a_400(client):
    _register(client)
    r = client.post("/api/viewer/commands", json={"command": {"action": "camera", "fit": "everything"}})
    assert r.status_code == 400
    assert r.json()["detail"]


# ── sessions and commands ────────────────────────────────────────────────────────────────────────


def test_no_viewer_open_is_a_404(client):
    r = client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0})
    assert r.status_code == 404
    assert "no viewer open" in r.json()["detail"]


def test_a_command_goes_to_the_tab_and_its_answer_comes_back(client):
    sid = _register(client, state={"scope": "user:me", "loaded_models": ["models/frame.glb"]})
    (listed,) = client.get("/api/viewer/sessions").json()["sessions"]
    assert listed["session_id"] == sid and listed["state"]["loaded_models"] == ["models/frame.glb"]

    sent = client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0})
    assert sent.status_code == 200, sent.text
    cid = sent.json()["command_id"]
    assert sent.json()["results"] == [{"session_id": sid, "status": "pending"}]

    polled = client.get(f"/api/viewer/sessions/{sid}/commands", params={"wait_s": 0}).json()["commands"]
    assert [(c["command_id"], c["command"]["joint_id"]) for c in polled] == [(cid, "0123456789ab")]
    # Taken once: the next poll is empty.
    assert client.get(f"/api/viewer/sessions/{sid}/commands", params={"wait_s": 0}).json()["commands"] == []

    state = {"scope": "user:me", "selection": {"count": 2}, "clash": {"selected_joint": "0123456789ab"}}
    r = client.post(
        f"/api/viewer/sessions/{sid}/commands/{cid}", json={"ok": True, "result": {"selected": 2}, "state": state}
    )
    assert r.status_code == 200
    (answer,) = client.get(f"/api/viewer/commands/{cid}").json()["results"]
    assert answer["status"] == "done" and answer["result"] == {"selected": 2}
    assert answer["state"]["clash"]["selected_joint"] == "0123456789ab"
    # The answer's state is the session's state now.
    assert client.get(f"/api/viewer/sessions/{sid}").json()["state"] == state


def test_the_agent_waits_for_the_answer(client):
    sid = _register(client)
    out: dict = {}

    def agent():
        out["r"] = client.post("/api/viewer/commands", json={"command": {"action": "get_state"}, "wait_s": 20})

    t = threading.Thread(target=agent)
    t.start()
    # The tab's poll is held until the command arrives.
    (cmd,) = client.get(f"/api/viewer/sessions/{sid}/commands", params={"wait_s": 20}).json()["commands"]
    assert cmd["command"] == {"action": "get_state"}
    r = client.post(
        f"/api/viewer/sessions/{sid}/commands/{cmd['command_id']}",
        json={"ok": False, "error": "no 3D view", "state": {"scope": "user:me"}},
    )
    assert r.status_code == 200
    t.join(30)
    assert out["r"].status_code == 200
    (answer,) = out["r"].json()["results"]
    assert answer["status"] == "error" and answer["error"] == "no 3D view"


def test_a_command_goes_to_every_tab_or_to_the_one_named(client):
    one, two = _register(client), _register(client)
    both = client.post("/api/viewer/commands", json={"command": {"action": "clear_selection"}, "wait_s": 0}).json()
    assert sorted(r["session_id"] for r in both["results"]) == sorted([one, two])
    only = client.post(
        "/api/viewer/commands", json={"command": {"action": "clear_selection"}, "session_id": two, "wait_s": 0}
    ).json()
    assert [r["session_id"] for r in only["results"]] == [two]
    assert len(client.get(f"/api/viewer/sessions/{one}/commands", params={"wait_s": 0}).json()["commands"]) == 1
    assert len(client.get(f"/api/viewer/sessions/{two}/commands", params={"wait_s": 0}).json()["commands"]) == 2


def test_a_closed_tab_answers_gone(client):
    sid = _register(client)
    cid = client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0}).json()["command_id"]
    assert client.delete(f"/api/viewer/sessions/{sid}").status_code == 200
    (answer,) = client.get(f"/api/viewer/commands/{cid}").json()["results"]
    assert answer["status"] == "gone"
    assert client.get(f"/api/viewer/sessions/{sid}/commands", params={"wait_s": 0}).status_code == 404


def test_a_state_report_is_bounded(client):
    sid = _register(client)
    assert client.put(f"/api/viewer/sessions/{sid}/state", json={"state": {"x": "y"}}).status_code == 200
    assert client.put(f"/api/viewer/sessions/{sid}/state", json={"state": "nope"}).status_code == 400
    big = {"names": ["n" * 100] * 5000}
    assert client.put(f"/api/viewer/sessions/{sid}/state", json={"state": big}).status_code == 413


# ── whose viewer ─────────────────────────────────────────────────────────────────────────────────


def test_one_user_cannot_see_or_drive_another_users_viewer(client):
    alices = _register(client, ALICE)
    _as(client, BOB)
    assert client.get("/api/viewer/sessions").json()["sessions"] == []
    assert client.get(f"/api/viewer/sessions/{alices}").status_code == 404
    # Naming her session does not reach it; nor does "all my sessions".
    r = client.post("/api/viewer/commands", json={"command": FOCUS, "session_id": alices, "wait_s": 0})
    assert r.status_code == 404
    assert client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0}).status_code == 404
    # Nor can he poll it, report its state or close it.
    assert client.get(f"/api/viewer/sessions/{alices}/commands", params={"wait_s": 0}).status_code == 404
    assert client.put(f"/api/viewer/sessions/{alices}/state", json={"state": {}}).status_code == 404
    assert client.delete(f"/api/viewer/sessions/{alices}").status_code == 404

    _as(client, ALICE)
    cid = client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0}).json()["command_id"]
    # Bob can neither read the command's answers nor answer it, even naming his own session.
    bobs = _register(client, BOB)
    assert client.get(f"/api/viewer/commands/{cid}").status_code == 404
    assert client.post(f"/api/viewer/sessions/{alices}/commands/{cid}", json={"ok": True}).status_code == 404
    assert client.post(f"/api/viewer/sessions/{bobs}/commands/{cid}", json={"ok": True}).status_code == 404
    _as(client, ALICE)
    (answer,) = client.get(f"/api/viewer/commands/{cid}").json()["results"]
    assert answer["status"] == "pending"


# ── the hub alone ────────────────────────────────────────────────────────────────────────────────


def test_a_silent_session_expires():
    now = [1000.0]
    hub = ViewerHub(clock=lambda: now[0])
    sid = hub.register("alice")["session_id"]
    assert [s["session_id"] for s in hub.sessions("alice")] == [sid]
    now[0] += 3600
    assert hub.sessions("alice") == []
    assert hub.issue("alice", {"action": "get_state"}) is None


# ── batches ──────────────────────────────────────────────────────────────────────────────────────


BATCH = [
    {"action": "open_detail_model", "detail_id": "20260101T000000000Z-0123abcd"},
    FOCUS,
    {"action": "camera", "fit": "selection"},
]


def test_a_batch_runs_in_order_and_reports_each_step(client):
    sid = _register(client)
    sent = client.post("/api/viewer/commands", json={"commands": BATCH, "wait_s": 0}).json()
    assert [c["action"] for c in sent["commands"]] == ["open_detail_model", "focus_joint", "camera"]
    assert "command" not in sent
    (item,) = client.get(f"/api/viewer/sessions/{sid}/commands", params={"wait_s": 0}).json()["commands"]
    assert [c["action"] for c in item["commands"]] == ["open_detail_model", "focus_joint", "camera"]
    # The tab stopped at the second: the third was never run.
    steps = [
        {"index": 0, "status": "done", "result": {"glb_key": "k"}},
        {"index": 1, "status": "error", "error": "joint 0123456789ab is not in the clash result open here"},
    ]
    r = client.post(
        f"/api/viewer/sessions/{sid}/commands/{item['command_id']}",
        json={"ok": False, "error": "focus_joint failed", "steps": steps, "state": {}},
    )
    assert r.status_code == 200
    (answer,) = client.get(f"/api/viewer/commands/{sent['command_id']}").json()["results"]
    assert answer["status"] == "error"
    assert [(s["action"], s["status"]) for s in answer["steps"]] == [
        ("open_detail_model", "done"),
        ("focus_joint", "error"),
        ("camera", "skipped"),
    ]
    assert answer["steps"][0]["result"] == {"glb_key": "k"}
    assert answer["steps"][1]["error"].startswith("joint 0123456789ab")


def test_a_request_is_one_command_or_one_batch(client):
    _register(client)
    for body in (
        {},
        {"command": FOCUS, "commands": [FOCUS]},
        {"commands": []},
        {"commands": [FOCUS] * 21},
        {"commands": [FOCUS, {"action": "explode"}]},
        {"command": FOCUS, "wait_s": -1},
    ):
        assert client.post("/api/viewer/commands", json=body).status_code == 400, body


# ── off by default ───────────────────────────────────────────────────────────────────────────────


def test_a_server_with_viewer_control_off_answers_404_and_tells_the_page(tmp_path):
    (tmp_path / "store").mkdir()
    app = create_app(_settings(tmp_path / "store", viewer_control=False))
    with TestClient(app) as c:
        for method, path in (
            ("get", "/api/viewer/sessions"),
            ("get", "/api/viewer/commands/schema"),
            ("post", "/api/viewer/commands"),
            ("post", "/api/viewer/sessions"),
        ):
            r = getattr(c, method)(path)
            assert r.status_code == 404, (path, r.text)
            assert "ADA_VIEWER_CONTROL" in r.json()["detail"]
        assert "ADA_VIEWER_CONTROL" not in c.get("/config.js").text
        assert c.get("/api/config").json()["viewerControl"] is False


def test_a_server_with_viewer_control_on_tells_the_page(client):
    assert "window.ADA_VIEWER_CONTROL = true;" in client.get("/config.js").text
    assert client.get("/api/config").json()["viewerControl"] is True


def test_the_setting_is_read_from_the_environment(monkeypatch):
    from ada.comms.rest.config import load_settings

    monkeypatch.setenv("ADA_VIEWER_STORAGE_KIND", "local")
    monkeypatch.delenv("ADA_VIEWER_CONTROL", raising=False)
    assert load_settings().viewer_control is False
    monkeypatch.setenv("ADA_VIEWER_CONTROL", "true")
    assert load_settings().viewer_control is True
