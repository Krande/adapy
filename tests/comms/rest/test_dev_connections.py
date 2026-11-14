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


def _settled(client: TestClient, quiet: float = 2.0, timeout: float = 120.0) -> dict:
    """The state once the library has gone ``quiet`` seconds (several watch cycles) without reloading."""
    from ada.comms.rest.dev_connections import POLL_INTERVAL_S, SETTLE_S

    quiet = max(quiet, 3 * (POLL_INTERVAL_S + SETTLE_S))
    deadline = time.monotonic() + timeout
    state = client.get("/api/dev/connections").json()
    since = time.monotonic()
    while time.monotonic() - since < quiet:
        if time.monotonic() > deadline:
            raise AssertionError(f"the library never settled; last state: {state}")
        time.sleep(0.2)
        now = client.get("/api/dev/connections").json()
        if now["generation"] != state["generation"] or now["status"] != state["status"]:
            state, since = now, time.monotonic()
    return state


def test_routes_absent_without_a_library(tmp_path: pathlib.Path, monkeypatch):
    monkeypatch.delenv(ENV_CONFIG, raising=False)
    with TestClient(create_app(_settings(tmp_path))) as c:
        assert c.get("/api/dev/connections").status_code == 404
        assert "ADA_DEV_CONNECTIONS" not in c.get("/config.js").text


def test_a_local_viewer_tells_the_page_what_it_lacks(tmp_path: pathlib.Path, monkeypatch):
    # No database and a local store: the page does not poll the admin routes or ask for presigned URLs.
    monkeypatch.delenv(ENV_CONFIG, raising=False)
    with TestClient(create_app(_settings(tmp_path))) as c:
        script = c.get("/config.js").text
    assert "window.ADA_NO_DATABASE = true;" in script
    assert "window.ADA_NO_PRESIGNED_URLS = true;" in script


def test_serve_build_and_reload(client: TestClient, lib_dir: pathlib.Path):
    assert "window.ADA_DEV_CONNECTIONS = true;" in client.get("/config.js").text

    state = _wait_for(client, lambda s: s["status"] in ("ready", "error"))
    assert state["status"] == "ready", state["error"]
    assert state["library"]["name"] == "rest test library"
    assert [s["name"] for s in state["specs"] if s["in_library"]] == ["devrest.plate_on_box"]
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
    assert [s["name"] for s in state["specs"] if s["in_library"]] == ["devrest.renamed"]
    assert any("joint.py" in c for c in state["changed"])
    generation = state["generation"]

    # A builder that raises is a 422 with its traceback, and the library keeps serving.
    _write(lib_dir, name="devrest.renamed", body='raise ValueError("bad detail")')
    state = _wait_for(client, lambda s: s["generation"] > generation and s["status"] != "reloading")
    r = client.post("/api/dev/connections/build", json={"spec_name": "devrest.renamed", "inputs": {}})
    assert r.status_code == 422
    body = r.json()
    assert "bad detail" in body["error"]
    assert body["declined"] is False
    # The traceback starts at the library's own code, not at the host / build_component frames.
    assert "joint.py" in body["traceback"]
    assert "devhost.py" not in body["traceback"] and "build.py" not in body["traceback"]

    # A builder that raises NotApplicable declined these members: still a 422, flagged as such.
    generation = state["generation"]
    _write(
        lib_dir,
        name="devrest.renamed",
        body='raise __import__("ada.api.connections.spec", fromlist=["_"]).NotApplicable("needs a stub")',
    )
    state = _wait_for(client, lambda s: s["generation"] > generation and s["status"] != "reloading")
    r = client.post("/api/dev/connections/build", json={"spec_name": "devrest.renamed", "inputs": {}})
    assert r.status_code == 422
    assert r.json()["declined"] is True
    assert "needs a stub" in r.json()["error"]

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


WELDED_BODY = """
    import numpy as np
    import ada
    from ada.api.curves import CurveOpen3d

    H = 0.04
    landing_ = ada.Beam("landing_", (-0.5, 0, 0), (0.5, 0, 0), "BOX80x80x4x4")
    incoming_ = ada.Beam("incoming_", (0, 0, H), (0, 0, 1.0), "BOX80x80x4x4")
    part = ada.Part("welded")
    part.add_beam(landing_)
    part.add_beam(incoming_)
    corners = [(-H, -H, H), (H, -H, H), (H, H, H), (-H, H, H)]
    for i in range(4):
        p1, p2 = corners[i], corners[(i + 1) % 4]
        outward = (np.array(p1) + np.array(p2)) / 2 - np.array([0, 0, H])
        profile_normal = np.cross([0, 0, -1], outward / np.linalg.norm(outward))
        part.add_weld(
            ada.Weld(
                f"w{i}",
                weld_type="FILLET",
                members=(incoming_, landing_),
                profile=[(0, 0), (-0.003, 0), (0, 0.003)],
                xdir=ada.Direction(0, 0, -1),
                sweep_curve=CurveOpen3d([p1, p2]),
                profile_normal=ada.Direction(*profile_normal),
                throat=0.0028,
            )
        )
    return part
"""


def test_the_studio_offers_its_detail_as_a_weld_assembly(client: TestClient, lib_dir: pathlib.Path):
    from ada.simulation.subjects import parse_weld_assembly

    _write(lib_dir, name="devrest.welded", body=WELDED_BODY.strip())
    state = _wait_for(
        client, lambda s: any(x["name"] == "devrest.welded" for x in s["specs"]) and s["status"] == "ready"
    )
    assert state["status"] == "ready", state["error"]
    # The first load can already see the file while the watcher's baseline predates it, so one more
    # reload may follow. A subject's key names the library generation: let it settle first.
    state = _settled(client)
    assert state["status"] == "ready", state["error"]

    r = client.post("/api/dev/connections/subject", json={"spec_name": "devrest.welded", "inputs": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scope"] == "user:me" and body["kind"] == "weld-assembly"
    assert body["welds"] == 4
    assert body["label"] == "devrest.welded (4 welds)"
    assert body["key"].startswith("_dev/connections/devrest.welded/")
    assert body["key"].endswith(".weld-assembly.json")

    blob = client.get(f"/api/scopes/user:me/blobs/{body['key']}")
    assert blob.status_code == 200
    doc = parse_weld_assembly(blob.content)
    assert len(doc["welds"]) == 4
    assert doc["source"]["kind"] == "connection-spec" and doc["source"]["spec_name"] == "devrest.welded"
    assert "incoming" in doc["source"]["spec_inputs"]

    # The same inputs are the same key; a preview build does not delete the subject.
    again = client.post("/api/dev/connections/subject", json={"spec_name": "devrest.welded", "inputs": {}}).json()
    assert again["key"] == body["key"]
    assert (
        client.post("/api/dev/connections/build", json={"spec_name": "devrest.welded", "inputs": {}}).status_code == 200
    )
    assert client.get(f"/api/scopes/user:me/blobs/{body['key']}").status_code == 200

    # Different inputs, another subject; only the newest ones are kept.
    keys = {body["key"]}
    for i in range(12):
        t = 0.021 + 0.001 * i
        r = client.post(
            "/api/dev/connections/subject",
            json={"spec_name": "devrest.welded", "inputs": {"incoming": {"thickness": t}}},
        )
        assert r.status_code == 200, r.text
        keys.add(r.json()["key"])
    assert len(keys) == 13
    alive = [k for k in keys if client.get(f"/api/scopes/user:me/blobs/{k}").status_code == 200]
    assert len(alive) == 10
    assert r.json()["key"] in alive


def test_a_detail_without_welds_is_not_offered(client: TestClient):
    _wait_for(client, lambda s: s["status"] == "ready")
    r = client.post("/api/dev/connections/subject", json={"spec_name": "devrest.plate_on_box", "inputs": {}})
    assert r.status_code == 409, r.text
    assert "no welds" in r.json()["detail"]
    r = client.post("/api/dev/connections/subject", json={"inputs": {}})
    assert r.status_code == 400
