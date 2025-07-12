from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Literal

from ada.api.bounding_box import BoundingBox
from ada.api.primitives.base import Shape
from ada.api.transforms import Placement
from ada.base.units import Units
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
from ada.geom.direction import Direction
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.geom.solids import Box, ExtrudedAreaSolid
    from ada.materials.concept import Material


class PrimBox(Shape):
    """Primitive Box. Length, width & height are local x, y and z respectively"""

    def __init__(
        self, name, p1, p2, origin=None, placement=None, material: Material | Literal["S355", "S420"] = None, **kwargs
    ):
        self.p1 = p1 if isinstance(p1, Point) else Point(*p1)
        self.p2 = p2 if isinstance(p2, Point) else Point(*p2)

        if origin is not None:
            if placement is None:
                placement = Placement(origin=origin)
            else:
                placement.origin = origin

        super(PrimBox, self).__init__(name=name, placement=placement, material=material, **kwargs)
        self._bbox = BoundingBox(self)

    def solid_occ(self):
        from ada.occ.geom.cache import get_solid_occ

        return get_solid_occ(self)

    def solid_geom(self) -> Geometry:
        from ada.geom.solids import Box

        p1, p2 = self.p1.copy(), self.p2.copy()
        if not self.placement.is_identity():
            abs_place = self.placement.get_absolute_placement()
            if abs_place.origin is not None:
                p1 += abs_place.origin
                p2 += abs_place.origin
        box = Box.from_2points(p1, p2)
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, box, self.color, bool_operations=booleans)

    def get_bottom_points(self):
        p11 = self.p1 + self.placement.origin
        p2 = self.p2 + self.placement.origin
        # get bottom 4 points
        p12 = Point(p2.x, p11.y, p11.z)
        p21 = Point(p11.x, p2.y, p11.z)
        p22 = Point(p2.x, p2.y, p11.z)
        return [p11, p12, p21, p22]

    @staticmethod
    def from_p_and_dims(name, p, length, width, height, **kwargs):
        p1 = p
        p2 = [p[0] + length, p[1] + width, p[2] + height]
        return PrimBox(name, p1, p2, **kwargs)

    @staticmethod
    def from_box_geom(name, box_geom: Box, **kwargs):
        p1 = box_geom.position.location
        p2 = p1 + Direction(box_geom.x_length, box_geom.y_length, box_geom.z_length)

        return PrimBox(name, p1, p2, **kwargs)

    @staticmethod
    def from_extruded_rect_profile(name, extrusion: ExtrudedAreaSolid, **kwargs):
        from ada.geom.surfaces import RectangleProfileDef

        if not isinstance(extrusion.swept_area, RectangleProfileDef):
            raise ValueError(f"Only RectangleProfileDef is supported for extrusion, got {extrusion.swept_area}")

        rect_profile = extrusion.swept_area
        p1 = extrusion.position.location
        p2 = [rect_profile.p2[0], rect_profile.p2[1], rect_profile.p2[2] + extrusion.depth]
        return PrimBox(name, p1, p2, **kwargs)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)
            self.p1 = Point(*[x * scale_factor for x in self.p1])
            self.p2 = Point(*[x * scale_factor for x in self.p2])
            self._units = value

    def copy_to(
        self,
        name: str = None,
        position: list[float] | Point = None,
        rotation_axis: Iterable[float] = None,
        rotation_angle: float = None,
    ) -> PrimBox:
        """Copy the box to a new position and/or rotation."""
        if name is None:
            name = self.name

        copy_box = PrimBox(
            name=name,
            p1=self.p1.copy(),
            p2=self.p2.copy(),
            color=self.color,
            mass=self.mass,
            cog=self.cog,
            material=self.material.copy_to() if hasattr(self.material, "copy_to") else self.material,
            units=self.units,
            metadata=self.metadata,
            placement=self.placement.copy_to(),
        )
        if position is not None:
            if not isinstance(position, Point):
                position = Point(*position)

            copy_box.placement.origin = position

        if rotation_axis is not None and rotation_angle is not None:
            copy_box.placement = copy_box.placement.rotate(rotation_axis, rotation_angle)

        return copy_box

    def __repr__(self):
        p1s = self.p1.tolist()
        p2s = self.p2.tolist()
        return f'PrimBox("{self.name}", {p1s}, {p2s})'
