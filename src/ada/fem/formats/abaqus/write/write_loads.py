from __future__ import annotations

from ada.fem import Load, LoadLine, LoadPressure
from ada.fem.exceptions.model_definition import UnsupportedLoadType

from ..grammar import format_number
from .helper_utils import get_instance_name, render_block


def load_str(load: Load) -> str:
    load_map = {
        Load.TYPES.GRAVITY: acceleration_field_str,
        Load.TYPES.ACC: acceleration_field_str,
        Load.TYPES.FORCE: force_load_str,
        Load.TYPES.PRESSURE: pressure_load_str,
        Load.TYPES.LINE: line_load_str,
        Load.TYPES.ACC_ROT: rotation_load_str,
        Load.TYPES.TRACTION: traction_load_str,
        Load.TYPES.TEMPERATURE: temperature_load_str,
    }
    load_str_func = load_map.get(load.type, None)

    if load_str_func is None:
        raise ValueError("Unsupported load type", load.type)

    return load_str_func(load)


#: The conversion report's stage for this writer.
STAGE = "abaqus writer"


def _node_lines(fem, node_ids, value: str) -> list[str]:
    """``<instance>.<node>, value`` per node (the bare node at assembly level), the form *Temperature and *Initial
    Conditions take node by node."""
    from ada import Assembly

    owner = fem.parent
    prefix = "" if isinstance(owner, Assembly) else f"{fem.instance_name}."
    return [f" {prefix}{nid}, {value}" for nid in node_ids]


def _model_fems(load: Load) -> list:
    step = load.parent
    assembly = step.parent.parent if step is not None and step.parent is not None else None
    if assembly is None:
        raise UnsupportedLoadType(f"{STAGE}: temperature load {load.name!r} is in no assembly's step")
    return [p.fem for p in assembly.get_all_parts_in_assembly(include_self=True) if p.fem is not None]


def temperature_load_str(load: Load) -> str:
    """A uniform temperature (:class:`~ada.fem.LoadTemperature`) as ``*Temperature`` on the nodes of the elements it
    acts in (every node of the model when it has no set); the reference is the nodes' initial temperature
    (:func:`temperature_initial_conditions_str`). Abaqus's thermal strain is ``alpha (T - T0)`` with ``*Expansion``."""
    value = format_number(load.temperature)
    if load.fem_set is not None:
        fems = [load.fem_set.parent]
    else:
        fems = _model_fems(load)
    lines = []
    for fem in fems:
        nodes = sorted({int(n.id) for el in load.elements(fem) for n in el.nodes})
        lines += _node_lines(fem, nodes, value)
    return render_block("Temperature", [], lines, [f"Name: {load.name}   Type: Temperature"])


def temperature_initial_conditions_str(assembly, steps) -> str:
    """``*Initial Conditions, type=TEMPERATURE`` at the reference of the deck's temperature loads on every node of the
    model (model data), or "" when it has none. A fluid cavity's reference node keeps the temperature its cavity
    gives it (write_cavities)."""
    from ada.fem.formats.carried_loads import temperature_reference

    from .write_cavities import all_cavities

    ref = temperature_reference(steps, STAGE)
    if ref is None:
        return ""
    cavity_nodes = {id(c.ref_node) for c in all_cavities(assembly)}
    lines = []
    for p in assembly.get_all_parts_in_assembly(include_self=True):
        if p.fem is None:
            continue
        ids = sorted(int(n.id) for n in p.fem.nodes if id(n) not in cavity_nodes)
        lines += _node_lines(p.fem, ids, format_number(ref))
    if not lines:
        return ""
    return render_block(
        "Initial Conditions", [("type", "TEMPERATURE")], lines, ["The reference of the temperature loads"]
    )


def acceleration_field_str(load: Load) -> str:
    # Named for the load, and typed: gravity and an acceleration field are both *Dload GRAV to
    # Abaqus, so the comment is what tells them apart on the way back. This wrote every one of
    # them as "** Name: gravity   Type: Gravity".
    dof = [0, 0, 1] if load.dof is None else [dof if dof is not None else 0 for dof in load.dof]
    dof_str = ", ".join([format_number(x) for x in dof[:3]])
    kind = "Gravity" if load.type == Load.TYPES.GRAVITY else "Acceleration"
    return render_block(
        "Dload", (), [f", GRAV, {format_number(load.magnitude)}, {dof_str}"], [f"Name: {load.name}   Type: {kind}"]
    )


def traction_load_str(load: Load) -> str:
    """A surface traction (:class:`~ada.fem.LoadSurfaceTraction`): ``*Dsload TRVEC`` on its surface, the traction's
    magnitude and its global direction (a general surface traction, Abaqus Analysis User's Guide 34.4.3)."""
    import numpy as np

    t = np.asarray(load.traction, dtype=float)
    mag = float(np.linalg.norm(t))
    if mag == 0.0:
        return ""
    d = ", ".join(format_number(float(c)) for c in t / mag)
    target = get_instance_name(load.surface, True)
    return render_block(
        "Dsload", (), [f"{target}, TRVEC, {format_number(mag)}, {d}"], [f"Name: {load.name}   Type: Traction"]
    )


def rotation_load_str(load: Load) -> str:
    """A rotating frame (:class:`~ada.fem.LoadRotation`): ``*Dload CENTRIF`` with ``w^2`` and ``*Dload ROTA`` with
    the angular acceleration, each with a point on the axis and its direction; on the load's element set, or on the
    whole model (a blank set, as ``GRAV`` is written)."""
    from ada.fem.loads import LoadRotation

    if not isinstance(load, LoadRotation):
        raise UnsupportedLoadType(
            f"abaqus writer: load {load.name!r} of type acc_rot is not a LoadRotation; no axis given"
        )
    target = "" if load.fem_set is None else get_instance_name(load.fem_set, True)
    where = ", ".join(format_number(float(c)) for c in (*load.origin, *load.axis))
    lines = []
    if load.angular_velocity != 0.0:
        lines.append(
            render_block(
                "Dload",
                (),
                [f"{target}, CENTRIF, {format_number(load.angular_velocity**2)}, {where}"],
                [f"Name: {load.name}   Type: Rotational body force"],
            )
        )
    if load.angular_acceleration != 0.0:
        lines.append(
            render_block(
                "Dload",
                (),
                [f"{target}, ROTA, {format_number(load.angular_acceleration)}, {where}"],
                [f"Name: {load.name}   Type: Rotary acceleration"],
            )
        )
    return "\n".join(lines)


def force_load_str(load: Load) -> str:
    """``*Cload`` for a point load: forces as ``<name>_F``, moments as ``<name>_M``.

    Every component from ``Load.forces`` -- the DOF entry times the magnitude, moments included,
    as the Sesam and Code_Aster writers read it. This scaled the forces but wrote the moments'
    raw entries, so a moment of 0.5 x 100 went out as 0.5.
    """
    instance_name = get_instance_name(load.fem_set, True)
    forces = load.forces
    params = [] if load.follower_force is False else [("follower", None)]
    # the Amplitude by name (a bare string, as older models hold it, is already one)
    amplitude = getattr(load.amplitude, "name", load.amplitude)
    params += [("amplitude", amplitude)] if amplitude is not None else []

    def block(name: str, kind: str, dofs: range) -> str:
        lines = [f" {instance_name}, {i + 1}, {format_number(forces[i])}" for i in dofs if forces[i] != 0.0]
        if not lines:
            return ""
        return render_block("Cload", params, lines, [f"Name: {name}   Type: {kind}"])

    parts = [block(load.name + "_F", "Concentrated force", range(3)), block(load.name + "_M", "Moment", range(3, 6))]
    return "\n".join(p for p in parts if p)


def pressure_load_str(load: LoadPressure) -> str:
    instance_name = get_instance_name(load.surface, True)
    if load.distribution == LoadPressure.P_DIST_TYPES.TOTAL_FORCE:
        raise UnsupportedLoadType("Total Force calculation is not yet supported for Abaqus")
    if getattr(load, "gradient", None) is not None:
        # *Dsload P is one magnitude; a varying one needs a user subroutine (PNU / DLOAD), not written here.
        raise UnsupportedLoadType(
            f"pressure load {load.name!r} varies over the model; Abaqus *Dsload P takes one value"
        )
    data = [f"{instance_name}, P, {format_number(load.magnitude)}"]
    return render_block("Dsload", (), data, [f"Name: {load.name}   Type: Pressure"])


#: ``*Dload`` labels for a beam element's distributed load in the global x, y, z directions, force per length.
_BEAM_LOAD_LABELS = ("PX", "PY", "PZ")


def _element_label(el) -> str:
    """An element as a ``*Dload`` data line names it at assembly level: ``<instance>.<id>``."""
    from ada import Assembly

    fem = el.parent
    return str(el.id) if isinstance(fem.parent, Assembly) else f"{fem.instance_name}.{el.id}"


def line_load_str(load: LoadLine) -> str:
    """A distributed line load: ``*Dload PX/PY/PZ`` per beam element it loads uniformly end to end, consistent nodal
    ``*Cload`` for everything else.

    Abaqus' beam distributed loads are constant over an element, and its shell edge loads need an edge surface,
    which this writer has no form for. So a linearly varying or partial stretch of a beam, and a load along a shell
    edge, go in as the consistent nodal forces of a linear element (:meth:`LoadLine.nodal_loads`) -- what Abaqus 2025
    makes of a ``*Dload`` on a B31 itself (measured, see there), and exact in the resultant. Reported as a note.
    Abaqus adds two loads on one element or node within a step (measured: two ``*Dload PZ, -1000`` on one 0.5 m
    element reacted 1000 N, two ``*Cload -1000`` on one node 2000 N), so nothing needs merging here.
    """
    from ada.fem.formats import conversion_report

    from .write_bc import STAGE

    dload, rest = [], []
    for seg in load.segments:
        if seg.uniform_over_beam_element():
            label = _element_label(seg.elem)
            dload += [f"{label}, {lab}, {format_number(q)}" for lab, q in zip(_BEAM_LOAD_LABELS, seg.q1) if q != 0.0]
        else:
            rest.append(seg)
    nodal = LoadLine.summed_nodal_loads(rest)

    blocks = []
    if dload:
        blocks.append(render_block("Dload", (), dload, [f"Name: {load.name}   Type: Line load"]))
    if nodal:
        lines = [
            f" {get_instance_name(node, True)}, {dof + 1}, {format_number(float(f[dof]))}"
            for node, f in nodal
            for dof in range(3)
            if f[dof] != 0.0
        ]
        blocks.append(render_block("Cload", (), lines, [f"Name: {load.name}   Type: Line load as nodal forces"]))
        conversion_report.current().note(
            STAGE,
            "LoadLine",
            load.name,
            "a varying or partial beam line load, or a shell edge load, is written as the consistent nodal forces "
            "of the (linear or quadratic) edges it acts on: Abaqus' beam loads are constant per element and this writer has no "
            "shell edge surface",
            n_nodes=len(nodal),
        )
    return "\n".join(blocks)
