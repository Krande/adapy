"""A canonical, comparable description of everything an Abaqus deck can say about an adapy model.

``canonical(assembly)`` returns plain dicts/lists/strings/numbers, deterministic and JSON-safe, so
two models compare with ``==`` and :func:`diff_paths` lists exactly where they part. It covers
every construct the Abaqus writer emits (see ``ada/fem/formats/abaqus/write``), so a round trip
``canonical(read(write(model))) == canonical(model)`` is the whole-model check.

Representation-only differences are canonicalised, each by a rule stated where it is applied and
listed here. Nothing else is: a difference that survives these rules is a real one.

R1  Names are compared case-insensitively (lower-cased). Abaqus names are case-insensitive, and
    CAE and adapy's writer do not preserve case consistently.
R2  An element's type is compared as the Abaqus type it is written as -- the formulation it was
    read as if that is a formulation of its shape, else the writer's default for the shape
    (``mapping.element_types().write_type``). A model built in adapy has no formulation; a deck
    read back has the type it was written as. Both mean the same element.
R3  Boundary conditions are compared assembly-wide, keyed by (part owning the set, set name).
    A flat deck's part-level BC and the writer's assembly-level BC on the instance's set are the
    same BC. Step-level BCs are compared within their step.
R4  A BC's DOFs are compared as the set of constrained DOFs with the magnitude of each
    (``{dof: magnitude}``), or the named restraint (``ENCASTRE``, ...) -- whether the list has six
    slots or one entry per DOF is representation. BC types are compared through the mapping
    table (``DISPL_ROT`` is ``DISPL``).
R5  Floats are rounded to 12 significant digits: the text format is decimal, so a binary float
    may not survive exactly, but anything a writer truncates (e.g. ``.6E``) still shows.
R6  Parts are keyed by their FEM's instance name when they have one, else the part name, lower
    case: the writer names instances ``<part>-1`` and the reader keys the part by that.
R7  Object references (a section's material, a load's set, ...) are compared by name (R1), not by
    identity -- identity cannot survive a file.
R8  Sections are keyed by their element set, and an element refers to its section the same way.
    In Abaqus a section has no name of its own: it IS the property assignment of its elset
    (CAE's name lives in a ``** Section:`` comment). The name is still compared, as a field, so
    a writer that loses it shows once -- not once per element that refers to the section.
R9  A beam section's ``section_type`` / ``line1`` / ``temperature`` metadata is the reader's
    verbatim copy of the ``*Beam Section`` lines, holding what the typed profile holds; present on
    one side only, it is representation and is not compared. The typed profile is, so a profile
    that changes still shows. (Other verbatim text the writer emits in place of a construct IS
    compared -- except R11.)
R11 A material's ``aba_inp`` is the text written in its place; the reader rebuilds the typed
    material from that text and keeps no copy. The typed values are compared, so the text has to
    say what the typed model says (a zoo model that did not was a model contradicting itself).
R10 A surface is compared as the list of ``(set, face label)`` data lines it stands for, with the
    labels Abaqus uses: ``S<n>`` on a solid face, ``SPOS``/``SNEG`` on a shell side. adapy's
    shell face index carries only the sign (the writer writes -1 as SNEG, any other value as
    SPOS), and ``id_refs`` is the same list held pre-formatted -- which of the two a model holds
    is representation. A node surface's weight is compared as a field.
R11 Connectors are compared assembly-wide, keyed by element id, with their end nodes as
    ``(part, node id)``: the writer writes every connector at assembly level (it may join
    instances), so a connector defined in a part reads back on the assembly. The connector's own
    element set and orientation are its attributes, compared on it -- not as separate entries
    of whichever FEM happens to hold them.
R12 A constraint's DOFs are compared expanded (``ada.fem.constraints.expand_dofs``): ``[1, 2, 3]``
    and the reader's ``(first, last)`` ranges ``[[1, 1], [2, 2], [3, 3]]`` are one DOF list. An
    operand is compared by its nodes/elements, and by name -- except where Abaqus has no name for
    it: an MPC names nodes, not sets, so MPC operand set names are not compared.
R13 An orientation owned by a constraint, connector or section is compared on its owner (R11,
    ``csys`` fields); ``lcsys`` lists only the orientations nothing owns. Whether the owner's
    orientation is also registered in its FEM's ``lcsys`` is representation. An orientation with
    neither coordinates nor nodes is the global one, and is compared as its axes
    ``[[1, 0, 0], [0, 1, 0]]`` -- which is what the writer writes for it.
R14 A NODE surface over exactly one node set of the same name, used as a coupling operand, IS
    that set for comparison: ``*Coupling`` takes a surface, so the writer writes one over the
    coupling's node set, and the reader reads the surface. It is compared on the constraint
    (R12), not as a surface of its own.
R15 A nonstructural mass has no Abaqus element: ``*Nonstructural Mass`` spreads a value over a
    set of structural elements. adapy holds it as a pseudo-element, whose id and one-member set
    are not in the deck, so it is compared under ``nonstructural_masses``, keyed by the
    structural set it spreads over, by value and units. A point mass or rotary inertia IS an
    element (``*Element, type=MASS``) and is compared as one, values included.
"""

from __future__ import annotations

import enum
import math
from typing import Any

import numpy as np

__all__ = ["canonical", "diff_paths"]


# ── leaves ──────────────────────────────────────────────────────────────────────────────────


def _num(x: float) -> float:
    """R5: 12 significant digits, and -0.0 as 0.0."""
    x = float(x)
    if x == 0.0 or not math.isfinite(x):
        return 0.0 if x == 0.0 else x
    return float(f"{x:.12g}")


def _name(obj) -> str | None:
    """R1/R7: a referenced object by its name, lower case."""
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj.lower()
    name = getattr(obj, "name", None)
    return name.lower() if isinstance(name, str) else repr(obj)


def _v(x: Any) -> Any:
    """Any leaf value, made comparable."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, enum.Enum):
        return str(x.value)
    if isinstance(x, (int, np.integer)) and not isinstance(x, bool):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return _num(x)
    if isinstance(x, np.ndarray):
        return [_v(i) for i in x.tolist()]
    if isinstance(x, dict):
        return {str(k): _v(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple)):
        return [_v(i) for i in x]
    if isinstance(x, (set, frozenset)):
        return sorted((_v(i) for i in x), key=repr)
    # adapy points/directions are ndarray subclasses (handled above); anything else by name/id
    for attr in ("name", "id"):
        val = getattr(x, attr, None)
        if val is not None:
            return {"ref": type(x).__name__, attr: _v(val).lower() if isinstance(val, str) else _v(val)}
    return repr(x)


def _ids(members) -> list:
    return sorted(_v(getattr(m, "id", m)) for m in members)


def _meta(obj) -> dict:
    """Metadata the writer consumes (verbatim text it writes instead of the construct)."""
    md = getattr(obj, "metadata", None) or {}
    return {k: _v(md[k]) for k in sorted(md) if k in _WRITTEN_METADATA}


#: Metadata keys the Abaqus writer reads to change what it writes. Anything else in ``metadata``
#: is adapy-internal and has no Abaqus form.
_WRITTEN_METADATA = {
    "aba_inp",
    "aba_bulk",
    "no_compression",
    "rotary_inertia",
}  # not section_type / line1 / temperature: R9


# ── constructs ──────────────────────────────────────────────────────────────────────────────


def _set(s) -> dict:
    return {"type": _v(s.type), "members": _ids(s.members)}


def _element(el, defaults) -> dict:
    from ada.fem.formats.abaqus.mapping import element_types
    from ada.fem.shapes.definitions import ConnectorTypes, MassTypes, SpringTypes

    etype = _v(el.type)
    if not isinstance(el.type, (ConnectorTypes, MassTypes, SpringTypes)):
        try:
            etype = element_types().write_type(el, defaults)  # R2
        except Exception:
            etype = f"{_v(el.type)} (no Abaqus type)"
    out = {
        "type": etype,
        "nodes": [_v(n.id) for n in el.nodes],
        "elset": _name(el.elset),
        "section": _name(getattr(getattr(el, "fem_sec", None), "elset", None)),  # R8
    }
    if isinstance(el.type, MassTypes):  # R15: a mass element's values are the element
        out["mass"] = _v(el.mass)
        out["point_mass_type"] = _v(getattr(el, "point_mass_type", None))
        out["units"] = _v(getattr(el, "units", None))
    return out


def _section(sec) -> dict:
    prof = None
    if sec.section is not None:
        s = sec.section
        prof = {"type": _v(getattr(s, "type", None))}
        for dim in ("h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt"):
            val = getattr(s, dim, None)
            if val is not None:
                prof[dim] = _v(val)
    return {
        "name": _name(sec),  # R8: compared, not the key
        "type": _v(sec.type),
        "material": _name(sec.material),
        "thickness": _v(sec.thickness),
        "int_points": _v(sec.int_points),
        "local_y": _v(sec.local_y),
        "local_z": _v(sec.local_z),
        "profile": prof,
        "is_rigid": _v(getattr(sec, "_is_rigid", False)),
        "metadata": _meta(sec),
    }


def _material(mat) -> dict:
    m = mat.model
    pl = m.plasticity_model
    return {
        "E": _v(m.E),
        "v": _v(m.v),
        "rho": _v(m.rho),
        "expansion": _v(m.zeta),
        "damping": [_v(m.rayleigh_damping.alpha), _v(m.rayleigh_damping.beta)],
        "plastic": None if pl is None or pl.eps_p is None else [_v(pl.sig_p), _v(pl.eps_p)],
        "metadata": {k: v for k, v in _meta(mat).items() if k != "aba_inp"},  # R11
    }


def _mass(mass) -> dict:
    return {
        "type": _v(mass.type),
        "point_mass_type": _v(getattr(mass, "point_mass_type", None)),
        "mass": _v(mass.mass),
        "set": _name(getattr(mass, "fem_set", None)),
        "members": _ids(getattr(mass, "members", None) or []),
    }


def _spring(sp) -> dict:
    return {"type": _v(sp.type), "stiff": _v(sp.stiff), "set": _name(sp.fem_set), "nodes": _ids(sp.nodes)}


def _node_ref(node) -> list:
    """R11: a node as ``[part, id]`` -- which FEM holds it is part of what it is."""
    fem = getattr(node, "parent", None)
    part = getattr(fem, "parent", None)
    owner = _part_key(part) if part is not None and hasattr(part, "fem") else None
    return [owner, _v(node.id)]


def _connector(con) -> dict:
    return {
        "type": _v(con.con_type),
        "n1": _node_ref(con.n1),
        "n2": _node_ref(con.n2),
        "section": _name(con.con_sec),
        "csys": _csys(con.csys),
        "elset": _name(getattr(con, "elset", None)),
    }


def _con_section(cs) -> dict:
    return {
        "elastic": _v(cs.elastic_comp),
        "damping": _v(cs.damping_comp),
        "plastic": _v(cs.plastic_comp),
        "rigid_dofs": _v(cs.rigid_dofs),
    }


def _csys(cs) -> dict | None:
    if cs is None:
        return None
    coords = cs.coords
    if coords is None and not cs.nodes:
        coords = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]  # R13: the global orientation
    return {
        "definition": _v(cs.definition),
        "system": _v(cs.system),
        "coords": _v(coords),
        "nodes": _ids(cs.nodes or []),
    }


def _operand_members(op) -> list | None:
    """R12/R14: an operand's nodes or elements, through a surface to the set it stands on."""
    if op is None:
        return None
    if hasattr(op, "members"):
        return _ids(op.members)
    fs = getattr(op, "fem_set", None)
    sets = fs if isinstance(fs, list) else [fs]
    if all(s is not None and hasattr(s, "members") for s in sets):
        return sorted(i for s in sets for i in _ids(s.members))
    return None


def _operand_name(op) -> str | None:
    """R14: a node surface named as the one set it stands on is that set."""
    fs = getattr(op, "fem_set", None)
    if fs is not None and not isinstance(fs, list) and str(_v(getattr(op, "type", ""))).upper() == "NODE":
        if _name(fs) == _name(op):
            return _name(fs)
    return _name(op)


def _expanded_dofs(dofs):
    """R12."""
    from ada.fem.constraints import expand_dofs

    if dofs is None:
        return None
    try:
        return list(expand_dofs(dofs))
    except (TypeError, ValueError):
        return _v(dofs)


def _constraint(c) -> dict:
    is_mpc = str(_v(c.type)).lower() == "mpc"
    return {
        "type": _v(c.type),
        "m_set": None if is_mpc else _operand_name(c.m_set),
        "m_members": _operand_members(c.m_set),
        "s_set": None if is_mpc else _operand_name(c.s_set),
        "s_members": _operand_members(c.s_set),
        "dofs": _expanded_dofs(c.dofs),
        "pos_tol": _v(c.pos_tol),
        "influence_distance": _v(c.influence_distance),
        "mpc_type": _v(c.mpc_type),
        "csys": _csys(c.csys),
    }


def _face_label(fs, index) -> str:
    """R10: the label the writer emits for ``index`` on the elements of ``fs``."""
    from ada.fem.elements import find_element_type_from_list
    from ada.fem.shapes import ElemType

    if isinstance(index, str):
        return index.upper()
    try:
        el_type = find_element_type_from_list(fs.members)
    except Exception:
        return str(index)
    if el_type == ElemType.SOLID:
        return f"S{int(index) + 1}"
    if el_type == ElemType.SHELL:
        return "SNEG" if index == -1 else "SPOS"
    return str(index)


def _surface(s) -> dict:
    """R10."""
    out = {"type": _v(s.type)}
    if s.id_refs is not None:
        out["members"] = [[_name(str(m[0]).split(".")[-1]), str(m[1]).upper()] for m in s.id_refs]
        return out
    sets = s.fem_set if isinstance(s.fem_set, list) else [s.fem_set]
    if str(_v(s.type)).upper() == "NODE":
        out["members"] = [[_name(fs), None] for fs in sets]
        out["weight"] = _v(s.weight_factor)
        return out
    faces = s.el_face_index if isinstance(s.el_face_index, list) else [s.el_face_index]
    out["members"] = [[_name(fs), _face_label(fs, f)] for fs, f in zip(sets, faces)]
    return out


def _intprop(p) -> dict:
    return {"friction": _v(p.friction), "pressure_overclosure": _v(p.pressure_overclosure), "tabular": _v(p.tabular)}


def _interaction(i) -> dict:
    return {
        "type": _v(i.type),
        "surface_type": _v(i.surface_type),
        "surf1": _name(i.surf1),
        "surf2": _name(i.surf2),
        "property": _name(i.interaction_property),
        "constraint": _v(i.constraint),
        "metadata": _meta(i),
    }


def _amplitude(a) -> dict:
    return {"x": _v(a.x), "y": _v(a.y), "smooth": _v(a.smooth)}


def _predefined(p) -> dict:
    return {"type": _v(p.type), "set": _name(p.fem_set), "dofs": _v(p.dofs), "magnitude": _v(p.magnitude)}


def _bc_dofs(bc) -> Any:
    """R4: named restraint, or {dof: magnitude} over the constrained DOFs."""
    if isinstance(bc.dofs, str):
        return bc.dofs.lower()
    dofs = list(bc.dofs)
    mags = list(bc.magnitudes or [])
    out = {}
    for i, d in enumerate(dofs):
        if d is None:
            continue
        if isinstance(d, str):
            out[d.lower()] = None
            continue
        out[str(int(d))] = _v(mags[i]) if i < len(mags) else None
    return out


def _bc_type(t) -> str:
    from ada.fem.formats.abaqus.mapping import bc_types

    table = bc_types()
    try:
        return str(table.from_abaqus(table.to_abaqus(t)))  # R4: DISPL_ROT is DISPL
    except Exception:
        return str(t).lower()


def _bc(bc) -> dict:
    owner = bc.fem_set.parent
    owner_name = getattr(owner, "instance_name", None) or getattr(owner, "name", None) if owner is not None else None
    return {
        "key": f"{(owner_name or '').lower()}.{_name(bc.fem_set)}",  # R3
        "type": _bc_type(bc.type),
        "dofs": _bc_dofs(bc),
        "amplitude": _name(bc.amplitude),
    }


def _load(ld) -> dict:
    out = {"type": _v(ld.type), "magnitude": _v(ld.magnitude), "dof": _v(ld.dof)}
    for attr in ("fem_set", "amplitude", "csys", "surface"):
        val = getattr(ld, attr, None)
        out[attr] = _csys(val) if attr == "csys" else _name(val)
    for attr in ("follower_force", "acc_vector", "distribution", "forces"):
        try:
            out[attr] = _v(getattr(ld, attr))
        except AttributeError:
            continue  # not an attribute of this load type
        except ValueError:
            continue  # defined only for another load type (acc_vector raises for gravity)
    return out


def _field_output(fo) -> dict:
    return {
        "nodal": _v(fo.nodal),
        "element": _v(fo.element),
        "contact": _v(fo.contact),
        "int_type": _v(fo.int_type),
        "int_value": _v(fo.int_value),
    }


def _hist_output(ho) -> dict:
    return {
        "type": _v(ho.type),
        "set": _name(ho.fem_set) if not isinstance(ho.fem_set, (list, tuple)) else [_name(x) for x in ho.fem_set],
        "variables": _v(ho.variables),
        "int_type": _v(ho.int_type),
        "int_value": _v(ho.int_value),
    }


_STEP_ATTRS = (
    "nl_geom",
    "total_time",
    "init_incr",
    "min_incr",
    "max_incr",
    "total_incr",
    "dyn_type",
    "num_eigen_modes",
    "friction_damping",
    "fmin",
    "fmax",
    "alpha",
    "beta",
)


def _step(st) -> dict:
    out = {"type": type(st).__name__}
    for attr in _STEP_ATTRS:
        if hasattr(st, attr):
            out[attr] = _v(getattr(st, attr))
    if hasattr(st, "unit_load"):
        out["unit_load"] = _name(st.unit_load)
    out["loads"] = {_name(ld): _load(ld) for ld in st.loads}
    out["bcs"] = _keyed([_bc(b) for b in (st.bcs.values() if isinstance(st.bcs, dict) else st.bcs)])
    out["field_outputs"] = {_name(f): _field_output(f) for f in st.field_outputs}
    out["hist_outputs"] = {_name(h): _hist_output(h) for h in st.hist_outputs}
    out["interactions"] = sorted(
        _name(i) for i in (st.interactions.values() if isinstance(st.interactions, dict) else st.interactions)
    )
    abq = getattr(getattr(st, "options", None), "ABAQUS", None)
    if abq is not None:
        out["abaqus_options"] = {k: _v(v) for k, v in sorted(vars(abq).items()) if not k.startswith("_")}
    out["metadata"] = _meta(st)
    return out


# ── the whole model ─────────────────────────────────────────────────────────────────────────


def _part_key(part) -> str:
    """R6."""
    inst = getattr(part.fem, "instance_name", None)
    return (inst or part.name).lower()


def _is_connector_set(s) -> bool:
    """R11: a set holding connector elements only is the connector's own attribute."""
    from ada.fem import Connector

    members = s.members
    return bool(members) and all(isinstance(m, Connector) for m in members)


def _is_nonstructural_mass_set(s) -> bool:
    """R15: the one-member set holding a nonstructural mass's pseudo-element."""
    from ada.fem.shapes.definitions import MassTypes

    members = s.members
    return bool(members) and all(getattr(m, "type", None) == MassTypes.NONSTRUCTURAL for m in members)


def _coupling_surface(s, fem) -> bool:
    """R14: a node surface named as its one set, used as a coupling operand of this FEM."""
    return any(c.s_set is s for c in fem.constraints.values()) and _operand_name(s) == _name(
        getattr(s, "fem_set", None)
    )


def _fem(fem, owned_csys: set) -> dict:
    from ada.fem.shapes.definitions import ConnectorTypes, MassTypes

    defaults = fem.options.ABAQUS.default_elements
    elements = {}
    nonstructural = {}
    for el in fem.elements:
        if isinstance(el.type, ConnectorTypes):  # R11, compared assembly-wide
            continue
        if el.type == MassTypes.NONSTRUCTURAL:  # R15
            nonstructural[_name(getattr(el, "fem_set", None))] = {
                "mass": _v(el.mass),
                "units": _v(getattr(el, "units", None)),
            }
            continue
        elements[str(_v(el.id))] = _element(el, defaults)
    return {
        "nodes": {str(_v(n.id)): _v(n.p) for n in fem.nodes},
        "elements": elements,
        "nonstructural_masses": nonstructural,
        "sets": {
            f"{_v(s.type)}:{_name(s)}": _set(s)
            for s in fem.sets
            if not (_is_connector_set(s) or _is_nonstructural_mass_set(s))
        },
        "sections": {_name(s.elset): _section(s) for s in fem.sections},  # R8
        "masses": {_name(k): _mass(m) for k, m in fem.masses.items()},
        "springs": {_name(k): _spring(s) for k, s in fem.springs.items()},
        "constraints": {_name(k): _constraint(c) for k, c in fem.constraints.items()},
        "surfaces": {_name(k): _surface(s) for k, s in fem.surfaces.items() if not _coupling_surface(s, fem)},
        "interaction_properties": {_name(k): _intprop(p) for k, p in fem.intprops.items()},
        "interactions": {_name(k): _interaction(i) for k, i in fem.interactions.items()},
        "amplitudes": {_name(k): _amplitude(a) for k, a in fem.amplitudes.items()},
        "predefined_fields": {_name(k): _predefined(p) for k, p in fem.predefined_fields.items()},
        "lcsys": {_name(k): _csys(c) for k, c in fem.lcsys.items() if id(c) not in owned_csys},  # R13
        "ref_points": sorted(_v(n.id) for n in fem.ref_points),
        "steps": {_name(st): _step(st) for st in fem.steps},
    }


def canonical(assembly) -> dict:
    """Everything the Abaqus format can carry about ``assembly``, comparable with ``==``."""
    from ada.fem.shapes.definitions import ConnectorTypes

    all_parts = assembly.get_all_parts_in_assembly(include_self=True)
    owned_csys: set = set()  # R13
    connectors: dict = {}  # R11
    connector_sections: dict = {}
    for p in all_parts:
        fem = p.fem
        owned_csys |= {id(c.csys) for c in fem.constraints.values() if c.csys is not None}
        owned_csys |= {id(s.csys) for s in fem.sections if getattr(s, "csys", None) is not None}
        for el in fem.elements:
            if isinstance(el.type, ConnectorTypes):
                if el.csys is not None:
                    owned_csys.add(id(el.csys))
                connectors[str(_v(el.id))] = _connector(el)
        connector_sections.update({_name(k): _con_section(c) for k, c in fem.connector_sections.items()})

    parts = {}
    bcs = []
    for p in all_parts:
        bcs += [_bc(b) for b in p.fem.bcs]  # R3
        if p is not assembly and p.fem.is_empty():
            continue
        # The assembly is always present, so what it holds (steps, assembly-level sets and
        # constraints, amplitudes...) is compared field by field, never as one opaque key.
        parts[_part_key(p) if p is not assembly else "<assembly>"] = _fem(p.fem, owned_csys)
    materials = {}
    for p in assembly.get_all_parts_in_assembly(include_self=True):
        for m in p.materials:
            materials[_name(m)] = _material(m)
    return {
        "parts": parts,
        "materials": materials,
        "bcs": _keyed(bcs),
        "connectors": connectors,
        "connector_sections": connector_sections,
    }


def _keyed(bcs: list[dict]) -> dict:
    """BCs as ``{key: bc}`` (R3), so a diff names the BC; repeats on one set get ``#n``."""
    out: dict = {}
    for bc in sorted(bcs, key=repr):
        key = bc["key"]
        n = 1
        while key in out:
            n += 1
            key = f"{bc['key']}#{n}"
        out[key] = {k: v for k, v in bc.items() if k != "key"}
    return out


# ── diff ────────────────────────────────────────────────────────────────────────────────────


def diff_paths(a: Any, b: Any, path: str = "", limit: int = 200) -> list[str]:
    """Paths where ``a`` and ``b`` differ, with both values, for a readable failure."""
    out: list[str] = []

    def walk(x, y, p):
        if len(out) >= limit:
            return
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y), key=str):
                if k not in y:
                    out.append(f"{p}/{k}: only in original")
                elif k not in x:
                    out.append(f"{p}/{k}: only in read-back")
                else:
                    walk(x[k], y[k], f"{p}/{k}")
        elif isinstance(x, list) and isinstance(y, list) and len(x) == len(y) and x != y:
            for i, (xi, yi) in enumerate(zip(x, y)):
                walk(xi, yi, f"{p}[{i}]")
        elif x != y:
            sx, sy = str(x)[:120], str(y)[:120]
            if sx == sy:  # same text, different type: show it
                sx, sy = repr(x)[:120], repr(y)[:120]
            out.append(f"{p}: {sx} != {sy}")

    walk(a, b, path)
    return out
