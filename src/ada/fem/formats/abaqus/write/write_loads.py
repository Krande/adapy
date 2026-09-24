from ada.fem import Load, LoadPressure
from ada.fem.exceptions.model_definition import UnsupportedLoadType

from ..grammar import format_number
from .helper_utils import get_instance_name, render_block


def load_str(load: Load) -> str:
    load_map = {
        Load.TYPES.GRAVITY: acceleration_field_str,
        Load.TYPES.ACC: acceleration_field_str,
        Load.TYPES.FORCE: force_load_str,
        Load.TYPES.PRESSURE: pressure_load_str,
    }
    load_str_func = load_map.get(load.type, None)

    if load_str_func is None:
        raise ValueError("Unsupported load type", load.type)

    return load_str_func(load)


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
    data = [f"{instance_name}, P, {format_number(load.magnitude)}"]
    return render_block("Dsload", (), data, [f"Name: {load.name}   Type: Pressure"])
