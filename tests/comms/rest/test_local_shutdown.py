"""`POST /api/local/shutdown`: a local viewer can be stopped from its page, and
a deployment can never be.

What is pinned here:

* the route does not exist by default, nor with a token on a server bound to a
  non-loopback address (every deployment binds 0.0.0.0);
* with both, it refuses a non-loopback client, a non-loopback ``Host`` (DNS
  rebinding), and a missing or wrong token;
* ``/api/config`` hands the token only to a local page, and ``/config.js`` (a
  script any page may include) never carries it;
* a real ``python -m ada.comms.rest`` stops when asked, and exits cleanly.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

# Importing ada.comms.rest.app evaluates a module-level `create_app()` which
# materializes a local Storage. Point it at a temp dir so the import succeeds in
# environments without `./viewer-data`.
os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

from ada.comms.rest import local_shutdown
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings

TOKEN = "0123456789abcdef0123456789abcdef"
LOCAL = "http://127.0.0.1"


def _settings(tmp_path, *, token: str = "", host: str = "127.0.0.1") -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host=host,
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
        local_shutdown_token=token,
    )


def _client(settings: Settings, *, client_host: str = "127.0.0.1", base_url: str = LOCAL) -> TestClient:
    return TestClient(create_app(settings), base_url=base_url, client=(client_host, 50000))


@pytest.fixture
def calls(monkeypatch):
    """The shutdown hook, recorded instead of stopping anything."""
    made: list[str] = []
    monkeypatch.setattr(local_shutdown, "_hook", lambda: made.append("stop"))
    return made


def test_no_route_and_no_capability_by_default(tmp_path, calls):
    with _client(_settings(tmp_path)) as client:
        assert client.post(local_shutdown.ROUTE, headers={local_shutdown.HEADER: TOKEN}).status_code in (404, 405)
        assert client.get("/api/config").json()["localShutdown"] == {"available": False}
        assert "ADA_LOCAL_SHUTDOWN" not in client.get("/config.js").text
    assert calls == []


def test_a_token_on_a_server_bound_to_every_interface_enables_nothing(tmp_path, calls):
    """The deployment case: 0.0.0.0. A token that leaked into its environment
    still gives it no route."""
    with _client(_settings(tmp_path, token=TOKEN, host="0.0.0.0")) as client:
        assert client.post(local_shutdown.ROUTE, headers={local_shutdown.HEADER: TOKEN}).status_code in (404, 405)
        assert client.get("/api/config").json()["localShutdown"] == {"available": False}
    assert calls == []


def test_the_local_page_gets_the_capability_and_the_token(tmp_path, calls):
    with _client(_settings(tmp_path, token=TOKEN)) as client:
        entry = client.get("/api/config").json()["localShutdown"]
        assert entry == {"available": True, "token": TOKEN, "header": local_shutdown.HEADER}
        script = client.get("/config.js").text
        assert "window.ADA_LOCAL_SHUTDOWN = true;" in script
        # A classic script any page can include: the token must never be in it.
        assert TOKEN not in script


def test_stops_with_the_token(tmp_path, calls):
    with _client(_settings(tmp_path, token=TOKEN)) as client:
        reply = client.post(local_shutdown.ROUTE, headers={local_shutdown.HEADER: TOKEN})
    assert reply.status_code == 202
    assert reply.json() == {"stopping": True}
    assert calls == ["stop"]


@pytest.mark.parametrize("headers", [{}, {local_shutdown.HEADER: ""}, {local_shutdown.HEADER: "not-the-token"}])
def test_refused_without_the_right_token(tmp_path, calls, headers):
    with _client(_settings(tmp_path, token=TOKEN)) as client:
        assert client.post(local_shutdown.ROUTE, headers=headers).status_code == 403
    assert calls == []


def test_refused_from_a_client_that_is_not_this_machine(tmp_path, calls):
    with _client(_settings(tmp_path, token=TOKEN), client_host="10.1.2.3") as client:
        assert client.post(local_shutdown.ROUTE, headers={local_shutdown.HEADER: TOKEN}).status_code == 403
        # Nor does such a client learn the token.
        assert client.get("/api/config").json()["localShutdown"] == {"available": False}
    assert calls == []


def test_refused_for_a_host_name_that_is_not_this_machine(tmp_path, calls):
    """DNS rebinding: a site whose name resolves to 127.0.0.1 is same-origin with
    this server to the browser, but it still sends its own name as Host."""
    with _client(_settings(tmp_path, token=TOKEN), base_url="http://rebound.example:8080") as client:
        assert client.post(local_shutdown.ROUTE, headers={local_shutdown.HEADER: TOKEN}).status_code == 403
        assert client.get("/api/config").json()["localShutdown"] == {"available": False}
    assert calls == []


def test_without_a_hook_it_says_so_rather_than_pretend(tmp_path, monkeypatch):
    monkeypatch.setattr(local_shutdown, "_hook", None)
    with _client(_settings(tmp_path, token=TOKEN)) as client:
        assert client.post(local_shutdown.ROUTE, headers={local_shutdown.HEADER: TOKEN}).status_code == 503


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("127.5.6.7", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("0.0.0.0", False),
        ("10.0.0.1", False),
        ("localhost.example", False),
        ("", False),
        (None, False),
    ],
)
def test_is_loopback(host, expected):
    assert local_shutdown.is_loopback(host) is expected


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_a_real_server_stops_when_asked(tmp_path):
    """The whole path: `python -m ada.comms.rest`, the route, uvicorn's exit."""
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "ADA_VIEWER_STORAGE_KIND": "local",
            "ADA_VIEWER_LOCAL_PATH": str(tmp_path),
            "ADA_VIEWER_HOST": "127.0.0.1",
            "ADA_VIEWER_PORT": str(port),
            "ADA_VIEWER_LOCAL_SHUTDOWN_TOKEN": TOKEN,
        }
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "ada.comms.rest"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=2):
                    break
            except (urllib.error.URLError, OSError):
                if proc.poll() is not None or time.monotonic() > deadline:
                    pytest.fail(f"the server did not start (exit code {proc.poll()})")
                time.sleep(0.25)
        request = urllib.request.Request(
            f"{base}{local_shutdown.ROUTE}", method="POST", headers={local_shutdown.HEADER: TOKEN}
        )
        with urllib.request.urlopen(request, timeout=10) as reply:
            assert reply.status == 202
        assert proc.wait(timeout=30) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
