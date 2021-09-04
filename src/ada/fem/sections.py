import logging

import numpy as np

from ada.core.utils import (
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
        offset=None,
        hinges=None,
        metadata=None,
        parent=None,
    ):
        """:type elset: ada.fem.FemSet"""
        super().__init__(name, metadata, parent)
        _valid_secs = [ElemType.LINE, ElemType.SHELL, ElemType.SOLID]
        self._sec_type = sec_type
        if sec_type is None:
            raise ValueError("Section type cannot be None")
        if sec_type not in _valid_secs:
            raise ValueError(f'Element section type "{sec_type}" is not supported. Must be in {_valid_secs}')
        self._elset = elset
        self._material = material
        self._section = section
        self._local_z = local_z
        self._local_y = local_y
        self._thickness = thickness
        self._int_points = int_points
        self._offset = offset
        self._hinges = hinges

    def link_elements(self):
        from .elements import Elem

        def link_elem(el: Elem):
            el.fem_sec = self

        list(map(link_elem, self.elset.members))

    def get_offset_coords(self):
        elem = self.elset.members[0]
        nodes = [n.p for n in elem.nodes]
        if self.offset is None:
            return nodes

        for n_old, ecc in self.offset:
            mat = np.eye(3)
            new_p = np.dot(mat, ecc) + n_old.p
            i = elem.nodes.index(n_old)
            nodes[i] = new_p

        return nodes

    @property
    def type(self):
        return self._sec_type

    @property
    def elset(self):
        return self._elset

    @elset.setter
    def elset(self, value):
        self._elset = value

    @property
    def local_z(self):
        """Local Z describes the up vector of the cross section"""
        if self._local_z is None:
            if self.type == ElemType.LINE:
                n1, n2 = self.elset.members[0].nodes[0], self.elset.members[0].nodes[-1]
                v = n2.p - n1.p
                if vector_length(v) == 0.0:
                    logging.error(f"Element {self.elset.members[0].id} has zero length")
                    xvec = [1, 0, 0]
                else:
                    xvec = unit_vector(v)
                self._local_z = calc_zvec(xvec, self.local_y)
            else:
                self._local_z = normal_to_points_in_plane([n.p for n in self.elset.members[0].nodes])
                # raise NotImplementedError("Local Z is not implemented for shell elements, yet.")
        return self._local_z

    @property
    def local_y(self):
        """Local y describes the cross vector of the beams X and Z axis"""
        if self._local_y is None:
            if self.type in (ElemType.LINE, ElemType.SHELL):
                n1, n2 = self.elset.members[0].nodes[0], self.elset.members[0].nodes[-1]
                v = n2.p - n1.p

                xvec = [1, 0, 0] if vector_length(v) == 0.0 else unit_vector(v)

                # See https://en.wikipedia.org/wiki/Cross_product#Coordinate_notation for order of cross product
                self._local_y = calc_yvec(xvec, self.local_z)
            else:
                raise NotImplementedError("Local Y is not implemented for solid elements.")
        return self._local_y

    @property
    def local_x(self):
        if self.type == ElemType.LINE:
            el = self.elset.members[0]
            return unit_vector(el.nodes[-1].p - el.nodes[0].p)
        else:
            logging.error(f"X-vector not defined for {self.type}")

    @property
    def csys(self):
        return [self.local_x, self.local_y, self.local_z]

    @property
    def section(self) -> Section:
        return self._section

    @property
    def material(self) -> Material:
        return self._material

    @property
    def thickness(self):
        return self._thickness

    @property
    def int_points(self):
        return self._int_points

    @property
    def offset(self):
        return self._offset

    @property
    def hinges(self):
        return self._hinges

    def __eq__(self, other):
        for key, val in self.__dict__.items():
            if "parent" in key:
                continue
            # Re-evaluate the elset exemption. Maybe this should raise False based on the fem set only?
            # if 'elset' in key:
            #     for m in self.__dict__[key].members:
            #         if m.type != other.__dict__[key].members[0].type:
            #             return False
            if other.__dict__[key] != val:
                return False

        return True

    def __repr__(self):
        return (
            f'FemSection({self.type} - name: "{self.name}", sec: "{self.section}", '
            f'mat: "{self.material}",  elset: "{self.elset}")'
        )


class ConnectorSection(FemBase):
    """
    A Connector Section


    :param name:
    :param elastic_comp:
    :param damping_comp:
    :param plastic_comp:
    :param rigid_dofs:
    :param soft_elastic_dofs:
    """

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
