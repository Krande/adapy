"""The provider contract: three protocols, told apart by CAPABILITIES PRESENT, never by id.

Core dispatches on which methods a provider has, the same "presence IS the declaration"
convention core already uses for optional catalogue methods. That is what lets a mesh catalogue,
a private-format CSG store and the in-tree IFC provider ride one pipeline without core growing a
branch per vendor.

Only ``AssetTreeProvider`` is required. A provider that merely publishes into the store needs no
runtime hierarchy code at all -- it rides the built-in ``published`` provider and supplies a
publisher and/or a builder.

The delivery split is the whole of Decision 1:

* ``mesh`` -- core asks for a URL and loads it. No job, no derived blob, no provenance beyond the
  revision. This is a live catalogue's shape.
* ``build`` -- core asks for a capability plus OPAQUE options, enqueues a plugin job, then reads
  a summary whose provenance block it validates. Core never derives a key from any value inside
  ``options``, so a provider cannot smuggle a source path into a key core would have to
  understand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

from ada.assets.projection import HierarchySlice

__all__ = [
    "AssetBuilder",
    "AssetPublisher",
    "AssetTreeProvider",
    "BuildDelivery",
    "CollectionInfo",
    "DeliveryClaim",
    "MeshDelivery",
    "provider_capabilities",
]


@dataclass(frozen=True)
class CollectionInfo:
    id: str
    label: str | None = None
    node_count: int | None = None
    latest_revision: str | None = None


@dataclass(frozen=True)
class MeshDelivery:
    url: str
    revision: str
    source_up_axis: Literal["z", "y"] = "z"
    headers: Mapping[str, str] = field(default_factory=dict)
    kind: Literal["mesh"] = "mesh"


@dataclass(frozen=True)
class BuildDelivery:
    capability: str
    revision: str
    options: Mapping[str, Any] = field(default_factory=dict)  # opaque to core; hashed and forwarded
    fingerprint_inputs: tuple[str, ...] = ()
    kind: Literal["build"] = "build"


DeliveryClaim = MeshDelivery | BuildDelivery


@runtime_checkable
class AssetTreeProvider(Protocol):
    """The one required protocol: somewhere to get collections, a spine, and a delivery claim."""

    id: str

    def collections(self, scope: Any) -> list[CollectionInfo]: ...

    def hierarchy(self, scope: Any, collection: str, *, root: str | None = None, depth: int = 1) -> HierarchySlice: ...

    def delivery(
        self, scope: Any, collection: str, node: str, *, revision: str | None = None
    ) -> DeliveryClaim | None: ...


@runtime_checkable
class AssetPublisher(Protocol):
    """Optional: providers that accept a publish INTO this scope.

    ``derive`` receives the staged blob keys and returns a plan. It must NOT set
    ``change.published_by`` -- that is core's to stamp from the authenticated caller, and a
    provider that tries is refused by name at the publish job.
    """

    def derive(
        self, scope: Any, staged: Mapping[str, str], *, collection: str | None = None, dry_run: bool = False
    ) -> Any: ...


@runtime_checkable
class AssetBuilder(Protocol):
    """Optional: the worker-side half of a ``build`` claim.

    This IS the plugin ``job_entrypoint`` -- the signature is exactly what core's generic
    plugin-job dispatch already negotiates, so an existing provider ``run()`` becomes an
    AssetBuilder by rename.
    """

    def build(
        self,
        options: Mapping[str, Any],
        *,
        storage: Any,
        scope: Any,
        on_progress: Any,
        derived_prefix: str,
        cancel_event: Any = None,
    ) -> Any: ...


def provider_capabilities(provider: Any) -> dict[str, bool]:
    """What this provider can do, by what it HAS -- never by what it is called."""
    return {
        "tree": all(hasattr(provider, m) for m in ("collections", "hierarchy", "delivery")),
        "publish": hasattr(provider, "derive"),
        "build": hasattr(provider, "build"),
    }
