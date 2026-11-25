"""``ada dev connections`` runs on a plain ada-py: none of the viewer API's packages are needed.

The command is run for real in a subprocess where those packages cannot be imported (a
``sitecustomize`` on ``PYTHONPATH`` refuses them, in the library host the server starts too, which
may only use pydantic: ada-py's GLB export needs it and ada-py depends on it), and
the connection builder's routes are driven over HTTP: the viewer and its ``config.js``, the
library's state, a build and the GLB it stored, the event stream, a reload and a restart, then the
page's own shutdown route, which ends the command.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

#: The viewer API's packages (pixi's viewer-api feature) and FastAPI's own dependencies.
BLOCKED = (
    "uvicorn",
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "pydantic_settings",
    "obstore",
    "nats",
    "aiohttp",
    "jwt",
    "cryptography",
    "httpx",
    "multipart",
    "python_multipart",
    "psycopg",
    "asyncpg",
)

#: ada-py's own GLB export uses pydantic (an ada-py dependency, unlike the rest): the library host
#: that builds the joints may import it, the command and its server may not.
HOST_ALLOWED = ("pydantic", "pydantic_core")

SITECUSTOMIZE = f"""
import sys

_BLOCKED = set({BLOCKED!r})
if "ada.api.connections.devhost" in sys.orig_argv:
    _BLOCKED -= set({HOST_ALLOWED!r})


class _Refuse:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in _BLOCKED:
            raise ModuleNotFoundError(f"No module named {{name!r}} (blocked by the test)", name=name)
        return None


sys.meta_path.insert(0, _Refuse())
"""

SPEC = """
from ada.api.connections import ConnectionSpec, MemberCriteria, MemberKind, MemberRole, register_connection

SPEC = ConnectionSpec(
    name="stdlib.plate_on_box",
    roles=(
        MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.PLATE),
        MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({"BOX"})),
    ),
)


@register_connection(SPEC)
def build(incoming, landing, **_):
    return None
"""

BASE_TIMEOUT = 180.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _get(url: str, timeout: float = 10.0) -> tuple[int, dict, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _post(url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 120.0):
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


@pytest.fixture
def library(tmp_path: pathlib.Path) -> pathlib.Path:
    lib = tmp_path / "lib"
    (lib / "stdlibjoints").mkdir(parents=True)
    (lib / "stdlibjoints" / "__init__.py").write_text("")
    (lib / "stdlibjoints" / "joint.py").write_text(SPEC)
    (lib / "ada_connections.toml").write_text(
        textwrap.dedent(
            """
            [library]
            name = "stdlib test library"
            package = "stdlibjoints"
            python_path = ["."]
            """
        )
    )
    return lib


def test_the_command_serves_the_builder_without_the_viewer_api(tmp_path: pathlib.Path, library: pathlib.Path):
    blocker = tmp_path / "blocker"
    blocker.mkdir()
    (blocker / "sitecustomize.py").write_text(SITECUSTOMIZE)
    src = pathlib.Path(__file__).resolve().parents[2] / "src"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(blocker), str(src), env.get("PYTHONPATH", "")) if p)
    env.pop("ADA_SIMULATION_REMOTE_URL", None)
    env.pop("ADA_VIEWER_STATIC_PATH", None)
    env.pop("ADA_VIEWER_BUNDLE", None)
    env["LOCALAPPDATA"] = env["XDG_CACHE_HOME"] = str(tmp_path / "cache")  # where the viewer zip unpacks

    # The blocker works: the full server cannot even start here.
    full = subprocess.run([sys.executable, "-c", "import ada.comms.rest.app"], env=env, capture_output=True, text=True)
    assert full.returncode != 0 and "blocked by the test" in full.stderr

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    cmd = [sys.executable, "-m", "ada_cli.main", "dev", "connections", "--dir", str(library)]
    proc = subprocess.Popen([*cmd, "--no-window", "--port", str(port)], env=env)
    try:
        deadline = time.monotonic() + BASE_TIMEOUT
        while True:
            assert proc.poll() is None, f"ada dev connections exited ({proc.returncode})"
            try:
                status, _, raw = _get(f"{base}/api/config", timeout=2.0)
                if status == 200:
                    break
            except OSError:
                pass
            assert time.monotonic() < deadline, "the viewer never answered"
            time.sleep(0.2)
        config = json.loads(raw)
        assert config["transport"] == "rest" and config["localShutdown"]["available"] is True

        # The viewer and the switches that open the connection builder (and nothing else).
        status, headers, page = _get(f"{base}/")
        assert status == 200 and b"<html" in page.lower() and headers["Content-Type"].startswith("text/html")
        _, _, script = _get(f"{base}/config.js")
        script = script.decode()
        for line in (
            "window.ADA_DEV_CONNECTIONS = true;",
            "window.ADA_NO_DATABASE = true;",
            "window.ADA_NO_PRESIGNED_URLS = true;",
            "window.CONVERT_ENABLED = false;",
            "window.LOCAL_JOBS = false;",
            "window.AUTH_ENABLED = false;",
        ):
            assert line in script
        # A route the full app has and this one does not is a plain 404.
        assert _get(f"{base}/api/scopes")[0] == 404
        # The page's message channel has nothing behind it: "no message", which the page takes quietly.
        assert _post(f"{base}/api/rpc")[0] == 204

        # The library loads in its host process.
        while True:
            state = json.loads(_get(f"{base}/api/dev/connections")[2])
            if state["status"] in ("ready", "error"):
                break
            assert time.monotonic() < deadline, f"the library never loaded: {state}"
            time.sleep(0.2)
        assert state["status"] == "ready", state["error"]
        assert state["library"]["name"] == "stdlib test library"
        assert [s["name"] for s in state["specs"] if s["in_library"]] == ["stdlib.plate_on_box"]

        # The event stream starts with the current state.
        with urllib.request.urlopen(f"{base}/api/dev/connections/events", timeout=30) as r:
            assert r.headers["Content-Type"].startswith("text/event-stream")
            first = r.readline().decode()
        assert first.startswith("data: ")
        assert json.loads(first[len("data: ") :])["reason"] == "subscribe"

        # A build: the answer as the full app gives it, and the GLB it stored.
        status, body = _post(
            f"{base}/api/dev/connections/build",
            {"spec_name": "stdlib.plate_on_box", "inputs": {"incoming": {"thickness": 0.05}}},
        )
        assert status == 200, body
        assert body["scope"] == "user:me" and body["counts"]["plates"] == 1
        assert body["inputs"]["incoming"]["thickness"] == 0.05
        quoted = urllib.parse.quote(body["key"], safe="")
        status, headers, glb = _get(f"{base}/api/scopes/user%3Ame/blobs/{quoted}")
        assert status == 200 and glb[:4] == b"glTF"
        assert _post(f"{base}/api/dev/connections/build", {"inputs": {}})[0] == 400
        status, body = _post(f"{base}/api/dev/connections/subject", {"spec_name": "stdlib.plate_on_box"})
        assert status == 409 and "no welds" in body["detail"]

        status, state = _post(f"{base}/api/dev/connections/reload")
        assert status == 200 and state["status"] in ("ready", "error")
        status, state = _post(f"{base}/api/dev/connections/restart")
        assert status == 200 and state["status"] == "ready", state

        # The page's stop button ends the server, and with it the command.
        token = config["localShutdown"]["token"]
        assert _post(f"{base}/api/local/shutdown", headers={"X-Ada-Local-Shutdown": "wrong"})[0] == 403
        assert _post(f"{base}/api/local/shutdown", headers={"X-Ada-Local-Shutdown": token})[0] == 202
        assert proc.wait(timeout=60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
