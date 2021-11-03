from typing import TYPE_CHECKING

from ada.fem import Mass

if TYPE_CHECKING:
    from ada import FEM


def masses_str(fem: "FEM"):
    return "\n".join([mass_str(m) for m in fem.masses.values()]) if len(fem.masses) > 0 else "** No Masses"


def mass_str(mass: Mass) -> str:
    if mass.point_mass_type in (Mass.PTYPES.ISOTROPIC, None):
        type_str = ""
    else:
        type_str = f", type={mass.point_mass_type}"

    mstr = ",".join([str(x) for x in mass.mass]) if type(mass.mass) is list else str(mass.mass)

    if mass.type == Mass.TYPES.MASS:
        return f"""*Mass, elset={mass.fem_set.name}{type_str}\n {mstr}"""
    elif mass.type == Mass.TYPES.NONSTRU:
        return f"""*Nonstructural Mass, elset={mass.fem_set.name}, units={mass.units}\n  {mstr}"""
    elif mass.type == Mass.TYPES.ROT_INERTIA:
        return f"""*Rotary Inertia, elset={mass.fem_set.name}\n  {mstr}"""
    else:
        raise ValueError(f'Mass type "{mass.type}" is not supported by Abaqus')
