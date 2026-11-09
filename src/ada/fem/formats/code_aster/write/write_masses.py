"""Discrete elements for Code_Aster: point masses, rotary inertias, non-structural masses and springs.

Code_Aster gives a cell stiffness or mass only through a modelisation and element characteristics (U4.42.01
``DISCRET``), so each kind is a group of cells, modelled ``DIS_T`` (three translations) or ``DIS_TR`` (six dofs), and
given its matrix in ``AFFE_CARA_ELEM``:

* a point mass (``MASS``): its ``POI1`` cell, ``DIS_T``, ``M_T_D_N`` (isotropic) or ``M_T_N`` (``(mx, my, mz)``);
* a rotary inertia (``ROTARYI``, ``[I11, I22, I33, I12, I13, I23]`` as Abaqus gives it): its ``POI1`` cell, ``DIS_TR``,
  ``M_TR_N`` with the inertia tensor in the rotation block, no mass. Only on a node with rotational dofs (a beam's, a
  shell's, or a 6 x 6 spring's): on any other its rotations would be free;
* a non-structural mass (``NONSTRUCTURAL``) on elements: ``MASS_REP`` spreads it over ``POI1`` cells on the region's
  nodes, weighted by the size of the cells around each node -- ``TOTALE``, ``LINEIQUE`` (per length) or ``SURFACIQUE``
  (per area); a mass per volume has no MASS_REP form and is refused by name. On nodes, a total is lumped equally on
  them (reported). The cells are the writer's own (:func:`discrete_cells`);
* a grounded spring (``SPRING1``): its ``POI1`` cell, ``DIS_TR`` (``DIS_T`` for a 3 x 3 stiffness), ``K_TR_N``: the full
  stiffness, coupled terms and all, in global axes;
* a two-node spring (``SPRING2``): a ``SEG2`` cell, ``DIS_TR``, ``K_TR_L`` with ``REPERE='GLOBAL'``: the 12 x 12
  stiffness of adapy's links (``k`` at ``(i, j)`` resists ``u1_i - u2_j``, as Abaqus and the Sesam writer read it).

Every symmetric matrix is given as its **upper triangle column by column** (``K11, K12, K22, K13, K23, K33, ...``;
Code_Aster's catalogue, AFFE_CARA_ELEM DISCRET: "triangulaire supérieure par colonne"). An anisotropic point mass was
once written row by row, ``(mx, 0, 0, my, 0, mz)``: read by columns that is m22 = 0 and an x-z coupling of ``my``.

A discrete with only a mass gets a zero stiffness, and one with only a stiffness a zero mass: Code_Aster wants both
matrices of every discrete element.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ada.fem import Mass, Spring
from ada.fem.shapes.definitions import LineShapes, MassTypes, ShellShapes, SpringTypes

if TYPE_CHECKING:
    from ada.api.spatial import Part
    from ada.fem import FEM, FemSet

STAGE = "code_aster writer"

#: MASS_REP's TYPE by Abaqus' *Nonstructural Mass UNITS (adapy's Mass.units).
MASS_REP_TYPE = {"TOTAL": "TOTALE", "PER UNIT LENGTH": "LINEIQUE", "PER UNIT AREA": "SURFACIQUE"}


def upper_by_columns(k) -> list[float]:
    """The upper triangle of the symmetric ``k``, column by column: ``K11, K12, K22, K13, K23, K33, ...``."""
    k = np.asarray(k, dtype=float)
    return [float(k[i, j]) for j in range(k.shape[0]) for i in range(j + 1)]


def _report():
    from ada.fem.formats import conversion_report

    return conversion_report.current()


# ----------------------------------------------------------------------------------------- the writer's own cells
@dataclass
class Discretes:
    """The point cells and element groups the writer adds for discretes, kept off the model (as write_skins' are)."""

    #: POI1 cells on a non-structural mass's nodes
    elements: list = field(default_factory=list)
    #: element groups: one per spring, one per non-structural mass's cells
    sets: list = field(default_factory=list)
    #: ``id(spring) -> group``
    spring_group: dict = field(default_factory=dict)
    #: ``id(mass) -> group`` of a non-structural mass's POI1 cells
    poi_group: dict = field(default_factory=dict)


_current: list[Discretes] = []


@contextlib.contextmanager
def deck(discretes: Discretes) -> Iterator[Discretes]:
    _current.append(discretes)
    try:
        yield discretes
    finally:
        _current.pop()


def current() -> Discretes:
    return _current[-1] if _current else Discretes()


def _region_nodes(fem_set: FemSet) -> list:
    """The nodes of a region: its members if it is a node set, else every node of its elements, once each."""
    members = list(fem_set.members)
    if getattr(fem_set, "type", None) == "nset" or not any(getattr(m, "nodes", None) for m in members):
        return members
    seen, out = set(), []
    for el in members:
        for n in el.nodes:
            if n.id not in seen:
                seen.add(n.id)
                out.append(n)
    return out


def discrete_cells(fem: FEM, first_id: int, taken: set[str] = frozenset()) -> Discretes:
    """The groups the springs need (a spring has a node set; Code_Aster names cells by element group) and the POI1
    cells non-structural masses are spread on, with ids from ``first_id``. Group names are short (MED drops group
    names over 24 chars) and avoid the model's and ``taken``."""
    from ada.fem import Elem, FemSet

    out = Discretes()
    names = set(fem.elsets) | set(taken)

    def fresh(prefix: str) -> str:
        n = 1
        while f"{prefix}{n}" in names:
            n += 1
        names.add(f"{prefix}{n}")
        return f"{prefix}{n}"

    for spring in sorted(fem.springs.values(), key=lambda s: s.id):
        group = FemSet(fresh("sp"), [spring], FemSet.TYPES.ELSET)
        out.sets.append(group)
        out.spring_group[id(spring)] = group
    next_id = first_id
    for mass in fem.elements.masses:
        if mass.type != MassTypes.NONSTRUCTURAL or mass.fem_set is None:
            continue
        cells = [Elem(next_id + i, [n], MassTypes.MASS) for i, n in enumerate(_region_nodes(mass.fem_set))]
        next_id += len(cells)
        group = FemSet(fresh("ns"), cells, FemSet.TYPES.ELSET)
        out.elements += cells
        out.sets.append(group)
        out.poi_group[id(mass)] = group
    return out


# ------------------------------------------------------------------------------------------------- the discretes
class Discrete:
    """One discrete's group, modelisation and ``DISCRET`` items (and, for a non-structural mass, its ``MASS_REP``)."""

    def __init__(self, obj, group: str, modelisation: str, items: list[str], mass_rep: str = ""):
        self.obj, self.group, self.modelisation, self.items, self.mass_rep = obj, group, modelisation, items, mass_rep

    @property
    def name(self) -> str:
        return self.obj.name


class DiscreteList(list):
    """The part's discretes (:func:`point_masses`), what ``create_sections_str`` gets as its ``masses``."""


def _has_rotations(node) -> bool:
    """Whether something stiffens ``node``'s rotations: a beam, a shell, or a 6 x 6 spring on it (DIS_TR)."""
    for r in getattr(node, "refs", ()):
        el_type = getattr(r, "type", None)
        if isinstance(el_type, (LineShapes, ShellShapes)):
            return True
        if isinstance(r, Spring) and np.asarray(r.stiff).shape == (6, 6):
            return True
    return False


def _point_mass(mass: Mass) -> Discrete:
    group = mass.elset.name
    value = mass.mass
    if isinstance(value, (list, tuple)):
        mx, my, mz = (float(v) for v in value)
        items = [f"_F(GROUP_MA='{group}', CARA='M_T_N', VALE={tuple(upper_by_columns(np.diag([mx, my, mz])))!r}),"]
    else:
        items = [f"_F(GROUP_MA='{group}', CARA='M_T_D_N', VALE={float(value)!r}),"]
    items.append(f"_F(GROUP_MA='{group}', CARA='K_T_D_N', VALE=(0.0, 0.0, 0.0)),")
    return Discrete(mass, group, "DIS_T", items)


def _rotary_inertia(mass: Mass) -> Discrete | None:
    nodes = list(mass.members)
    if len(nodes) != 1 or not _has_rotations(nodes[0]):
        _report().omitted(
            STAGE,
            "Mass",
            mass.name,
            "a rotary inertia on a node without rotational dofs (no beam or shell there): its rotations would be free",
        )
        return None
    raw = [float(x) for x in (mass._mass if isinstance(mass._mass, (list, tuple)) else [mass._mass])]
    if len(raw) == 1:  # one value: the same about every axis
        raw = raw * 3
    i11, i22, i33, i12, i13, i23 = (raw + [0.0] * 6)[:6]
    m = np.zeros((6, 6))
    m[3:, 3:] = [[i11, i12, i13], [i12, i22, i23], [i13, i23, i33]]
    group = mass.elset.name
    items = [
        f"_F(GROUP_MA='{group}', CARA='M_TR_N', VALE={tuple(upper_by_columns(m))!r}),",
        f"_F(GROUP_MA='{group}', CARA='K_TR_D_N', VALE=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),",
    ]
    return Discrete(mass, group, "DIS_TR", items)


def _nonstructural(mass: Mass, discretes: Discretes) -> Discrete | None:
    poi = discretes.poi_group.get(id(mass))
    if poi is None or not poi.members:
        _report().omitted(STAGE, "Mass", mass.name, "a non-structural mass on no region")
        return None
    group = poi.name
    raw = mass._mass
    total = float(raw[0] if isinstance(raw, (list, tuple)) else raw)
    units = str(mass.units or "TOTAL").strip().upper()
    zero_k = f"_F(GROUP_MA='{group}', CARA='K_T_D_N', VALE=(0.0, 0.0, 0.0)),"
    region = mass.fem_set
    on_elements = getattr(region, "type", None) != "nset" and any(getattr(m, "nodes", None) for m in region.members)
    if not on_elements:
        if units != "TOTAL":
            _report().omitted(
                STAGE, "Mass", mass.name, f'a non-structural mass "{units}" on nodes has no size to scale'
            )
            return None
        _report().approximated(
            STAGE,
            "Mass",
            mass.name,
            "a non-structural mass on nodes is lumped equally on them",
            n_nodes=len(poi.members),
        )
        per = total / len(poi.members)
        return Discrete(mass, group, "DIS_T", [f"_F(GROUP_MA='{group}', CARA='M_T_D_N', VALE={per!r}),", zero_k])
    kind = MASS_REP_TYPE.get(units)
    if kind is None:
        _report().omitted(
            STAGE,
            "Mass",
            mass.name,
            f'a non-structural mass "{units}" has no MASS_REP form (TOTAL, per length, per area)',
        )
        return None
    mass_rep = f"_F(GROUP_MA='{region.name}', GROUP_MA_POI1='{group}', VALE={total!r}, TYPE='{kind}'),"
    return Discrete(mass, group, "DIS_T", [zero_k], mass_rep)


def _spring(spring: Spring, discretes: Discretes) -> Discrete | None:
    fs = discretes.spring_group.get(id(spring))
    if fs is None:
        _report().omitted(STAGE, "Spring", spring.name, "a spring the writer made no group for")
        return None
    group = fs.name
    k = np.asarray(spring.stiff, dtype=float)
    if k.ndim != 2 or k.shape[0] != k.shape[1] or k.shape[0] not in (3, 6):
        _report().omitted(
            STAGE, "Spring", spring.name, f"a spring stiffness of shape {k.shape}; 3 x 3 or 6 x 6 is read"
        )
        return None
    # A SPRING1's matrix is its stiffness; a SPRING2's is a table of links, whose assembled 12 x 12 is symmetric always
    if spring.type == SpringTypes.SPRING1 and not np.allclose(k, k.T):
        _report().omitted(STAGE, "Spring", spring.name, "a non-symmetric spring stiffness (SYME='NON') is not written")
        return None
    six = k.shape[0] == 6
    if spring.type == SpringTypes.SPRING1:
        cara, mass = ("K_TR_N", "M_TR_D_N") if six else ("K_T_N", "M_T_D_N")
        zero_m = (0.0,) * 10 if six else 0.0
        items = [
            f"_F(GROUP_MA='{group}', CARA='{cara}', VALE={tuple(upper_by_columns(k))!r}),",
            f"_F(GROUP_MA='{group}', CARA='{mass}', VALE={zero_m!r}),",
        ]
    else:
        from ada.fem.formats.sesam.write.write_springs import two_node_matrix

        cara, mass = ("K_TR_L", "M_TR_D_L") if six else ("K_T_L", "M_T_D_L")
        zero_m = (0.0,) * 4 if six else 0.0
        items = [
            f"_F(GROUP_MA='{group}', REPERE='GLOBAL', CARA='{cara}', VALE={tuple(upper_by_columns(two_node_matrix(k)))!r}),",
            f"_F(GROUP_MA='{group}', CARA='{mass}', VALE={zero_m!r}),",
        ]
    return Discrete(spring, group, "DIS_TR" if six else "DIS_T", items)


def point_masses(part: Part) -> DiscreteList:
    """The part's discretes Code_Aster takes -- masses of every kind and springs, in model order; each one it cannot
    take is reported as left out."""
    discretes = current()
    out = DiscreteList()
    for mass in part.fem.elements.masses:
        if mass.type == MassTypes.NONSTRUCTURAL:
            d = _nonstructural(mass, discretes)
        elif mass.elset is None:
            _report().omitted(STAGE, "Mass", mass.name, "a point mass in no element set (add it with FEM.add_mass)")
            d = None
        elif mass.type == MassTypes.ROTARYI:
            d = _rotary_inertia(mass)
        elif mass.type == MassTypes.MASS:
            d = _point_mass(mass)
        else:
            _report().omitted(STAGE, "Mass", mass.name, f'a "{mass.type}" mass has no Code_Aster form in this writer')
            d = None
        if d is not None:
            out.append(d)
    for spring in sorted(part.fem.springs.values(), key=lambda s: s.id):
        d = _spring(spring, discretes)
        if d is not None:
            out.append(d)
    return out


def mass_groups(masses) -> list[str]:
    """The element groups of ``masses``, each once, in order."""
    groups = []
    for d in masses:
        if d.group not in groups:
            groups.append(d.group)
    return groups


def mass_model_str(masses) -> str:
    """The ``AFFE_MODELE`` entries modelling the discretes' groups, ``DIS_T`` and ``DIS_TR``; empty for none."""
    out = ""
    for modelisation in ("DIS_T", "DIS_TR"):
        groups = mass_groups([d for d in masses if d.modelisation == modelisation])
        if groups:
            names = ", ".join(f"'{g}'" for g in groups)
            out += f"_F(GROUP_MA=({names},), PHENOMENE='MECANIQUE', MODELISATION='{modelisation}',),"
    return out


def mass_discrete_str(masses) -> str:
    """The ``DISCRET`` items of ``AFFE_CARA_ELEM`` for the discretes."""
    return "\n".join(f"            {item}" for d in masses for item in d.items)


def mass_rep_str(masses) -> str:
    """The ``MASS_REP`` items of ``AFFE_CARA_ELEM``: the non-structural masses spread over their regions."""
    return "\n".join(f"            {d.mass_rep}" for d in masses if d.mass_rep)
