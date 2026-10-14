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
not decided here: it needs the members' axes, which the GLB does not carry, and is resolved when a
detail job binds the members (``ada.clash.convex_contact``).
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
    #: ``[{"id": element id, "name": name}]`` in the engine's order; contacts index into it.
    members: tuple[Mapping[str, str], ...]
    contacts: tuple[ContactRecord, ...]
    #: adacpp version and anything else worth knowing about how it ran.
    provenance: Mapping[str, Any] = field(default_factory=dict)
    schema: str = CLASH_SIDECAR_SCHEMA

    def to_dict(self) -> dict:
        return {
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

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    def summary(self, key: str) -> dict:
        """The run's line in its target's index."""
        return {
            "run_id": self.run_id,
            "key": key,
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

    members = []
    for m in raw["members"]:
        if not isinstance(m, Mapping) or "id" not in m or "name" not in m:
            raise SidecarError("every clash sidecar member needs an id and a name")
        members.append({"id": str(m["id"]), "name": str(m["name"])})

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
    )


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
