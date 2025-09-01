from __future__ import annotations

from ada import PrimSweep
from ada.cadit.ifc.write.geom import curves as write_cu
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.cadit.ifc.write.geom.surfaces import arbitrary_profile_def
from ada.core.utils import to_real
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su


def generate_ifc_prim_sweep_geom(shape: PrimSweep, f):
    geom = shape.solid_geom_2d_profile()
    if isinstance(geom.geometry.swept_area, geo_su.ArbitraryProfileDef):
        profile = arbitrary_profile_def(geom.geometry.swept_area, f)
    else:
        raise NotImplementedError(f"Not implemented {type(geom.geometry.swept_area).__name__}")

    if isinstance(geom.geometry, geo_so.FixedReferenceSweptAreaSolid):
        if isinstance(geom.geometry.directrix, geo_cu.IndexedPolyCurve):
            sweep_curve = write_cu.indexed_poly_curve(geom.geometry.directrix, f)
        elif isinstance(geom.geometry.directrix, geo_cu.Edge):
            curve = geo_cu.IndexedPolyCurve(segments=[geom.geometry.directrix])
            sweep_curve = write_cu.indexed_poly_curve(curve, f)
        else:
            raise NotImplementedError(f"Unsupported curve type {type(geom.geometry.directrix)}")
    else:
        raise NotImplementedError(f"Unsupported curve type {type(geom.geometry.sweep_curve)}")

    # FixedReference must lie in the plane defined by Position and fixes the Y-axis of the profile
    # Use the profile's local Y direction (as encoded in the Position) for FixedReference
    local_x_dir = shape.profile_curve_outer.xdir
    fixed_ref_dir = to_real(local_x_dir)
    fixed_ref = f.create_entity("IfcDirection", fixed_ref_dir)

    position = geom.geometry.position
    ifc_axis3d = ifc_placement_from_axis3d(position, f)

    if shape.derived_reference:
        sweep_type = "IfcDirectrixDerivedReferenceSweptAreaSolid"
    else:
        sweep_type = "IfcFixedReferenceSweptAreaSolid"

    solid = f.create_entity(
        sweep_type,
        SweptArea=profile,
        Position=ifc_axis3d,
        Directrix=sweep_curve,
        FixedReference=fixed_ref,
    )

    return solid
