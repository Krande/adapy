"""``ada viewer`` (``ada_cli.viewer``): the CLI over viewer control.

Remote: the REST app (``ADA_VIEWER_CONTROL=opt-in``) with a tab on its websocket that answers what
it is sent. Local: a real desktop websocket server with a stand-in viewer connected to it as a web
client. Both through ``ada_cli.main.main``, so the exit codes are the CLI's own.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-cli-viewer-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.clash.sidecar import CLASH_SIDECAR_SCHEMA, contact_id  # noqa: E402
from ada.comms.fb_wrap_model_gen import CommandTypeDC  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.viewer_wire import answer_message, payload_of, read  # noqa: E402
from ada_cli import viewer as cli_viewer  # noqa: E402
from ada_cli.main import _build_parser, main  # noqa: E402

JOINT = "0123456789ab"
URL = "http://testserver"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in ("ADAPY_API_BASE", "ADAPY_BASE_URL", "ADAPY_VIEWER_URL", "ADAPY_API_TOKEN", "ADAPY_VIEWER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env picked up from the working directory


def _settings(tmp_path, mode: str) -> Settings:
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
        viewer_control=mode,
    )


def _http(tmp_path, monkeypatch, mode: str = "opt-in") -> TestClient:
    store = tmp_path / "store"
    (store / "users/local-dev").mkdir(parents=True)
    client = TestClient(create_app(_settings(store, mode)))
    client.__enter__()
    monkeypatch.setattr(cli_viewer, "HTTP_FACTORY", lambda: client)
    return client


class _Tab:
    """A viewer tab on the REST server's websocket, answering each command with ``answer(payload)``."""

    def __init__(self, http: TestClient, answer):
        self.http, self.answer, self.received = http, answer, []
        self.ready, self.stop = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        assert self.ready.wait(10)

    def _run(self):
        ticket = self.http.post("/api/viewer/ws-ticket", json={"client": {"title": "viewer"}}).json()["ticket"]
        with self.http.websocket_connect(f"/api/viewer/ws?ticket={ticket}") as ws:
            self.ready.set()
            while not self.stop.is_set():
                msg = read(ws.receive_bytes())
                if msg.command_type != CommandTypeDC.VIEWER_COMMAND:
                    continue
                payload = payload_of(msg)
                self.received.append(payload)
                ws.send_bytes(answer_message(msg.request_id, self.answer(payload)))


def _ok(payload):
    if "commands" in payload:
        steps = [{"index": i, "status": "done", "result": {"n": i}} for i in range(len(payload["commands"]))]
        return {"ok": True, "steps": steps, "state": {"scope": "user:me"}}
    return {"ok": True, "result": {"selected": 2}, "state": {"scope": "user:me", "selection": {"count": 2}}}


def _remote(*args: str) -> list[str]:
    return ["viewer", *args, "--via", "remote", "--url", URL, "--wait", "10"]


# ── parsing ──────────────────────────────────────────────────────────────────────────────────────


def test_every_subcommand_parses():
    parser = _build_parser()
    for argv in (
        ["viewer", "sessions"],
        ["viewer", "state", "--session", "s1"],
        ["viewer", "focus-joint", JOINT, "--run-key", "_clash/0/r.json", "--scope", "user:me"],
        ["viewer", "select", "column_1", "brace_1", "--file", "models/frame.glb"],
        ["viewer", "clear-selection"],
        ["viewer", "camera", "--fit", "selection"],
        ["viewer", "camera", "--view", "top"],
        ["viewer", "camera", "--eye", "1,2,3", "--target", "0,0,0"],
        ["viewer", "open-detail", "20260101T000000000Z-0123abcd", "--joint", JOINT],
        ["viewer", "find", "column_1"],
        ["viewer", "run", "-"],
        ["viewer", "schema"],
        ["viewer", "joints", "--run", "latest", "--ids", JOINT, "--member", "column_1"],
        ["viewer", "detail", "20260101T000000000Z-0123abcd", "--joints", JOINT],
    ):
        args = parser.parse_args(argv)
        assert callable(args.func), argv


def test_a_wrong_invocation_is_exit_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["viewer", "camera", "--fit", "all", "--view", "top"])
    assert e.value.code == 2
    assert main(["viewer", "camera", "--eye", "1,2", "--target", "0,0,0", "--via", "local"]) == 2
    assert main(["viewer", "camera", "--eye", "1,2,3", "--via", "local"]) == 2
    assert "--eye and --target go together" in capsys.readouterr().err


def test_auto_picks_remote_when_a_url_is_configured(monkeypatch):
    parser = _build_parser()
    args = parser.parse_args(["viewer", "sessions"])
    assert cli_viewer._via(args) == "local"
    monkeypatch.setenv("ADAPY_API_BASE", "https://viewer.example.invalid")
    assert cli_viewer._via(args) == "remote"
    assert cli_viewer._via(parser.parse_args(["viewer", "sessions", "--via", "local"])) == "local"


# ── remote ───────────────────────────────────────────────────────────────────────────────────────


def test_a_command_reaches_the_tab_and_the_summary_says_done(tmp_path, monkeypatch, capsys):
    http = _http(tmp_path, monkeypatch)
    tab = _Tab(http, _ok)
    assert main(_remote("focus-joint", JOINT, "--run-key", "_clash/0/r.json")) == 0
    assert tab.received[-1] == {
        "command": {
            "action": "focus_joint",
            "joint_id": JOINT,
            "run_key": "_clash/0/r.json",
            "scope": None,
            "frame": True,
            "reveal_panel": True,
        }
    }
    assert "done" in capsys.readouterr().out

    assert main(_remote("camera", "--eye", "1,2,3", "--target", "0,0,0", "--json")) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["command"]["eye"] == [1.0, 2.0, 3.0] and out["results"][0]["status"] == "done"

    assert main(_remote("sessions")) == 0
    assert "viewer" in capsys.readouterr().out
    assert main(_remote("state")) == 0
    assert "selection: 2" in capsys.readouterr().out


def test_a_batch_from_a_file_and_its_steps(tmp_path, monkeypatch, capsys):
    http = _http(tmp_path, monkeypatch)
    _Tab(http, _ok)
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            [
                {"action": "open_detail_model", "detail_id": "20260101T000000000Z-0123abcd"},
                {"action": "focus_joint", "joint_id": JOINT},
                {"action": "camera", "view": "isometric"},
            ]
        )
    )
    assert main(_remote("run", str(batch))) == 0
    out = capsys.readouterr().out
    assert "1. open_detail_model: done" in out and "3. camera: done" in out
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert main(_remote("run", str(bad))) == 2


def test_a_tab_that_answers_an_error_is_exit_1(tmp_path, monkeypatch, capsys):
    http = _http(tmp_path, monkeypatch)
    _Tab(http, lambda payload: {"ok": False, "error": "this viewer has no 3D view open", "state": {}})
    assert main(_remote("clear-selection")) == 1
    assert "error  this viewer has no 3D view open" in capsys.readouterr().out


def test_no_viewer_open_is_exit_1(tmp_path, monkeypatch, capsys):
    _http(tmp_path, monkeypatch)
    assert main(_remote("find", "column_1")) == 1
    assert "no viewer open" in capsys.readouterr().err


def test_a_server_with_viewer_control_off_is_exit_1_with_its_hint(tmp_path, monkeypatch, capsys):
    _http(tmp_path, monkeypatch, mode="off")
    assert main(_remote("sessions")) == 1
    assert "ADA_VIEWER_CONTROL" in capsys.readouterr().err


def test_the_schema_and_the_clash_read_outs(tmp_path, monkeypatch, capsys):
    http = _http(tmp_path, monkeypatch)
    assert main(["viewer", "schema", "--via", "remote", "--url", URL]) == 0
    assert "focus_joint" in capsys.readouterr().out

    members = [{"id": "e1", "name": "column_1"}, {"id": "e2", "name": "brace_1"}]
    run = {
        "schema": CLASH_SIDECAR_SCHEMA,
        "run_id": "a" * 32,
        "created_at": "2026-01-01T00:00:00Z",
        "engine": "wasm",
        "target": {"kind": "file", "source_key": "models/frame.ifc"},
        "geometry": {"key": "_derived/models/frame.glb", "sha256": "0" * 64, "up_axis": "z"},
        "options": {"security_margin": 0.001, "min_contact_area": 0.0, "line_contact_thickness": 0.0},
        "members": members,
        "contacts": [
            {
                "id": contact_id(["e1", "e2"]),
                "members": [0, 1],
                "near_points": [[1, 0, 0], [1, 0, 0.002]],
                "normal": [0, 0, 1],
                "distance": -0.002,
                "contact_area": 0.01,
                "patches": [],
                "applicable": [{"spec": "provider.high", "priority": 10}],
                "roles": {"incoming": 1, "landing": 0},
            }
        ],
        "provenance": {},
    }
    assert http.post("/api/scopes/user:me/clash-contacts/runs", json={"run": run, "annotate": False}).status_code == 200
    assert main(["viewer", "joints", "--via", "remote", "--url", URL, "--member", "brace_1"]) == 0
    out = capsys.readouterr().out
    assert "column_1 (landing), brace_1 (incoming)" in out and "specs: provider.high" in out
    assert main(["viewer", "joints", "--via", "remote", "--url", URL, "--ids", "ffffffffffff"]) == 1
    assert main(["viewer", "joints", "--via", "local"]) == 2
    assert main(["viewer", "detail", "20990101T000000000Z-ffffffff", "--via", "remote", "--url", URL]) == 1


# ── local: the desktop websocket server ──────────────────────────────────────────────────────────


@pytest.fixture
def ws_server():
    from ada.comms.wsock.server import WebSocketAsyncServer

    loop = asyncio.new_event_loop()
    ready, holder = threading.Event(), {}

    def serve():
        asyncio.set_event_loop(loop)
        server = WebSocketAsyncServer("localhost", 0)

        async def start():
            await server.start_async_non_blocking()
            holder["port"] = server.server.sockets[0].getsockname()[1]
            ready.set()

        loop.run_until_complete(start())
        loop.run_forever()

    threading.Thread(target=serve, daemon=True).start()
    assert ready.wait(10)
    yield holder["port"]
    loop.call_soon_threadsafe(loop.stop)


def _desktop_viewer(port: int, answer, instance_id: int = 4242):
    """A stand-in viewer on the desktop server, answering VIEWER_COMMAND to whoever sent it."""
    from websockets.sync.client import connect

    ws = connect(f"ws://localhost:{port}", additional_headers={"Client-Type": "web", "instance-id": str(instance_id)})

    def run():
        try:
            while True:
                msg = read(ws.recv())
                if msg.command_type == CommandTypeDC.VIEWER_COMMAND:
                    ws.send(
                        answer_message(
                            msg.request_id, answer(payload_of(msg)), instance_id=instance_id, target_id=msg.instance_id
                        )
                    )
        except Exception:  # noqa: BLE001 - the socket closed
            pass

    threading.Thread(target=run, daemon=True).start()
    return ws


def test_the_desktop_viewer_through_its_websocket_server(ws_server, capsys):
    local = ["--via", "local", "--ws-port", str(ws_server), "--wait", "10"]
    ws = _desktop_viewer(ws_server, _ok)
    try:
        assert main(["viewer", "sessions", "--via", "local", "--ws-port", str(ws_server)]) == 0
        assert "4242" in capsys.readouterr().out
        assert main(["viewer", "select", "column_1", *local]) == 0
        assert "done" in capsys.readouterr().out
        assert main(["viewer", "state", *local]) == 0
        assert "selection: 2" in capsys.readouterr().out
        assert main(["viewer", "camera", "--fit", "all", "--session", "9999", *local]) == 1
        assert "no viewer '9999'" in capsys.readouterr().err
    finally:
        ws.close()


def test_the_desktop_viewer_refusing_is_exit_1(ws_server, capsys):
    ws = _desktop_viewer(ws_server, lambda p: {"ok": False, "error": "external controls are off in this viewer"})
    try:
        assert main(["viewer", "clear-selection", "--via", "local", "--ws-port", str(ws_server)]) == 1
        assert "external controls are off" in capsys.readouterr().out
    finally:
        ws.close()


def test_no_desktop_server_is_exit_1(capsys):
    assert main(["viewer", "sessions", "--via", "local", "--ws-port", "1"]) == 1
    assert "no websocket server" in capsys.readouterr().err
