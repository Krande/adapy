"""The viewer's leg of viewer control over a websocket (``/api/viewer/ws``, ``ada.comms.viewer_wire``).

A tab asks for a single-use ticket (``POST /api/viewer/ws-ticket``, authenticated as any route), opens
the socket with it, and is a session of the ticket's user for as long as the socket lives: an agent's
command arrives as ``VIEWER_COMMAND`` and the tab answers ``VIEWER_ANSWER``; ``VIEWER_STATE`` reports
what it shows. Also the desktop websocket server's handling of the same messages.
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-viewer-ws-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from ada.comms.fb_wrap_model_gen import (  # noqa: E402
    CommandTypeDC,
    MessageDC,
    TargetTypeDC,
)
from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.auth import User  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.viewer_wire import (  # noqa: E402
    answer_message,
    command_message,
    payload_of,
    read,
    state_message,
)


def _user(sub: str) -> User:
    return User(sub=sub, email=f"{sub}@example.invalid", display_name=sub, groups=frozenset(), is_admin=False)


ALICE, BOB = _user("alice"), _user("bob")
FOCUS = {"action": "focus_joint", "joint_id": "0123456789ab"}


def _settings(tmp_path, viewer_control: bool) -> Settings:
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


def _client(tmp_path, viewer_control: bool = True):
    (tmp_path / "store").mkdir()
    app = create_app(_settings(tmp_path / "store", viewer_control))
    who = {"user": ALICE}
    app.dependency_overrides[auth_module.current_user] = lambda: who["user"]
    c = TestClient(app)
    c.who = who
    return c


@pytest.fixture
def client(tmp_path):
    with _client(tmp_path) as c:
        yield c


def _ticket(client, user: User = ALICE) -> str:
    client.who["user"] = user
    r = client.post("/api/viewer/ws-ticket", json={"client": {"title": "viewer"}})
    assert r.status_code == 200, r.text
    assert r.json()["path"] == "/api/viewer/ws"
    return r.json()["ticket"]


def test_a_command_reaches_the_tab_over_its_socket_and_the_answer_comes_back(client):
    with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client)}") as ws:
        (session,) = client.get("/api/viewer/sessions").json()["sessions"]
        assert session["client"] == {"title": "viewer"}

        # What it shows, reported unasked.
        ws.send_bytes(state_message({"scope": "user:me", "loaded_models": ["models/frame.glb"]}))
        out: dict = {}

        def agent():
            out["r"] = client.post(
                "/api/viewer/commands",
                json={"commands": [FOCUS, {"action": "camera", "fit": "selection"}], "wait_s": 20},
            )

        t = threading.Thread(target=agent)
        t.start()
        msg = read(ws.receive_bytes())
        assert msg.command_type == CommandTypeDC.VIEWER_COMMAND
        assert [c["action"] for c in payload_of(msg)["commands"]] == ["focus_joint", "camera"]
        steps = [{"index": 0, "status": "done", "result": {"selected": 2}}, {"index": 1, "status": "done"}]
        ws.send_bytes(
            answer_message(
                msg.request_id, {"ok": True, "steps": steps, "state": {"scope": "user:me", "selection": {"count": 2}}}
            )
        )
        t.join(30)
        (answer,) = out["r"].json()["results"]
        assert answer["status"] == "done"
        assert [s["status"] for s in answer["steps"]] == ["done", "done"]
        assert answer["state"]["selection"] == {"count": 2}

    # The socket closed: the session is gone.
    assert client.get("/api/viewer/sessions").json()["sessions"] == []


def test_the_state_a_tab_reports_is_its_sessions(client):
    with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client)}") as ws:
        ws.send_bytes(state_message({"scope": "user:me", "clash": {"selected_joint": "0123456789ab"}}))
        # A command round trip makes sure the state message was handled before reading the sessions.
        sent = client.post("/api/viewer/commands", json={"command": {"action": "get_state"}, "wait_s": 0}).json()
        msg = read(ws.receive_bytes())
        assert msg.request_id == sent["command_id"]
        (session,) = client.get("/api/viewer/sessions").json()["sessions"]
        assert session["state"]["clash"]["selected_joint"] == "0123456789ab"


def test_a_ticket_opens_one_socket_once(client):
    ticket = _ticket(client)
    with client.websocket_connect(f"/api/viewer/ws?ticket={ticket}"):
        pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/viewer/ws?ticket={ticket}"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/viewer/ws?ticket=not-a-ticket"):
            pass


def test_a_tab_is_its_tickets_users_only(client):
    with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client, ALICE)}"):
        client.who["user"] = BOB
        assert client.get("/api/viewer/sessions").json()["sessions"] == []
        assert client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0}).status_code == 404
        client.who["user"] = ALICE
        assert len(client.get("/api/viewer/sessions").json()["sessions"]) == 1


def test_off_by_default_no_ticket_and_no_socket(tmp_path):
    with _client(tmp_path, viewer_control=False) as c:
        assert c.post("/api/viewer/ws-ticket", json={}).status_code == 404
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/viewer/ws?ticket=anything"):
                pass


def test_a_command_goes_to_every_tab_or_to_the_one_named(client):
    with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client)}") as one:
        with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client)}") as two:
            sessions = [s["session_id"] for s in client.get("/api/viewer/sessions").json()["sessions"]]
            assert len(sessions) == 2
            both = client.post("/api/viewer/commands", json={"command": {"action": "get_state"}, "wait_s": 0})
            assert sorted(r["session_id"] for r in both.json()["results"]) == sorted(sessions)
            assert read(one.receive_bytes()).request_id == both.json()["command_id"]
            assert read(two.receive_bytes()).request_id == both.json()["command_id"]
            named = sessions[0]
            only = client.post("/api/viewer/commands", json={"command": FOCUS, "session_id": named, "wait_s": 0}).json()
            assert [r["session_id"] for r in only["results"]] == [named]


def test_a_tab_that_closes_before_answering_is_gone(client):
    with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client)}") as ws:
        cid = client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0}).json()["command_id"]
        assert read(ws.receive_bytes()).request_id == cid
    (answer,) = client.get(f"/api/viewer/commands/{cid}").json()["results"]
    assert answer["status"] == "gone"


def test_an_oversized_state_is_not_kept(client):
    with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client)}") as ws:
        ws.send_bytes(state_message({"names": ["n" * 100] * 5000}))
        ws.send_bytes(state_message({"scope": "user:me"}))
        client.post("/api/viewer/commands", json={"command": {"action": "get_state"}, "wait_s": 0})
        ws.receive_bytes()
        (session,) = client.get("/api/viewer/sessions").json()["sessions"]
        assert session["state"] == {"scope": "user:me"}


def test_another_user_can_neither_drive_nor_read_a_tab(client):
    with client.websocket_connect(f"/api/viewer/ws?ticket={_ticket(client, ALICE)}") as ws:
        (session,) = client.get("/api/viewer/sessions").json()["sessions"]
        sid = session["session_id"]
        cid = client.post("/api/viewer/commands", json={"command": FOCUS, "wait_s": 0}).json()["command_id"]
        ws.receive_bytes()
        client.who["user"] = BOB
        assert client.get(f"/api/viewer/sessions/{sid}").status_code == 404
        assert client.post("/api/viewer/commands", json={"command": FOCUS, "session_id": sid}).status_code == 404
        assert client.get(f"/api/viewer/commands/{cid}").status_code == 404
        client.who["user"] = ALICE


# ── the desktop websocket server ─────────────────────────────────────────────────────────────────


@dataclass
class _Client:
    instance_id: int = 5
    group_type: TargetTypeDC = TargetTypeDC.WEB


@dataclass
class _Server:
    instance_id: int = 99
    debug: bool = False
    sent: list = field(default_factory=list)

    def send_message_threadsafe(self, client, payload: bytes) -> None:
        self.sent.append(payload)


def test_the_desktop_server_keeps_each_viewers_state_and_only_relays_the_rest():
    from ada.comms.msg_handling.default_on_message import default_on_message
    from ada.comms.msg_handling.viewer_control import viewer_states

    server, viewer, script = _Server(), _Client(instance_id=5), _Client(instance_id=7, group_type=TargetTypeDC.LOCAL)
    default_on_message(server, viewer, state_message({"scope": "user:me"}, instance_id=5))
    assert viewer_states(server) == {5: {"scope": "user:me"}}
    # A command on its way to a viewer, and its answer on its way back: relayed, not answered here.
    default_on_message(server, script, command_message("c1", {"command": FOCUS}, instance_id=7, target_id=5))
    default_on_message(server, viewer, answer_message("c1", {"ok": True}, instance_id=5, target_id=7))
    assert server.sent == []


def test_the_desktop_server_refuses_a_command_addressed_to_itself():
    from ada.comms.fb_wrap_serializer import serialize_root_message
    from ada.comms.msg_handling.default_on_message import default_on_message

    server = _Server()
    message = MessageDC(
        command_type=CommandTypeDC.VIEWER_COMMAND,
        target_group=TargetTypeDC.SERVER,
        request_id="c1",
        payload_json='{"command": {"action": "get_state"}}',
    )
    default_on_message(server, _Client(group_type=TargetTypeDC.LOCAL), serialize_root_message(message))
    (reply,) = [read(b) for b in server.sent]
    assert reply.command_type == CommandTypeDC.ERROR and reply.request_id == "c1"
    assert "target_group WEB" in reply.server_reply.error.message
