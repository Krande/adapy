from ada import FEM
from ada.fem.shapes.definitions import MassTypes

from .write_utils import write_ff


def _bnmass_components(mass, n_members: int) -> list[float]:
    """Six BNMASS components [m1..m6] for a Mass element. Per the Sesam Input Interface File
    spec, BNMASS gives mass per DOF: 1-3 translational, 4-6 rotational (rotary inertia about
    x/y/z). A scalar value applies to all three of its DOFs; a list maps to the first three.

    - MASS          -> translational (DOF 1-3); isotropic scalar or anisotropic 3-vector.
    - ROTARYI       -> rotary inertia (DOF 4-6); only the diagonal I11/I22/I33 is kept,
                       BNMASS has no off-diagonal terms.
    - NONSTRUCTURAL -> a region mass lumped equally onto its member nodes (total / N) as
                       translational mass — BNMASS is nodal, there is no distributed form.
    """
    raw = mass._mass
    vals = [float(x) for x in (raw if isinstance(raw, (list, tuple)) else [raw])]
    tri = (vals * 3)[:3] if len(vals) == 1 else vals[:3]
    comps = [0.0] * 6
    mt = mass.type
    if mt == MassTypes.MASS:
        comps[0:3] = tri
    elif mt == MassTypes.ROTARYI:
        comps[3:6] = tri
    elif mt == MassTypes.NONSTRUCTURAL:
        per = (vals[0] / n_members) if n_members else vals[0]
        comps[0:3] = [per, per, per]
    else:
        raise NotImplementedError(f"Mass type {mt} is not yet supported")
    return comps


def mass_str(fem: FEM) -> str:
    out_str = ""

    for mass in fem.elements.masses:
        members = list(mass.members)
        comps = _bnmass_components(mass, max(1, len(members)))
        for m in members:
            data = (tuple([m.id, 6] + comps[:2]), tuple(comps[2:]))
            out_str += write_ff("BNMASS", data)
    return out_str


def write_node_with_mass_point():
    raise NotImplementedError()
