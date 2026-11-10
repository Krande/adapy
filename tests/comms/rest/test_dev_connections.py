"""``/api/dev/connections/*``: a connection library served live (``ada dev connections``).

Drives the real thing: the viewer starts the library host process, builds through it, and the
file watcher reloads it after an edit.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import textwrap
import time

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.dev_connections import ENV_CONFIG  # noqa: E402

from .test_components import _settings  # noqa: E402

SPEC = """
from ada.api.connections import ConnectionSpec, MemberCriteria, MemberKind, MemberRole, register_connection

SPEC = ConnectionSpec(
    name="{name}",
    roles=(
        MemberCriteria(role=MemberRole.INCOMING, kind=MemberKind.PLATE),
        MemberCriteria(role=MemberRole.LANDING, kind=MemberKind.BEAM, section_in=frozenset({{"BOX"}})),
    ),
)


@register_connection(SPEC)
def build(incoming, landing, **_):
    {body}
"""


def _write(lib: pathlib.Path, name: str = "devrest.plate_on_box", body: str = "return None") -> None:
    pkg = lib / "devrest"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "joint.py").write_text(SPEC.format(name=name, body=body))


@pytest.fixture
def lib_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    lib = tmp_path / "lib"
    _write(lib)
    (lib / "ada_connections.toml").write_text(
        textwrap.dedent(
            """
            [library]
            name = "rest test library"
            package = "devrest"
            python_path = ["."]
            """
        )
    )
    return lib


@pytest.fixture
def client(tmp_path: pathlib.Path, lib_dir: pathlib.Path, monkeypatch):
    monkeypatch.setenv(ENV_CONFIG, str(lib_dir))
    storage = tmp_path / "storage"
    storage.mkdir()
    app = create_app(_settings(storage))
    with TestClient(app) as c:
        yield c


def _wait_for(client: TestClient, pred, timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        state = client.get("/api/dev/connections").json()
        if pred(state):
            return state
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out; last state: {state}")
        time.sleep(0.2)


def test_routes_absent_without_a_library(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(ENV_CONFIG, raising=False)
    with TestClient(create_app(_settings(tmp_path))) as c:
        assert c.get("/api/dev/connections").status_code == 404
        assert "ADA_DEV_CONNECTIONS" not in c.get("/config.js").text


def test_serve_build_and_reload(client: TestClient, lib_dir: pathlib.Path):
    assert "window.ADA_DEV_CONNECTIONS = true;" in client.get("/config.js").text

    state = _wait_for(client, lambda s: s["status"] in ("ready", "error"))
    assert state["status"] == "ready", state["error"]
    assert state["library"]["name"] == "rest test library"
    assert [s["name"] for s in state["specs"]] == ["devrest.plate_on_box"]
    generation = state["generation"]

    r = client.post(
        "/api/dev/connections/build",
        json={"spec_name": "devrest.plate_on_box", "inputs": {"incoming": {"thickness": 0.05}}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "user:me"
    assert body["inputs"]["incoming"]["thickness"] == 0.05
    assert body["counts"]["plates"] == 1
    blob = client.get(f"/api/scopes/user:me/blobs/{body['key']}")
    assert blob.status_code == 200
    assert blob.content[:4] == b"glTF"

    # An edit reloads the library: the renamed spec replaces the old one.
    _write(lib_dir, name="devrest.renamed")
    state = _wait_for(client, lambda s: s["generation"] > generation and s["status"] != "reloading")
    assert state["status"] == "ready", state["error"]
    assert [s["name"] for s in state["specs"]] == ["devrest.renamed"]
    assert any("joint.py" in c for c in state["changed"])
    generation = state["generation"]

    # A builder that raises is a 422 with its traceback, and the library keeps serving.
    _write(lib_dir, name="devrest.renamed", body='raise ValueError("bad detail")')
    state = _wait_for(client, lambda s: s["generation"] > generation and s["status"] != "reloading")
    r = client.post("/api/dev/connections/build", json={"spec_name": "devrest.renamed", "inputs": {}})
    assert r.status_code == 422
    assert "bad detail" in r.json()["error"]
    assert "Traceback" in r.json()["traceback"]

    # A syntax error is a load error the studio shows, until the next edit fixes it.
    generation = state["generation"]
    (lib_dir / "devrest" / "joint.py").write_text("def broken(:\n")
    state = _wait_for(client, lambda s: s["generation"] > generation and s["status"] != "reloading")
    assert state["status"] == "error"
    assert "SyntaxError" in state["error"]["error"]

    generation = state["generation"]
    _write(lib_dir)
    state = _wait_for(client, lambda s: s["generation"] > generation and s["status"] != "reloading")
    assert state["status"] == "ready", state["error"]
