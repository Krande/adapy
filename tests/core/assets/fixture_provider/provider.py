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
    body = json.dumps({"asset": {"version": "2.0", "generator": "fixture-lines"}}, separators=(",", ":")).encode()
    body += b" " * (-len(body) % 4)
    header = b"glTF" + (2).to_bytes(4, "little") + (12 + 8 + len(body)).to_bytes(4, "little")
    return header + len(body).to_bytes(4, "little") + b"JSON" + body


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
