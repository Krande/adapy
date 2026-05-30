from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Literal, TypeAlias, Union

import numpy as np

from ada.api.bounding_box import BoundingBox
from ada.api.curves import CurvePoly2d
from ada.api.nodes import Node
from ada.base.physical_objects import BackendGeom
from ada.base.units import Units
from ada.config import Config
from ada.core.vector_utils import poly2d_center_of_gravity
from ada.geom import Geometry
from ada.geom.direction import Direction
from ada.geom.points import Point
from ada.geom.solids import ExtrudedAreaSolid
from ada.materials import Material
from ada.materials.metals import CarbonSteel

if TYPE_CHECKING:
    from OCC.Core.TopoDS import TopoDS_Shape

    from ada import Placement
    from ada.cad import ShapeHandle

_NTYPE: TypeAlias = Union[int, float]
# Define coordinate types
Coordinate: TypeAlias = tuple[_NTYPE, _NTYPE]
CoordinateSequence: TypeAlias = (
    list[Coordinate]
    | list[list[Coordinate]]
    | list[tuple[Coordinate, ...]]
    | tuple[Coordinate, ...]
    | tuple[list[Coordinate], ...]
)


class Plate(BackendGeom):
    """
    A plate object. The plate element covers all plate elements.

    Contains a dictionary with each point of the plate
    described by an id (index) and a Node object.

    :param name: Name of plate
    :param points: List of 2D point coordinates (or a PolyCurve) that make up the plate. Each point is (x, y, optional [radius])
    :param t: Thickness of plate
    :param mat: Material. Can be either Material object or built-in materials ('S420' or 'S355')
    :param origin: Explicitly define origin of plate. If not set
    :param xdir: Explicitly define x direction of plate. If not set
    :param normal: Explicitly define normal direction of plate. If not set
    """

    def __init__(
        self,
        name: str,
        points: CurvePoly2d | CoordinateSequence,
        t: float,
        mat: str | Material = "S420",
        origin: Iterable | Point = None,
        xdir: Iterable | Direction = None,
        normal: Iterable | Direction = None,
        orientation: Placement = None,
        pl_id=None,
        tol=None,
        **kwargs,
    ):
        super().__init__(name, **kwargs)
        self._pl_id = pl_id
        self._material = mat if isinstance(mat, Material) else Material(mat, mat_model=CarbonSteel(mat), parent=self)
        self._material.refs.append(self)
        self._t = t
        self._hash = None

        if tol is None:
            tol = Units.get_general_point_tol(self.units)

        if isinstance(points, CurvePoly2d):
            self._poly = points
        else:
            self._poly = CurvePoly2d(
                points2d=points,
                normal=normal,
                origin=origin,
                xdir=xdir,
                tol=tol,
                parent=self,
                orientation=orientation,
            )

        self._bbox = None

    @staticmethod
    def from_3d_points(
        name, points, t, mat="S420", xdir=None, color=None, metadata=None, flip_normal=False, **kwargs
    ) -> Plate:
        poly = CurvePoly2d.from_3d_points(points, xdir=xdir, flip_n=flip_normal, **kwargs)
        return Plate(name, poly, t, mat=mat, color=color, metadata=metadata, **kwargs)

    @staticmethod
    def from_extruded_area_solid(name, solid: ExtrudedAreaSolid): ...

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(self.guid)
        return self._hash

    def __eq__(self, other: Plate) -> bool:
        if self is other:
            return True
        if not isinstance(other, Plate):
            return NotImplemented
        return self._guid == other._guid

    def bbox(self) -> BoundingBox:
        """Bounding Box of plate"""
        if self._bbox is None:
            self._bbox = BoundingBox(self)

        return self._bbox

    def line_occ(self):
        return self._poly.occ_wire()

    def shell_occ(self):
        from ada.occ.geom.cache import get_shell_occ

        return get_shell_occ(self)

    def solid_occ(self) -> ShapeHandle:
        from ada.occ.geom.cache import get_solid_occ

        return get_solid_occ(self)

    def shell_geom(self) -> Geometry:
        import ada.geom.surfaces as geo_su
        from ada.geom.booleans import BooleanOperation
        from ada.geom.placement import Axis2Placement3D

        outer_curve = self.poly.curve_geom()
        place = Axis2Placement3D(self.poly.orientation.origin, axis=self.poly.normal, ref_direction=self.poly.xdir)
        face = geo_su.CurveBoundedPlane(geo_su.Plane(place), outer_curve, inner_boundaries=[])

        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, face, self.color, bool_operations=booleans)

    def solid_geom(self) -> Geometry:
        import numpy as np

        import ada.geom.solids as geo_so
        import ada.geom.surfaces as geo_su
        from ada import Placement
        from ada.geom.booleans import BooleanOperation
        from ada.geom.placement import Axis2Placement3D

        outer_curve = self.poly.curve_geom(use_3d_segments=False)
        profile = geo_su.ArbitraryProfileDef(geo_su.ProfileType.AREA, outer_curve, [])
        origin = self.poly.origin
        normal = self.poly.normal
        xdir = self.poly.xdir

        if not self.placement.is_identity():
            ident_place = Placement()
            place_abs = self.placement.get_absolute_placement(include_rotations=True)
            place_abs_rot_mat = place_abs.rot_matrix
            ident_rot_mat = ident_place.rot_matrix
            if not np.allclose(place_abs_rot_mat, ident_rot_mat):
                new_vectors = place_abs.transform_array_from_other_place(
                    np.asarray([normal, xdir]), ident_place, ignore_translation=True
                )
                new_normal = new_vectors[0]
                if Direction(new_normal).get_length() != 0.0:
                    normal = new_normal
                xdir = new_vectors[1]

            origin = place_abs.origin + origin

        # Origin location is already included in the outer_curve definition
        place = Axis2Placement3D(location=origin, axis=normal, ref_direction=xdir)
        solid = geo_so.ExtrudedAreaSolid(profile, place, self.t, Direction(0, 0, 1))
        booleans = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in self.booleans]
        return Geometry(self.guid, solid, self.color, bool_operations=booleans)

    def copy_to(self, name: str = None, origin=None, xdir=None, n=None):
        import copy

        if name is None:
            name = self.name

        if origin is None:
            origin = self.placement.origin

        return Plate(name, self.poly.copy_to(origin, xdir, n), copy.copy(self.t), self.material.copy_to())

    def get_cog(self) -> Point:
        """
        Plate centroid in global coordinates.

        Convention:
        - poly.points2d are expressed in the plate's 2D local system (X,Y).
        - poly.origin is the local 3D origin of that 2D system.
        - poly.xdir defines local X direction in 3D.
        - poly.normal defines local Z (plane normal) in 3D.
        - local Y is constructed as (normal × xdir) to enforce right-hand rule.

        If plate has a non-identity placement, we:
        - rotate xdir and normal by placement rotation
        - translate origin by placement translation (origin = place_abs.origin + poly.origin)
        """
        from ada import Placement

        # 2D centroid
        c2 = poly2d_center_of_gravity(np.asarray(self.poly.points2d, dtype=float))
        if c2 is None:
            # degenerate polygon
            return Point(self.poly.origin.copy(), units=self.units)

        cx, cy = float(c2[0]), float(c2[1])

        # Start from poly basis
        origin = np.asarray(self.poly.origin, dtype=float)
        xdir = np.asarray(self.poly.xdir, dtype=float)
        normal = np.asarray(self.poly.normal, dtype=float)

        # Apply plate placement the same way as solid_geom
        if self.placement is not None:
            ident = Placement()
            place_abs = self.placement.get_absolute_placement(include_rotations=True)

            # rotate basis vectors (ignore translation)
            new_vectors = place_abs.transform_array_from_other_place(
                np.asarray([normal, xdir], dtype=float), ident, ignore_translation=True
            )
            normal = new_vectors[0]
            xdir = new_vectors[1]

            # translate origin (do NOT rotate origin here; you don't in solid_geom either)
            origin = np.asarray(place_abs.origin, dtype=float) + origin

        # Enforce right-handed in-plane y
        # y = z × x
        ydir = np.cross(normal, xdir)
        yn = np.linalg.norm(ydir)
        if yn == 0.0:
            raise ValueError(f"Plate '{self.name}': invalid basis, normal and xdir are parallel.")
        ydir = ydir / yn

        # Normalize xdir too (safe)
        xn = np.linalg.norm(xdir)
        if xn == 0.0:
            raise ValueError(f"Plate '{self.name}': xdir has zero length.")
        xdir = xdir / xn

        cog = origin + cx * xdir + cy * ydir
        return Point(cog)

    def get_volume(self) -> float:
        return self.t * self.poly.get_area()

    def get_mass(self) -> float:
        return self.get_volume() * self.material.model.rho

    @property
    def id(self):
        return self._pl_id

    @id.setter
    def id(self, value):
        self._pl_id = value

    @property
    def t(self) -> float:
        """Plate thickness"""
        return self._t

    @t.setter
    def t(self, value: float):
        self._t = value

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value: Material):
        self._material = value

    @property
    def normal(self) -> Direction:
        """Normal vector"""
        return self.poly.normal

    @property
    def nodes(self) -> list[Node]:
        return self.poly.nodes

    @property
    def poly(self) -> CurvePoly2d:
        return self._poly

    @property
    def units(self) -> Units:
        return self._units

    @units.setter
    def units(self, value: Units | Literal["mm", "m"]):
        if isinstance(value, str):
            value = Units.from_str(value)
        if self._units != value:
            scale_factor = Units.get_scale_factor(self._units, value)
            tol = Config().general_mmtol if value == "mm" else Config().general_mtol
            self._t *= scale_factor
            self.poly.scale(scale_factor, tol)
            for pen in self.booleans:
                pen.units = value
            self.material.units = value
            self._units = value
            # Todo: incorporate change_type
            # self.change_type = ChangeAction.MODIFIED

    def __repr__(self):
        pts = [
            list(x) + [self.poly.radiis.get(i)] if i in self.poly.radiis.keys() else list(x)
            for i, x in enumerate(self.poly.points2d)
        ]
        origin = f"origin={self.poly.origin.tolist()}"
        xdir = f"xdir={self.poly.xdir.tolist()}"
        normal = f"normal={self.poly.normal.tolist()}"
        return f'{self.__class__.__name__}("{self.name}", {pts}, t={self.t}, mat="{self.material.name}", {origin}, {xdir}, {normal})'


class PlateCurved(BackendGeom):
    """Plate built on a non-planar face (typically a B-spline patch).

    Used by readers that surface a curved surface — the gxml importer
    for advanced SAT faces, and the loft tool for ruled corner-
    transition surfaces between sharp and rounded profiles. Carries
    the underlying :class:`~ada.geom.Geometry` directly; rendering
    paths convert it via :func:`ada.occ.geom.geom_to_occ_geom` and the
    GLB tessellator's PlateCurved branch.

    Quacks like :class:`Plate` for the parts of the Part-attachment
    contract that ``add_plate`` exercises: exposes ``nodes`` (derived
    from the face's outer wire), accepts a same-value ``units``
    re-assignment, and inherits ``change_type`` from
    :class:`Root`. Cross-unit conversion isn't implemented yet — set
    the right units before constructing the plate.
    """

    def __init__(self, name, face_geom: Geometry, t: float, mat: str | Material = "S420", **kwargs):
        super().__init__(name, **kwargs)
        self._geom = face_geom
        self._material = mat if isinstance(mat, Material) else Material(mat, mat_model=CarbonSteel(mat), parent=self)
        self._material.refs.append(self)
        self._t = t
        self._nodes_cache: list[Node] | None = None
        self._bbox = None
        self._hash = None
        # Optional raw-OCC-face override; populated by
        # :meth:`from_occ_face` when the plate was constructed straight
        # from a TopoDS_Face (e.g. the loft tool's corner-transition
        # B-spline surfaces). When set, the OCC-bound methods bypass
        # the Geometry → OCC conversion entirely.
        self._occ_face_override = None

    @classmethod
    def from_occ_face(cls, name: str, occ_face, t: float, mat: str | Material = "S420", **kwargs) -> "PlateCurved":
        """Construct a PlateCurved from a raw OCC ``TopoDS_Face``.

        Bypasses the :class:`~ada.geom.Geometry` →
        :class:`~ada.geom.surfaces.AdvancedFace` round-trip that the
        regular ``__init__`` path relies on. The loft tool uses this
        when it already has the OCC face from
        ``BRepOffsetAPI_ThruSections`` — going via AdvancedFace would
        only re-decode the same surface back into OCC, and the
        ``occ_face_to_ada_face`` → ``make_face_from_geom`` round-trip
        currently has a bounds-structure mismatch (the STEP reader
        emits raw curve types as ``AdvancedFace.bounds`` while the
        OCC builder expects ``FaceBound`` wrappers around
        ``EdgeLoop``s).

        Behaviour: ``solid_occ`` returns the wrapped face directly;
        ``extruded_solid_occ`` extrudes it along its normal; ``nodes``
        walks the face's outer wire. The ``geom`` / ``solid_geom``
        accessors return ``None`` — callers that need an adapy
        ``Geometry`` must use the ``__init__`` constructor instead.
        """
        instance = cls.__new__(cls)
        BackendGeom.__init__(instance, name, **kwargs)
        instance._geom = None
        instance._material = (
            mat if isinstance(mat, Material)
            else Material(mat, mat_model=CarbonSteel(mat), parent=instance)
        )
        instance._material.refs.append(instance)
        instance._t = t
        instance._nodes_cache = None
        instance._bbox = None
        instance._hash = None
        instance._occ_face_override = occ_face
        return instance

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(self.guid)
        return self._hash

    def __eq__(self, other) -> bool:
        if self is other:
            return True
        if not isinstance(other, PlateCurved):
            return NotImplemented
        return self.guid == other.guid

    @property
    def t(self) -> float:
        return self._t

    @property
    def material(self) -> Material:
        return self._material

    @material.setter
    def material(self, value: Material):
        self._material = value

    @property
    def geom(self) -> Geometry:
        return self._geom

    @property
    def units(self) -> Units:
        return self._units

    @units.setter
    def units(self, value: Units | str):
        # Cross-unit scaling on the wrapped AdvancedFace / BSpline data
        # isn't implemented (would need to rescale every control point
        # in the geometry tree); for now accept a no-op same-value
        # assignment so ``Part.add_plate`` works, and reject any actual
        # unit change with a clear message.
        if isinstance(value, str):
            value = Units.from_str(value)
        if value == self._units:
            return
        raise NotImplementedError(
            f"PlateCurved {self.name!r}: cross-unit conversion not "
            f"implemented ({self._units!r} -> {value!r}). Construct "
            f"the plate in the target units instead."
        )

    @property
    def nodes(self) -> list[Node]:
        """Boundary nodes from the outer wire of the wrapped face.

        ``Part.add_plate`` registers these into the parent Part's
        node container so the curved plate participates in node-based
        lookups (selection, FEM mesh anchors) the same way a planar
        ``Plate.nodes`` would. Cached on first access; the underlying
        geometry isn't expected to mutate post-construction.

        Falls back to an empty list when the geometry can't be
        converted to an OCC face — the gxml importer flags some
        advanced faces with a flat-fallback path, and we'd rather
        let the plate attach with zero boundary nodes than blow up
        the caller.
        """
        if self._nodes_cache is not None:
            return self._nodes_cache
        try:
            from ada.occ.plate_curved import boundary_nodes_of_face, boundary_nodes_of

            if self._occ_face_override is not None:
                nodes = boundary_nodes_of_face(self._occ_face_override)
            else:
                nodes = boundary_nodes_of(self.solid_geom())
        except Exception:
            nodes = []
        self._nodes_cache = nodes
        return nodes

    def bbox(self) -> BoundingBox:
        if self._bbox is None:
            self._bbox = BoundingBox(self)
        return self._bbox

    def solid_geom(self) -> Geometry:
        return self.geom

    def solid_occ(self) -> ShapeHandle:
        if self._occ_face_override is not None:
            return self._occ_face_override
        from ada.occ.geom import geom_to_occ_geom

        return geom_to_occ_geom(self.solid_geom())

    def extruded_solid_occ(self) -> TopoDS_Shape:
        """Prism-extrude the curved face by ``t`` along its normal so
        the rendered plate carries thickness like a planar
        ``Plate.from_3d_points`` does.

        Returns a ``TopoDS_Shape`` (Solid) ready for the tessellator's
        raw-OCC fast path. Falls back to the bare face shape on any
        prism failure so the caller still gets *something* to render.
        """
        try:
            from ada.occ.plate_curved import (
                extrude_face_along_normal,
                extrude_face_geom_along_normal,
            )

            if self._occ_face_override is not None:
                return extrude_face_along_normal(self._occ_face_override, self.t)
            return extrude_face_geom_along_normal(self.solid_geom(), self.t)
        except Exception:
            # Conversion failures (gxml flat-fallback faces, malformed
            # control-net data) — defer to ``solid_occ`` so the caller
            # at least sees the underlying face.
            return self.solid_occ()


class Surface(Plate):
    """Planar surface — :class:`Plate` without thickness.

    Same geometry contract as Plate (planar polygon bounded by a
    ``CurvePoly2d``) but rendered as a 2D face rather than an
    extruded prism. Useful for visualisation-only output or for
    pipelines that supply thickness separately (FEM shell elements
    where the thickness lives on the section, not the geometry).

    Subclasses Plate so every Plate-dispatching consumer (the GLB
    tessellator, IFC writer, ``Part.add_plate``, BoundingBox) picks
    it up automatically. ``solid_occ`` is overridden to return the
    planar face shape instead of attempting a zero-thickness prism
    extrusion (which would otherwise crash in
    ``BRepPrimAPI_MakePrism``).
    """

    def __init__(
        self,
        name: str,
        points: CurvePoly2d | CoordinateSequence,
        mat: str | Material = "S420",
        origin: Iterable | Point = None,
        xdir: Iterable | Direction = None,
        normal: Iterable | Direction = None,
        orientation: Placement = None,
        pl_id=None,
        tol=None,
        **kwargs,
    ):
        super().__init__(
            name, points, t=0.0, mat=mat, origin=origin, xdir=xdir,
            normal=normal, orientation=orientation, pl_id=pl_id, tol=tol,
            **kwargs,
        )

    @staticmethod
    def from_3d_points(
        name, points, mat="S420", xdir=None, color=None, metadata=None, flip_normal=False, **kwargs
    ) -> Surface:
        poly = CurvePoly2d.from_3d_points(points, xdir=xdir, flip_n=flip_normal, **kwargs)
        return Surface(name, poly, mat=mat, color=color, metadata=metadata, **kwargs)

    def solid_occ(self) -> ShapeHandle:
        # Override the extrusion-based path on Plate; a Surface has no
        # thickness so the "solid" representation is simply the
        # bounded face. Reuses the existing shell builder.
        return self.shell_occ()

    def solid_geom(self) -> Geometry:
        # Same idea for the geometry side: emit the bounded planar
        # face (CurveBoundedPlane) rather than an ExtrudedAreaSolid
        # with zero depth.
        return self.shell_geom()


class SurfaceCurved(PlateCurved):
    """Non-planar surface — :class:`PlateCurved` without thickness.

    Same underlying B-spline / advanced face data as PlateCurved but
    rendered as a 2D face. The PlateCurved render path already
    short-circuits to the bare face when ``t == 0`` (in
    ``extruded_solid_occ``), so subclassing with a forced zero
    thickness is the entire change.
    """

    def __init__(
        self, name: str, face_geom: Geometry,
        mat: str | Material = "S420", **kwargs,
    ):
        super().__init__(name, face_geom, t=0.0, mat=mat, **kwargs)

    @classmethod
    def from_occ_face(cls, name: str, occ_face, mat: str | Material = "S420", **kwargs) -> SurfaceCurved:
        """Construct a thickness-less curved surface from a raw OCC face.

        Mirrors :meth:`PlateCurved.from_occ_face` but pins thickness to
        zero so downstream rendering emits the bare face.
        """
        return super().from_occ_face(name, occ_face, t=0.0, mat=mat, **kwargs)
