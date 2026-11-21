"""From a GLB element id to an element key: which element of which model a run's member is.

A clash run names its members by GLB element id and name (``ada.clash.sidecar``), and a GLB id is
an ordinal of one export. The key is what outlives the export (``ada.assoc.keys``):

* a FILE target: the element's name path in the GLB's ``id_hierarchy`` (``#p:``);
* a NODE target: the published node the GLB's ``node_ids`` maps the id to; an id it does not map is
  the subject's key with the element's name path (``node:...#p:``);
* a GROUP run: each member by its own part's target and GLB.

Without the GLB (the slim API storing a browser's run whose GLB is too large to read there) the
path is the member's name alone; the annotate that follows writes the resolved keys into the run
(``provenance.elements``) and the rows are written again.

STDLIB ONLY.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .keys import file_key, key_ancestors, node_key

__all__ = [
    "ELEMENTS_PROVENANCE",
    "Resolver",
    "glb_json",
    "member_identities",
    "resolver_for",
    "target_anchor",
]

#: Where a run keeps its members' resolved identities: ``provenance[ELEMENTS_PROVENANCE]`` is one
#: ``{"key", "anc", "fp"?}`` per member, in member order.
ELEMENTS_PROVENANCE = "elements"


def glb_json(data: bytes) -> dict:
    """The JSON chunk of a GLB (gzipped bytes accepted); ``{}`` for bytes that are not one."""
    import gzip

    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data[:4] != b"glTF" or len(data) < 20:
        return {}
    length, kind = struct.unpack_from("<I4s", data, 12)
    if kind != b"JSON":
        return {}
    try:
        return json.loads(data[20 : 20 + length].decode("utf-8"))
    except ValueError:
        return {}


def _extras(js: Mapping[str, Any]) -> dict:
    for holder in (js.get("scenes") or [{}])[:1] + [js, js.get("asset") or {}]:
        extras = (holder or {}).get("extras") or {}
        if extras.get("id_hierarchy"):
            return extras
    return {}


def target_anchor(target: Mapping[str, Any]) -> str | None:
    """The key of what a run was made on -- the file, or the published node -- or None (a group)."""
    kind = target.get("kind")
    if kind == "file" and target.get("source_key"):
        return file_key(target["source_key"])
    if kind == "node" and target.get("collection") and (target.get("node") or target.get("subject")):
        return node_key(target.get("provider") or "", target["collection"], target.get("node") or target["subject"])
    return None


@dataclass
class Resolver:
    """Keys for the elements of one GLB of one target."""

    target: Mapping[str, Any]
    #: GLB id -> (name, parent id), parent ``"*"`` at the top.
    by_id: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: GLB id -> published node id.
    node_ids: Mapping[str, str] = field(default_factory=dict)

    def chain(self, eid: str) -> list[str]:
        """The ids from the top of the hierarchy down to ``eid``."""
        out: list[str] = []
        seen: set[str] = set()
        cur = str(eid)
        while cur in self.by_id and cur not in seen:
            seen.add(cur)
            out.append(cur)
            cur = self.by_id[cur][1]
        return out[::-1]

    def identity(self, eid: str, name: str | None = None) -> tuple[str, list[str]]:
        """``(key, ancestors)`` of GLB element ``eid`` (named ``name`` when the GLB is not known)."""
        chain = self.chain(eid)
        names = [self.by_id[i][0] for i in chain] if chain else [name or str(eid)]
        kind = self.target.get("kind")
        anchor = target_anchor(self.target)
        if kind == "node":
            provider = self.target.get("provider") or ""
            collection = self.target["collection"]
            nid = self.node_ids.get(str(eid))
            key = (
                node_key(provider, collection, nid)
                if nid
                else node_key(provider, collection, self.target.get("node") or self.target["subject"], path=names)
            )
            anc = [node_key(provider, collection, self.node_ids[i]) for i in chain[:-1] if self.node_ids.get(i)]
            anc += [] if nid else key_ancestors(key)
            if anchor:
                anc.insert(0, anchor)
            return key, list(dict.fromkeys(a for a in anc if a != key))
        if kind == "file":
            key = file_key(self.target["source_key"], path=names)
            return key, key_ancestors(key)
        raise ValueError(f"a {kind!r} target has no element keys of its own")


def resolver_for(target: Mapping[str, Any], js: Mapping[str, Any] | None) -> Resolver:
    """A resolver over a GLB's JSON (``glb_json``); an empty one -- names only -- without it."""
    extras = _extras(js or {})
    by_id = {
        str(k): (str(v[0]), str(v[1]) if len(v) > 1 else "*")
        for k, v in (extras.get("id_hierarchy") or {}).items()
        if isinstance(v, (list, tuple)) and v
    }
    ids = extras.get("node_ids") if isinstance(extras.get("node_ids"), dict) else {}
    return Resolver(dict(target), by_id, {str(k): str(v) for k, v in ids.items() if v is not None})


def member_identities(
    run,
    glb_by_part: Sequence[Mapping[str, Any] | None] | Mapping[str, Any] | None = None,
    fps_by_part: Sequence[Mapping[str, str] | None] | Mapping[str, str] | None = None,
) -> list[dict]:
    """One ``{"key", "anc", "fp"?}`` per member of ``run`` (an ``ada.clash.sidecar.ContactRun``).

    ``glb_by_part``: the GLB JSON of the run (one GLB) or of each part (a group run, by part index).
    ``fps_by_part``: GLB id -> fingerprint, the same way (``ada.assoc.fingerprints``)."""
    parts = list(run.parts)
    group = run.target.get("kind") == "group"

    def per_part(value, k):
        if value is None:
            return None
        if group:
            return value[k] if isinstance(value, Sequence) and k < len(value) else None
        return value

    from ada.clash.sidecar import target_token

    resolvers: dict[int, Resolver] = {}
    out = []
    for m in run.members:
        k = int(m.get("part", 0)) if group else 0
        if k not in resolvers:
            target = dict(parts[k]["source"]) if group else dict(run.target)
            resolvers[k] = resolver_for(target, per_part(glb_by_part, k))
        eid = str(m.get("element") if group else m["id"])
        try:
            key, anc = resolvers[k].identity(eid, str(m.get("name") or ""))
        except (KeyError, ValueError):
            out.append({"key": None, "anc": []})
            continue
        entry: dict[str, Any] = {"key": key, "anc": anc, "source": target_token(dict(resolvers[k].target))}
        fps = per_part(fps_by_part, k)
        if fps and fps.get(eid):
            entry["fp"] = fps[eid]
        out.append(entry)
    return out
