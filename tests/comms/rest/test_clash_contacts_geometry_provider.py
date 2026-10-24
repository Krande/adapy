"""Annotating a convex-contact run through a GEOMETRY PROVIDER: ``POST .../clash-contacts/annotate``
(and ``/runs``) with ``geometry_provider``.

A run measured over a MESH-ONLY provider's members cannot be read into objects by that provider, so
its clashes get roles from the meshes and no specs. Through a provider that publishes the same
elements under its own ids (matched by name, ``ada.clash.geometry_source``) the members are read
from that provider's publish and the clashes bind -- the classic check's "Geometry from", applied to
a stored run. Pinned here:

* a group run and a node run bind through the provider, and say so in their provenance;
* without the provider the same run does not rebuild (the mesh-only provider has no reader);
* a provider nobody can read is refused by name, before anything is enqueued or stored;
* a file run has no provider, so the choice is dropped for one.

The fixture is ``test_clash_geometry_plan.py``'s: one collection two providers publish into.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-contacts-gp-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.assets.keys import asset_key  # noqa: E402
from ada.assets.manifest import (  # noqa: E402
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)
from ada.assets.projection import build_hierarchy  # noqa: E402
from ada.clash.sidecar import contact_id, group_geometry_sha256, parse_run  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings  # noqa: E402

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
COLLECTION = "plant"
MESH = "mesh-only"
READER = "member-reader"
R1 = "20260901T000000Z"
R2 = "20260902T000000Z"
# Z100-BEAMS3 starts ON Z100-BEAMS2: a T, BEAMS3 incoming.
LINES = b"\n".join([b"Z100-BEAMS2 0 0 0 2 0 0", b"Z100-BEAMS3 1 0 0 1 2 0"])


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


def _write(scope_dir: Path, key: str, data: bytes) -> None:
    dest = scope_dir / key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def _row(node_id, parent, label, leaf=True) -> dict:
    return {"id": node_id, "parent": parent, "label": label, "kind": "item", "leaf": leaf}


def _publish(scope_dir: Path, *, provider, subject, revision, nodes, build_source=None) -> None:
    spine = build_hierarchy(provider=provider, collection=COLLECTION, produced_at=revision, nodes=nodes)
    _write(scope_dir, asset_key(COLLECTION, subject, revision, HIERARCHY_FILENAME), spine.to_json())
    artefacts = [ArtefactEntry(role="hierarchy", file=HIERARCHY_FILENAME, sha256="", size=1)]
    build = None
    if build_source is not None:
        key = asset_key(COLLECTION, subject, revision, "lines.txt")
        _write(scope_dir, key, build_source)
        artefacts.append(ArtefactEntry(role="source", key=key, sha256="", size=len(build_source)))
        build = BuildSpec(capability="build-lines", options={"source_key": key})
    manifest = AssetManifest(
        provider=provider,
        collection=COLLECTION,
        subject=subject,
        revision=revision,
        node=subject,
        produced_at=revision,
        published_at=revision,
        delivery="build" if build else "mesh",
        build=build,
        artefacts=tuple(artefacts),
    )
    _write(scope_dir, asset_key(COLLECTION, subject, revision, MANIFEST_FILENAME), manifest.to_json())


class _LinesConcepts:
    """The member-reader's reader: one beam per line, named as the line names it (no leading ``/``)."""

    def concepts(self, options, *, storage, scope=None, node=None):
        import ada

        part = ada.Part(node or "lines")
        for line in storage.get_bytes(options["source_key"]).decode().splitlines():
            name, *xyz = line.split()
            x0, y0, z0, x1, y1, z1 = (float(v) for v in xyz)
            part.add_beam(ada.Beam(name, (x0, y0, z0), (x1, y1, z1), "IPE200"))
        return part


@pytest.fixture
def client(tmp_path, monkeypatch):
    from ada.clash import contacts

    # No GLB behind these runs: mesh-derived roles have nothing to read, and none are needed when
    # the members bind.
    monkeypatch.setattr(contacts, "member_vertices", lambda run, get_bytes, read_members=None: {})
    scope_dir = tmp_path / SCOPE_DIR
    _publish(
        scope_dir,
        provider=MESH,
        subject="fnzone",
        revision=R2,
        nodes=[
            _row("fnzone", None, "/Z100-ZONE", leaf=False),
            _row("fn01", "fnzone", "/Z100-BEAMS2"),
            _row("fn02", "fnzone", "/Z100-BEAMS3"),
        ],
    )
    _publish(
        scope_dir,
        provider=READER,
        subject="ref-100",
        revision=R1,
        build_source=LINES,
        nodes=[
            _row("ref-100", None, "/Z100-ZONE", leaf=False),
            _row("ref-101", "ref-100", "Z100-BEAMS2"),
            _row("ref-102", "ref-100", "Z100-BEAMS3"),
        ],
    )
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


@pytest.fixture
def reader():
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts

    register_asset_concepts(READER, lambda: _LinesConcepts())
    yield
    clear_asset_concepts()


def _wait(client, job_id, timeout_s=60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


MESH_NODE = {
    "kind": "node",
    "provider": MESH,
    "collection": COLLECTION,
    "subject": "fnzone",
    "revision": R2,
    "node": None,
}

_CONTACT = {
    "members": [0, 1],
    "near_points": [[1.0, 0.05, 0.0], [1.0, 0.05, 0.0]],
    "normal": [0.0, 1.0, 0.0],
    "distance": -0.002,
    "contact_area": 0.02,
    "patches": [],
}


def _run(target: dict, members: list[dict], *, run_id: str = "r1", **extra) -> dict:
    ids = [m["id"] for m in members]
    return {
        "schema": "ada.clash/contacts@1",
        "run_id": run_id,
        "created_at": "2026-10-16T12:00:00Z",
        "engine": "wasm",
        "checker": "convex-contact",
        "target": target,
        "geometry": extra.pop("geometry", {"key": "_derived/x/model.glb", "sha256": "ab" * 32, "up_axis": "z"}),
        "options": {"security_margin": 0.01, "min_contact_area": 1e-6, "line_contact_thickness": 1e-3},
        "members": members,
        "contacts": [{"id": contact_id(ids), **_CONTACT}],
        "provenance": {},
        **extra,
    }


def _group_run() -> dict:
    """A group run as a browser posts it: one part (the mesh-only zone), its two members by their
    full names -- what the mesh provider's GLB calls them."""
    parts = [{"key": "_derived/zone/model.glb", "sha256": "cd" * 32, "source": MESH_NODE}]
    return _run(
        {"kind": "group", "group_id": "g1"},
        [
            {"id": "0:11", "name": "/Z100-BEAMS2", "element": "11", "part": 0},
            {"id": "0:12", "name": "/Z100-BEAMS3", "element": "12", "part": 0},
        ],
        geometry={"key": "group:g1", "sha256": group_geometry_sha256(parts), "up_axis": "z", "parts": parts},
        group={
            "id": "g1",
            "name": "Deck 3",
            "members": [
                {"target": MESH_NODE, "element": "/Z100-BEAMS2", "path": ["/Z100-BEAMS2"]},
                {"target": MESH_NODE, "element": "/Z100-BEAMS3", "path": ["/Z100-BEAMS3"]},
            ],
        },
    )


def _annotated(client, key: str, **body) -> tuple[dict, object]:
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/annotate", json={"run_key": key, **body})
    assert r.status_code == 200, r.text
    assert _wait(client, r.json()["job_id"])["status"] == "done"
    got = client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": key}).json()
    return r.json(), parse_run(got)


def test_a_group_run_binds_through_the_geometry_provider(client, reader):
    stored = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": _group_run(), "annotate": False})
    assert stored.status_code == 200, stored.text
    assert stored.json()["job_id"] is None
    key = stored.json()["run_key"]

    # Each member's own provider publishes meshes only: nothing rebuilds.
    _, own = _annotated(client, key)
    assert "no model of this group rebuilt" in own.provenance["applicability"]["error"]
    assert "geometry_provider" not in own.provenance

    answer, run = _annotated(client, key, geometry_provider=READER)
    assert answer["geometry_provider"] == READER
    # Both members read from the reader's publish of the same names, and bound by name -- the
    # reader names them without the leading "/" the mesh provider's GLB carries.
    assert run.provenance["applicability"] == {"resolved": 1, "unbound": 0}
    assert run.provenance["geometry_provider"] == READER
    assert len(run.provenance["geometry_remap"]) == 2 and run.provenance["geometry_unmatched"] == []
    assert run.provenance["roles"]["members"] == 1
    assert run.contacts[0].roles == {"incoming": 1, "landing": 0}

    # And annotated again without it, the provider's provenance does not linger.
    _, again = _annotated(client, key)
    assert "geometry_provider" not in again.provenance


def test_a_member_the_provider_lacks_is_reported_and_left_unbound(client, reader):
    doc = _group_run()
    doc["group"]["members"].append({"target": MESH_NODE, "element": "/LOST", "path": ["/LOST"]})
    stored = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc, "geometry_provider": READER})
    assert stored.status_code == 200, stored.text
    assert _wait(client, stored.json()["job_id"])["status"] == "done"
    run = parse_run(
        client.get(f"/api/scopes/{SCOPE}/clash-contacts/run", params={"key": stored.json()["run_key"]}).json()
    )
    assert [m["element"] for m in run.provenance["geometry_unmatched"]] == ["/LOST"]
    assert run.provenance["applicability"] == {"resolved": 1, "unbound": 0}


def test_a_node_run_binds_through_the_geometry_provider(client, reader):
    target = {"kind": "node", "collection": COLLECTION, "subject": "fnzone", "revision": R2}
    doc = _run(target, [{"id": "11", "name": "/Z100-BEAMS2"}, {"id": "12", "name": "/Z100-BEAMS3"}])
    key = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc, "annotate": False}).json()[
        "run_key"
    ]
    _, run = _annotated(client, key, geometry_provider=READER)
    assert run.provenance["applicability"] == {"resolved": 1, "unbound": 0}
    assert run.provenance["geometry_provider"] == READER


def test_a_provider_nobody_reads_is_refused_before_anything_is_stored(client):
    doc = _group_run()
    r = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc, "geometry_provider": READER})
    assert r.status_code == 409 and READER in r.json()["detail"], r.text
    listing = client.get(
        f"/api/scopes/{SCOPE}/clash-contacts/runs", params={"target": '{"kind":"group","group_id":"g1"}'}
    )
    assert listing.json()["runs"] == []

    key = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc, "annotate": False}).json()[
        "run_key"
    ]
    a = client.post(f"/api/scopes/{SCOPE}/clash-contacts/annotate", json={"run_key": key, "geometry_provider": READER})
    assert a.status_code == 409 and READER in a.json()["detail"], a.text
    bad = client.post(f"/api/scopes/{SCOPE}/clash-contacts/annotate", json={"run_key": key, "geometry_provider": 3})
    assert bad.status_code == 400


def test_a_file_run_has_no_provider_so_the_choice_is_dropped(client):
    doc = _run({"kind": "file", "source_key": "models/a.ifc"}, [{"id": "1", "name": "g1"}, {"id": "2", "name": "g2"}])
    key = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc, "annotate": False}).json()[
        "run_key"
    ]
    # No reader is registered, and still not refused: a file's members are read from the file.
    answer, run = _annotated(client, key, geometry_provider=READER)
    assert answer["geometry_provider"] is None
    assert "geometry_provider" not in run.provenance


def test_a_provider_with_only_a_tree_says_the_geometry_must_be_requested(client, reader, tmp_path, monkeypatch):
    """The provider lists the zone (a tree-only export) but publishes no subject that reads into
    objects: the run's applicability carries the ``no_provider_geometry`` shape, so a client can
    offer to request the geometry instead of showing an opaque failure."""
    monkeypatch.setitem(globals(), "COLLECTION", "yard")
    scope_dir = tmp_path / SCOPE_DIR
    _publish(
        scope_dir,
        provider=MESH,
        subject="fnyard",
        revision=R2,
        nodes=[_row("fnyard", None, "/Y1-ZONE", leaf=False), _row("fn01", "fnyard", "/Y1-BEAMS2")],
    )
    _publish(
        scope_dir,
        provider=READER,
        subject="yard",
        revision=R1,
        nodes=[_row("ref-1", None, "/Y1-ZONE", leaf=False)],
    )
    target = {"kind": "node", "collection": "yard", "subject": "fnyard", "revision": R2}
    doc = _run(target, [{"id": "11", "name": "/Y1-BEAMS2"}, {"id": "12", "name": "/Y1-BEAMS3"}])
    key = client.post(f"/api/scopes/{SCOPE}/clash-contacts/runs", json={"run": doc, "annotate": False}).json()[
        "run_key"
    ]
    _, run = _annotated(client, key, geometry_provider=READER)
    applicability = run.provenance["applicability"]
    assert applicability["code"] == "no_provider_geometry"
    assert applicability["geometry_provider"] == READER and applicability["collections"] == ["yard"]
    assert "request it from" in applicability["error"]
