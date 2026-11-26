"""Core's convex-contact check end to end, the way the viewer's clash run reaches a worker
(``POST /scopes/{scope}/clash-contacts`` on the queue-less viewer), on a synthetic structure of box
members: face contacts, an end-to-end splice, an edge graze, a member driven into another along a line
under the area, the same only resting, and a member ending on another along a line.

The run keeps exactly the connections, whether it is annotated or not: the criteria apply when the
GLB is measured, not only when the run's specs are resolved.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-structure-"))

import pytest  # noqa: E402

clash = pytest.importorskip("adacpp.clash")
if not hasattr(clash, "find_glb_contacts"):  # pragma: no cover - adacpp without the GLB check
    pytest.skip("this adacpp has no find_glb_contacts", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

import ada  # noqa: E402
from ada.clash.sidecar import parse_run  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
TARGET = {"kind": "file", "source_key": "models/structure.ifc"}
GLB_KEY = "_derived/models/structure.ifc.glb"

BOX = "BOX200x200x10x10"
S = 0.5**0.5
HALF_DIAGONAL = 0.1 * 2**0.5

#: What the check keeps with its defaults.
KEPT = {("col", "girder"), ("s1", "s2"), ("brace", "low"), ("low3", "stub")}


def _structure():
    driven = 0.07 + HALF_DIAGONAL - 0.005
    resting = 0.07 + HALF_DIAGONAL
    on_low3 = 0.15 + HALF_DIAGONAL
    return ada.Assembly("a") / (
        ada.Part("p")
        / [
            # A girder square into a column's side, and a splice: face contacts.
            ada.Beam("col", (0, 0, 0), (0, 0, 3), BOX),
            ada.Beam("girder", (0.1, 0, 1.5), (2, 0, 1.5), BOX),
            ada.Beam("s1", (0, 3, 0), (1, 3, 0), BOX),
            ada.Beam("s2", (1, 3, 0), (2, 3, 0), BOX),
            # Side by side, sharing one edge: a graze.
            ada.Beam("g1", (0, 6, 0), (1, 6, 0), BOX),
            ada.Beam("g2", (0, 6.2, 0.2), (1, 6.2, 0.2), BOX),
            # Turned 45 degrees and driven 5 mm edge-first into a 140 mm box: a 140 mm² line.
            ada.Beam("low", (5, -1, 0), (5, 1, 0), "BOX140x140x10x10"),
            ada.Beam("brace", (4, 0, driven), (6, 0, driven), BOX, up=(0, S, S)),
            # The same, only resting on it.
            ada.Beam("low2", (8, -1, 0), (8, 1, 0), "BOX140x140x10x10"),
            ada.Beam("rail", (7, 0, resting), (9, 0, resting), BOX, up=(0, S, S)),
            # Turned 45 degrees and ending on a 300 mm box: a 300 mm line at a member end.
            ada.Beam("low3", (11, -1, 0), (11, 1, 0), "BOX300x300x10x10"),
            ada.Beam("stub", (10, 0, on_low3), (11.15, 0, on_low3), BOX, up=(0, S, S)),
        ]
    )


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


@pytest.fixture(scope="module")
def structure_bytes(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("structure")
    model = _structure()
    ifc, glb = tmp / "structure.ifc", tmp / "structure.glb"
    model.to_ifc(ifc)
    model.to_gltf(glb)
    return ifc.read_bytes(), glb.read_bytes()


@pytest.fixture
def client(tmp_path, structure_bytes):
    scope_dir = tmp_path / "store" / SCOPE_DIR
    for key, data in ((TARGET["source_key"], structure_bytes[0]), (GLB_KEY, structure_bytes[1])):
        dest: Path = scope_dir / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    with TestClient(create_app(_settings(tmp_path / "store"))) as c:
        yield c


def _run(client, **body):
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts", json={"target": TARGET, "glb_key": GLB_KEY, **body})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + 120.0
    while True:
        status = client.get(f"/api/convert/{r.json()['job_id']}").json()
        if status["status"] != "running":
            break
        assert time.monotonic() < deadline, f"the clash run did not finish: {status}"
        time.sleep(0.05)
    assert status["status"] == "done", status
    got = client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": r.json()["run_key"]})
    assert got.status_code == 200, got.text
    return parse_run(got.json())


def _pairs(run) -> set[tuple[str, ...]]:
    return {tuple(sorted(run.names(c))) for c in run.contacts}


@pytest.mark.parametrize("annotate", [True, False])
def test_a_worker_run_keeps_the_connections_and_drops_the_grazes(client, annotate):
    run = _run(client, annotate=annotate)
    assert _pairs(run) == KEPT
    assert run.options["keep_penetration_depth"] == pytest.approx(0.002)
    assert run.options["min_contact_width_ratio"] == pytest.approx(0.5)
    assert run.provenance["below_min_width"] >= 1  # g1 on g2
    (driven,) = [c for c in run.contacts if set(run.names(c)) == {"brace", "low"}]
    assert driven.penetration_depth == pytest.approx(0.005, abs=1e-4)
    assert driven.contact_area < run.options["min_contact_area"]
    if annotate:
        assert all(c.roles is not None for c in run.contacts)


def test_the_options_turn_the_rules_off(client):
    assert _pairs(_run(client, options={"keep_penetration_depth": 0})) == KEPT - {("brace", "low")}
    assert _pairs(_run(client, options={"min_contact_width_ratio": 0})) == KEPT | {("g1", "g2")}
