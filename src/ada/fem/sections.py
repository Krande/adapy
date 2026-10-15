from __future__ import annotations

from typing import TYPE_CHECKING, List, Literal, TypeVar, Union

import numpy as np

from ada.base.types import GeomRepr
from ada.config import logger
from ada.core.utils import Counter
from ada.core.vector_transforms import normal_to_points_in_plane
from ada.core.vector_utils import calc_yvec, calc_zvec, unit_vector, vector_length
from ada.fem.common import FemBase
from ada.fem.formulations.lines import LineTheory
from ada.fem.formulations.shells import ShellTheory
from ada.fem.shapes import ElemType
from ada.materials import Material
from ada.sections import Section

if TYPE_CHECKING:
    from ada import Beam, Plate
    from ada.fem import FemSet


#: A section's theory, as the enum or its value.
TheoryLike = Union[ShellTheory, LineTheory, Literal["shell", "membrane", "beam", "truss", "cable"]]

#: The compression stiffness of a cable relative to its tension stiffness, where a solver asks for one (Code_Aster's
#: ``EC_SUR_E``, whose own default this is). Small but not zero, so a slack cable does not leave its nodes unsupported.
CABLE_COMPRESSION_RATIO = 1.0e-4


class FemSection(FemBase):
    """The properties of an element set: material, and a cross-section (lines) or thickness (shells).

    ``theory`` says what the section carries. A shell section is a bending shell (:attr:`ShellTheory.SHELL`, the
    default) or a membrane (:attr:`ShellTheory.MEMBRANE`); a line section is a beam (:attr:`LineTheory.BEAM`, the
    default), a truss (:attr:`LineTheory.TRUSS`) or a tension-only cable (:attr:`LineTheory.CABLE`). A truss or cable
    takes its area from ``section.properties.Ax`` and needs no ``local_y`` / ``local_z``.

    ``pretension`` is the initial axial force of a cable (N, positive in tension), and ``compression_ratio`` the
    ratio of a cable's compression stiffness to its tension stiffness for a solver that asks for one. Both are for
    cables only.
    """

    id_count = Counter()
    SEC_TYPES = ElemType

    def __init__(
        self,
        name,
        sec_type: GeomRepr | str,
        elset: FemSet,
        material: Material,
        section=None,
        local_z=None,
        local_y=None,
        thickness=None,
        int_points=5,
        metadata=None,
        parent=None,
        refs=None,
        sec_id=None,
        is_rigid=False,
        theory: TheoryLike | None = None,
        pretension: float = 0.0,
        compression_ratio: float = CABLE_COMPRESSION_RATIO,
    ):
        from ada.fem import FemSet

        super().__init__(name, metadata, parent)
        if isinstance(sec_type, str):
            sec_type = GeomRepr.from_str(sec_type)
        self._theory = _resolve_theory(sec_type, theory)
        if self._theory != LineTheory.CABLE and (pretension != 0.0 or compression_ratio != CABLE_COMPRESSION_RATIO):
            raise ValueError(f"FemSection {name!r}: pretension and compression_ratio are for cable sections only")
        if compression_ratio < 0.0:
            raise ValueError(f"FemSection {name!r}: compression_ratio cannot be negative")
        self._pretension = float(pretension)
        self._compression_ratio = float(compression_ratio)

        self._id = sec_id if sec_id is not None else next(FemSection.id_count)
        self._sec_type = sec_type
        self._elset = elset
        self._material = material
        material.refs.append(self)
        self._section = section
        if section is not None:
            section.refs.append(self)
        if self._sec_type == ElemType.LINE and not self._theory.is_axial_only:
            if local_y is None and local_z is None:
                raise ValueError("You need to specify either local_y or local_z")
        if self._sec_type == ElemType.LINE and self._theory.is_axial_only and section is None:
            raise ValueError(f"A {self._theory.value} section needs a section to take its area from")
        self._local_z = local_z
        self._local_y = local_y
        self._local_x = None
        if self._sec_type == ElemType.SHELL and thickness is None:
            raise ValueError("Thickness of shell cannot be None")
        self._thickness = thickness
        self._int_points = int_points
        self._refs = refs
        self._is_rigid = is_rigid

        if isinstance(elset, FemSet):
            elset.refs.append(self)

    def __hash__(self):
        # Tuple hash of the underlying attrs — was previously
        # ``hash(f"{self.name}{self.id}")`` which paid for an
        # f-string format AND went through the ``name`` / ``id``
        # property descriptors on every call. A jacket FEM model
        # → Genie XML conversion calls this 5.8 BILLION times
        # during ``consolidate_materials`` (set membership checks
        # on the materials' ref lists). The format/descriptor
        # overhead burned ~50 minutes of the 64-minute run; the
        # tuple form is ~5× cheaper at the same collision rate.
        return hash((self._name, self._id))

    def link_elements(self):
        from .elements import Elem

        def link_elem(el: Elem):
            el.fem_sec = self

        list(map(link_elem, self.elset.members))

    @property
    def type(self):
        return self._sec_type

    @property
    def theory(self) -> ShellTheory | LineTheory | None:
        """What the section carries (:class:`ShellTheory` for a shell, :class:`LineTheory` for a line, ``None`` for a
        solid)."""
        return self._theory

    @theory.setter
    def theory(self, value: TheoryLike | None):
        theory = _resolve_theory(self._sec_type, value)
        if theory == LineTheory.BEAM and self._local_y is None and self._local_z is None:
            raise ValueError("A beam section needs local_y or local_z")
        if isinstance(theory, LineTheory) and theory.is_axial_only and self._section is None:
            raise ValueError(f"A {theory.value} section needs a section to take its area from")
        if theory != LineTheory.CABLE:
            self._pretension = 0.0
            self._compression_ratio = CABLE_COMPRESSION_RATIO
        self._theory = theory

    @property
    def is_membrane(self) -> bool:
        return self._theory == ShellTheory.MEMBRANE

    @property
    def is_axial_only(self) -> bool:
        """A truss or a cable."""
        return isinstance(self._theory, LineTheory) and self._theory.is_axial_only

    @property
    def is_cable(self) -> bool:
        return self._theory == LineTheory.CABLE

    @property
    def area(self) -> float | None:
        """The cross-section area of a line section (``section.properties.Ax``)."""
        if self._section is None:
            return None
        return self._section.properties.Ax

    @property
    def pretension(self) -> float:
        return self._pretension

    @pretension.setter
    def pretension(self, value: float):
        if self._theory != LineTheory.CABLE:
            raise ValueError("pretension is for cable sections only")
        self._pretension = float(value)

    @property
    def compression_ratio(self) -> float:
        return self._compression_ratio

    @compression_ratio.setter
    def compression_ratio(self, value: float):
        if self._theory != LineTheory.CABLE:
            raise ValueError("compression_ratio is for cable sections only")
        if value < 0.0:
            raise ValueError("compression_ratio cannot be negative")
        self._compression_ratio = float(value)

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        self._id = value

    @property
    def elset(self):
        return self._elset

    @elset.setter
    def elset(self, value):
        self._elset = value

    @property
    def local_z(self) -> np.ndarray:
        """Local Z describes the up vector of the cross section"""
        if self._local_z is not None:
            return self._local_z

        if self.type == ElemType.LINE:
            n1, n2 = self.elset.members[0].nodes[0], self.elset.members[0].nodes[-1]
            v = n2.p - n1.p
            if vector_length(v) == 0.0:
                logger.error(f"Element {self.elset.members[0].id} has zero length")
                xvec = [1, 0, 0]
            else:
                xvec = unit_vector(v)
            self._local_z = calc_zvec(xvec, self.local_y)
        elif self.type == ElemType.SHELL:
            self._local_z = normal_to_points_in_plane([n.p for n in self.elset.members[0].nodes])
        else:
            from ada.core.constants import Z

            self._local_z = np.array(Z, dtype=float)

        return self._local_z

    @property
    def local_y(self) -> np.ndarray:
        """Local y describes the cross vector of the beams X and Z axis"""
        if self._local_y is not None:
            return self._local_y

        if self.type == ElemType.LINE:
            el = self.elset.members[0]
            n1, n2 = el.nodes[0], el.nodes[-1]
            v = n2.p - n1.p
            if vector_length(v) == 0.0:
                raise ValueError(f'Element "{el}" has no length. UNable to calculate y-vector')

            xvec = unit_vector(v)
            if self._local_z is None and self.theory.is_axial_only:
                # A truss or cable has no cross-section orientation; any vector normal to it serves the writers
                # that ask for one.
                self._local_y = _any_normal(xvec)
                return self._local_y

            # See https://en.wikipedia.org/wiki/Cross_product#Coordinate_notation for order of cross product
            vec = calc_yvec(xvec, self.local_z)
        elif self.type == ElemType.SHELL:
            vec = calc_yvec(self.local_x, self.local_z)
        else:
            vec = calc_yvec(self.local_x, self.local_z)

        self._local_y = np.where(abs(vec) == 0, 0, vec)

        return self._local_y

    @property
    def local_x(self) -> np.ndarray:
        if self._local_x is not None:
            return self._local_x

        el = self.elset.members[0]

        if self.type == ElemType.LINE:
            vec = unit_vector(el.nodes[-1].p - el.nodes[0].p)
        elif self.type == ElemType.SHELL:
            vec = unit_vector(el.nodes[1].p - el.nodes[0].p)
        else:
            from ada.core.constants import X

            vec = np.array(X, dtype=float)

        self._local_x = np.round(np.where(abs(vec) == 0, 0, vec), 8)

        return self._local_x

    @property
    def csys(self):
        return [self.local_x, self.local_y, self.local_z]

    @property
    def section(self) -> Section:
        return self._section

    @section.setter
    def section(self, value):
        self._section = value

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value):
        self._material = value

    @property
    def thickness(self):
        return self._thickness

    @thickness.setter
    def thickness(self, value):
        self._thickness = value

    @property
    def int_points(self):
        return self._int_points

    @property
    def refs(self) -> List[Union[Beam, Plate]]:
        return self._refs

    def has_equal_props(self, other: FemSection):
        equal_mat = self.material == other.material
        if self.theory != other.theory or (self.pretension, self.compression_ratio) != (
            other.pretension,
            other.compression_ratio,
        ):
            return False
        if self.type == self.SEC_TYPES.SHELL:
            equal_sec = self.thickness == other.thickness
        elif self.type == self.SEC_TYPES.LINE:
            equal_sec = self.section.equal_props(other.section)
            if self.is_axial_only:
                pass  # no orientation to compare
            elif tuple(self.local_y) != tuple(other.local_y) or tuple(self.local_z) != tuple(other.local_z):
                equal_sec = False
        else:
            equal_sec = True
        if equal_mat is True and equal_sec is True:
            return True
        return False

    # def __eq__(self, other: FemSection):
    #     self_perm = self.unique_fem_section_permutation()
    #     other_perm = other.unique_fem_section_permutation()
    #     return self_perm == other_perm

    def __repr__(self):
        fem_sec_type = self.type
        name = self.name
        sec_name = self.section.name if self.section is not None else "SHELL"
        mat_name = self.material.name
        elset_name = self.elset.name
        theory = "" if self.theory in (None, ShellTheory.SHELL, LineTheory.BEAM) else f" ({self.theory.value})"
        return (
            f'FemSection({fem_sec_type}{theory} - name: "{name}", sec: "{sec_name}", '
            f'mat: "{mat_name}",  elset: "{elset_name}")'
        )


def _resolve_theory(sec_type, theory: TheoryLike | None) -> ShellTheory | LineTheory | None:
    """The theory of a section of ``sec_type``: the default for the type when ``theory`` is None, and an error for
    a theory of another type's (a membrane line, a truss shell) or a theory on a solid."""
    if sec_type == ElemType.SHELL:
        enum, default = ShellTheory, ShellTheory.SHELL
    elif sec_type == ElemType.LINE:
        enum, default = LineTheory, LineTheory.BEAM
    else:
        if theory is not None:
            raise ValueError(f"A {sec_type} section takes no theory (got {theory!r})")
        return None
    if theory is None:
        return default
    try:
        return enum(getattr(theory, "value", theory))
    except ValueError:
        valid = ", ".join(t.value for t in enum)
        raise ValueError(f"A {sec_type} section's theory is one of {valid} (got {theory!r})") from None


def _any_normal(xvec: np.ndarray) -> np.ndarray:
    """A unit vector normal to ``xvec``: global Z projected off it, or global Y when ``xvec`` is vertical."""
    ref = np.array([0.0, 0.0, 1.0]) if abs(xvec[2]) < 0.9 else np.array([0.0, 1.0, 0.0])
    vec = ref - np.dot(ref, xvec) * xvec
    return unit_vector(vec)


# Todo: This should be improved
_A = TypeVar("_A", float, int)
_T_E = TypeVar(
    "_T_E",
    float,
    list[float | int],
    list[tuple[tuple[float | int, float | int]]],
    list[tuple[tuple[float | int, float | int]]],
)
_T_D = TypeVar(
    "_T_D",
    float,
    list[float],
    list[int],
    list[tuple[tuple[float, float]]],
    list[tuple[tuple[int, int]]],
)
_T_P = TypeVar(
    "_T_P",
    float,
    list[float],
    list[int],
    list[tuple[tuple[float, float]]],
    list[tuple[tuple[int, int]]],
)
_T_R = TypeVar(
    "_T_R",
    float,
    list[float],
    list[int],
    list[tuple[tuple[float, float]]],
    list[tuple[tuple[int, int]]],
)


class ConnectorSection(FemBase):
    """A connector section.

    All *_comp properties can be one of the following

     * scalar stiffness value <float | int> (assumed linear stiffness in all degrees of freedom)
     * Tabular stiffness values <list[list[list[scalar numeric, scalar numeric]]]> where the structure is as follows

        list[                                           # Tabular data for stiffness in all Degrees of Freedom
            list[                                       # Tabular data for stiffness in Degree of Freedom i]
                list[numeric scalar, numeric scalar]    # Tabular scalars [Force, Displacement]
                ]
            ]
    """

    def __init__(
        self,
        name,
        elastic_comp: _T_E = None,
        damping_comp: _T_D = None,
        plastic_comp: _T_P = None,
        rigid_dofs: _T_R = None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._elastic_comp = elastic_comp if elastic_comp is not None else []
        self._damping_comp = damping_comp if damping_comp is not None else []
        self._plastic_comp = plastic_comp
        self._rigid_dofs = rigid_dofs

    @property
    def elastic_comp(self) -> _T_E:
        return self._elastic_comp

    @elastic_comp.setter
    def elastic_comp(self, value):
        self._elastic_comp = value

    @property
    def damping_comp(self) -> _T_D:
        return self._damping_comp

    @damping_comp.setter
    def damping_comp(self, value):
        self._damping_comp = value

    @property
    def plastic_comp(self) -> _T_P:
        return self._plastic_comp

    @property
    def rigid_dofs(self) -> _T_R:
        return self._rigid_dofs

    def __repr__(self):
        return f"ConnectorSection({self.name})"
