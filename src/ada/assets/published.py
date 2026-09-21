"""``published`` -- the built-in provider that reads the store itself.

This is what makes a private source format cheap. A provider with its own format needs NO runtime
hierarchy code: it publishes ``hierarchy.json`` + ``asset.json`` under the key grammar, and this
provider serves them back. What such a provider must supply is a publisher (to get the files in)
and, if its nodes claim ``build``, a builder. Nothing else.

Everything here reads blobs core wrote the schema for. It never opens a provider's source
artefact -- those are an opaque ``role`` on an opaque ``file``, and core's only uses for them are
refcounting on unpublish and passing keys through to a job.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from ada.assets.index import AssetIndex, fold_listing
from ada.assets.keys import ASSET_PREFIX, asset_key
from ada.assets.manifest import (
    HIERARCHY_FILENAME,
    MANIFEST_FILENAME,
    AssetManifest,
    parse_manifest,
)
from ada.assets.projection import HierarchySlice, parse_hierarchy
from ada.assets.provider import (
    BuildDelivery,
    CollectionInfo,
    DeliveryClaim,
    MeshDelivery,
)

__all__ = ["PublishedAssetProvider", "StorageReader"]


class StorageReader:
    """The slice of a scope's storage this provider needs.

    Deliberately two callables rather than a storage object: it keeps this module testable with a
    dict, and keeps core's storage API from leaking into the provider contract.
    """

    def __init__(self, list_prefix: Callable[[str], Iterable[str]], get_bytes: Callable[[str], bytes]):
        self.list_prefix = list_prefix
        self.get_bytes = get_bytes


class PublishedAssetProvider:
    id = "published"
    delivery_kinds = ("mesh", "build")

    def __init__(self, reader: StorageReader, *, provider_id: str = "published"):
        self._reader = reader
        self.id = provider_id

    # -- tree -------------------------------------------------------------------------------

    def index(self, collection: str | None = None) -> AssetIndex:
        prefix = f"{ASSET_PREFIX}/{collection}/" if collection else f"{ASSET_PREFIX}/"
        return fold_listing(self._reader.list_prefix(prefix))

    def collections(self, scope: Any = None) -> list[CollectionInfo]:
        idx = self.index()
        out = []
        for name, subjects in idx.collections.items():
            latest = max((r.revision for s in subjects for r in s.revisions), default=None)
            out.append(CollectionInfo(id=name, label=name, node_count=len(subjects), latest_revision=latest))
        return out

    def hierarchy(self, scope: Any, collection: str, *, root: str | None = None, depth: int = 1) -> HierarchySlice:
        """Serve the stored projection.

        ``root=None`` is the collection index, stored at the collection-level subject; a named
        root is the subtree spine stored at that node's subject. Both are plain blobs -- the
        browser cannot tell a published provider from a live one, which is the point.
        """
        subject = root or collection
        revision = self._latest_revision(collection, subject)
        if revision is None:
            raise FileNotFoundError(
                f"no published hierarchy for collection={collection!r} subject={subject!r} "
                f"(nothing under {ASSET_PREFIX}/{collection}/{subject}/)"
            )
        key = asset_key(collection, subject, revision, HIERARCHY_FILENAME)
        return parse_hierarchy(self._reader.get_bytes(key))

    # -- delivery ---------------------------------------------------------------------------

    def delivery(self, scope: Any, collection: str, node: str, *, revision: str | None = None) -> DeliveryClaim | None:
        manifest = self.manifest(collection, node, revision=revision)
        if manifest is None or manifest.delivery == "none":
            return None
        if manifest.delivery == "build":
            spec = manifest.build
            return BuildDelivery(
                capability=spec.capability,
                revision=manifest.revision,
                options=dict(spec.options),
                fingerprint_inputs=tuple(spec.fingerprint_inputs),
            )
        # mesh: the URL is minted by the route from the stored artefact, never cached here.
        mesh_artefact = next((a for a in manifest.artefacts if a.role == "mesh"), None)
        if mesh_artefact is None:
            return None
        key = mesh_artefact.key or asset_key(collection, node, manifest.revision, mesh_artefact.file)
        return MeshDelivery(url=key, revision=manifest.revision)

    def manifest(self, collection: str, subject: str, *, revision: str | None = None) -> AssetManifest | None:
        """Read ``asset.json``. Resolves to the newest revision that HAS one.

        Manifests are written last, so a newer revision without one is a half-written publish and
        must not shadow the last good revision.
        """
        if revision is None:
            revision = self._latest_revision(collection, subject)
            if revision is None:
                return None
        key = asset_key(collection, subject, revision, MANIFEST_FILENAME)
        try:
            raw = self._reader.get_bytes(key)
        except (FileNotFoundError, KeyError):
            return None
        manifest = parse_manifest(raw)
        if not manifest.matches_key(_KeyView(collection, subject, revision)):
            raise ValueError(
                f"{key}: manifest records collection/subject/revision "
                f"{manifest.collection}/{manifest.subject}/{manifest.revision}, which is not where it is "
                f"stored. Neither is auditable if they disagree."
            )
        return manifest

    def _latest_revision(self, collection: str, subject: str) -> str | None:
        entry = self.index(collection).subject(collection, subject)
        if entry is None:
            return None
        complete = entry.latest_complete
        return complete.revision if complete is not None else None


class _KeyView:
    __slots__ = ("collection", "subject", "revision")

    def __init__(self, collection: str, subject: str, revision: str):
        self.collection = collection
        self.subject = subject
        self.revision = revision
