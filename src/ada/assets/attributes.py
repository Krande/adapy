"""``attributes.json`` -- what a node IS, as opposed to where it sits or what it draws.

The third core artefact role (``manifest.py:CORE_ARTEFACT_ROLES``) and the last one to be filled
in. ``hierarchy.json`` answers "what is under this", a delivery claim answers "what do I load for
this"; neither says a beam is an IPE300 in S355 carrying a fire rating. This document does, for
every node a subject covers.

WHY IT IS PUBLISHED RATHER THAN READ ON CLICK. The alternative -- a provider method that opens
the source file when a row is selected -- is the same information for strictly worse latency:

* the serving process would need the source reader (ifcopenshell, a CAD kernel, a licensed
  client) in an image that otherwise never opens one, and the slim viewer image carries none;
* a several-hundred-MB source is seconds to open COLD, which is the state every first click is
  in, and holding files open across requests trades that for unbounded memory;
* the answer is immutable per revision, so recomputing it per click is work that was already done
  once at publish time.

Writing it at publish costs one pass over products the publisher is ALREADY walking (the sweep
index hashes every one of them), and turns a click into a dictionary lookup over a cached blob.

WHY THE SERVER EXTRACTS ONE NODE. A subject's document covers its whole subtree, so shipping it
to answer one selection would send a storey to render one beam. The route reads the blob (cached:
it is immutable, keyed by a revision that never changes content) and returns the one node. The
browser fetches per selection, in the background, and holds nothing it has to invalidate.

The three reading rules are ``projection.py``'s, for the same reasons:

1. **Refuse an unknown schema** rather than reading the keys you recognise out of a future
   document.
2. **Index by NAME.** Nothing here is positional.
3. **An absent node is not an error.** A subject may cover nodes its provider had nothing to say
   about, and a reader that raised would turn "no properties" into a broken panel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "ATTRIBUTES_FILENAME",
    "ATTRIBUTES_ROLE",
    "ATTRIBUTES_SCHEMA",
    "AttributesDocument",
    "AttributesError",
    "NodeAttributes",
    "build_attributes",
    "parse_attributes",
]

ATTRIBUTES_FILENAME = "attributes.json"
ATTRIBUTES_ROLE = "attributes"
ATTRIBUTES_SCHEMA = "ada.assets/attributes@1"


class AttributesError(ValueError):
    """An attributes document that cannot be read."""


@dataclass(frozen=True)
class NodeAttributes:
    """One node's own facts, in three groups core keeps apart.

    ``own`` is what the source says about the entity itself -- its class, name, tag. ``groups`` is
    named sets of properties (an IFC property set, another format's user-defined attribute block,
    a catalogue's attribute group): the grouping is the source's and core preserves it, because
    flattening two properties
    that share a name into one map loses which one a reader is looking at. ``quantities`` is
    separated from ``groups`` only because a consumer that wants numbers -- a take-off, a mass
    roll-up -- should not have to guess which group holds them by name.

    Every value is JSON-primitive by the time it lands here. A provider that has a richer type
    renders it; core stores what it is given and interprets none of it.
    """

    kind: str | None = None
    own: Mapping[str, Any] = field(default_factory=dict)
    groups: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    quantities: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.own or self.groups or self.quantities)


@dataclass(frozen=True)
class AttributesDocument:
    provider: str
    collection: str
    root: str
    produced_at: str
    nodes: Mapping[str, NodeAttributes]
    schema: str = ATTRIBUTES_SCHEMA

    def node(self, node_id: str) -> NodeAttributes | None:
        """The one node, or None. Rule 3: absent is an answer, not a fault."""
        return self.nodes.get(node_id)

    def to_json(self) -> bytes:
        return json.dumps(_document_to_dict(self), indent=2, sort_keys=False).encode("utf-8")


def _node_to_dict(n: NodeAttributes) -> dict:
    out: dict[str, Any] = {}
    if n.kind:
        out["kind"] = n.kind
    if n.own:
        out["own"] = dict(n.own)
    if n.groups:
        out["groups"] = {name: dict(props) for name, props in n.groups.items()}
    if n.quantities:
        out["quantities"] = {name: dict(props) for name, props in n.quantities.items()}
    return out


def _document_to_dict(doc: AttributesDocument) -> dict:
    return {
        "schema": doc.schema,
        "provider": doc.provider,
        "collection": doc.collection,
        "root": doc.root,
        "produced_at": doc.produced_at,
        "nodes": {node_id: _node_to_dict(attrs) for node_id, attrs in doc.nodes.items()},
    }


def build_attributes(
    *,
    provider: str,
    collection: str,
    root: str,
    produced_at: str,
    nodes: Mapping[str, NodeAttributes],
) -> AttributesDocument:
    """Assemble a document, dropping nodes that carry nothing.

    An empty entry and an absent one read identically (rule 3), so writing the empty one costs
    bytes to say what silence already says -- and on a spatial-heavy tree that is most of the
    nodes.
    """
    kept = {node_id: attrs for node_id, attrs in nodes.items() if not attrs.is_empty()}
    return AttributesDocument(
        provider=provider,
        collection=collection,
        root=root,
        produced_at=produced_at,
        nodes=kept,
    )


def _as_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AttributesError(f"{where}: expected an object, got {type(value).__name__}")
    return value


def _parse_node(node_id: str, raw: Any) -> NodeAttributes:
    raw = _as_mapping(raw, f"nodes[{node_id!r}]")
    groups_raw = _as_mapping(raw.get("groups"), f"nodes[{node_id!r}].groups")
    quantities_raw = _as_mapping(raw.get("quantities"), f"nodes[{node_id!r}].quantities")
    return NodeAttributes(
        kind=raw.get("kind") or None,
        own=dict(_as_mapping(raw.get("own"), f"nodes[{node_id!r}].own")),
        groups={name: dict(_as_mapping(v, f"nodes[{node_id!r}].groups[{name!r}]")) for name, v in groups_raw.items()},
        quantities={
            name: dict(_as_mapping(v, f"nodes[{node_id!r}].quantities[{name!r}]")) for name, v in quantities_raw.items()
        },
    )


def parse_attributes(payload: bytes | str | Mapping[str, Any]) -> AttributesDocument:
    """Read a document. Accepts raw bytes off a blob fetch, decoded text, or a parsed mapping --
    every caller has a different one of those and none should have to guess at the encoding."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AttributesError(f"not readable as JSON: {exc}") from exc
    elif isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AttributesError(f"not readable as JSON: {exc}") from exc
    doc = _as_mapping(payload, "document")

    schema = doc.get("schema")
    if schema != ATTRIBUTES_SCHEMA:
        raise AttributesError(
            f"attributes declares schema {schema!r}, this reader speaks {ATTRIBUTES_SCHEMA!r} -- "
            f"refused rather than read for the keys it recognises"
        )

    nodes_raw = _as_mapping(doc.get("nodes"), "nodes")
    return AttributesDocument(
        provider=str(doc.get("provider") or ""),
        collection=str(doc.get("collection") or ""),
        root=str(doc.get("root") or ""),
        produced_at=str(doc.get("produced_at") or ""),
        nodes={str(node_id): _parse_node(str(node_id), raw) for node_id, raw in nodes_raw.items()},
        schema=ATTRIBUTES_SCHEMA,
    )
