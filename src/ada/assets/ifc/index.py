"""``ifc.index.json`` -- the provider-private artefact the Phase-4 sweep diffs against.

Core knows this file exists (it is one more entry in ``AssetManifest.artefacts``, role
``ifc-index``, which is NOT one of the three roles core reads -- ``manifest``, ``hierarchy``,
``attributes``) and nothing about its shape. That is the point: the sweep (``asset-sweep-ifc``,
Phase 4) needs to tell "this product changed" from "this product is untouched" without
re-tessellating either file, and the only honest way to do that without per-product owner-history
timestamps (Decision 4 rejects those -- adapy, like most producers, writes one owner history per
FILE) is a hash of what a change would actually move: the representation, the placement, and the
product's own attributes.

**What the hash covers, and why not more.** ``entity.get_info(recursive=True)`` walks every
attribute the product carries, including the referenced ``IfcProductDefinitionShape`` and
``IfcObjectPlacement`` graphs -- so a moved instance, a re-profiled beam or a renamed product all
change the hash. It does NOT cover the product's property sets (``IfcPropertySet`` /
``IfcElementQuantity``, reached via ``IsDefinedBy`` rather than a direct attribute): the optional
``attributes`` artefact that would carry those is not shipped in this phase (Decision 4 lists it as
an ADD-ON, and the design's own "Still open" note leaves the exact sweep hash inputs -- "property
sets included or not" -- an unmeasured, provider-internal choice). Widening this hash to include
property sets is additive and does not change the artefact's shape.

**Why STEP entity-instance numbers must be stripped, recursively.** ``get_info(recursive=True)``
expands every referenced entity into its own nested dict rather than leaving a bare ``#123``
token -- but EACH of those nested dicts still carries its own numeric ``"id"`` key alongside the
expanded attributes (verified against ifcopenshell's own output, not assumed), at every depth: the
product's own top-level id, its placement's id, its placement's axis' id, and so on down the whole
representation graph. Two files that assign different STEP line numbers to logically identical
entities -- the ordinary case for ``v1`` and an independently re-exported ``v2``, where adding or
removing ANY entity anywhere in the file shifts numbering for everything written after it -- would
therefore hash as "changed" everywhere, not just where content actually moved, if only the
top-level ``"id"`` were dropped. :func:`_strip_step_ids` walks the WHOLE nested structure and drops
every ``"id"`` key it finds, which is what makes the hash a statement about content rather than
about write order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import ifcopenshell

__all__ = [
    "IFC_INDEX_FILENAME",
    "IFC_INDEX_ROLE",
    "IfcIndexEntry",
    "build_ifc_index",
    "parse_ifc_index",
]

IFC_INDEX_FILENAME = "ifc.index.json"
IFC_INDEX_ROLE = "ifc-index"

# Own axis, provider-private -- core never reads this. Bumped only if the hash inputs change in a
# way that would make an old and a new index disagree about "unchanged" for the same product.
_INDEX_SCHEMA = "ada.assets.ifc/index@1"


@dataclass(frozen=True)
class IfcIndexEntry:
    id: str  # GlobalId, verbatim -- same identity the hierarchy uses
    hash: str  # sha256 of representation + placement + own attributes


def _strip_step_ids(node: Any) -> Any:
    """Drop every ``"id"`` key (the STEP entity-instance number) at every depth of a
    ``get_info(recursive=True)`` tree -- see the module docstring for why this must be recursive,
    not just applied to the top-level dict."""
    if isinstance(node, dict):
        return {k: _strip_step_ids(v) for k, v in node.items() if k != "id"}
    if isinstance(node, (list, tuple)):
        return [_strip_step_ids(v) for v in node]
    return node


def _product_hash(product: ifcopenshell.entity_instance) -> str:
    info = product.get_info(recursive=True)
    # OwnerHistory carries a per-write timestamp that moves on every re-export even when nothing
    # about the product itself changed (adapy writes one per file, `store.py:157`); GlobalId is
    # the entry's own key already. Both would make an untouched product hash as "changed".
    info.pop("OwnerHistory", None)
    info.pop("GlobalId", None)
    info = _strip_step_ids(info)
    blob = json.dumps(info, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_ifc_index(f: ifcopenshell.file, node_ids: list[str]) -> list[IfcIndexEntry]:
    """One entry per id in ``node_ids`` (branches and leaves alike -- a branch's own attributes,
    e.g. a storey's elevation, can change without any leaf beneath it changing)."""
    return [IfcIndexEntry(id=node_id, hash=_product_hash(f.by_guid(node_id))) for node_id in node_ids]


def index_to_json(entries: list[IfcIndexEntry]) -> bytes:
    doc = {"schema": _INDEX_SCHEMA, "entries": {e.id: e.hash for e in entries}}
    return json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")


def parse_ifc_index(doc: bytes | str) -> dict[str, str]:
    """id -> hash. A raw dict rather than a list of dataclasses on read: the Phase-4 sweep only
    ever does point lookups against it, never iterates it as a whole."""
    raw = json.loads(doc)
    if not isinstance(raw, dict) or raw.get("schema") != _INDEX_SCHEMA:
        raise ValueError(f"unknown or missing {IFC_INDEX_FILENAME} schema (expected {_INDEX_SCHEMA!r})")
    return dict(raw.get("entries") or {})
