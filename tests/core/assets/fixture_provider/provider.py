"""The ``fixture-lines`` provider: private source format in, core schemas out."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

from ada.assets.keys import asset_key, revision_from_instant
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    ArtefactEntry,
    AssetManifest,
    BuildSpec,
)
from ada.assets.projection import build_hierarchy
from ada.assets.published import PublishedAssetProvider, StorageReader

FIXTURE_PROVIDER_ID = "fixture-lines"
SOURCE_FILENAME = "source.jsonl"  # the private format; never named inside src/ada
MESH_FILENAME = "model.glb"
BUILD_CAPABILITY = "asset-build-fixture"

# A 3-level tree in the VENDOR's vocabulary -- "ref"/"up"/"title"/"cat", not core's
# id/parent/label/kind. The translation happens here, at publish time, and nowhere else.
FIXTURE_SOURCE_LINES: tuple[dict, ...] = (
    {"_fmt": "vendor-lines/2", "_axes": ["ref", "up", "title", "cat"]},  # header line
    {"ref": "site", "up": None, "title": "Fixture Site", "cat": "site"},
    {"ref": "unit-1", "up": "site", "title": "Unit One", "cat": "unit"},
    {"ref": "unit-2", "up": "site", "title": "Unit Two", "cat": "unit"},
    {"ref": "pump-a", "up": "unit-1", "title": "Pump A", "cat": "equipment"},
    {"ref": "pump-b", "up": "unit-1", "title": "Pump B", "cat": "equipment"},
    {"ref": "tank-c", "up": "unit-2", "title": "Tank C", "cat": "equipment"},
)


# A minimal but REAL glTF binary: 'glTF', version 2, total length, one JSON chunk. Enough that the
# mesh delivery path has a byte-exact artefact to serve rather than a placeholder.
def _tiny_glb() -> bytes:
    """A minimal but LOADABLE glTF binary: one scene, one node, one triangle.

    Loadable is the point, and it was learned the hard way. A JSON-only GLB (no scene, no mesh,
    no BIN chunk) satisfies every schema check core makes and every byte-level assertion a test
    can write -- and then fails in the viewer, because a delivery is only delivered once
    something is on screen. This fixture is the `mesh` kind's only witness in CI, so it has to be
    a file the real loader can actually put in a scene.
    """
    import struct

    # One triangle: three vec3 positions, little-endian float32, plus an index buffer.
    #
    # INDEXED on purpose. Everything the viewer loads in practice comes out of a converter that
    # emits indexed geometry, and parts of the scene pipeline read `geometry.index` without
    # asking whether it is there. A non-indexed fixture therefore fails deep in the viewer for a
    # reason that has nothing to do with asset delivery -- so the witness matches what a real
    # delivery looks like.
    positions = struct.pack("<9f", 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    indices = struct.pack("<3H", 0, 1, 2)
    idx_offset = len(positions)
    bin_chunk = positions + indices
    bin_chunk += b"\x00" * (-len(bin_chunk) % 4)
    # `id_hierarchy` is what turns a loaded model into ROWS in the Files tab: core's model-cache
    # worker builds the tree from it, and a GLB without one loads into the scene and contributes
    # no tree at all. The mesh witness carries a one-node hierarchy (parent `"*"` marks the root)
    # so the `mesh` delivery kind is exercised all the way to "visible in Files", which is what
    # Phase 3's acceptance actually asks of it.
    doc = {
        "asset": {"version": "2.0", "generator": "fixture-lines"},
        "scene": 0,
        "scenes": [{"nodes": [0], "extras": {"id_hierarchy": {"0": ["Fixture mesh", "*"]}}}],
        "nodes": [{"mesh": 0, "name": "fixture-triangle"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,  # FLOAT
                "count": 3,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 1.0, 0.0],
            },
            {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},  # UNSIGNED_SHORT
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": idx_offset, "byteLength": len(indices), "target": 34963},
        ],
        "buffers": [{"byteLength": len(bin_chunk)}],
    }
    body = json.dumps(doc, separators=(",", ":")).encode()
    body += b" " * (-len(body) % 4)  # JSON chunks pad with spaces, binary chunks with zeros
    total = 12 + 8 + len(body) + 8 + len(bin_chunk)
    out = b"glTF" + (2).to_bytes(4, "little") + total.to_bytes(4, "little")
    out += len(body).to_bytes(4, "little") + b"JSON" + body
    out += len(bin_chunk).to_bytes(4, "little") + b"BIN\x00" + bin_chunk
    return out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_private_source(raw: bytes) -> list[dict]:
    """Read the vendor format. This function is the ONLY thing that knows it, and it lives in
    tests/ -- never under src/ada."""
    lines = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if not lines or lines[0].get("_fmt") != "vendor-lines/2":
        raise ValueError("not a vendor-lines/2 document")
    return lines[1:]


class FakeStore:
    """An in-memory stand-in for a scope's object storage."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def put(self, key: str, data: bytes) -> None:
        self.blobs[key] = data

    def put_bytes(self, key: str, data: bytes, content_encoding: str | None = None) -> None:
        """Same shape as the sync storage facade a builder is actually handed
        (``ada.comms.rest.worker.source_nodes._SyncStorageFacade.put_bytes``) -- ``content_encoding``
        is accepted and ignored, since this in-memory store has no transport encoding to apply."""
        del content_encoding
        self.put(key, data)

    def list_prefix(self, prefix: str) -> Iterable[str]:
        return [k for k in sorted(self.blobs) if k.startswith(prefix)]

    def get_bytes(self, key: str) -> bytes:
        try:
            return self.blobs[key]
        except KeyError:
            raise FileNotFoundError(key) from None

    def reader(self) -> StorageReader:
        return StorageReader(list_prefix=self.list_prefix, get_bytes=self.get_bytes)


def publish_fixture(
    store: FakeStore,
    *,
    collection: str = "fixture-a",
    instant: str = "2026-09-21T14:30:01Z",
    mesh_node: str = "pump-a",
) -> str:
    """Translate the private source into core's schemas and write a full revision.

    Writes, in order: the source blob, the per-node manifests, the collection hierarchy, and the
    collection manifest LAST -- so a half-written publish is invisible rather than
    discoverable-and-broken.
    """
    revision = revision_from_instant(instant)
    raw = ("\n".join(json.dumps(line) for line in FIXTURE_SOURCE_LINES)).encode("utf-8")
    source_key = asset_key(collection, collection, revision, SOURCE_FILENAME)
    store.put(source_key, raw)

    records = _parse_private_source(raw)
    children = {r["ref"] for r in records if r["up"] is not None}
    parents = {r["up"] for r in records if r["up"] is not None}

    nodes = []
    for rec in records:
        ref = rec["ref"]
        is_leaf = ref not in parents
        delivery = "mesh" if ref == mesh_node else ("build" if is_leaf else "")
        nodes.append(
            {
                "id": ref,
                "parent": rec["up"],
                "label": rec["title"],  # vendor "title" -> core "label"
                "kind": rec["cat"],  # vendor "cat" -> core "kind"
                "leaf": is_leaf,
                "delivery": delivery,
            }
        )

    # One manifest per node subject.
    for node in nodes:
        artefacts = [ArtefactEntry(role="source", key=source_key, sha256=_sha(raw), size=len(raw))]
        build = None
        if node["delivery"] == "build":
            build = BuildSpec(
                capability=BUILD_CAPABILITY,
                options={"ref": node["id"], "source_key": source_key},
                fingerprint_inputs=("source_key", "ref"),
            )
        if node["delivery"] == "mesh":
            glb = _tiny_glb()
            mesh_key = asset_key(collection, node["id"], revision, MESH_FILENAME)
            store.put(mesh_key, glb)
            artefacts.append(ArtefactEntry(role="mesh", file=MESH_FILENAME, sha256=_sha(glb), size=len(glb)))
        manifest = AssetManifest(
            provider=FIXTURE_PROVIDER_ID,
            collection=collection,
            subject=node["id"],
            revision=revision,
            node=node["id"],
            produced_at=instant,
            published_at=instant,
            delivery=node["delivery"] or "none",
            build=build,
            artefacts=tuple(artefacts),
            counts={"nodes": 1},
        )
        store.put(asset_key(collection, node["id"], revision, MANIFEST_FILENAME), manifest.to_json())

    slice_ = build_hierarchy(
        provider=FIXTURE_PROVIDER_ID, collection=collection, produced_at=instant, nodes=nodes, depth=3
    )
    store.put(asset_key(collection, collection, revision, HIERARCHY_FILENAME), slice_.to_json())

    # Collection manifest LAST.
    hierarchy_bytes = slice_.to_json()
    store.put(
        asset_key(collection, collection, revision, MANIFEST_FILENAME),
        AssetManifest(
            provider=FIXTURE_PROVIDER_ID,
            collection=collection,
            subject=collection,
            revision=revision,
            produced_at=instant,
            published_at=instant,
            delivery="none",
            artefacts=(
                ArtefactEntry(
                    role="hierarchy", file=HIERARCHY_FILENAME, sha256=_sha(hierarchy_bytes), size=len(hierarchy_bytes)
                ),
                ArtefactEntry(role="source", file=SOURCE_FILENAME, sha256=_sha(raw), size=len(raw)),
            ),
            counts={"nodes": len(nodes), "leaves": len(children & {n["id"] for n in nodes if n["leaf"]})},
        ).to_json(),
    )
    return revision


class FixtureLinesProvider(PublishedAssetProvider):
    """Rides PublishedAssetProvider entirely -- it has no runtime hierarchy code of its own,
    which is exactly the claim Decision 1 makes about private-format providers."""

    delivery_kinds = ("mesh", "build")

    def __init__(self, reader: StorageReader):
        super().__init__(reader, provider_id=FIXTURE_PROVIDER_ID)


def register_fixture_provider(store: FakeStore) -> None:
    from ada.assets.registry import register_asset_provider

    register_asset_provider(FIXTURE_PROVIDER_ID, lambda: FixtureLinesProvider(store.reader()), label="Fixture (lines)")
