"""What a simulation run can be started ON: a subject.

A subject is a file in the scope of a kind core defines (:data:`SUBJECT_KINDS`, a closed set like
:data:`~ada.simulation.interfaces.SIMULATION_DOMAINS`). Whatever produces one (the connection studio,
a detailing engine) writes the kind's file format, and whatever consumes one (a simulation provider)
reads it and says in its description which kinds it ``accepts``; the two never know each other, only
this module. A run is started on a subject by naming its kind and key
(``POST /scopes/{scope}/simulations`` with ``subject``), and the provider finds it as
``case.subject``.

The one kind so far is ``weld-assembly``: a part with welds assigned -- its members as closed
triangle meshes and each weld's root line with the two face normals at the seam. Its file is JSON
(``application/json``; gzip is allowed, ``.json.gz``, and found by its magic) of schema
:data:`WELD_ASSEMBLY_SCHEMA`, in metres, in the PART FRAME (+Z up, origin under the part as it stands
on a flat surface, +X along the member the welds lie on)::

    schema      "ada-weld-assembly/1"
    units       "m"
    frame       a sentence on the frame
    landing     name of the member the welds lie on ("" when none)
    mass_kg     the part's mass, cog its centre of gravity (part frame)
    members     [{name, mass_kg, vertices: [[x, y, z]..], faces: [[i, j, k]..]}]
    welds       [{id: "weld1".., name, type, throat, members: [names], points: [[x, y, z]..] (root
                  line), normal_a (the face the seam lies on), normal_b (the other face)}]
    source      optional, provenance only: {kind: "connection-spec" | "detailing" | .., spec_name?,
                  spec_inputs?, model?, joint?}

Parsing and writing are stdlib; the converter from a built part (:func:`weld_assembly_from_part`)
needs numpy and the modelling stack, and imports nothing from the REST layer.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "SUBJECT_KINDS",
    "WELD_ASSEMBLY_KIND",
    "WELD_ASSEMBLY_MEDIA_TYPE",
    "WELD_ASSEMBLY_SCHEMA",
    "STEEL_DENSITY",
    "SubjectKind",
    "SubjectRef",
    "WeldAssemblyError",
    "crop_prism",
    "dump_weld_assembly",
    "parse_weld_assembly",
    "subject_kind",
    "weld_assembly_from_part",
    "write_weld_assembly",
]

WELD_ASSEMBLY_KIND = "weld-assembly"
WELD_ASSEMBLY_SCHEMA = "ada-weld-assembly/1"
WELD_ASSEMBLY_MEDIA_TYPE = "application/json"

#: Steel, for a member whose material does not say [kg/m3].
STEEL_DENSITY = 7850.0


@dataclass(frozen=True)
class SubjectKind:
    """One kind of subject: its id, what it is and the media type of its file."""

    id: str
    title: str
    description: str
    media_type: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "description": self.description, "media_type": self.media_type}


#: The kinds of subject a run can be started on, by id. A closed set: a provider's ``accepts`` and a
#: run's ``subject`` must name one of these, and adding a kind is a change here (with its file format).
SUBJECT_KINDS: dict[str, SubjectKind] = {
    k.id: k
    for k in (
        SubjectKind(
            WELD_ASSEMBLY_KIND,
            "Weld assembly",
            "A part with welds assigned: its members as closed triangle meshes and each weld's root line "
            f"with the two face normals at the seam ({WELD_ASSEMBLY_SCHEMA}).",
            WELD_ASSEMBLY_MEDIA_TYPE,
        ),
    )
}


def subject_kind(kind_id: str) -> SubjectKind:
    """The kind with this id. ValueError, naming the known ones, for anything else."""
    try:
        return SUBJECT_KINDS[kind_id]
    except (KeyError, TypeError):
        raise ValueError(f"unknown subject kind {kind_id!r} (known: {', '.join(SUBJECT_KINDS)})") from None


@dataclass(frozen=True)
class SubjectRef:
    """What a run is on: the kind of subject and the storage key (scope-relative) of its file.
    ``SimulationCase.subject``; the key is also one of the case's ``inputs``."""

    kind: str
    key: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key}

    @classmethod
    def from_dict(cls, raw: Any) -> "SubjectRef | None":
        """The reference a run record holds, or None when it holds none (or not a well-formed one)."""
        if isinstance(raw, Mapping) and isinstance(raw.get("kind"), str) and isinstance(raw.get("key"), str):
            return cls(raw["kind"], raw["key"])
        return None


class WeldAssemblyError(ValueError):
    """A weld assembly cannot be made of a part, or a file is not one; the message says why."""


# -- reading and writing the file ------------------------------------------------------------------


def _is_vector(v: Any, n: int = 3) -> bool:
    return (
        isinstance(v, (list, tuple))
        and len(v) == n
        and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
    )


def _points(value: Any, what: str, *, at_least: int) -> None:
    if not isinstance(value, list) or len(value) < at_least or not all(_is_vector(p) for p in value):
        raise WeldAssemblyError(f"{what} must be a list of at least {at_least} [x, y, z] points")


def parse_weld_assembly(raw: bytes | str) -> dict[str, Any]:
    """A weld assembly from its file's bytes (plain or gzipped JSON), checked: the schema, at least one
    weld, and the shape of each member and weld. WeldAssemblyError, saying what is wrong, otherwise."""
    if isinstance(raw, (bytes, bytearray, memoryview)):
        raw = bytes(raw)
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError) as exc:
                raise WeldAssemblyError(f"the gzip stream is damaged: {exc}") from exc
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise WeldAssemblyError(f"not JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != WELD_ASSEMBLY_SCHEMA:
        found = doc.get("schema") if isinstance(doc, dict) else None
        raise WeldAssemblyError(f"not a {WELD_ASSEMBLY_SCHEMA} document (schema: {found!r})")
    if doc.get("units") != "m":
        raise WeldAssemblyError(f"units must be 'm', not {doc.get('units')!r}")

    members = doc.get("members")
    if not isinstance(members, list) or not members:
        raise WeldAssemblyError("the assembly has no members")
    names: set[str] = set()
    for i, m in enumerate(members):
        if not isinstance(m, dict) or not isinstance(m.get("name"), str):
            raise WeldAssemblyError(f"member {i} has no name")
        names.add(m["name"])
        _points(m.get("vertices"), f"member {m['name']!r} vertices", at_least=4)
        faces = m.get("faces")
        if not isinstance(faces, list) or not faces or not all(_is_vector(f) for f in faces):
            raise WeldAssemblyError(f"member {m['name']!r} faces must be a non-empty list of [i, j, k] triangles")
        if any(not isinstance(i, int) or i < 0 or i >= len(m["vertices"]) for f in faces for i in f):
            raise WeldAssemblyError(f"member {m['name']!r} has a face that names a vertex it does not have")

    welds = doc.get("welds")
    if not isinstance(welds, list) or not welds:
        raise WeldAssemblyError("the assembly has no welds")
    for i, w in enumerate(welds):
        if not isinstance(w, dict) or not isinstance(w.get("id"), str):
            raise WeldAssemblyError(f"weld {i} has no id")
        _points(w.get("points"), f"weld {w['id']!r} points", at_least=2)
        for normal in ("normal_a", "normal_b"):
            if not _is_vector(w.get(normal)):
                raise WeldAssemblyError(f"weld {w['id']!r} {normal} must be an [x, y, z] vector")
    if len({w["id"] for w in welds}) != len(welds):
        raise WeldAssemblyError("two welds share an id")
    return doc


def dump_weld_assembly(assembly: Mapping[str, Any]) -> bytes:
    """The file's bytes: compact JSON. Refuses what :func:`parse_weld_assembly` would."""
    raw = json.dumps(assembly, separators=(",", ":")).encode("utf-8")
    parse_weld_assembly(raw)
    return raw


def write_weld_assembly(storage: Any, scope: Any, key: str, assembly: Mapping[str, Any]) -> Any:
    """Store ``assembly`` at ``key`` (gzip at rest, like the run records) and return what the storage's
    ``put_bytes`` returns.

    ``storage`` is the API's scope-aware storage (``put_bytes(scope, key, data, content_encoding=)``,
    async: await the result) or, with ``scope=None``, a scope-bound facade such as a job is handed
    (``put_bytes(key, data, content_encoding=)``, sync)."""
    data = dump_weld_assembly(assembly)
    if scope is None:
        return storage.put_bytes(key, data, content_encoding="gzip")
    return storage.put_bytes(scope, key, data, content_encoding="gzip")


# -- from a built part -----------------------------------------------------------------------------


def _unit(v):
    import numpy as np

    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        raise WeldAssemblyError("a weld has a zero-length direction")
    return v / n


def _member_mesh(member):
    """A member's tessellation as ``(vertices V x 3, faces F x 3)``, in model coordinates."""
    import numpy as np

    mesh = member.to_trimesh()
    if hasattr(mesh, "geometry"):  # a trimesh.Scene: one mesh out of its parts
        mesh = mesh.to_geometry() if hasattr(mesh, "to_geometry") else mesh.dump(concatenate=True)
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=int)
    if len(vertices) == 0 or len(faces) == 0:
        raise WeldAssemblyError(f"member {getattr(member, 'name', '?')!r} has no geometry")
    return vertices, faces


def _volume_and_centroid(vertices, faces):
    """Volume and centroid of a closed, outward-wound triangle mesh (signed tetrahedra from the origin)."""
    import numpy as np

    tri = np.asarray(vertices, dtype=float)[np.asarray(faces, dtype=int)]
    signed = np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])) / 6.0
    volume = float(signed.sum())
    if volume <= 0:
        raise WeldAssemblyError("a member's mesh is not a closed, outward-facing solid: its mass cannot be had")
    return volume, (signed[:, None] * tri.sum(axis=1) / 4.0).sum(axis=0) / volume


def _density(member) -> float:
    try:
        return float(member.material.model.rho) or STEEL_DENSITY
    except Exception:  # noqa: BLE001 - no material, or one without a density
        return STEEL_DENSITY


def crop_prism(vertices, origin, axis, lo: float, hi: float, *, tol: float = 1e-6):
    """Shorten a prismatic member's mesh to the axial range ``lo``..``hi``.

    A beam's mesh is its section swept along its axis: every vertex past the kept range belongs to an
    end cap. Sliding those vertices along the axis onto the range's end plane is therefore an exact
    cut -- no remeshing, no capping of a hollow section. It is refused where it would not be exact:
    when the vertices beyond a plane are not all at one axial position (a coped or mitred end out
    there), the member is left at full length on that side.
    """
    import numpy as np

    vertices = np.asarray(vertices, dtype=float).copy()
    axis = _unit(axis)
    s = (vertices - np.asarray(origin, dtype=float)) @ axis
    for beyond, limit in ((s < lo - tol, lo), (s > hi + tol, hi)):
        if not np.any(beyond):
            continue
        if float(np.ptp(s[beyond])) > 1e-4:
            continue  # not a flat end cap: keep it as it is
        vertices[beyond] += np.outer(limit - s[beyond], axis)
    return vertices


def _weld_points(weld):
    import numpy as np

    points = np.asarray([[float(p[0]), float(p[1]), float(p[2])] for p in weld.geometry.sweep_curve.points3d])
    if len(points) < 2:
        raise WeldAssemblyError(f"weld {weld.name!r} has no path")
    return points


def weld_assembly_from_part(
    part,
    *,
    members: Iterable[Any] | None = None,
    stub_length: float | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The weld assembly of an ``ada.Connection`` or any ``ada.Part`` that holds welds.

    The welds are ``part.get_all_welds()``. The members are ``members`` when given -- for a part whose
    welds reference members that are NOT inside it (a detailing engine's connection, whose beams live
    in another part) -- else the members the welds name plus the physical objects of the part, once
    each. The part must sit at the model origin, unrotated, as a built component does.

    ``stub_length`` (metres) keeps each beam only that far past the welds: a preview's members are a
    metre or two long and a cell welds the joint, not the beams. ``None`` (the default) keeps every
    member whole, as a part detailed in a model has long members and wants them all.

    ``source`` is provenance only and is carried into the document as given.
    WeldAssemblyError, saying why, when no assembly can be made.
    """
    import numpy as np

    welds = list(part.get_all_welds())
    if not welds:
        raise WeldAssemblyError("the part has no welds")

    # -- the welds, in model coordinates ---------------------------------------
    raw = []
    for weld in welds:
        if weld.xdir is None:
            raise WeldAssemblyError(f"weld {weld.name!r} has no profile direction (xdir)")
        points = _weld_points(weld)
        weld_members = list(weld.members)
        raw.append(
            {
                "weld": weld,
                "points": points,
                # The profile's +x is the incoming's cut-face outward normal: INTO the face the seam lies on.
                "normal_a": -_unit(weld.xdir),
                "first": weld_members[0] if weld_members else None,
                "on": weld_members[1] if len(weld_members) > 1 else None,
            }
        )
    # The other face at the seam looks away from the middle of the end it runs around.
    for item in raw:
        loop = np.concatenate([o["points"] for o in raw if o["first"] is item["first"]])
        centre = loop.mean(axis=0)
        points, n_a = item["points"], item["normal_a"]
        tangent = _unit(points[-1] - points[0])
        mid = points[len(points) // 2] if len(points) > 2 else 0.5 * (points[0] + points[-1])
        outward = np.cross(tangent, n_a)
        if float(np.dot(outward, mid - centre)) < 0:
            outward = -outward
        item["normal_b"] = _unit(outward)

    all_points = np.concatenate([item["points"] for item in raw])

    # -- the members -----------------------------------------------------------
    if members is None:
        candidates: list[Any] = [m for item in raw for m in item["weld"].members]
        candidates += list(part.get_all_physical_objects())
    else:
        candidates = list(members)
    members_out = []
    seen: set[int] = set()
    for member in candidates:
        if id(member) in seen or not hasattr(member, "to_trimesh"):
            continue
        seen.add(id(member))
        if any(member is w for w in welds) or type(member).__name__ in ("Weld", "PrimSweep"):
            continue
        vertices, faces = _member_mesh(member)
        if stub_length is not None and hasattr(member, "n1") and hasattr(member, "n2"):
            origin = np.asarray(member.n1.p, dtype=float)
            axis = _unit(np.asarray(member.n2.p, dtype=float) - origin)
            along = (all_points - origin) @ axis
            vertices = crop_prism(
                vertices, origin, axis, float(along.min()) - stub_length, float(along.max()) + stub_length
            )
        volume, centroid = _volume_and_centroid(vertices, faces)
        members_out.append(
            {
                "name": str(member.name),
                "vertices": vertices,
                "faces": faces,
                "mass": volume * _density(member),
                "centroid": centroid,
            }
        )
    if not members_out:
        raise WeldAssemblyError("the part has no members with geometry")

    # -- the fixture frame ------------------------------------------------------
    # Up: the way the faces the seams lie on look -- the incoming stands on the landing.
    up = _unit(np.mean([item["normal_a"] for item in raw], axis=0))
    landing = next((item["on"] for item in raw if item["on"] is not None and hasattr(item["on"], "n1")), None)
    if landing is not None:
        along = np.asarray(landing.n2.p, dtype=float) - np.asarray(landing.n1.p, dtype=float)
    else:  # no beam to align with: the direction the geometry is longest in
        cloud = np.concatenate([m["vertices"] for m in members_out])
        along = np.linalg.svd(cloud - cloud.mean(axis=0), full_matrices=False)[2][0]
    along = along - float(np.dot(along, up)) * up
    if float(np.linalg.norm(along)) < 1e-6:
        along = np.cross(up, [1.0, 0.0, 0.0] if abs(up[0]) < 0.9 else [0.0, 1.0, 0.0])
    x_axis = _unit(along)
    y_axis = np.cross(up, x_axis)
    R = np.vstack([x_axis, y_axis, up])  # model -> part frame
    cloud = np.concatenate([m["vertices"] for m in members_out]) @ R.T
    origin = np.array(
        [
            0.5 * (cloud[:, 0].min() + cloud[:, 0].max()),
            0.5 * (cloud[:, 1].min() + cloud[:, 1].max()),
            cloud[:, 2].min(),
        ]
    )

    total_mass = sum(m["mass"] for m in members_out)
    cog = sum(m["mass"] * m["centroid"] for m in members_out) / total_mass

    def to_frame(points) -> list:
        return np.round(np.asarray(points, dtype=float) @ R.T - origin, 6).tolist()

    def direction(v) -> list:
        return np.round(R @ np.asarray(v, dtype=float), 6).tolist()

    doc: dict[str, Any] = {
        "schema": WELD_ASSEMBLY_SCHEMA,
        "units": "m",
        "frame": "part: +Z up, origin under the part, +X along the landing member",
        "landing": str(landing.name) if landing is not None else "",
        "mass_kg": round(float(total_mass), 4),
        "cog": to_frame([cog])[0],
        "members": [
            {
                "name": m["name"],
                "mass_kg": round(float(m["mass"]), 4),
                "vertices": to_frame(m["vertices"]),
                "faces": np.asarray(m["faces"]).tolist(),
            }
            for m in members_out
        ],
        "welds": [
            {
                # Numbered within the document: the connection library names welds from a process-wide
                # counter, so the same joint built twice has other names.
                "id": f"weld{number}",
                "name": str(item["weld"].name),
                "type": str(getattr(item["weld"].type, "value", item["weld"].type)).lower(),
                "throat": float(item["weld"].throat or 0.0),
                "members": [str(m.name) for m in item["weld"].members],
                "points": to_frame(item["points"]),
                "normal_a": direction(item["normal_a"]),
                "normal_b": direction(item["normal_b"]),
            }
            for number, item in enumerate(raw, start=1)
        ],
    }
    if stub_length is not None:
        doc["stub_length"] = float(stub_length)
    if source:
        doc["source"] = dict(source)
    return doc
