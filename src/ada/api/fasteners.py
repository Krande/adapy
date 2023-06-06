from __future__ import annotations

from typing import TYPE_CHECKING

from ada.base.physical_objects import BackendGeom
from ada.base.types import BaseEnum
from ada.core.vector_utils import unit_vector

if TYPE_CHECKING:
    from ada import Beam, Node, Plate, PrimExtrude


class Bolts(BackendGeom):
    """

    TODO: Create a bolt class based on the IfcMechanicalFastener concept.

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcmechanicalfastener.htm

    Which in turn should likely be inside another element components class

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcelementcomponent.htm

    """

    def __init__(self, name, p1, p2, normal, members, parent=None):
        super(Bolts, self).__init__(name, parent=parent)


class WeldProfileEnum(BaseEnum):
    V = "V"


class Weld(BackendGeom):
    def __init__(
        self,
        name,
        p1,
        p2,
        weld_type: WeldProfileEnum | str,
        members,
        profile: list[tuple],
        xdir: tuple,
        groove: list[tuple] = None,
        parent=None,
    ):
        super(Weld, self).__init__(name, parent=parent)
        from ada import Node, PrimExtrude

        p1 = Node(p1) if isinstance(p1, Node) is False else p1
        p2 = Node(p2) if isinstance(p2, Node) is False else p2
        vec = unit_vector(p2.p - p1.p)

        if isinstance(weld_type, str):
            weld_type = WeldProfileEnum.from_str(weld_type)

        if isinstance(profile, list):
            geom = PrimExtrude.from_2points_and_curve(f"{self.name}_geom", p1.p, p2.p, profile, xdir)
            geom.parent = self
        else:
            raise NotImplementedError()

        if groove is not None:
            p_start = p1.p - p1.p * vec * 0.02
            p_end = p2.p + p2.p * vec * 0.02
            groove = PrimExtrude.from_2points_and_curve(f"{self.name}_groove", p_start, p_end, groove, xdir)
            groove.parent = self

        self._xdir = xdir
        self._geom = geom
        self._groove = groove
        self._p1 = p1
        self._p2 = p2
        self._members = members
        self._weld_type = weld_type

    @property
    def type(self) -> WeldProfileEnum:
        return self._weld_type

    @property
    def p1(self) -> Node:
        return self._p1

    @property
    def p2(self) -> Node:
        return self._p2

    @property
    def members(self) -> list[Plate | Beam]:
        return self._members

    @property
    def geometry(self) -> PrimExtrude:
        return self._geom

    @property
    def groove(self) -> PrimExtrude:
        return self._groove
