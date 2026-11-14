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
:data:`WELD_ASSEMBLY_SCHEMA` or :data:`WELD_ASSEMBLY_SCHEMA_CURVED`, in metres, in the PART FRAME (+Z
up, origin under the part as it stands on a flat surface, +X along the member the welds lie on)::

    schema      "ada-weld-assembly/1" | "ada-weld-assembly/2"
    units       "m"
    frame       a sentence on the frame
    landing     name of the member the welds lie on ("" when none)
    mass_kg     the part's mass, cog its centre of gravity (part frame)
    members     [{name, mass_kg, vertices: [[x, y, z]..], faces: [[i, j, k]..]}]
    welds       [{id: "weld1".., name, type, throat, members: [names], points: [[x, y, z]..] (root
                  line), normal_a (the face the seam lies on), normal_b (the other face)}]
    source      optional, provenance only: {kind: "connection-spec" | "detailing" | .., spec_name?,
                  spec_inputs?, model?, joint?}

**Straight and curved seams.** In ``/1`` every seam is straight: its points lie on one line and the
two normals hold all along it. ``/2`` adds curved and closed seams (a tubular joint's saddle weld runs
round the brace and back to where it started). Every weld of a ``/2`` document also carries, per point
of its root line:

    closed      true when the line runs on from its last point back to its first (the first point
                  is NOT repeated at the end)
    tangents    [[x, y, z]..]: the direction of travel at each point (unit; a closed line's wraps)
    normals_a   [[x, y, z]..]: the outward normal of the face the seam lies on, at each point
    normals_b   [[x, y, z]..]: the outward normal of the other face, at each point

and ``normal_a`` / ``normal_b`` are then the normals at the line's middle point. A straight seam is
the special case: two (or more, collinear) points, one tangent, the same normals at every point. The
writer (:func:`weld_assembly_from_part`) writes ``/1`` while every seam is straight -- the document is
then exactly what it always was -- and ``/2`` once one is not, so a reader that knows only ``/1``
refuses a curved seam rather than misreading it as a line from its first point to its last.
:func:`weld_seam` gives any weld of either version as the per-point form.

Parsing and writing are stdlib; the converter from a built part (:func:`weld_assembly_from_part`)
needs numpy and the modelling stack, and imports nothing from the REST layer.
"""

from __future__ import annotations

import gzip
import itertools
import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "SUBJECT_KINDS",
    "WELD_ASSEMBLY_KIND",
    "WELD_ASSEMBLY_MEDIA_TYPE",
    "WELD_ASSEMBLY_SCHEMA",
    "WELD_ASSEMBLY_SCHEMA_CURVED",
    "WELD_ASSEMBLY_SCHEMAS",
    "STEEL_DENSITY",
    "SubjectKind",
    "SubjectRef",
    "WeldAssemblyError",
    "crop_prism",
    "dump_weld_assembly",
    "parse_weld_assembly",
    "subject_kind",
    "weld_assembly_from_part",
    "weld_seam",
    "write_weld_assembly",
]

WELD_ASSEMBLY_KIND = "weld-assembly"
#: Every seam straight: points on one line, one pair of normals for it.
WELD_ASSEMBLY_SCHEMA = "ada-weld-assembly/1"
#: Curved and closed seams too: every weld carries ``closed`` and its tangents and normals per point.
WELD_ASSEMBLY_SCHEMA_CURVED = "ada-weld-assembly/2"
#: The schemas :func:`parse_weld_assembly` reads.
WELD_ASSEMBLY_SCHEMAS = (WELD_ASSEMBLY_SCHEMA, WELD_ASSEMBLY_SCHEMA_CURVED)
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
            f"with the two face normals at the seam ({WELD_ASSEMBLY_SCHEMA}; {WELD_ASSEMBLY_SCHEMA_CURVED} "
            "for curved and closed seams).",
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
    if not isinstance(doc, dict) or doc.get("schema") not in WELD_ASSEMBLY_SCHEMAS:
        found = doc.get("schema") if isinstance(doc, dict) else None
        raise WeldAssemblyError(f"not a {' or '.join(WELD_ASSEMBLY_SCHEMAS)} document (schema: {found!r})")
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
        if doc["schema"] == WELD_ASSEMBLY_SCHEMA_CURVED:
            _check_seam(w)
    if len({w["id"] for w in welds}) != len(welds):
        raise WeldAssemblyError("two welds share an id")
    return doc


def _norm(v) -> float:
    return math.sqrt(sum(float(x) * float(x) for x in v))


def _check_seam(w: Mapping[str, Any]) -> None:
    """A ``/2`` weld's per-point fields: there, one per point, unit, and a line without a zero-length step."""
    wid = w["id"]
    if not isinstance(w.get("closed"), bool):
        raise WeldAssemblyError(f"weld {wid!r} closed must be true or false")
    points = w["points"]
    if w["closed"] and len(points) < 3:
        raise WeldAssemblyError(f"weld {wid!r} is closed and must have at least 3 points")
    for field in ("tangents", "normals_a", "normals_b"):
        value = w.get(field)
        if not isinstance(value, list) or len(value) != len(points) or not all(_is_vector(v) for v in value):
            raise WeldAssemblyError(f"weld {wid!r} {field} must be one [x, y, z] vector per point")
        if any(abs(_norm(v) - 1.0) > 1e-3 for v in value):
            raise WeldAssemblyError(f"weld {wid!r} {field} must be unit vectors")
    steps = list(itertools.pairwise(points)) + ([(points[-1], points[0])] if w["closed"] else [])
    if any(_norm([b[k] - a[k] for k in range(3)]) < 1e-9 for a, b in steps):
        raise WeldAssemblyError(f"weld {wid!r} has two points in one place: a zero-length step")


def weld_seam(weld: Mapping[str, Any]) -> dict[str, Any]:
    """One weld of a parsed assembly (either schema) as its seam, point by point: ``{points, closed,
    tangents, normals_a, normals_b}``, plain lists. A ``/1`` weld is straight: its one direction (first
    point to last) and its two normals at every point."""
    points = [[float(x) for x in p] for p in weld["points"]]
    if "tangents" in weld and "normals_a" in weld and "normals_b" in weld:
        return {
            "points": points,
            "closed": bool(weld.get("closed", False)),
            "tangents": [[float(x) for x in v] for v in weld["tangents"]],
            "normals_a": [[float(x) for x in v] for v in weld["normals_a"]],
            "normals_b": [[float(x) for x in v] for v in weld["normals_b"]],
        }
    d = [points[-1][k] - points[0][k] for k in range(3)]
    n = _norm(d)
    if n < 1e-12:
        raise WeldAssemblyError(f"weld {weld.get('id')!r} is straight and has no length")
    t = [x / n for x in d]
    return {
        "points": points,
        "closed": False,
        "tangents": [list(t) for _ in points],
        "normals_a": [[float(x) for x in weld["normal_a"]] for _ in points],
        "normals_b": [[float(x) for x in weld["normal_b"]] for _ in points],
    }


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


#: The longest step between two points of a curved seam's line where it is sampled from an arc [m],
#: and the most an arc turns between two of them [rad].
SEAM_SPACING = 0.01
SEAM_ARC_STEP = math.radians(10.0)
#: Two points of a weld line closer than this are one [m]; a line whose ends are this close is closed.
_SAME_POINT = 1e-5
#: How far from a member's mesh a seam point may be for the mesh's faces there to say its normal [m]:
#: the root lies on both faces, a tessellated round surface a fraction of a millimetre off it.
_ON_SURFACE = 0.003


def _arc_points(seg) -> list:
    """An arc segment's points after its start, at most SEAM_SPACING / SEAM_ARC_STEP apart; None when the
    segment does not say its centre (then only its end is had)."""
    import numpy as np

    centre, mid = getattr(seg, "center", None), getattr(seg, "midpoint", None)
    if centre is None or mid is None:
        return None
    c = np.asarray(centre, dtype=float)
    a, m, b = (np.asarray(x, dtype=float) - c for x in (seg.p1, mid, seg.p2))
    r = float(np.linalg.norm(a))
    normal = np.cross(a, m)
    if r < 1e-9 or float(np.linalg.norm(normal)) < 1e-12:
        return None
    normal /= np.linalg.norm(normal)

    def angle(u, v):
        return math.atan2(float(np.dot(np.cross(u, v), normal)), float(np.dot(u, v))) % (2 * math.pi)

    total = angle(a, m) + angle(m, b)
    n = max(1, math.ceil(max(total / SEAM_ARC_STEP, total * r / SEAM_SPACING)))
    e1, e2 = a / r, np.cross(normal, a / r)
    return [c + r * (math.cos(total * k / n) * e1 + math.sin(total * k / n) * e2) for k in range(1, n + 1)]


def _weld_line(weld):
    """A weld's root line, model coordinates: ``(points N x 3, closed)``. An arc of the sweep curve is
    sampled; two points in one place are one; a line that ends where it started is closed, and its first
    point is then not repeated at its end."""
    import numpy as np

    curve = weld.geometry.sweep_curve
    raw = [[float(p[0]), float(p[1]), float(p[2])] for p in curve.points3d]
    segments = getattr(curve, "segments", None) or []
    if segments and any(type(seg).__name__ == "ArcSegment" for seg in segments):
        sampled = [np.asarray(segments[0].p1, dtype=float)]
        for seg in segments:
            arc = _arc_points(seg) if type(seg).__name__ == "ArcSegment" else None
            sampled += arc if arc is not None else [np.asarray(seg.p2, dtype=float)]
        raw = [list(map(float, p)) for p in sampled]
    points = [np.asarray(raw[0])] if raw else []
    for p in raw[1:]:
        if float(np.linalg.norm(np.asarray(p) - points[-1])) >= _SAME_POINT:
            points.append(np.asarray(p))
    closed = len(points) >= 4 and float(np.linalg.norm(points[0] - points[-1])) < _SAME_POINT * 10
    if closed:
        points = points[:-1]
    if len(points) < 2:
        raise WeldAssemblyError(f"weld {weld.name!r} has no path")
    return np.asarray(points, dtype=float), closed


def _is_straight(points, closed: bool) -> bool:
    import numpy as np

    if closed:
        return False
    chord = points[-1] - points[0]
    length = float(np.linalg.norm(chord))
    if length < 1e-9:
        return False
    off = points - points[0]
    across = off - np.outer(off @ (chord / length), chord / length)
    return float(np.linalg.norm(across, axis=1).max()) < 1e-6 + 1e-6 * length


def _tangents(points, closed: bool):
    """The direction of travel at each point of a line: central differences, wrapped round a closed line,
    one-sided at an open line's ends. Never zero: the line has no two points in one place."""
    import numpy as np

    if closed:
        d = np.roll(points, -1, axis=0) - np.roll(points, 1, axis=0)
    else:
        d = np.gradient(points, axis=0)
    n = np.linalg.norm(d, axis=1)
    if float(n.min()) < 1e-12:
        raise WeldAssemblyError("a weld has a zero-length direction")
    return d / n[:, None]


def _distance_to_triangles(p, tri):
    """Distance from the point ``p`` to each triangle of ``tri`` (T x 3 x 3), and the triangles' unit normals."""
    import numpy as np

    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    n = np.cross(b - a, c - a)
    area2 = np.linalg.norm(n, axis=1)
    ok = area2 > 1e-18
    n = np.where(ok[:, None], n / np.maximum(area2, 1e-18)[:, None], 0.0)
    d_plane = np.einsum("ij,ij->i", p - a, n)
    q = p - d_plane[:, None] * n
    # Inside the triangle when the projection is on the inner side of all three edges.
    inside = ok.copy()
    for u, v in ((a, b), (b, c), (c, a)):
        inside &= np.einsum("ij,ij->i", np.cross(v - u, q - u), n) >= -1e-12
    best = np.full(len(tri), np.inf)
    for u, v in ((a, b), (b, c), (c, a)):
        e = v - u
        t = np.clip(np.einsum("ij,ij->i", p - u, e) / np.maximum(np.einsum("ij,ij->i", e, e), 1e-18), 0.0, 1.0)
        best = np.minimum(best, np.linalg.norm(p - (u + t[:, None] * e), axis=1))
    dist = np.where(inside, np.abs(d_plane), best)
    return dist, n, 0.5 * area2


def _face_normal(tri, point, accept):
    """The outward normal of a member's surface at ``point``: the area-weighted mean of the normals of its
    mesh's faces within _ON_SURFACE of it that ``accept(normals)`` keeps; None when there are none."""
    import numpy as np

    if tri is None or not len(tri):
        return None
    dist, normals, area = _distance_to_triangles(np.asarray(point, dtype=float), tri)
    near = dist <= _ON_SURFACE
    if not np.any(near):
        return None
    keep = near & accept(normals)
    if not np.any(keep):
        return None
    mean = (normals[keep] * area[keep][:, None]).sum(axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm > 1e-9 else None


def _seam_frames(points, closed: bool, up_hint, centre, tri_a=None, tri_b=None):
    """Per point of a curved seam: ``(tangents, normals_a, normals_b)``, model coordinates.

    ``normals_a`` is the outward normal of the face the seam lies on (the second member's surface: of the
    faces of its mesh at the point, those looking the way ``up_hint`` -- the profile's -x, the first
    member's cut-face normal -- does), made square to the seam; where the mesh says nothing there,
    ``up_hint`` itself. ``normals_b`` is the other face's (the first member's faces at the point that are
    NOT the end resting on the first face: their normal lies across ``normals_a``), else the direction
    square to both the seam and ``up_hint`` -- the side of a member standing on its cut face, as a
    straight seam's is; it looks away from ``centre``, the middle of what the seam runs round.
    """
    import numpy as np

    tangents = _tangents(points, closed)
    up = _unit(up_hint)
    normals_a, normals_b = [], []
    for p, t in zip(points, tangents):
        n_a = _face_normal(tri_a, p, lambda n: n @ up > 0.2)
        n_a = up if n_a is None else n_a
        n_a = n_a - float(np.dot(n_a, t)) * t
        if float(np.linalg.norm(n_a)) < 1e-6:  # the seam runs along up_hint: no face to speak of
            raise WeldAssemblyError("a weld runs along the normal of the face it lies on")
        n_a = _unit(n_a)
        across = np.cross(t, up)
        if float(np.linalg.norm(across)) < 1e-6:
            across = np.cross(t, n_a)
        across = _unit(across)
        if float(np.dot(across, p - centre)) < 0:
            across = -across
        n_b = _face_normal(tri_b, p, lambda n, n_a=n_a: np.abs(n @ n_a) < 0.8)
        if n_b is None or float(np.dot(n_b, across)) <= 0.0:
            n_b = across
        normals_a.append(n_a)
        normals_b.append(_unit(n_b))
    return tangents, np.asarray(normals_a), np.asarray(normals_b)


def _member_triangles(member):
    """A member's mesh as triangles (T x 3 x 3), model coordinates; None for one without geometry."""
    if member is None or not hasattr(member, "to_trimesh"):
        return None
    try:
        vertices, faces = _member_mesh(member)
    except Exception:  # noqa: BLE001 - no mesh: the seam's normals come from its line alone
        return None
    return vertices[faces]


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
        points, closed = _weld_line(weld)
        weld_members = list(weld.members)
        raw.append(
            {
                "weld": weld,
                "points": points,
                "closed": closed,
                "straight": _is_straight(points, closed),
                # The profile's +x is the incoming's cut-face outward normal: INTO the face the seam lies on.
                "normal_a": -_unit(weld.xdir),
                "first": weld_members[0] if weld_members else None,
                "on": weld_members[1] if len(weld_members) > 1 else None,
            }
        )
    # The other face at the seam looks away from the middle of the end it runs around.
    meshes: dict[int, Any] = {}

    def triangles_of(member):
        if member is not None and id(member) not in meshes:
            meshes[id(member)] = _member_triangles(member)
        return None if member is None else meshes[id(member)]

    for item in raw:
        loop = np.concatenate([o["points"] for o in raw if o["first"] is item["first"]])
        centre = loop.mean(axis=0)
        points, n_a = item["points"], item["normal_a"]
        if not item["straight"]:
            # A curved or closed seam: its tangent and both faces' normals change along it.
            item["frames"] = _seam_frames(
                points, item["closed"], n_a, centre, triangles_of(item["on"]), triangles_of(item["first"])
            )
            mid = len(points) // 2
            item["normal_mid_a"], item["normal_b"] = item["frames"][1][mid], item["frames"][2][mid]
            continue
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

    def directions(vs) -> list:
        vs = np.asarray(vs, dtype=float) @ R.T
        return np.round(vs / np.linalg.norm(vs, axis=1)[:, None], 6).tolist()

    curved = any(not item["straight"] for item in raw)

    def weld_doc(number: int, item) -> dict[str, Any]:
        out = {
            # Numbered within the document: the connection library names welds from a process-wide
            # counter, so the same joint built twice has other names.
            "id": f"weld{number}",
            "name": str(item["weld"].name),
            "type": str(getattr(item["weld"].type, "value", item["weld"].type)).lower(),
            "throat": float(item["weld"].throat or 0.0),
            "members": [str(m.name) for m in item["weld"].members],
            "points": to_frame(item["points"]),
            "normal_a": direction(item.get("normal_mid_a", item["normal_a"])),
            "normal_b": direction(item["normal_b"]),
        }
        if curved:  # every weld of a /2 document is given point by point, a straight one too
            n = len(item["points"])
            if "frames" in item:
                tangents, normals_a, normals_b = item["frames"]
            else:
                tangents = [_unit(item["points"][-1] - item["points"][0])] * n
                normals_a, normals_b = [item["normal_a"]] * n, [item["normal_b"]] * n
            out.update(
                closed=bool(item["closed"]),
                tangents=directions(tangents),
                normals_a=directions(normals_a),
                normals_b=directions(normals_b),
            )
        return out

    doc: dict[str, Any] = {
        "schema": WELD_ASSEMBLY_SCHEMA_CURVED if curved else WELD_ASSEMBLY_SCHEMA,
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
        "welds": [weld_doc(number, item) for number, item in enumerate(raw, start=1)],
    }
    if stub_length is not None:
        doc["stub_length"] = float(stub_length)
    if source:
        doc["source"] = dict(source)
    return doc
