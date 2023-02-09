from ada import FEM

from .write_utils import write_ff


def mass_str(fem: FEM) -> str:
    out_str = ""

    for mass in fem.elements.masses:
        for m in mass.members:
            if mass.type == mass.TYPES.MASS:
                if mass.point_mass_type == mass.PTYPES.ISOTROPIC:
                    masses = [mass.mass for _ in range(0, 3)] + [0, 0, 0]
                elif mass.point_mass_type == mass.PTYPES.ANISOTROPIC:
                    masses = mass.mass + [0, 0, 0]
                else:
                    raise NotImplementedError(f"Mass point mass type {mass.point_mass_type} is not yet supported")
            else:
                raise NotImplementedError(f"Mass type {mass.type} is not yet supported")
            data = (tuple([m.id, 6] + masses[:2]), tuple(masses[2:]))
            out_str += write_ff("BNMASS", data)
    return out_str


def write_node_with_mass_point():
    raise NotImplementedError()
