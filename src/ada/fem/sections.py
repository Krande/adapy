from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, Tuple, Union

import numpy as np

from ada.base.types import GeomRepr
from ada.config import get_logger
from ada.core.utils import Counter
from ada.core.vector_utils import (
    calc_yvec,
    calc_zvec,
    normal_to_points_in_plane,
    unit_vector,
    vector_length,
)
from ada.materials import Material
from ada.sections import Section

from .common import FemBase
from .shapes import ElemType

if TYPE_CHECKING:
    from ada import Beam, Plate
    from ada.fem import FemSet

logger = get_logger()


class FemSection(FemBase):
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
    ):
        from ada.fem import FemSet

        super().__init__(name, metadata, parent)
        if isinstance(sec_type, str):
            sec_type = GeomRepr.from_str(sec_type)

        self._id = sec_id if sec_id is not None else next(FemSection.id_count)
        self._sec_type = sec_type
        self._elset = elset
        self._material = material
        material.refs.append(self)
        self._section = section
        if section is not None:
            section.refs.append(self)
        if self._sec_type == ElemType.LINE:
            if local_y is None and local_z is None:
                raise ValueError("You need to specify either local_y or local_z")
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
        return hash(f"{self.name}{self.id}")

    def link_elements(self):
        from .elements import Elem

        def link_elem(el: Elem):
            el.fem_sec = self

        list(map(link_elem, self.elset.members))

    @property
    def type(self):
        return self._sec_type

    @property
    def id(self):
        return self._id

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

    def unique_fem_section_permutation(self) -> Tuple[int, Material, Section, tuple, tuple, float]:
        if self.type == self.SEC_TYPES.LINE:
            return self.id, self.material, self.section, tuple(self.local_x), tuple(self.local_z), self.thickness
        elif self.type == self.SEC_TYPES.SHELL:
            return self.id, self.material, self.section, (None,), tuple(self.local_z), self.thickness
        else:
            return self.id, self.material, self.section, (None,), tuple(self.local_z), 0.0

    def has_equal_props(self, other: FemSection):
        equal_mat = self.material == other.material
        if self.type == self.SEC_TYPES.SHELL:
            equal_sec = self.thickness == other.thickness
        elif self.type == self.SEC_TYPES.LINE:
            equal_sec = self.section.equal_props(other.section)
            if tuple(self.local_y) != tuple(other.local_y) or tuple(self.local_z) != tuple(other.local_z):
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
        return (
            f'FemSection({fem_sec_type} - name: "{name}", sec: "{sec_name}", '
            f'mat: "{mat_name}",  elset: "{elset_name}")'
        )


class ConnectorSection(FemBase):
    """A Connector Section"""

    def __init__(
        self,
        name,
        elastic_comp: Union[None, float, List[Any]] = None,
        damping_comp: Union[None, float, List[Any]] = None,
        plastic_comp: Union[None, float, List[Any]] = None,
        rigid_dofs: Union[None, float, List[Any]] = None,
        soft_elastic_dofs=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._elastic_comp = elastic_comp if elastic_comp is not None else []
        self._damping_comp = damping_comp if damping_comp is not None else []
        self._plastic_comp = plastic_comp
        self._rigid_dofs = rigid_dofs
        self._soft_elastic_dofs = soft_elastic_dofs

    @property
    def elastic_comp(self):
        return self._elastic_comp

    @elastic_comp.setter
    def elastic_comp(self, value):
        self._elastic_comp = value

    @property
    def damping_comp(self):
        return self._damping_comp

    @property
    def plastic_comp(self):
        return self._plastic_comp

    @property
    def rigid_dofs(self):
        return self._rigid_dofs

    @property
    def soft_elastic_dofs(self):
        return self._soft_elastic_dofs
