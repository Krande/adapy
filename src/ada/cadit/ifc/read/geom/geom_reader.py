from typing import TYPE_CHECKING, Union

import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

if TYPE_CHECKING:
    from ada.geom import Geometry

from .solids import (
    extruded_solid_area,
    extruded_solid_area_tapered,
    faceted_brep,
    fixed_reference_swept_area_solid,
    ifc_block,
    ifc_cone,
    ifc_cylinder,
    ifc_rectangular_pyramid,
    ifc_sphere,
    revolved_solid_area,
    sectioned_solid_horizontal,
    swept_disk_solid,
)
from .surfaces import (
    advanced_face,
    closed_shell,
    connected_face_set,
    curve_bounded_plane,
)
from .surfaces import face as read_face
from .surfaces import (
    face_based_surface_model,
    half_space_solid,
    open_shell,
    polygonal_face_set,
    shell_based_surface_model,
    triangulated_face_set,
)

GEOM = Union[geo_so.SOLID_GEOM_TYPES | geo_cu.CURVE_GEOM_TYPES | geo_su.SURFACE_GEOM_TYPES]


def get_product_definitions(prod_def: ifcopenshell.entity_instance) -> list[GEOM]:
    geometries = []
    for representation in prod_def.Representation.Representations:
        if representation.RepresentationIdentifier != "Body":
            continue
        for item in representation.Items:
            geometries.append(import_geometry_from_ifc_geom(item))

    return geometries


def import_geometry_from_ifc_geom(geom_repr: ifcopenshell.entity_instance) -> GEOM:
    if geom_repr.is_a("IfcExtrudedAreaSolidTapered"):
        # Must precede IfcExtrudedAreaSolid — Tapered is a subtype of it.
        return extruded_solid_area_tapered(geom_repr)
    elif geom_repr.is_a("IfcExtrudedAreaSolid"):
        return extruded_solid_area(geom_repr)
    elif geom_repr.is_a("IfcRevolvedAreaSolid"):
        return revolved_solid_area(geom_repr)
    elif geom_repr.is_a("IfcFixedReferenceSweptAreaSolid"):
        return fixed_reference_swept_area_solid(geom_repr)
    elif geom_repr.is_a("IfcSectionedSolidHorizontal"):
        # Subtype of IfcSectionedSolid — a profile swept along an alignment directrix.
        return sectioned_solid_horizontal(geom_repr)
    elif geom_repr.is_a("IfcSweptDiskSolid"):
        # Covers the IfcSweptDiskSolidPolygonal subtype too.
        return swept_disk_solid(geom_repr)
    elif geom_repr.is_a("IfcTriangulatedFaceSet"):
        return triangulated_face_set(geom_repr)
    elif geom_repr.is_a("IfcPolygonalFaceSet"):
        return polygonal_face_set(geom_repr)
    elif geom_repr.is_a("IfcBlock"):
        return ifc_block(geom_repr)
    elif geom_repr.is_a("IfcRectangularPyramid"):
        return ifc_rectangular_pyramid(geom_repr)
    elif geom_repr.is_a("IfcSphere"):
        return ifc_sphere(geom_repr)
    elif geom_repr.is_a("IfcRightCircularCylinder"):
        return ifc_cylinder(geom_repr)
    elif geom_repr.is_a("IfcRightCircularCone"):
        return ifc_cone(geom_repr)
    elif geom_repr.is_a("IfcFacetedBrep") or geom_repr.is_a("IfcFacetedBrepWithVoids"):
        # WithVoids is a sibling of FacetedBrep (both direct IfcManifoldSolidBrep subtypes),
        # so it must be matched explicitly — is_a("IfcFacetedBrep") does not cover it.
        return faceted_brep(geom_repr)
    elif geom_repr.is_a("IfcAdvancedBrep"):
        # IfcManifoldSolidBrep subtype: an outer ClosedShell of IfcAdvancedFaces (analytic /
        # B-spline surfaces). Serialized like any closed shell; the AdvancedFaces carry their
        # surface + trimming bounds. (Voids on IfcAdvancedBrepWithVoids ignored for now.)
        return closed_shell(geom_repr.Outer)
    elif geom_repr.is_a("IfcAdvancedFace"):
        return advanced_face(geom_repr)
    elif geom_repr.is_a("IfcFace"):
        # Plain polygonal face (after IfcAdvancedFace, which is a subtype) — used by
        # faceted-brep closed shells.
        return read_face(geom_repr)
    elif geom_repr.is_a("IfcShellBasedSurfaceModel"):
        return shell_based_surface_model(geom_repr)
    elif geom_repr.is_a("IfcFaceBasedSurfaceModel"):
        return face_based_surface_model(geom_repr)
    elif geom_repr.is_a("IfcClosedShell"):
        # Bare shell as the representation item (write_shapes emits imported
        # ClosedShell/ConnectedFaceSet bodies this way).
        return closed_shell(geom_repr)
    elif geom_repr.is_a("IfcOpenShell"):
        return open_shell(geom_repr)
    elif geom_repr.is_a("IfcConnectedFaceSet"):
        # A bare connected face set (the member type of a face-based surface model). MUST come after
        # its IfcClosedShell / IfcOpenShell subtypes so those keep their specific readers.
        return connected_face_set(geom_repr)
    elif geom_repr.is_a("IfcCurveBoundedPlane"):
        return curve_bounded_plane(geom_repr)
    elif geom_repr.is_a("IfcHalfSpaceSolid"):
        # Covers the IfcPolygonalBoundedHalfSpace subtype too.
        return half_space_solid(geom_repr)
    elif geom_repr.is_a("IfcCsgSolid"):
        # A CSG container — the solid is its tree-root expression (a CSG primitive or a
        # boolean result). adapy has no distinct CsgSolid type; read through to the root.
        return import_geometry_from_ifc_geom(geom_repr.TreeRootExpression)
    elif geom_repr.is_a("IfcBooleanResult"):
        # Covers IfcBooleanClippingResult (a subtype). Returns a wrapped Geometry carrying
        # the cut(s) as bool_operations, not a raw geom — callers handle both (read_shapes).
        return boolean_result(geom_repr)
    elif geom_repr.is_a("IfcMappedItem"):
        # Instanced reuse of a mapped representation, placed by a transform. Unwrap + read the
        # underlying geometry natively (no OCC kernel, which otherwise builds the mapped body).
        return mapped_item(geom_repr)
    elif geom_repr.is_a("IfcCurve"):
        # Curve-only body (Curve3D representation) — e.g. a SAT wire body round-tripped
        # through write_shapes. IfcCurve is the supertype of every concrete curve entity;
        # get_curve dispatches to the matching ada.geom curve.
        from .curves import get_curve

        return get_curve(geom_repr)
    else:
        raise NotImplementedError(f"Geometry type {geom_repr.is_a()} not implemented")


def mapped_item(geom_repr: ifcopenshell.entity_instance) -> GEOM:
    """IfcMappedItem — reuse of a MappingSource representation placed by a MappingTarget transform.

    Unwrap the mapped representation item(s), read them natively (recurse), and apply the
    mapped-item 4x4 (MappingTarget composed with MappingOrigin — via ifcopenshell's pure-Python
    ``get_mappeditem_transformation``, no OCC kernel). A single-item source bakes the transform
    into the geometry when it is rigid, else carries it as a mesh-level ``Geometry.transforms``.
    A *multi-item* mapped representation (e.g. a detailed part exported as several
    IfcPolygonalFaceSets) merges its faceted items into one geometry and carries the shared 4x4 as a
    mesh-level transform. Non-faceted multi-item sources raise NotImplementedError so the caller
    keeps the kernel fallback for that product."""
    import ifcopenshell.util.placement as _placement
    import numpy as np

    from ada.geom import Geometry

    items = list(geom_repr.MappingSource.MappedRepresentation.Items)
    matrix = _placement.get_mappeditem_transformation(geom_repr)

    if len(items) == 1:
        geom = import_geometry_from_ifc_geom(items[0])
        try:
            return _transform_geometry(geom, matrix)
        except NotImplementedError:
            # A non-rigid (scale/shear) transform, or an analytic solid whose parameters can't absorb
            # the 4x4, can't be baked into the geometry. Carry it as a mesh-level world transform
            # instead (Geometry.transforms) — applied to the tessellated mesh, so it renders natively
            # without the OCC kernel fallback. Any geom kind + any affine works this way.
            base = geom if isinstance(geom, Geometry) else Geometry(items[0].id(), geom)
            base.transforms = [np.asarray(matrix, dtype=float)]
            return base

    # Several items under one mapping source: merge the faceted items into a single geometry (one
    # Shape carries one Geometry, so we can't emit them separately) and carry the shared mapped 4x4
    # as a mesh-level transform. This renders natively instead of the OCC kernel building the mapped
    # multi-item body. _merge_face_sets raises NotImplementedError for non-face-set items.
    merged = _merge_face_sets([import_geometry_from_ifc_geom(it) for it in items])
    base = Geometry(geom_repr.id(), merged)
    m = np.asarray(matrix, dtype=float)
    if not np.allclose(m, np.eye(4), atol=1e-12):
        base.transforms = [m]
    return base


def _merge_face_sets(geoms: list) -> GEOM:
    """Merge a list of homogeneous face sets into one, concatenating coordinates and offsetting the
    (1-based) vertex indices. Supports all-PolygonalFaceSet or all-TriangulatedFaceSet inputs; any
    other/mixed content raises NotImplementedError (the multi-item mapped-item caller then keeps the
    kernel fallback)."""
    if geoms and all(isinstance(g, geo_su.PolygonalFaceSet) for g in geoms):
        coordinates: list = []
        faces: list[list[int]] = []
        offset = 0
        closed = True
        for g in geoms:
            faces.extend([[i + offset for i in face] for face in g.faces])
            coordinates.extend(g.coordinates)
            offset += len(g.coordinates)
            closed = closed and g.closed
        return geo_su.PolygonalFaceSet(coordinates=coordinates, faces=faces, closed=closed)
    if geoms and all(isinstance(g, geo_su.TriangulatedFaceSet) for g in geoms):
        coordinates = []
        normals: list = []
        indices: list[int] = []
        offset = 0
        for g in geoms:
            indices.extend([i + offset for i in g.indices])
            coordinates.extend(g.coordinates)
            normals.extend(g.normals)
            offset += len(g.coordinates)
        return geo_su.TriangulatedFaceSet(coordinates=coordinates, normals=normals, indices=indices)
    kinds = ", ".join(sorted({type(g).__name__ for g in geoms})) or "empty"
    raise NotImplementedError(f"multi-item mapped representation with non-mergeable items ({kinds})")


def mapped_instance_group(prod_def: ifcopenshell.entity_instance):
    """If a product's Body representation is several IfcMappedItems that all reuse the SAME mapping
    source (one IfcRepresentationMap instanced N times, e.g. mapped-shape-with-multiple-items), read
    the source geometry ONCE and return a single Geometry carrying every instance's 4x4 in
    ``transforms`` — rendered natively via mesh-level instancing instead of the multi-item kernel
    fallback. Returns None when the pattern doesn't hold (mixed item kinds, differing sources, or a
    source that isn't itself a single item)."""
    import ifcopenshell.util.placement as _placement
    import numpy as np

    from ada.geom import Geometry

    items = []
    for rep in prod_def.Representation.Representations:
        if rep.RepresentationIdentifier == "Body":
            items.extend(rep.Items)
    if len(items) < 2 or not all(i.is_a("IfcMappedItem") for i in items):
        return None
    src = items[0].MappingSource.MappedRepresentation
    if not all(i.MappingSource.MappedRepresentation == src for i in items):
        return None
    src_items = list(src.Items)
    if len(src_items) != 1:
        return None
    base = import_geometry_from_ifc_geom(src_items[0])
    base = base if isinstance(base, Geometry) else Geometry(src_items[0].id(), base)
    base.transforms = [np.asarray(_placement.get_mappeditem_transformation(i), dtype=float) for i in items]
    return base


def _transform_curve(curve, xf, r_mat, scale):
    """Apply a rigid (optionally uniformly-scaled) 4x4 to a curve by transforming its vertices —
    Edge / ArcLine / IndexedPolyCurve / PolyLine (the swept-solid directrix forms)."""
    from ada.geom import curves as geo_cu

    if isinstance(curve, geo_cu.Edge):
        return geo_cu.Edge(xf(curve.start), xf(curve.end))
    if isinstance(curve, geo_cu.ArcLine):
        return geo_cu.ArcLine(xf(curve.start), xf(curve.midpoint), xf(curve.end))
    if isinstance(curve, geo_cu.PolyLine):
        return geo_cu.PolyLine(points=[xf(p) for p in curve.points])
    if isinstance(curve, geo_cu.IndexedPolyCurve):
        return geo_cu.IndexedPolyCurve(
            [_transform_curve(s, xf, r_mat, scale) for s in curve.segments], curve.self_intersect
        )
    raise NotImplementedError(f"mapped-item transform of directrix {type(curve).__name__}")


def _transform_geometry(geom: GEOM, matrix) -> GEOM:
    """Apply a mapped-item 4x4 to a native geometry. Identity returns the geometry unchanged
    (the common case — the mapped body sits at the source origin and the product's ObjectPlacement
    does the placing). Otherwise the transform must be rigid (optionally uniform scale): point-set
    geometries transform their coordinates, an IfcSweptDiskSolid transforms its directrix + scales
    its radius. Non-rigid transforms and unsupported analytic types raise NotImplementedError."""
    import numpy as np

    from ada.geom import solids as geo_so
    from ada.geom import surfaces as geo_su
    from ada.geom.direction import Direction
    from ada.geom.points import Point

    m = np.asarray(matrix, dtype=float)
    if np.allclose(m, np.eye(4), atol=1e-12):
        return geom

    rot = m[:3, :3]
    rtr = rot.T @ rot
    s2 = float(np.trace(rtr)) / 3.0
    if s2 <= 0 or not np.allclose(rtr, s2 * np.eye(3), atol=1e-6):
        raise NotImplementedError("non-rigid (shear / non-uniform scale) mapped-item transform")
    scale = float(np.sqrt(s2))

    def xf(p) -> Point:
        q = m @ np.array([float(p[0]), float(p[1]), float(p[2]), 1.0])
        return Point(q[0], q[1], q[2])

    if isinstance(geom, geo_su.PolygonalFaceSet):
        return geo_su.PolygonalFaceSet(
            coordinates=[xf(p) for p in geom.coordinates], faces=geom.faces, closed=geom.closed
        )
    if isinstance(geom, geo_su.TriangulatedFaceSet):
        rn = rot / scale  # pure rotation for the normals
        normals = [Direction(*(rn @ np.asarray([n[0], n[1], n[2]], float))) for n in geom.normals]
        return geo_su.TriangulatedFaceSet(
            coordinates=[xf(p) for p in geom.coordinates], normals=normals, indices=geom.indices
        )
    if isinstance(geom, geo_so.SweptDiskSolid):
        return geo_so.SweptDiskSolid(
            directrix=_transform_curve(geom.directrix, xf, rot / scale, scale),
            radius=geom.radius * scale,
            inner_radius=(geom.inner_radius * scale if geom.inner_radius else None),
            start_param=geom.start_param,
            end_param=geom.end_param,
        )
    raise NotImplementedError(f"mapped-item transform of {type(geom).__name__} (kernel fallback)")


def boolean_result(geom_repr: ifcopenshell.entity_instance) -> "Geometry":
    """Read an IfcBooleanResult/IfcBooleanClippingResult into a base Geometry with the cut
    operand(s) attached as bool_operations (applied downstream by apply_geom_booleans).

    Nested booleans (FirstOperand is itself a boolean — adapy stacks each clip as its own
    result) collapse onto a single base Geometry: the recursive call returns a Geometry and
    we append this level's operation to it."""
    from ada.geom import Geometry
    from ada.geom.booleans import BooleanOperation, BoolOpEnum

    first = import_geometry_from_ifc_geom(geom_repr.FirstOperand)
    second = import_geometry_from_ifc_geom(geom_repr.SecondOperand)

    base = first if isinstance(first, Geometry) else Geometry(geom_repr.FirstOperand.id(), first)
    operand = second if isinstance(second, Geometry) else Geometry(geom_repr.SecondOperand.id(), second)
    base.bool_operations.append(BooleanOperation(operand, BoolOpEnum.from_str(geom_repr.Operator)))
    return base
