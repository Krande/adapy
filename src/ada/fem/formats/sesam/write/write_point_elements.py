"""Point masses as the Sesam elements they are.

A point mass on one node is a 1-noded mass element (GELMNT1 ELTYP 11) whose GELREF1 MATNO
refers to an MGMASS mass matrix (manual 7.4.7), named by a TDELEM record (manual 4.2.1).

Masses used to go out as BNMASS only, which is nodal: the element a mass is in adapy (its
id, and the element set naming it) was not in the file, so a set naming it could not be read
back. BNMASS is still what a mass spread over several nodes -- a nonstructural mass -- is
written as (``write_masses.mass_str``): it has no one node to be an element on.

The MGMASS matrices take material numbers after the model's own materials and the springs'.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.fem.shapes.definitions import MassTypes

from .write_utils import write_ff

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Elem, Mass

    from .writer import NodeDofs

MASS_ELTYP = 11


def is_mass_element(mass: Mass) -> bool:
    """A point mass or rotary inertia on one node, with an id: written as a mass element."""
    return mass.type in (MassTypes.MASS, MassTypes.ROTARYI) and len(_nodes(mass)) == 1 and mass.id is not None


def point_elements(fem: FEM) -> list[Elem]:
    """The masses written as elements, in element id order."""
    return sorted((m for m in fem.elements.masses if is_mass_element(m)), key=lambda el: el.id)


def _nodes(el) -> list:
    from ada.fem.elements import Mass

    if isinstance(el, Mass):
        return list(el.members or el.nodes or ())
    return list(el.nodes or ())


def eltyp(el) -> int:
    return MASS_ELTYP


def node_ids(el) -> list[int]:
    return [n.id for n in _nodes(el)]


def matnos(fem: FEM, first: int) -> dict[int, int]:
    """``{element id: MATNO}`` of the matrices, numbered from ``first``."""
    return {el.id: first + i for i, el in enumerate(point_elements(fem))}


def first_free_matno(fem: FEM) -> int:
    """The first MATNO past the materials the writer writes (``assembly.get_all_materials``)
    and the springs' stiffness records, which take the numbers right after the materials
    (``write_springs.spring_matnos``)."""
    part = fem.parent
    assembly = part.get_assembly() if part is not None else None
    materials = assembly.get_all_materials(True) if assembly is not None else list(getattr(part, "materials", []))
    return max((int(m.id) for m in materials), default=0) + 1 + len(list(fem.elements.springs))


def gelref_str(el, matno: int) -> str:
    return write_ff("GELREF1", [(el.id, matno, 0, 0), (0, 0, 0, 0), (0, 0, 0, 0)])


def point_elements_str(fem: FEM, ndofs: NodeDofs | None = None) -> str:
    """The TDELEM names and the MGMASS matrices of :func:`point_elements`."""
    from .writer import ALL_SIX_DOF

    if ndofs is None:
        ndofs = ALL_SIX_DOF
    numbers = matnos(fem, first_free_matno(fem))
    out = ""
    for el in point_elements(fem):
        if el.name:
            out += write_ff("TDELEM", [(4, el.id, 100 + len(el.name), 0), (el.name,)])
        out += _matrix_card("MGMASS", numbers[el.id], mass_matrix(el), ndofs.ndof(node_ids(el)[0]), el)
    return out


def mass_matrix(mass: Mass) -> np.ndarray:
    """The 6x6 mass matrix of a point mass (translational) or rotary inertia (rotational).

    A rotary inertia is ``I11, I22, I33, I12, I13, I23`` -- Abaqus's *Rotary Inertia order --
    of which a shorter list gives the leading terms."""
    raw = mass._mass
    vals = [float(x) for x in (raw if isinstance(raw, (list, tuple, np.ndarray)) else [raw])]
    m = np.zeros((6, 6))
    if mass.type == MassTypes.MASS:
        m[0, 0], m[1, 1], m[2, 2] = (vals * 3)[:3] if len(vals) == 1 else vals[:3]
        return m
    inertia = (vals + [0.0] * 6)[:6]
    i11, i22, i33, i12, i13, i23 = inertia
    m[3:, 3:] = [[i11, i12, i13], [i12, i22, i23], [i13, i23, i33]]
    return m


def _lower_columns(k: np.ndarray) -> list[float]:
    """The on- and below-diagonal terms column by column: K(1,1), K(2,1), ..., K(n,1), K(2,2), ..."""
    n = k.shape[0]
    return [float(k[i, j]) for j in range(n) for i in range(j, n)]


def _rows(head: tuple, values: list[float]) -> list[tuple]:
    """``head`` then ``values``, four fields to a line."""
    flat = list(head) + values
    return [tuple(flat[i : i + 4]) for i in range(0, len(flat), 4)]


def _require_dofs(k: np.ndarray, keep: list[int], el, ndof_text: str) -> None:
    """Raise when ``k`` has a term on a dof outside ``keep`` -- one the node does not have."""
    drop = [i for i in range(k.shape[0]) if i not in keep]
    if drop and (np.any(k[drop, :]) or np.any(k[:, drop])):
        raise ValueError(
            f'sesam writer: "{el.name}" (element {el.id}) has terms on dofs its node(s) do not have '
            f"({ndof_text}); a node attached only to solid elements has no rotational dofs."
        )


def _matrix_card(card: str, matno: int, k: np.ndarray, ndof: int, el) -> str:
    """MGMASS: NDOF must equal the node's GNODE NDOF (manual 7.4.7)."""
    _require_dofs(k, list(range(ndof)), el, f"NDOF={ndof}")
    return write_ff(card, _rows((matno, ndof), _lower_columns(k[:ndof, :ndof])))
