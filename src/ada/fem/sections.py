from __future__ import annotations

import logging
from typing import List, Tuple, Union

import numpy as np

from ada.concepts.structural import Beam, Plate
from ada.core.utils import (
    Counter,
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


class FemSection(FemBase):
    id_count = Counter()
    SEC_TYPES = ElemType

    def __init__(
        self,
        name,
        sec_type,
        elset,
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
    ):
        """:type elset: ada.fem.FemSet"""
        super().__init__(name, metadata, parent)
        if sec_type is None:
            raise ValueError("Section type cannot be None")

        if sec_type not in ElemType.all:
            raise ValueError(f'Element section type "{sec_type}" is not supported. Must be in {ElemType.all}')
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
                logging.error(f"Element {self.elset.members[0].id} has zero length")
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

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value):
        self._material = value

    @property
    def thickness(self):
        return self._thickness

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

    def __eq__(self, other: FemSection):
        self_perm = self.unique_fem_section_permutation()
        other_perm = other.unique_fem_section_permutation()
        return self_perm == other_perm

    def __repr__(self):
        return (
            f'FemSection({self.type} - name: "{self.name}", sec: "{self.section}", '
            f'mat: "{self.material}",  elset: "{self.elset}")'
        )


class ConnectorSection(FemBase):
    """A Connector Section"""

    def __init__(
        self,
        name,
        elastic_comp,
        damping_comp,
        plastic_comp=None,
        rigid_dofs=None,
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
