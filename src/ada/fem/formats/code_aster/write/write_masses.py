"""Point masses for Code_Aster.

A point mass (:class:`ada.fem.Mass` of type ``MASS``) is a ``POI1`` cell in the MED file, in the element group of
its own element set (``FEM.add_mass`` makes ``<name>_set``). Code_Aster gives a cell stiffness or mass only through
a modelisation and element characteristics, so each such group is modelled ``DIS_T`` (a discrete element with the
three translations, U3.11.02) and given its mass in ``AFFE_CARA_ELEM``:

* isotropic: ``M_T_D_N``, the mass, the same in x, y and z;
* anisotropic (``(mx, my, mz)``): ``M_T_N``, the upper triangle of the 3x3 mass matrix in global axes, row by row
  (``mx, 0, 0, my, 0, mz``; U4.42.01 ``DISCRET``).

A zero ``K_T_D_N`` stiffness goes with it: the discrete element adds mass, not stiffness, to its node.

These cells used to be written to the MED file and left out of the command file, so the model had no point mass at
all: an acceleration field applied to it loaded the structure only, and an eigenvalue analysis ran without them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import Mass

if TYPE_CHECKING:
    from ada.api.spatial import Part

STAGE = "code_aster writer"


def point_masses(part: Part) -> list[Mass]:
    """The part's point masses Code_Aster takes (``MASS`` type, in an element set); each other mass is reported as
    left out."""
    from ada.fem.formats import conversion_report

    out = []
    for mass in part.fem.elements.masses:
        if mass.type != Mass.TYPES.MASS:
            conversion_report.current().omitted(
                STAGE, "Mass", mass.name, f'a "{mass.type}" mass has no Code_Aster form in this writer'
            )
            continue
        if mass.elset is None:
            conversion_report.current().omitted(
                STAGE, "Mass", mass.name, "a point mass in no element set (add it with FEM.add_mass)"
            )
            continue
        out.append(mass)
    return out


def mass_groups(masses: list[Mass]) -> list[str]:
    """The element groups of ``masses``, each once, in order."""
    groups = []
    for mass in masses:
        if mass.elset.name not in groups:
            groups.append(mass.elset.name)
    return groups


def mass_model_str(masses: list[Mass]) -> str:
    """The ``AFFE_MODELE`` entry modelling the masses' groups as ``DIS_T``; empty for none."""
    if not masses:
        return ""
    groups = ", ".join(f"'{g}'" for g in mass_groups(masses))
    return f"_F(GROUP_MA=({groups},), PHENOMENE='MECANIQUE', MODELISATION='DIS_T',),"


def mass_discrete_str(masses: list[Mass]) -> str:
    """The ``DISCRET`` items of ``AFFE_CARA_ELEM`` for the masses: a mass and a zero stiffness per group."""
    items = []
    for mass in masses:
        group = mass.elset.name
        value = mass.mass
        if isinstance(value, (list, tuple)):
            mx, my, mz = (float(v) for v in value)
            items.append(
                f"            _F(GROUP_MA='{group}', CARA='M_T_N', VALE=({mx!r}, 0.0, 0.0, {my!r}, 0.0, {mz!r})),"
            )
        else:
            items.append(f"            _F(GROUP_MA='{group}', CARA='M_T_D_N', VALE={float(value)!r}),")
        items.append(f"            _F(GROUP_MA='{group}', CARA='K_T_D_N', VALE=(0.0, 0.0, 0.0)),")
    return "\n".join(items)
