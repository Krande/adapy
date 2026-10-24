"""A convex-contact clash RUN as a sidecar document -- what was checked, on which geometry, and every
clash with the contact a connection is sized from.

WHY A SIDECAR. A clash check measures solids, and the geometry it measures is the viewer GLB (see
``adacpp.clash.find_glb_contacts``): the same bytes in the browser and on a worker, so the same clashes
whichever ran it. A run is therefore a fact about one GLB, worth keeping: it survives a reload, it is
listed beside the target it was run on, and it is the INPUT of detailing -- a detail job reads the
clashes from here (contact points, normal, distance, patch polygon) instead of re-running a check.

STDLIB-ONLY, like ``options`` and ``result``: the slim API lists, reads and validates these documents
and has no modelling stack.

LAYOUT. ``_clash/<target token>/<run id>.json`` per run and ``_clash/<target token>/index.json`` per
target, a scope-state prefix like ``_groups/`` (written by the browser through the blob PUT, by a
worker through storage). The target token hashes the target descriptor, so runs on the same target
list together and runs on different targets never collide.

IDENTITY. A clash's id hashes the pass name and the two members' ELEMENT ids (the GLB's draw-range
keys), not their names: names may repeat, element ids do not, and the same GLB gives the same ids in
every engine.

ORDER AND SIGN. ``members`` and every point list are in the engine's order (``i < j`` by element order
in the GLB); ``normal`` points from member ``i`` towards member ``j``; ``distance`` is coal's signed
distance -- negative is overlap, positive a gap inside the security margin. Which member is INCOMING is
not decided here: it needs the members' axes, which the GLB does not carry. A worker annotating the
run decides it (``ada.clash.contacts.annotate_applicable``) and writes it per clash as ``roles``
(``{"incoming": k, "landing": l}``, member indices; absent where unresolved) -- from the rebuilt
members where they bind, else from the members' meshes in the GLB. A detail job decides it again from
the members it binds (``ada.clash.convex_contact``).

GROUP RUNS. A saved group spans several models, so several GLBs: the browser reads the members of
each (adacpp's ``glbMembers``), keeps the group's elements and measures them as one soup. Such a run's
target is ``{"kind": "group", "group_id"}`` -- the id alone, so a renamed or edited group keeps listing
its runs together -- and ``group`` is the snapshot that was measured. ``geometry`` names every GLB as a
PART (``parts: [{key, sha256, source}]``, ``source`` the member target of that model) and its
``sha256`` is :func:`group_geometry_sha256` of them; ``key`` is ``group:<group id>``. Element ids repeat
across GLBs, so a member's id is ``"<part>:<element id>"`` (what the clash ids hash) and the member
also carries ``part`` and ``element``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "CLASH_SIDECAR_PREFIX",
    "CLASH_SIDECAR_SCHEMA",
    "CONTACT_ORIGIN",
    "ContactRecord",
    "ContactRun",
    "SidecarError",
    "contact_id",
    "group_geometry_sha256",
    "index_key",
    "index_with",
    "parse_index",
    "parse_run",
    "run_from_glb_contacts",
    "run_key",
    "target_token",
]

CLASH_SIDECAR_SCHEMA = "ada.clash/contacts@1"
CLASH_INDEX_SCHEMA = "ada.clash/contacts-index@1"
CLASH_SIDECAR_PREFIX = "_clash/"
#: The pass that produces these runs; part of every clash id, so a wire constant.
CONTACT_ORIGIN = "convex-contact"

Vec3 = tuple[float, float, float]


class SidecarError(ValueError):
    """A sidecar document that does not say what a run must say."""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def target_token(target: Mapping[str, Any]) -> str:
    """The key segment for a target descriptor ({"kind": "file", "source_key": ...} or a node's)."""
    return hashlib.sha256(_canonical(dict(target)).encode("utf-8")).hexdigest()[:16]


def run_key(target: Mapping[str, Any], run_id: str) -> str:
    return f"{CLASH_SIDECAR_PREFIX}{target_token(target)}/{run_id}.json"


def index_key(target: Mapping[str, Any]) -> str:
    return f"{CLASH_SIDECAR_PREFIX}{target_token(target)}/index.json"


def contact_id(element_ids: Sequence[str], origin: str = CONTACT_ORIGIN) -> str:
    """``sha256("<origin>|<sorted element ids>")[:12]`` -- the browser computes the same."""
    return hashlib.sha256("|".join([origin, *sorted(str(e) for e in element_ids)]).encode("utf-8")).hexdigest()[:12]


def group_geometry_sha256(parts: Sequence[Mapping[str, Any]]) -> str:
    """The geometry hash of a group run: sha256 of the sorted ``"<part key>\\n<part sha256>"`` lines,
    joined by ``\\n`` -- the browser computes the same."""
    lines = sorted(f"{p.get('key')}\n{p.get('sha256')}" for p in parts)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _vec3(v: Any, what: str) -> Vec3:
    try:
        x, y, z = (float(c) for c in v)
    except (TypeError, ValueError) as exc:
        raise SidecarError(f"{what} must be three numbers") from exc
    return (x, y, z)


def _points(v: Any, what: str) -> tuple[Vec3, ...]:
    if not isinstance(v, (list, tuple)):
        raise SidecarError(f"{what} must be a list of points")
    return tuple(_vec3(p, what) for p in v)


@dataclass(frozen=True)
class ContactPatch:
    """One contact patch: the polygon where the two members meet, on each of them."""

    normal: Vec3
    points1: tuple[Vec3, ...]
    points2: tuple[Vec3, ...]

    def to_dict(self) -> dict:
        return {
            "normal": list(self.normal),
            "points1": [list(p) for p in self.points1],
            "points2": [list(p) for p in self.points2],
        }


@dataclass(frozen=True)
class ContactRecord:
    """One clash: two members and their measured contact."""

    id: str
    #: Indices into the run's ``members``, ``i < j``.
    members: tuple[int, int]
    near_points: tuple[Vec3, Vec3]
    normal: Vec3
    distance: float
    contact_area: float
    patches: tuple[ContactPatch, ...] = ()
    #: Which connection specs could detail this clash, as a worker resolved them (empty until then).
    applicable: tuple[Mapping[str, Any], ...] = ()
    #: Which member is INCOMING and which LANDING: ``{"incoming": k, "landing": l}``, indices into the
    #: run's ``members`` (this clash's two, one each). Resolved on a worker when it annotates the run
    #: (``ada.clash.contacts.annotate_applicable``) -- the GLB carries no axes. ``None`` = unresolved.
    roles: Mapping[str, int] | None = None

    @property
    def centre(self) -> Vec3:
        a, b = self.near_points
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)

    @property
    def penetration_depth(self) -> float:
        """The overlap, positive -- core's ``ClashContact`` convention."""
        return abs(self.distance)

    def to_dict(self) -> dict:
        out = {
            "id": self.id,
            "members": list(self.members),
            "near_points": [list(p) for p in self.near_points],
            "normal": list(self.normal),
            "distance": self.distance,
            "contact_area": self.contact_area,
            "patches": [p.to_dict() for p in self.patches],
        }
        if self.applicable:
            out["applicable"] = [dict(a) for a in self.applicable]
        if self.roles is not None:
            out["roles"] = {"incoming": int(self.roles["incoming"]), "landing": int(self.roles["landing"])}
        return out


@dataclass(frozen=True)
class ContactRun:
    """One convex-contact run over one GLB."""

    run_id: str
    created_at: str
    #: ``"wasm"`` (a browser) or ``"worker"``.
    engine: str
    #: What was checked: ``{"kind": "file", "source_key": ...}`` or ``{"kind": "node", ...}``. A detail
    #: job rebuilds the members from here.
    target: Mapping[str, Any]
    #: The bytes that were measured: ``{"key", "sha256", "up_axis"}``. A detail job refuses a run whose
    #: GLB has since changed.
    geometry: Mapping[str, Any]
    #: security_margin, min_contact_area, line_contact_thickness.
    options: Mapping[str, Any]
    #: ``[{"id": element id, "name": name}]`` in the engine's order; contacts index into it. A group
    #: run's members also carry ``part`` (an index into ``geometry["parts"]``) and ``element``.
    members: tuple[Mapping[str, Any], ...]
    contacts: tuple[ContactRecord, ...]
    #: adacpp version and anything else worth knowing about how it ran.
    provenance: Mapping[str, Any] = field(default_factory=dict)
    #: A group run's snapshot of the group it measured (``{"id", "name", "members"}``); else None.
    group: Mapping[str, Any] | None = None
    schema: str = CLASH_SIDECAR_SCHEMA

    def to_dict(self) -> dict:
        out = {
            "schema": self.schema,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "engine": self.engine,
            "checker": CONTACT_ORIGIN,
            "target": dict(self.target),
            "geometry": dict(self.geometry),
            "options": dict(self.options),
            "members": [dict(m) for m in self.members],
            "contacts": [c.to_dict() for c in self.contacts],
            "provenance": dict(self.provenance),
        }
        if self.group is not None:
            out["group"] = dict(self.group)
        return out

    @property
    def parts(self) -> tuple[Mapping[str, Any], ...]:
        """A group run's GLBs (``geometry["parts"]``); empty for a run over one GLB."""
        return tuple(self.geometry.get("parts") or ())

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def label(self) -> str:
        """What the run was made on, as the panel names it: the group, the node or the file."""
        kind = self.target.get("kind")
        if kind == "group":
            return str((self.group or {}).get("name") or self.target.get("group_id") or "group")
        if kind == "node":
            return str(self.target.get("node") or self.target.get("subject") or "node")
        return str(self.target.get("source_key") or "file").rsplit("/", 1)[-1]

    def summary(self, key: str) -> dict:
        """The run's line in its target's index."""
        return {
            "run_id": self.run_id,
            "key": key,
            "target": dict(self.target),
            "label": self.label(),
            "created_at": self.created_at,
            "engine": self.engine,
            "geometry_sha256": self.geometry.get("sha256"),
            "options": dict(self.options),
            "members": len(self.members),
            "contacts": len(self.contacts),
        }

    def contact(self, cid: str) -> ContactRecord | None:
        return next((c for c in self.contacts if c.id == cid), None)

    def names(self, record: ContactRecord) -> tuple[str, str]:
        i, j = record.members
        return (self.members[i]["name"], self.members[j]["name"])


def run_from_glb_contacts(
    found: Mapping[str, Any],
    *,
    run_id: str,
    created_at: str,
    engine: str,
    target: Mapping[str, Any],
    geometry: Mapping[str, Any],
    options: Mapping[str, Any],
    provenance: Mapping[str, Any] | None = None,
) -> ContactRun:
    """A run from ``adacpp.clash.find_glb_contacts``'s result (columns + ``members`` + ``patches``)."""
    members = tuple({"id": str(i), "name": str(n)} for i, n in found["members"])
    records = []
    pairs = found["pairs"]
    pairs = pairs.tolist() if hasattr(pairs, "tolist") else list(pairs)
    for q, (i, j) in enumerate(pairs):
        patches = tuple(
            ContactPatch(
                normal=_vec3(p["normal"], "patch normal"),
                points1=_points(_as_list(p["points1"]), "patch points1"),
                points2=_points(_as_list(p["points2"]), "patch points2"),
            )
            for p in found["patches"][q]
        )
        records.append(
            ContactRecord(
                id=contact_id([members[i]["id"], members[j]["id"]]),
                members=(int(i), int(j)),
                near_points=(_vec3(found["near1"][q], "near1"), _vec3(found["near2"][q], "near2")),
                normal=_vec3(found["normal"][q], "normal"),
                distance=float(found["penetration_depth"][q]),
                contact_area=float(found["contact_area"][q]),
                patches=patches,
            )
        )
    return ContactRun(
        run_id=run_id,
        created_at=created_at,
        engine=engine,
        target=dict(target),
        geometry=dict(geometry),
        options=dict(options),
        members=members,
        contacts=tuple(records),
        provenance=dict(provenance or {}),
    )


def _as_list(v: Any) -> Any:
    return v.tolist() if hasattr(v, "tolist") else v


def parse_run(raw: str | bytes | Mapping[str, Any]) -> ContactRun:
    """Read and validate a run document. Raises :class:`SidecarError` for anything malformed."""
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SidecarError(f"clash sidecar is not JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise SidecarError("clash sidecar must be an object")
    if raw.get("schema") != CLASH_SIDECAR_SCHEMA:
        raise SidecarError(f"not a clash sidecar (schema {raw.get('schema')!r}, want {CLASH_SIDECAR_SCHEMA!r})")
    for key in ("run_id", "created_at", "engine", "target", "geometry", "options", "members", "contacts"):
        if key not in raw:
            raise SidecarError(f"clash sidecar has no {key!r}")
    if not isinstance(raw["target"], Mapping) or not raw["target"].get("kind"):
        raise SidecarError("clash sidecar target must say its kind")
    geometry = raw["geometry"]
    if not isinstance(geometry, Mapping) or not geometry.get("sha256"):
        raise SidecarError("clash sidecar geometry must carry the sha256 of the GLB it measured")
    group = _parse_group(raw) if raw["target"].get("kind") == "group" else None
    n_parts = len(geometry["parts"]) if group is not None else 0

    members = []
    for m in raw["members"]:
        if not isinstance(m, Mapping) or "id" not in m or "name" not in m:
            raise SidecarError("every clash sidecar member needs an id and a name")
        member: dict[str, Any] = {"id": str(m["id"]), "name": str(m["name"])}
        if m.get("element") is not None:
            member["element"] = str(m["element"])
        if m.get("part") is not None:
            try:
                member["part"] = int(m["part"])
            except (TypeError, ValueError) as exc:
                raise SidecarError("a member's part must be an integer") from exc
        if group is not None:
            if not (0 <= member.get("part", -1) < n_parts) or "element" not in member:
                raise SidecarError(f"group run member {member['id']!r} must name its part and element")
            if member["id"] != f"{member['part']}:{member['element']}":
                raise SidecarError(f"group run member id {member['id']!r} is not '<part>:<element id>'")
        members.append(member)

    contacts = []
    for c in raw["contacts"]:
        if not isinstance(c, Mapping):
            raise SidecarError("every clash must be an object")
        try:
            i, j = (int(x) for x in c["members"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SidecarError("every clash needs its two member indices") from exc
        if not (0 <= i < len(members) and 0 <= j < len(members)) or i == j:
            raise SidecarError(f"clash member indices {i}, {j} do not name two members of this run")
        near = _points(c.get("near_points"), "near_points")
        if len(near) != 2:
            raise SidecarError("every clash needs two near points")
        expected_id = contact_id([members[i]["id"], members[j]["id"]])
        if str(c.get("id")) != expected_id:
            raise SidecarError(f"clash id {c.get('id')!r} is not the id of its members ({expected_id})")
        patches = tuple(
            ContactPatch(
                normal=_vec3(p.get("normal"), "patch normal"),
                points1=_points(p.get("points1"), "patch points1"),
                points2=_points(p.get("points2"), "patch points2"),
            )
            for p in c.get("patches") or ()
        )
        contacts.append(
            ContactRecord(
                id=expected_id,
                members=(i, j),
                near_points=(near[0], near[1]),
                normal=_vec3(c.get("normal"), "normal"),
                distance=float(c.get("distance", 0.0)),
                contact_area=float(c.get("contact_area", 0.0)),
                patches=patches,
                applicable=tuple(dict(a) for a in c.get("applicable") or ()),
                roles=_parse_roles(c.get("roles"), (i, j)),
            )
        )
    return ContactRun(
        run_id=str(raw["run_id"]),
        created_at=str(raw["created_at"]),
        engine=str(raw["engine"]),
        target=dict(raw["target"]),
        geometry=dict(geometry),
        options=dict(raw["options"]),
        members=tuple(members),
        contacts=tuple(contacts),
        provenance=dict(raw.get("provenance") or {}),
        group=group,
    )


def _parse_roles(raw: Any, members: tuple[int, int]) -> dict | None:
    """A clash's ``roles`` -- ``{"incoming": k, "landing": l}``, its two members once each -- or None."""
    if raw is None:
        return None
    try:
        roles = {"incoming": int(raw["incoming"]), "landing": int(raw["landing"])}
    except (KeyError, TypeError, ValueError) as exc:
        raise SidecarError("a clash's roles must name its incoming and landing member indices") from exc
    if sorted(roles.values()) != sorted(members):
        raise SidecarError(f"a clash's roles {roles} must be its two members {list(members)}, one each")
    return roles


def _parse_group(raw: Mapping[str, Any]) -> dict:
    """A group run's ``group`` snapshot, checked against its target and its geometry's parts."""
    target, geometry, group = raw["target"], raw["geometry"], raw.get("group")
    if not str(target.get("group_id") or ""):
        raise SidecarError("a group run's target needs its group_id")
    if not isinstance(group, Mapping) or str(group.get("id") or "") != str(target["group_id"]):
        raise SidecarError("a group run must carry the group it measured, with the target's group_id")
    if not isinstance(group.get("members"), list):
        raise SidecarError("a group run's group must list its members")
    parts = geometry.get("parts")
    if not isinstance(parts, list) or not parts:
        raise SidecarError("a group run's geometry must list the GLBs it measured as parts")
    for p in parts:
        if (
            not isinstance(p, Mapping)
            or not p.get("key")
            or not p.get("sha256")
            or not isinstance(p.get("source"), Mapping)
        ):
            raise SidecarError("every part of a group run needs a key, a sha256 and its source")
    if geometry["sha256"] != group_geometry_sha256(parts):
        raise SidecarError("a group run's geometry sha256 is not the hash of its parts")
    return {"id": str(group["id"]), "name": str(group.get("name") or ""), "members": list(group["members"])}


def parse_index(raw: str | bytes | Mapping[str, Any] | None) -> list[dict]:
    """The runs listed in a target's index, newest first. A missing index is an empty one."""
    if raw is None:
        return []
    if isinstance(raw, (str, bytes)):
        raw = json.loads(raw)
    if not isinstance(raw, Mapping) or raw.get("schema") != CLASH_INDEX_SCHEMA:
        raise SidecarError("not a clash sidecar index")
    runs = [dict(r) for r in raw.get("runs") or () if isinstance(r, Mapping) and r.get("run_id")]
    return sorted(runs, key=lambda r: str(r.get("created_at", "")), reverse=True)


def index_with(raw: str | bytes | Mapping[str, Any] | None, summary: Mapping[str, Any], keep: int = 50) -> dict:
    """The index document with ``summary`` added (replacing a line with the same run id), newest
    ``keep`` kept. Read-modify-write, last write wins -- the ``_groups/`` precedent."""
    runs = [r for r in parse_index(raw) if r.get("run_id") != summary.get("run_id")]
    runs.insert(0, dict(summary))
    runs = sorted(runs, key=lambda r: str(r.get("created_at", "")), reverse=True)[:keep]
    return {"schema": CLASH_INDEX_SCHEMA, "runs": runs}
