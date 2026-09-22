"""Wrap any ``ExternalModelCatalog`` as a LIVE ``AssetTreeProvider`` (Decision 1's ``mesh`` witness).

Core already has a catalogue seam for externally-stored models -- collection/model, three required
methods, a presigned download URL minted at load time (``ada.plugins.external_models.catalog``).
The asset browser has a DIFFERENT seam: node-shaped, ``(scope, collection, node)`` -> a delivery
claim. This module is the one adapter between them, so a deployment can expose the same catalogue
through both surfaces without teaching either one about the other.

WHY THIS IS A LIVE PROVIDER, NEVER A PUBLISHED ONE (Decision 1's provider table). A catalogue
answers from whatever the underlying store holds right now: there is no ``hierarchy.json`` to
resolve and no revision the store itself tracks, and a mesh URL is presigned and short-lived.
``collections()`` / ``hierarchy()`` / ``delivery()`` are called straight through to the catalogue on
every request -- nothing here is cached, because nothing cached would still be correct a minute
later (``delivery()`` most of all: see its docstring).

NODE IDENTITY. The asset key grammar (``ada.assets.keys``) is ONE segment per node: no ``/``, a
fixed alphabet, 128 chars max. A catalogue's own identity is TWO parts -- collection, then model --
so a node id here is ``<collection>$<model>``, composed and reversed by ``_encode_node``/
``_decode_node`` in this module only. ``$`` was chosen because it is already admitted by the
grammar's alphabet (for IFC GlobalIds, see ``ada.assets.keys``) and is not a character either half
is expected to contain, so the join needs no escaping scheme to stay reversible. A catalogue whose
collection or model id cannot be composed this way -- it contains ``$``, or fails the segment
grammar outright (a space, a slash, a leading ``_``) -- is SKIPPED, with a warning naming the id,
rather than turned into a node the browser could not address in a route path.

REVISION. ``MeshDelivery.revision`` is what lets the browser judge coevality across a mixed tree.
A catalogue that keeps model revisions (``list_model_revisions``, optional on the Protocol) has a
real answer: the CURRENT revision's ``created_at``, or its opaque ``id`` when it cannot date itself.
A catalogue with no revisioning has no such fact, and inventing one -- "now", or the time of this
request -- would tell the browser a model just changed when nobody knows that. So it gets a fixed,
documented placeholder instead (``_UNVERSIONED_REVISION``): honest about knowing nothing, and
deliberately not instant-shaped so nothing downstream can mistake it for a real extraction time.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from ada.assets.keys import is_valid_segment
from ada.assets.projection import HierarchySlice, build_hierarchy
from ada.assets.provider import CollectionInfo, MeshDelivery
from ada.assets.registry import register_asset_provider
from ada.config import logger

__all__ = [
    "CatalogueAssetProvider",
    "register_catalogue_asset_provider",
]

# Separator between the two halves of a composed node id. Reserved HERE -- never legal inside a
# bare collection or model id we compose -- so a partition on the first occurrence is unambiguous
# and needs no escaping.
_NODE_SEP = "$"

# A catalogue with no revisioning info at all. Deliberately NOT instant-shaped (no compact-UTC
# digits) so nothing downstream can mistake it for a real extraction time -- see the REVISION note
# in the module docstring.
_UNVERSIONED_REVISION = "unversioned"

_DEFAULT_EXPIRES_IN_SECONDS = 900


def _encode_node(collection: str, model: str) -> str | None:
    """``<collection>$<model>``, or None when it cannot be composed. See the module docstring."""
    if _NODE_SEP in collection or _NODE_SEP in model:
        return None
    composed = f"{collection}{_NODE_SEP}{model}"
    return composed if is_valid_segment(composed) else None


def _decode_node(node: str) -> tuple[str, str] | None:
    if _NODE_SEP not in node:
        return None
    collection, _, model = node.partition(_NODE_SEP)
    if not collection or not model:
        return None
    return collection, model


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CatalogueAssetProvider:
    """Any ``ExternalModelCatalog`` as an ``AssetTreeProvider``. ``mesh`` only -- a catalogue entry
    is a stored file the browser loads directly, never something to build."""

    delivery_kinds = ("mesh",)

    def __init__(self, catalog: Any, *, provider_id: str, expires_in_seconds: int = _DEFAULT_EXPIRES_IN_SECONDS):
        self._catalog = catalog
        self.id = provider_id
        self._expires_in_seconds = expires_in_seconds

    # -- tree ---------------------------------------------------------------------------------

    def collections(self, scope: Any = None) -> list[CollectionInfo]:
        out: list[CollectionInfo] = []
        for c in self._catalog.list_collections():
            if not is_valid_segment(c.id):
                # A collection id rides unescaped in the route path
                # (/assets/tree/{provider}/{collection}); one that cannot is unreachable there no
                # matter what its models look like, so it is dropped at this level rather than
                # per-model.
                logger.warning(
                    "asset catalogue adapter %r: skipping collection %r -- not a valid asset node id",
                    self.id,
                    c.id,
                )
                continue
            try:
                models = self._catalog.list_models(c.id)
            except Exception as exc:  # a catalogue call failing must not take the whole list down
                logger.warning("asset catalogue adapter %r: list_models(%r) failed: %s", self.id, c.id, exc)
                models = []
            out.append(CollectionInfo(id=c.id, label=c.name, node_count=len(models)))
        return out

    def hierarchy(self, scope: Any, collection: str, *, root: str | None = None, depth: int = 1) -> HierarchySlice:
        """A depth-2 tree: the collection itself (a branch) over its models (leaves, ``mesh``).

        The whole thing, on every call -- a catalogue's own shape already IS this two-level tree
        (``ada.plugins.external_models.catalog``'s "TWO LEVELS, NOT FOUR"), so there is no lazy
        spine underneath it and ``root``/``depth`` name nothing this adapter would fetch less of.
        """
        models = self._catalog.list_models(collection)
        nodes: list[dict] = [
            {
                "id": collection,
                "parent": None,
                "label": collection,
                "kind": "collection",
                "leaf": False,
                "delivery": "",
            }
        ]
        for m in models:
            node_id = _encode_node(collection, m.id)
            if node_id is None:
                logger.warning(
                    "asset catalogue adapter %r: skipping model %r in collection %r -- (collection, model) "
                    "cannot be composed into a single asset node id",
                    self.id,
                    m.id,
                    collection,
                )
                continue
            nodes.append(
                {
                    "id": node_id,
                    "parent": collection,
                    "label": m.name,
                    "kind": "model",
                    "leaf": True,
                    "delivery": "mesh",
                }
            )
        return build_hierarchy(
            provider=self.id, collection=collection, produced_at=_now_iso(), nodes=nodes, root=root, depth=2
        )

    # -- delivery -------------------------------------------------------------------------------

    def delivery(self, scope: Any, collection: str, node: str, *, revision: str | None = None) -> MeshDelivery | None:
        """Mint a fresh URL from ``model_download_url``. NEVER cached -- see the module docstring."""
        decoded = _decode_node(node)
        if decoded is None:
            return None
        node_collection, model_id = decoded
        if node_collection != collection:
            # A node id names a different collection than the one it was looked up under: either a
            # stale row from a since-renamed collection, or a hand-built id. Both get "no claim",
            # the same answer as a node this catalogue has never heard of.
            return None
        kwargs: dict[str, Any] = {"expires_in_seconds": self._expires_in_seconds}
        if revision:
            # Passed ONLY when asked for, so a catalogue whose `model_download_url` predates the
            # `revision` keyword (most of them) never sees it -- the same convention
            # `external_models/adapy_plugin.py` uses for the same call.
            kwargs["revision"] = revision
        try:
            url = self._catalog.model_download_url(node_collection, model_id, **kwargs)
        except Exception as exc:
            logger.warning(
                "asset catalogue adapter %r: model_download_url(%r, %r) failed: %s",
                self.id,
                node_collection,
                model_id,
                exc,
            )
            return None
        headers: dict[str, str] = {}
        header_getter = getattr(self._catalog, "model_download_headers", None)
        if callable(header_getter):
            headers = dict(header_getter(node_collection, model_id) or {})
        return MeshDelivery(
            url=url,
            revision=revision or self._current_revision(node_collection, model_id),
            headers=headers,
        )

    def _current_revision(self, collection: str, model_id: str) -> str:
        """What this catalogue knows about this model's freshness, or the documented placeholder."""
        list_revisions = getattr(self._catalog, "list_model_revisions", None)
        if callable(list_revisions):
            try:
                revisions = list_revisions(collection, model_id)
            except Exception:
                revisions = []
            current = next((r for r in revisions if getattr(r, "current", False)), None)
            if current is not None:
                return current.created_at or current.id
        return _UNVERSIONED_REVISION


def register_catalogue_asset_provider(
    provider_id: str,
    catalog_factory: Any,
    *,
    label: str | None = None,
    expires_in_seconds: int = _DEFAULT_EXPIRES_IN_SECONDS,
) -> None:
    """Expose a catalogue as an asset collection.

    NOT called at import. ``ada.plugins.external_models.register_demo_provider`` is the same
    shape for the older seam: a deployment opts in explicitly, because registering as an import
    side effect is exactly the surprise the preload list exists to make explicit. ``catalog_factory``
    is a zero-arg callable (never an instance) so importing a module that calls this never opens a
    network client -- nothing runs until a request actually reaches this provider.
    """
    register_asset_provider(
        provider_id,
        lambda: CatalogueAssetProvider(
            catalog_factory(), provider_id=provider_id, expires_in_seconds=expires_in_seconds
        ),
        label=label,
    )
