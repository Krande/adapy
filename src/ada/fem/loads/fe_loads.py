from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Iterable, List, Union

import numpy as np

from ada.config import logger
from ada.fem.common import Amplitude, Csys, FemBase
from ada.fem.constants import GRAVITY
from ada.fem.exceptions.model_definition import UnsupportedLoadType
from ada.fem.sets import FemSet
from ada.fem.surfaces import Surface

if TYPE_CHECKING:
    from ada.api.nodes import Node
    from ada.fem.elements import Elem
    from ada.fem.steps import Step


class LoadTypes:
    GRAVITY = "gravity"
    ACC = "acc"
    ACC_ROT = "acc_rot"
    FORCE = "force"
    FORCE_SET = "force_set"
    MASS = "mass"
    PRESSURE = "pressure"
    LINE = "line"

    all = [GRAVITY, ACC, ACC_ROT, FORCE, FORCE_SET, MASS, PRESSURE, LINE]


class Load(FemBase):
    """


    :param load_type: Type of loads. See Load.TYPES for allowable load types.
    :param magnitude: Magnitude of load
    :param name: (Required in the event of a point load being applied)
    :param fem_set: Set reference (Required in the event of a point load being applied)
    :param dof: Degrees of freedom (Required in the event of a point load being applied)
    :param follower_force: Should follower force be accounted for
    :param amplitude: Attach an amplitude object to the load
    :param accr_origin: Origin of a rotational Acceleration field (necessary for load_type='acc_rot').
    """

    TYPES = LoadTypes

    def __init__(
        self,
        name: str,
        load_type: str,
        magnitude: float,
        fem_set: FemSet = None,
        dof: Union[int, List[int]] = None,
        amplitude: Amplitude = None,
        follower_force=False,
        acc_vector=None,
        accr_origin=None,
        accr_rot_axis=None,
        csys: Csys = None,
        metadata=None,
        parent: Step = None,
    ):
        super().__init__(name, metadata, parent)
        self._type = load_type
        self._magnitude = magnitude
        self._fem_set = fem_set

        if isinstance(dof, int):
            dofs = [None, None, None, None, None, None]
            dofs[dof - 1] = 1
            self._dof = dofs
        else:
            self._dof = dof

        self._amplitude = amplitude
        self._follower_force = follower_force
        self._acc_vector = acc_vector
        self._accr_origin = accr_origin
        self._accr_rot_axis = accr_rot_axis
        self._csys = csys

        if self.type == LoadTypes.FORCE:
            if self._dof is None or self._fem_set is None or self._name is None:
                raise Exception("self._dofs and nid (Node id) and name needs to be set in order to use point loads")

            if len(self._dof) != 6:
                raise Exception(
                    "You need to include all 6 dofs even though forces are not applied in all 6 dofs. "
                    "Use None or 0.0 for the dofs not applied with forces"
                )

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.lower() not in LoadTypes.all:
            raise UnsupportedLoadType(f'Load type "{value}" is not among supported load types: "{LoadTypes.all}"')
        self._type = value

    @property
    def dof(self):
        if self._dof is None and self.type is GRAVITY:
            self._dof = [0, 0, 1] if self._dof is None else self._dof
        return self._dof

    @property
    def forces(self):
        if self.type not in (LoadTypes.FORCE,):
            return None

        return [x * self.magnitude if x is not None else 0.0 for x in self.dof]

    @property
    def forces_global(self):
        if self.type not in (LoadTypes.FORCE,):
            return None
        # A dof given as one int leaves the others None (``LoadPoint(..., dof=3)``): no load there. Multiplying the None
        # raised, so every such point load failed in a writer that asks for global components.
        dofs = [0.0 if x is None else float(x) for x in self.dof]
        if self.csys is None:
            return [x * self.magnitude for x in dofs]
        else:
            csys = self.csys
            if csys.coords is None:
                logger.error("Calculating global forces without COORDS is not yet supported")
                return None

            from ada.core.vector_transforms import rotation_matrix_csys_rotate

            destination_csys = [(1, 0, 0), (0, 1, 0)]
            rmat = rotation_matrix_csys_rotate(csys.coords, destination_csys)
            res = np.concatenate([np.dot(rmat, np.array(dofs[:3])), np.dot(rmat, np.array(dofs[3:]))])
            return [x * self.magnitude for x in res]

    @property
    def amplitude(self):
        return self._amplitude

    @property
    def magnitude(self):
        return self._magnitude

    @property
    def fem_set(self) -> FemSet:
        return self._fem_set

    @fem_set.setter
    def fem_set(self, value: FemSet):
        self._fem_set = value

    @property
    def follower_force(self):
        return self._follower_force

    @property
    def acc_vector(self):
        if self.type not in (LoadTypes.ACC, LoadTypes.ACC_ROT):
            raise ValueError(f'Acceleration vector only applies for type "acc". Not "{self.type}"')

        dir_error = "If acc_vector is not specified, you must pass dof=[int] (int 1-3) for the acc field"

        if self._acc_vector is not None:
            return self._acc_vector

        dofs = [x for x in self.dof if x is not None]
        if len(dofs) != 1:
            raise ValueError(dir_error)

        acc_magnitude = dofs[0]
        acc_dir = int(self.dof.index(acc_magnitude) + 1)
        if 1 > acc_dir > 3:
            raise ValueError(f"Acceleration vector works only on the translational DOFs. DOF={acc_dir} was passed in")

        if acc_dir == 1:
            dvec = 1, 0, 0
        elif acc_dir == 2:
            dvec = 0, 1, 0
        else:
            dvec = 0, 0, 1

        return tuple([float(self._magnitude * d) if d != 0 else 0.0 for d in dvec])

    @property
    def acc_rot_origin(self):
        return self._accr_origin

    @acc_rot_origin.setter
    def acc_rot_origin(self, value):
        self._accr_origin = value

    @property
    def acc_rot_axis(self):
        return self._accr_rot_axis

    @property
    def csys(self) -> Csys:
        return self._csys

    @property
    def parent(self) -> "Step":
        return self._parent

    @parent.setter
    def parent(self, value: "Step"):
        self._parent = value

    def __repr__(self):
        if self.forces is None:
            # A pressure carries a magnitude and a surface, not a force vector.
            return f"Load({self.name}, {self.type}, magnitude={self.magnitude!r})"
        forc_str = ",".join(f"{f:.6E}" for f in self.forces)
        return f"Load({self.name}, {self.type}, [{forc_str}])"


class PressureDistribution:
    UNIFORM = "uniform"
    TOTAL_FORCE = "total_force"


class LoadPressure(Load):
    P_DIST_TYPES = PressureDistribution

    def __init__(self, name: str, magnitude: float, surface: Surface, distribution=P_DIST_TYPES.UNIFORM):
        super(LoadPressure, self).__init__(name, LoadTypes.PRESSURE, magnitude)
        self._surface = surface
        self._distribution = distribution

    @property
    def surface(self) -> Surface:
        return self._surface

    @property
    def distribution(self) -> str:
        return self._distribution


class LoadGravity(Load):
    def __init__(self, name, acceleration=-9.81):
        super(LoadGravity, self).__init__(name, Load.TYPES.GRAVITY, acceleration)


class LoadPoint(Load):
    def __init__(self, name, magnitude, fem_set, dof, amplitude=None, follower_force=True, csys=None):
        super(LoadPoint, self).__init__(
            name,
            Load.TYPES.FORCE,
            magnitude=magnitude,
            fem_set=fem_set,
            dof=dof,
            amplitude=amplitude,
            follower_force=follower_force,
            csys=csys,
        )


@dataclass(frozen=True)
class LineLoadSegment:
    """One element's share of a distributed line load: force per unit length, in global components.

    On a beam element (``edge`` is ``None``) the load acts along the element's axis from end 1 (its first node) to
    end 2, over the stretch that leaves ``l1`` unloaded at end 1 and ``l2`` at end 2; ``q1`` is the intensity at the
    start of that stretch and ``q2`` at its end, and it varies linearly in between. This is Sesam's BELOAD1
    (``L1``, ``L2`` and the intensities at the load's own start and end points, as GeniE writes them: a partial
    line load 1.3..2.9 m on 0.5 m elements gave L1 = 0.3 and L2 = 0.1 on the two end elements).

    On a shell element ``edge`` names the edge, 1-based, from corner node ``edge`` to the next one; the load covers
    the whole edge (``l1 = l2 = 0``), ``q1`` at the edge's first node and ``q2`` at its second (Sesam's BELLO2).
    """

    elem: Elem
    q1: tuple[float, float, float]
    q2: tuple[float, float, float]
    l1: float = 0.0
    l2: float = 0.0
    edge: int | None = None

    def ends(self) -> tuple[np.ndarray, np.ndarray]:
        """The positions of the element end (or edge end) the segment's ``l1`` and ``l2`` count from."""
        nodes = self.elem.nodes
        if self.edge is None:
            return np.asarray(nodes[0].p, dtype=float), np.asarray(nodes[1].p, dtype=float)
        corners = _corner_count(self.elem)
        a, b = nodes[self.edge - 1], nodes[self.edge % corners]
        return np.asarray(a.p, dtype=float), np.asarray(b.p, dtype=float)

    def loaded_ends(self) -> tuple[np.ndarray, np.ndarray]:
        """The start and end point of the loaded stretch."""
        a, b = self.ends()
        length = float(np.linalg.norm(b - a))
        axis = (b - a) / length
        return a + self.l1 * axis, b - self.l2 * axis

    def resultant(self) -> np.ndarray:
        """The segment's total force: the mean intensity times the loaded length."""
        start, end = self.loaded_ends()
        return (
            0.5
            * (np.asarray(self.q1, dtype=float) + np.asarray(self.q2, dtype=float))
            * float(np.linalg.norm(end - start))
        )

    def moment(self, about=(0.0, 0.0, 0.0)) -> np.ndarray:
        """The segment's moment about ``about``: a linearly varying load's force acts at its centroid."""
        start, end = self.loaded_ends()
        q1, q2 = np.asarray(self.q1, dtype=float), np.asarray(self.q2, dtype=float)
        length = float(np.linalg.norm(end - start))
        # q(t) = q1 (1 - t) + q2 t over t in 0..1: each end's share and its lever arm.
        f1, f2 = q1 * length / 2, q2 * length / 2
        r1 = start + (end - start) / 3 - np.asarray(about, dtype=float)
        r2 = start + 2 * (end - start) / 3 - np.asarray(about, dtype=float)
        return np.cross(r1, f1) + np.cross(r2, f2)

    def uniform_over_beam_element(self) -> bool:
        """A beam segment of one intensity from end to end: what a format's per-element beam load writes exactly."""
        return self.edge is None and self.l1 == 0.0 and self.l2 == 0.0 and tuple(self.q1) == tuple(self.q2)


def _corner_count(elem: Elem) -> int:
    from ada.fem.shapes.definitions import ShellShapes

    return 3 if elem.type in (ShellShapes.TRI, ShellShapes.TRI6, ShellShapes.TRI7) else 4


def edge_midside_node(elem: Elem, edge: int) -> Node | None:
    """The midside node of a second-order shell element's edge ``edge`` (1-based, corner ``edge`` to the next), or
    ``None`` for a first-order element. adapy's 6-, 7-, 8- and 9-node shells list the corners first and then one node
    per edge in the same order (gmsh's and Abaqus' S8/STRI65 order): edge ``i`` of an ``n``-corner element has node
    ``n + i - 1`` (0-based) in the middle."""
    from ada.fem.shapes.definitions import ShellShapes

    if elem.type not in (ShellShapes.TRI6, ShellShapes.TRI7, ShellShapes.QUAD8, ShellShapes.QUAD9):
        return None
    return elem.nodes[_corner_count(elem) + edge - 1]


class LoadLine(Load):
    """A distributed line load, as the element loads it acts through (:class:`LineLoadSegment`).

    What the concept to FE conversion makes of a ``LoadConceptLine``: per beam element under the line a BELOAD1-like
    segment, or per shell element edge under it a BELLO2-like one. A format with no element line load writes it as
    the consistent nodal loads of each segment (see :meth:`nodal_loads`), and says so.
    """

    def __init__(self, name: str, segments: Iterable[LineLoadSegment], metadata=None, parent: Step = None):
        super().__init__(name, LoadTypes.LINE, 1.0, metadata=metadata, parent=parent)
        self.segments: list[LineLoadSegment] = list(segments)

    def resultant(self) -> np.ndarray:
        return sum((s.resultant() for s in self.segments), np.zeros(3))

    def scaled(self, factor: float, name: str) -> LoadLine:
        segs = [
            replace(s, q1=tuple(factor * float(q) for q in s.q1), q2=tuple(factor * float(q) for q in s.q2))
            for s in self.segments
        ]
        return LoadLine(name, segs, metadata=dict(self.metadata or {}))

    @staticmethod
    def nodal_loads(seg: LineLoadSegment) -> list[tuple[Node, np.ndarray]]:
        """The consistent nodal forces of one segment for an element interpolating linearly between its two end
        nodes: ``F_i = int q(x) N_i(x) dx`` over the loaded stretch, ``N_1 = 1 - x/L`` and ``N_2 = x/L``.

        This is what Abaqus 2025 itself makes of a ``*Dload PZ`` on a B31 element: measured on a 4 m B31 beam in
        eight elements, ``*Dload PZ, -1000`` and ``*Cload -250/-500`` at the nodes gave the same mid-span
        deflection to all eight printed digits (-2.3587535E-04).

        On the edge of a second-order shell (6-, 7-, 8- or 9-node: :func:`edge_midside_node`) the edge interpolates
        quadratically through its midside node, and the forces go to its three nodes with ``N_a = (1 - x)(1 - 2x)``,
        ``N_m = 4x(1 - x)`` and ``N_b = x(2x - 1)``: a uniform ``q`` over the edge gives ``qL/6, 4qL/6, qL/6``. The two
        end nodes alone would load the midside node with nothing, which the element does not take as the same load.
        The midside node must lie halfway along the edge (a straight edge, as every edge a line load is put on is);
        otherwise the shape functions above are not the element's and the segment is refused.
        """
        a, b = seg.ends()
        length = float(np.linalg.norm(b - a))
        x1, x2 = seg.l1 / length, 1.0 - seg.l2 / length
        q1, q2 = np.asarray(seg.q1, dtype=float), np.asarray(seg.q2, dtype=float)
        mid = None if seg.edge is None else edge_midside_node(seg.elem, seg.edge)
        if mid is None:

            def shapes(x):
                return (1 - x, x)

        else:
            off = float(np.linalg.norm(np.asarray(mid.p, dtype=float) - 0.5 * (a + b)))
            if off > 1e-6 * length:
                raise ValueError(
                    f"line load on edge {seg.edge} of element {seg.elem.id}: its midside node {mid.id} is {off:.3g} "
                    f"from the middle of the edge; the consistent nodal loads of a curved edge are not written"
                )

            def shapes(x):
                return ((1 - x) * (1 - 2 * x), 4 * x * (1 - x), x * (2 * x - 1))

        # q(x) linear from q1 at x1 to q2 at x2 (x as a fraction of L); Gauss over the stretch, exact up to a cubic.
        span = x2 - x1
        jac = 0.5 * span * length
        forces = [np.zeros(3) for _ in shapes(0.0)]
        for g, w in zip(*np.polynomial.legendre.leggauss(3)):
            t = 0.5 * (g + 1.0)
            q = q1 * (1 - t) + q2 * t
            for f, n in zip(forces, shapes(x1 + t * span)):
                f += w * jac * q * n
        if seg.edge is None:
            nodes = [seg.elem.nodes[0], seg.elem.nodes[1]]
        else:
            corners = _corner_count(seg.elem)
            n_a, n_b = seg.elem.nodes[seg.edge - 1], seg.elem.nodes[seg.edge % corners]
            nodes = [n_a, n_b] if mid is None else [n_a, mid, n_b]
        return list(zip(nodes, forces))

    @staticmethod
    def summed_nodal_loads(segments: Iterable[LineLoadSegment]) -> list[tuple[Node, np.ndarray]]:
        """:meth:`nodal_loads` of every segment, summed per node and sorted by node id.

        Summed because not every format adds two loads on one node: Abaqus 2025 does, and so does CalculiX 2.23
        (measured: two ``*CLOAD`` lines of -500 on one node and dof moved it exactly as one of -1000 did), but
        Code_Aster's rule for ``AFFE_CHAR_MECA`` is that of two conflicting assignments the last one wins
        (U4.44.01, "Rules of overload and retention")."""
        nodal: dict = {}
        for seg in segments:
            for node, force in LoadLine.nodal_loads(seg):
                nodal[node] = nodal.get(node, np.zeros(3)) + force
        return sorted(nodal.items(), key=lambda x: x[0].id)

    @staticmethod
    def hermite_nodal_loads(seg: LineLoadSegment) -> list[tuple[Node, np.ndarray]]:
        """The consistent nodal forces *and moments* of one beam segment on a two-node Euler-Bernoulli element:
        ``(node, (Fx, Fy, Fz, Mx, My, Mz))`` at each end, global components.

        The transverse part of the load is shared by the element's cubic (Hermite) shape functions, the force by
        ``N1 = 1 - 3s^2 + 2s^3`` / ``N3 = 3s^2 - 2s^3`` and the moment by ``N2 = h (s - 2s^2 + s^3)`` /
        ``N4 = h (s^3 - s^2)`` about ``t x q`` (``t`` the element axis from end 1 to end 2); the axial part linearly,
        as the element interpolates its axial displacement. With these loads an Euler-Bernoulli element's nodal
        displacements are the exact ones for any load linear over the loaded stretch, which forces alone are not: a
        simply supported 4 m beam in 0.5 m elements under a uniform load misses the end moments ``q h^2 / 12``, which
        is ``0.8 (h / L)^2`` of the mid-span deflection. Integrated exactly (3-point Gauss; the integrand is a quartic).
        """
        if seg.edge is not None:
            raise ValueError("a shell edge segment has no beam (Hermite) form")
        a, b = seg.ends()
        length = float(np.linalg.norm(b - a))
        axis = (b - a) / length
        x1, x2 = seg.l1 / length, 1.0 - seg.l2 / length
        q1, q2 = np.asarray(seg.q1, dtype=float), np.asarray(seg.q2, dtype=float)
        out_a, out_b = np.zeros(6), np.zeros(6)
        span = x2 - x1
        jac = 0.5 * span * length
        for g, w in zip(*np.polynomial.legendre.leggauss(3)):
            t = 0.5 * (g + 1.0)
            s = x1 + t * span
            q = q1 * (1 - t) + q2 * t
            q_ax = float(np.dot(q, axis)) * axis
            q_tr = q - q_ax
            m = np.cross(axis, q_tr)
            out_a[:3] += w * jac * (q_ax * (1 - s) + q_tr * (1 - 3 * s**2 + 2 * s**3))
            out_b[:3] += w * jac * (q_ax * s + q_tr * (3 * s**2 - 2 * s**3))
            out_a[3:] += w * jac * m * length * (s - 2 * s**2 + s**3)
            out_b[3:] += w * jac * m * length * (s**3 - s**2)
        return [(seg.elem.nodes[0], out_a), (seg.elem.nodes[1], out_b)]

    @staticmethod
    def summed_beam_nodal_loads(segments: Iterable[LineLoadSegment], hermite) -> list[tuple[Node, np.ndarray]]:
        """Six-component nodal loads of ``segments``, summed per node and sorted by node id: :meth:`hermite_nodal_loads`
        for a segment whose element ``hermite(elem)`` says is a two-node Euler-Bernoulli beam, :meth:`nodal_loads`
        (forces, no moments) for the rest."""
        nodal: dict = {}
        for seg in segments:
            if seg.edge is None and hermite(seg.elem):
                pairs = LoadLine.hermite_nodal_loads(seg)
            else:
                pairs = [(n, np.concatenate([f, np.zeros(3)])) for n, f in LoadLine.nodal_loads(seg)]
            for node, f in pairs:
                nodal[node] = nodal.get(node, np.zeros(6)) + f
        return sorted(nodal.items(), key=lambda x: x[0].id)


def acceleration_vector(load: Load) -> tuple[float, float, float]:
    """The acceleration a gravity or acceleration load applies, as a global vector.

    A ``LoadGravity`` is its magnitude along its dof direction (``[0, 0, 1]`` unless given otherwise), and so is the
    ``Load`` of type gravity a concept acceleration field becomes (``|a|`` along a unit ``dof``). ``Load.acc_vector``
    wants exactly one non-``None`` dof entry, but a load read from an Abaqus deck names all three direction components
    (``[1, 0, 0]``), which it rejects; the components scaled by the magnitude are the same vector either way.
    """
    if load.type == LoadTypes.ACC:
        try:
            return tuple(float(a) for a in load.acc_vector)
        except ValueError:
            pass
    comps = [0.0 if d is None else float(d) for d in (list(load.dof or [0, 0, 1]) + [0, 0, 0])[:3]]
    return tuple(float(load.magnitude) * c for c in comps)


#: ``LoadCase.metadata`` key: the case's number in the analysis (GeniE's ``fem_loadcase_number``), which the Sesam
#: writer writes it under (TDLOAD, BNLOAD, BNDISPL ... LLC) when the step's numbers are distinct.
LOAD_CASE_NUMBER = "fem_loadcase_number"


class LoadCase(FemBase):
    def __init__(
        self,
        name,
        comment,
        loads=None,
        mass=None,
        lcsys=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._comment = comment
        self._loads = loads
        self._mass = mass
        self._lcsys = lcsys

    @property
    def loads(self):
        return self._loads

    @property
    def mass(self):
        return self._mass

    @property
    def comment(self):
        return self._comment

    @property
    def csys(self):
        return (1, 0, 0), (0, 1, 0), (0, 0, 1) if self._lcsys is None else self._lcsys

    def __repr__(self):
        return f"LC({self.name}, {self.comment})"
