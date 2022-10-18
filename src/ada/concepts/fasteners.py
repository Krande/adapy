from __future__ import annotations

from typing import TYPE_CHECKING

from ada.base.physical_objects import BackendGeom
from ada.base.types import BaseEnum
from ada.sections import Section

if TYPE_CHECKING:
    from ada import Node


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


class WeldVProfile(Section):
    def __init__(self, name, points, origin, normal, xdir):
        from ada import CurvePoly

        poly = CurvePoly(points2d=points, origin=origin, normal=normal, xdir=xdir)
        super(WeldVProfile, self).__init__(name=name, sec_type=Section.TYPES.POLY, outer_poly=poly)


class Weld(BackendGeom):
    def __init__(self, name, p1, p2, members, profile: Section | WeldProfileEnum | str, normal=None, parent=None):
        super(Weld, self).__init__(name, parent=parent)
        from ada import Node

        p1 = Node(p1) if isinstance(p1, Node) is False else p1
        p2 = Node(p2) if isinstance(p2, Node) is False else p2
        self._weld_line = (p1, p2)

        if isinstance(profile, str):
            profile = WeldProfileEnum.from_str(profile)
        if isinstance(profile, Section):
            section = profile
        else:
            if profile == WeldProfileEnum.V:
                section = WeldVProfile()
            else:
                raise NotImplementedError()

        self._members = members
        self._section = section
        section.parent = self
        self._normal = normal

    @property
    def points(self) -> tuple[Node, Node]:
        return self._weld_line

    @property
    def section(self) -> Section:
        return self._section

    @section.setter
    def section(self, value):
        self._section = value
