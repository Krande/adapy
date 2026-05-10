"""Admin storage list + delete behaviour around the streaming-FEA
artefact tree. Two things matter:

* The streaming bake produces ``_derived/<src>.fea/<file>`` keys
  that the older ``_derived_source_of`` parser didn't recognise —
  they used to land as phantom orphans (mesh.glb misparsed) or be
  silently dropped (.bin / .json). The list endpoint must now
  attribute them to their real source.
* Deleting any one FEA artefact has to reap the whole tree, since
  the manifest references the other artefacts by name; a partial
  set leaves the picker rendering against stale data.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault(
    "ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-")
)

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.scope import Scope  # noqa: E402


def _settings(tmp_path: pathlib.Path) -> Settings:
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
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="",
        ),
        database_url="",
    )


def _put(client: TestClient, key: str, data: bytes) -> None:
    """Stage bytes under the shared scope, bypassing the public PUT
    so we can install arbitrary derived-blob layouts. The blob_put
    endpoint refuses writes to ``_derived/`` for safety."""
    storage = None
    for route in client.app.routes:
        ep = getattr(route, "endpoint", None)
        if ep is None:
            continue
        cell = getattr(ep, "__closure__", None)
        if not cell:
            continue
        for c in cell:
            v = c.cell_contents
            if v.__class__.__name__ == "Storage":
                storage = v
                break
        if storage is not None:
            break
    asyncio.get_event_loop().run_until_complete(
        storage.put_bytes(Scope.shared(), key, data)
    )


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_admin_list_attributes_fea_artefacts_to_real_source(app_client: TestClient):
    """A baked source (``models/wall.rmed``) with a streaming-FEA
    artefact tree under ``_derived/models/wall.rmed.fea/`` shows up
    as one entry with the artefacts listed under its ``derived``
    field — no phantom orphans for the misparsed mesh GLB, no
    silent drops for the JSON / bin files."""

    src = "models/wall.rmed"
    _put(app_client, src, b"rmed-bytes")
    _put(
        app_client,
        f"_derived/{src}.fea/fea.manifest.json",
        b'{"version":1}',
    )
    _put(app_client, f"_derived/{src}.fea/fea.mesh.glb", b"glb-bytes")
    _put(app_client, f"_derived/{src}.fea/fea.mesh.edges.bin", b"edges-bytes")
    _put(app_client, f"_derived/{src}.fea/fea.DEPL.bin", b"\x00" * 16)

    r = app_client.get("/api/admin/scopes/shared/files")
    assert r.status_code == 200, r.text
    files = r.json()["files"]

    by_key = {f["key"]: f for f in files}
    assert src in by_key, f"source not listed: {by_key.keys()}"
    entry = by_key[src]
    assert entry.get("orphan") is not True
    derived = entry["derived"]
    derived_keys = {d["key"] for d in derived}
    assert f"_derived/{src}.fea/fea.manifest.json" in derived_keys
    assert f"_derived/{src}.fea/fea.mesh.glb" in derived_keys
    assert f"_derived/{src}.fea/fea.mesh.edges.bin" in derived_keys
    assert f"_derived/{src}.fea/fea.DEPL.bin" in derived_keys

    # No phantom orphans (misparsed `.fea/fea.mesh` etc.) leaking.
    orphan_keys = [f["key"] for f in files if f.get("orphan")]
    assert not any(".fea/" in k or k.endswith(".fea") for k in orphan_keys), (
        f"phantom orphans leaked: {orphan_keys}"
    )


def test_delete_one_fea_artefact_reaps_whole_tree(app_client: TestClient):
    """Deleting any single FEA artefact must wipe the rest of the
    tree — the manifest references the other artefacts by name, so a
    partial set leaves the picker rendering against stale data."""

    src = "models/wall.rmed"
    _put(app_client, src, b"rmed-bytes")
    artefacts = [
        f"_derived/{src}.fea/fea.manifest.json",
        f"_derived/{src}.fea/fea.mesh.glb",
        f"_derived/{src}.fea/fea.mesh.edges.bin",
        f"_derived/{src}.fea/fea.DEPL.bin",
    ]
    for k in artefacts:
        _put(app_client, k, b"x")

    # User clicks "delete cached" on just one of the artefacts.
    target = f"_derived/{src}.fea/fea.DEPL.bin"
    r = app_client.delete(f"/api/admin/scopes/shared/blobs/{target}")
    assert r.status_code == 200, r.text
    deleted = set(r.json()["deleted"])
    # The whole tree, not just the picked file.
    for k in artefacts:
        assert k in deleted, f"missing from reap: {k}"

    # Source still exists; the bake can re-run if the user re-opens
    # the picker.
    r = app_client.get("/api/admin/scopes/shared/files")
    by_key = {f["key"]: f for f in r.json()["files"]}
    assert src in by_key
    assert by_key[src]["derived"] == []


def test_source_delete_reaps_fea_artefact_tree_and_meta_and_profile(
    app_client: TestClient,
):
    """Deleting the source must also wipe the streaming-FEA artefact
    tree, the legacy result-meta cache, and any profile blobs —
    not just the bare ``<src>.<fmt>`` legacy GLB the old enumeration
    knew about."""

    src = "models/wall.rmed"
    _put(app_client, src, b"rmed-bytes")
    extras = [
        f"_derived/{src}.fea/fea.manifest.json",
        f"_derived/{src}.fea/fea.mesh.glb",
        f"_derived/{src}.fea/fea.DEPL.bin",
        f"_derived/{src}.meta.json",
        f"_derived/{src}.abc123.prof",
    ]
    for k in extras:
        _put(app_client, k, b"x")

    r = app_client.delete(f"/api/admin/scopes/shared/blobs/{src}")
    assert r.status_code == 200, r.text
    deleted = set(r.json()["deleted"])
    assert src in deleted
    for k in extras:
        assert k in deleted, f"missing from source-delete reap: {k}"


def test_prefix_collision_does_not_steal_other_sources_artefacts(
    app_client: TestClient,
):
    """``wall.rmed`` and ``wall.rmed.bak`` both have derived blobs
    under prefixes that share a common stem. Each deletion must
    only touch its own source's artefacts."""

    src_a = "wall.rmed"
    src_b = "wall.rmed.bak"
    _put(app_client, src_a, b"a")
    _put(app_client, src_b, b"b")
    a_artefacts = [
        f"_derived/{src_a}.fea/fea.manifest.json",
        f"_derived/{src_a}.fea/fea.mesh.glb",
    ]
    b_artefacts = [
        f"_derived/{src_b}.fea/fea.manifest.json",
        f"_derived/{src_b}.fea/fea.mesh.glb",
    ]
    for k in a_artefacts + b_artefacts:
        _put(app_client, k, b"x")

    # Delete one artefact of src_a; expect src_a's tree wiped and
    # src_b's tree intact.
    r = app_client.delete(
        f"/api/admin/scopes/shared/blobs/_derived/{src_a}.fea/fea.manifest.json",
    )
    assert r.status_code == 200, r.text
    deleted = set(r.json()["deleted"])
    for k in a_artefacts:
        assert k in deleted
    for k in b_artefacts:
        assert k not in deleted, f"crosstalk reaped {k}"
