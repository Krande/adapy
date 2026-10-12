"""``GET /assets/sources`` reads one manifest per published revision and one summary per publish
job -- thousands in a scope a few sweeps have filled. Those reads must be BOUNDED: the storage
client opens a new connection for every request it cannot serve from an idle one, so an unbounded
fan-out opened one socket per object at once and ran the object store out of file descriptors."""

import asyncio
import json
from types import SimpleNamespace

from ada.assets.keys import asset_key
from ada.assets.manifest import AssetManifest
from ada.comms.rest.routes import assets as assets_routes
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import FileEntry

COLLECTION = "plant"


class _CountingStorage:
    """Answers the two listings and every read, recording the most reads ever in flight at once."""

    def __init__(self, n_revisions: int, n_summaries: int) -> None:
        self.manifests: dict[str, bytes] = {}
        for i in range(n_revisions):
            subject, revision = f"node-{i:04d}", "20260901T100000Z"
            self.manifests[asset_key(COLLECTION, subject, revision, "asset.json")] = AssetManifest(
                provider="tree-maker",
                collection=COLLECTION,
                subject=subject,
                revision=revision,
                node=subject,
                produced_at="2026-09-01T10:00:00Z",
                published_at="2026-09-01T10:00:00Z",
                delivery="none",
            ).to_json()
        self.summaries = {
            f"_derived/assets/_publish/tree-maker/job{i:04d}/summary.json": json.dumps(
                {"collection": COLLECTION, "revision": "20260901T100000Z"}
            ).encode()
            for i in range(n_summaries)
        }
        self.in_flight = 0
        self.peak = 0
        self.reads = 0

    async def list_prefix(self, scope, prefix: str) -> list[FileEntry]:
        keys = self.manifests if prefix.startswith("assets/") else self.summaries
        return [FileEntry(key=k, size=len(v)) for k, v in keys.items()]

    async def get_bytes(self, scope, key: str) -> bytes:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.001)  # yield, so an unbounded gather really would pile up
            self.reads += 1
            return self.manifests.get(key) or self.summaries[key]
        finally:
            self.in_flight -= 1


def test_publish_sources_bounds_its_storage_fan_out():
    storage = _CountingStorage(n_revisions=400, n_summaries=60)
    ctx = SimpleNamespace(storage=storage)

    groups, orphans = asyncio.run(assets_routes._publish_sources(ctx, Scope.shared()))

    assert storage.reads == 460  # every manifest and summary still read...
    assert storage.peak <= assets_routes._MANIFEST_READ_CONCURRENCY  # ...but never all at once
    group = groups[(COLLECTION, "20260901T100000Z", "tree-maker")]
    assert len(group["subjects"]) == 400
    assert len(group["derived"]) == 60  # each summary linked to the revision it wrote
    assert orphans == []
