"""Convex-contact runs on a viewer GLB, and the hand-off from a run's clashes to a connection builder.

THE CHECK. :func:`check_glb` measures the members of a viewer GLB with adacpp
(``adacpp.clash.find_glb_contacts``) and returns the run as a sidecar (``ada.clash.sidecar``). A
browser runs the same C++ on the same bytes through adacpp's wasm module and posts the same document,
so a run is a run whichever engine made it.

THE HAND-OFF. A run names its members as the GLB does. A builder needs the members themselves, so a
detail job rebuilds the model from the run's target, BINDS each clash's two members by name
(:func:`bind_members`), and turns the stored clash into exactly what a clash pass hands a builder
(:func:`found_for`): the members incoming-first, the landing member, the end, and the contact in
core's vocabulary -- the measured contact from the run, patch polygons included, never re-measured.
Which member is incoming is decided here, from the rebuilt members' axes, because the GLB has none.

ROLES ON THE RUN. Annotating a run (:func:`annotate_applicable`) writes each clash's ``roles`` --
``{"incoming": k, "landing": l}``, member indices -- so a viewer can tell the two apart without
rebuilding anything. From the bound members where they resolve them; where they do not (a model whose
provider cannot be read into members in this process, a name that does not bind), from the members'
MESHES in the GLB (:func:`mesh_anchors`: an elongated mesh's two ends on its principal axis, else its
centroid), judged by the same nearest-anchor rule.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from ada.clash.sidecar import ContactRecord, ContactRun, run_from_glb_contacts
from ada.config import logger

__all__ = [
    "MemberBinding",
    "annotate_applicable",
    "available",
    "bind_members",
    "check_glb",
    "found_for",
    "founds_by_id",
    "member_vertices",
    "mesh_anchors",
    "roles_from_meshes",
]


def available() -> bool:
    """Whether the installed adacpp can measure a viewer GLB."""
    try:
        import adacpp.clash  # noqa: PLC0415 - probing an optional backend

        return hasattr(adacpp.clash, "find_glb_contacts")
    except Exception:  # noqa: BLE001 - an adacpp that cannot import is one that cannot answer
        return False


def _adacpp_version() -> str:
    try:
        import adacpp  # noqa: PLC0415

        return str(getattr(adacpp, "__version__", "unknown"))
    except Exception:  # noqa: BLE001
        return "unknown"


def check_glb(
    glb: bytes,
    *,
    target: Mapping[str, Any],
    glb_key: str,
    up_axis: str = "z",
    options: Mapping[str, Any] | None = None,
    engine: str = "worker",
    run_id: str | None = None,
) -> ContactRun:
    """Measure the members of the viewer GLB ``glb`` (the bytes stored at ``glb_key``)."""
    import adacpp.clash  # noqa: PLC0415 - guarded by available()

    opts = {
        "security_margin": float((options or {}).get("security_margin", adacpp.clash.DEFAULT_SECURITY_MARGIN)),
        "min_contact_area": float((options or {}).get("min_contact_area", adacpp.clash.DEFAULT_MIN_CONTACT_AREA)),
        "line_contact_thickness": float(
            (options or {}).get("line_contact_thickness", adacpp.clash.DEFAULT_LINE_CONTACT_THICKNESS)
        ),
    }
    found = adacpp.clash.find_glb_contacts(glb, up_axis=up_axis, **opts)
    for i, j, reason in found["failures"]:
        logger.warning(f"clash: no contact measured between members {i} and {j}: {reason}")
    return run_from_glb_contacts(
        found,
        run_id=run_id or uuid.uuid4().hex,
        created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        engine=engine,
        target=target,
        geometry={"key": glb_key, "sha256": hashlib.sha256(glb).hexdigest(), "up_axis": up_axis},
        options=opts,
        provenance={
            "adacpp": _adacpp_version(),
            "candidate_pairs": int(found["candidate_pairs"]),
            "below_min_area": int(found["below_min_area"]),
            "degenerate": [int(k) for k in found["degenerate"]],
        },
    )


class MemberBinding:
    """A run's members bound to a rebuilt model by name: ``member(k)`` or why not.

    A GROUP run spans several models: ``model`` is then a sequence, one rebuilt model per part of the
    run (``None`` for a part that did not rebuild), and each member binds by name inside its own
    part's model only -- the same name in two models is two members."""

    def __init__(self, run: ContactRun, model) -> None:
        models = list(model) if isinstance(model, (list, tuple)) else None
        indices: list[dict[str, list[Any]] | None] = []
        for m in models if models is not None else [model]:
            if m is None:
                indices.append(None)
                continue
            by_name: dict[str, list[Any]] = {}
            for obj in m.get_all_physical_objects():
                by_name.setdefault(str(getattr(obj, "name", "") or ""), []).append(obj)
            indices.append(by_name)
        self._objects: list[Any] = []
        self._why: list[str | None] = []
        for m in run.members:
            part = int(m.get("part", 0)) if models is not None else 0
            by_name = indices[part] if 0 <= part < len(indices) else None
            if by_name is None:
                self._objects.append(None)
                self._why.append(f"the model of part {part} did not rebuild, so {m['name']!r} cannot be bound")
                continue
            hits = by_name.get(m["name"], [])
            if len(hits) == 1:
                self._objects.append(hits[0])
                self._why.append(None)
            else:
                self._objects.append(None)
                self._why.append(
                    f"no member named {m['name']!r} in the rebuilt model"
                    if not hits
                    else f"{len(hits)} members are named {m['name']!r} in the rebuilt model; the clash cannot say which"
                )

    def member(self, k: int):
        return self._objects[k]

    def reason(self, k: int) -> str | None:
        return self._why[k]


def bind_members(run: ContactRun, model) -> MemberBinding:
    return MemberBinding(run, model)


def found_for(run: ContactRun, record: ContactRecord, binding: MemberBinding):
    """The stored clash as the ``_Found`` a clash pass returns -- incoming-first, landing resolved,
    the contact typed, its patch polygons in ``contact.extras["patches"]``. Raises ``LookupError``
    when either member does not bind."""
    from ada.clash.convex_contact import found_from_contact
    from ada.clash.result import ClashContact

    i, j = record.members
    for k in (i, j):
        if binding.member(k) is None:
            raise LookupError(binding.reason(k))
    members = [binding.member(i), binding.member(j)]
    found = found_from_contact(
        members,
        {
            "i": 0,
            "j": 1,
            "near1": record.near_points[0],
            "near2": record.near_points[1],
            "normal": record.normal,
            "penetration_depth": record.distance,
            "contact_area": record.contact_area,
        },
        float(run.options.get("security_margin", 0.0)),
    )
    flipped = found.members[0] is members[1]
    patches = [
        {
            # Restated incoming-first, like the near points and the normal.
            "normal": [-c for c in p.normal] if flipped else list(p.normal),
            "points_incoming": [list(q) for q in (p.points2 if flipped else p.points1)],
            "points_landing": [list(q) for q in (p.points1 if flipped else p.points2)],
        }
        for p in record.patches
    ]
    contact = dict(found.contact)
    extras = dict(contact.get("extras") or {})
    extras.update({"clash_id": record.id, "run_id": run.run_id, "patches": patches})
    contact["extras"] = extras
    found.origin = "convex-contact"
    found.contact = ClashContact.from_dict(contact)
    return found


def founds_by_id(run: ContactRun, model, ids: Sequence[str]) -> tuple[dict[str, Any], dict[str, str]]:
    """``(by_id, problems)`` for the clashes ``ids`` of ``run``, against the rebuilt ``model``: the
    ``_Found`` of every clash whose members bind, and why for every one that does not."""
    binding = bind_members(run, model)
    by_id: dict[str, Any] = {}
    problems: dict[str, str] = {}
    for cid in ids:
        record = run.contact(cid)
        if record is None:
            problems[cid] = "not a clash of this run"
            continue
        try:
            by_id[cid] = found_for(run, record, binding)
        except LookupError as exc:
            problems[cid] = str(exc)
    return by_id, problems


#: A mesh whose longest extent is at least this many times its second is read as a BEAM (two ends on
#: its long axis); anything stubbier as a plate-like part (its centroid, endless) -- the two anchor
#: kinds ``ada.clash.convex_contact`` gives a rebuilt beam and plate.
ELONGATED = 3.0


def mesh_anchors(vertices) -> list[tuple[Any, str | None]] | None:
    """Where a member can be met, read off its MESH (``(n, 3)`` vertices, any frame): an elongated
    mesh's two ends on its principal axis (through its centroid), else its centroid twice -- the
    stand-in for the axes a GLB does not carry. ``None`` for fewer than two distinct points."""
    import numpy as np

    pts = np.asarray(vertices, dtype=float).reshape(-1, 3)
    if len(pts) < 2:
        return None
    centroid = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
    proj = (pts - centroid) @ vt.T
    extent = proj.max(axis=0) - proj.min(axis=0)
    length = float(extent[0])
    if length <= 0.0:
        return None
    width = float(extent[1]) if len(extent) > 1 else 0.0
    if width > 0.0 and length / width < ELONGATED:
        return [(centroid, None), (centroid, None)]
    axis = vt[0]
    return [(centroid + axis * float(proj[:, 0].min()), "start"), (centroid + axis * float(proj[:, 0].max()), "end")]


def roles_from_found(record: ContactRecord, found, binding: MemberBinding) -> dict | None:
    """``{"incoming", "landing"}`` member indices of ``record`` from its bound ``_Found``, or None
    where the rebuilt members did not resolve them."""
    if found.landing is None:
        return None
    i, j = record.members
    incoming = j if found.members[0] is binding.member(j) else i
    return {"incoming": incoming, "landing": j if incoming == i else i}


def roles_from_meshes(record: ContactRecord, vertices: Mapping[int, Any]) -> dict | None:
    """``{"incoming", "landing"}`` from the two members' meshes (:func:`mesh_anchors`), by the rule a
    rebuilt member is judged by (``convex_contact.pick_incoming``); None without both meshes."""
    from ada.clash.convex_contact import pick_incoming

    i, j = record.members
    if i not in vertices or j not in vertices:
        return None
    picked = pick_incoming(mesh_anchors(vertices[i]), mesh_anchors(vertices[j]), record.near_points[0])
    if picked is None:
        return None
    incoming = i if picked[0] == 0 else j
    return {"incoming": incoming, "landing": j if incoming == i else i}


def member_vertices(
    run: ContactRun,
    get_bytes: Callable[[str], bytes],
    read_members: Callable[[bytes, str], Sequence[tuple[str, str, Any]]] | None = None,
) -> dict[int, Any]:
    """Each run member's vertices (Z-up model frame), by member index, read from the GLB(s) the run
    measured (``adacpp.clash.glb_members``, or ``read_members(glb, up_axis)``). A GLB whose sha256 no
    longer matches the run is skipped -- its element ids may name other members now -- and so is
    everything when adacpp cannot read a GLB here: those roles are then left unresolved."""
    if read_members is None:
        try:
            import adacpp.clash  # noqa: PLC0415 - optional backend
        except Exception:  # noqa: BLE001
            return {}
        if not hasattr(adacpp.clash, "glb_members"):
            return {}
        read_members = adacpp.clash.glb_members
    up_axis = str(run.geometry.get("up_axis") or "z")
    parts = run.parts or ({"key": run.geometry.get("key"), "sha256": run.geometry.get("sha256")},)
    # Which member each GLB element is: a group run's by (part, element), a one-GLB run's by its id.
    wanted: list[dict[str, int]] = [{} for _ in parts]
    for k, m in enumerate(run.members):
        p = int(m.get("part", 0)) if run.parts else 0
        if 0 <= p < len(wanted):
            wanted[p][str(m.get("element", m["id"])) if run.parts else str(m["id"])] = k
    out: dict[int, Any] = {}
    for p, part in enumerate(parts):
        if not wanted[p] or not part.get("key"):
            continue
        try:
            glb = get_bytes(str(part["key"]))
        except Exception as exc:  # noqa: BLE001 - one unreadable GLB leaves its roles unresolved
            logger.warning(f"clash: roles: could not read {part.get('key')}: {exc}")
            continue
        if hashlib.sha256(glb).hexdigest() != part.get("sha256"):
            logger.warning(f"clash: roles: {part.get('key')} changed since run {run.run_id}; its roles stay unresolved")
            continue
        for element, _name, verts in read_members(glb, up_axis):
            k = wanted[p].get(str(element))
            if k is not None:
                out[k] = verts
    return out


def annotate_applicable(
    run: ContactRun,
    model,
    capability_of: Callable[[Any], str | None] | None = None,
    *,
    vertices: Mapping[int, Any] | Callable[[], Mapping[int, Any]] | None = None,
) -> tuple[ContactRun, int]:
    """``run`` with each clash's ``applicable`` specs resolved against the rebuilt ``model`` (``None``:
    nothing rebuilt), and how many clashes could not be bound (left without specs).

    Each clash's ``roles`` too: from the bound members where they resolve them, else from the two
    members' meshes -- ``vertices`` by member index (:func:`member_vertices`), or a callable asked for
    them the first time one is needed -- so a run whose model cannot be rebuilt here still says which
    member is incoming. ``provenance["roles"]`` counts how each clash's roles were resolved."""
    from ada.clash.builtin_specs import register_builtin_specs
    from ada.clash.match import applicable_specs

    register_builtin_specs()
    binding = bind_members(run, model)
    meshes: Mapping[int, Any] | None = vertices if isinstance(vertices, Mapping) else None

    def mesh_roles(record: ContactRecord) -> dict | None:
        nonlocal meshes
        if meshes is None:
            meshes = (vertices() if callable(vertices) else None) or {}
        return roles_from_meshes(record, meshes)

    unbound = 0
    counts = {"members": 0, "mesh": 0, "unresolved": 0}
    records = []
    for record in run.contacts:
        try:
            found = found_for(run, record, binding)
        except LookupError:
            found = None
        roles = roles_from_found(record, found, binding) if found is not None else None
        if roles is not None:
            counts["members"] += 1
        else:
            roles = mesh_roles(record)
            counts["mesh" if roles is not None else "unresolved"] += 1
        if found is None:
            unbound += 1
            records.append(replace(record, applicable=(), roles=roles))
            continue
        specs = applicable_specs(found.members, landing=found.landing, capability_of=capability_of)
        records.append(replace(record, applicable=tuple(s.to_dict() for s in specs), roles=roles))
    provenance = dict(run.provenance)
    provenance["applicability"] = {"resolved": len(records) - unbound, "unbound": unbound}
    provenance["roles"] = counts
    return replace(run, contacts=tuple(records), provenance=provenance), unbound
