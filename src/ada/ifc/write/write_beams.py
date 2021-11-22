from ada import Beam
from ada.config import Settings
from ada.core.constants import O, X, Y
from ada.core.vector_utils import transform3d
from ada.ifc.utils import (
    add_colour,
    add_multiple_props_to_elem,
    convert_bm_jusl_to_ifc,
    create_guid,
    create_ifc_placement,
    create_IfcFixedReferenceSweptAreaSolid,
    create_local_placement,
)


def write_ifc_beam(beam: Beam):
    if beam.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = beam.parent.get_assembly()
    f = a.ifc_file

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = a.user.to_ifc()
    parent = beam.parent.get_ifc_elem()

    e1 = (0.0, 0.0, 0.0)
    e2 = (0.0, 0.0, 0.0)

    if Settings.include_ecc and beam.e1 is not None:
        e1 = beam.e1

    if Settings.include_ecc and beam.e2 is not None:
        e2 = beam.e2

    def to_real(v):
        return v.astype(float).tolist()

    xvec, yvec, _ = to_real(beam.xvec), to_real(beam.yvec), to_real(beam.up)
    beam_type = beam.section.ifc_beam_type
    profile = beam.section.ifc_profile

    profile_e = None
    if beam.section != beam.taper:
        profile_e = beam.taper.ifc_profile

    global_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)
    extrude_dir = f.create_entity("IfcDirection", (0.0, 0.0, 1.0))

    if beam.curve is not None:
        ifc_polyline = beam.curve.get_ifc_elem()
        loc_plac = create_ifc_placement(f)
        extrude_area_solid = create_IfcFixedReferenceSweptAreaSolid(
            f, ifc_polyline, profile, global_placement, 0.0, 1.0, extrude_dir
        )
    else:
        # Transform coordinates to local coords
        p1_global = tuple([float(x) + float(e1[i]) for i, x in enumerate(beam.n1.p)])
        p2_global = tuple([float(x) + float(e2[i]) for i, x in enumerate(beam.n2.p)])

        p1, p2 = transform3d([xvec, yvec], [X, Y], p1_global, [p1_global, p2_global])

        p1_ifc = f.createIfcCartesianPoint(to_real(p1))
        p2_ifc = f.createIfcCartesianPoint(to_real(p2))

        ifc_polyline = f.createIfcPolyLine([p1_ifc, p2_ifc])
        origin = f.createIfcCartesianPoint(O)
        ax23d = f.createIfcAxis2Placement3D(
            p1_ifc,
            f.createIfcDirection(xvec),
            f.createIfcDirection(yvec),
        )
        ifc_axis2plac3d = f.create_entity("IfcAxis2Placement3D", origin, None, None)

        if profile_e is not None:
            extrude_area_solid = f.createIfcExtrudedAreaSolidTapered(
                profile, ifc_axis2plac3d, extrude_dir, beam.length, profile_e
            )
        else:
            extrude_area_solid = f.createIfcExtrudedAreaSolid(profile, ifc_axis2plac3d, extrude_dir, beam.length)

        loc_plac = f.createIfcLocalPlacement(global_placement, ax23d)

    body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [extrude_area_solid])
    axis = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [ifc_polyline])
    prod_def_shp = f.createIfcProductDefinitionShape(None, None, (axis, body))

    if "hidden" in beam.metadata.keys():
        if beam.metadata["hidden"] is True:
            a.presentation_layers.append(body)

    ifc_beam = f.createIfcBeam(
        beam.guid,
        owner_history,
        beam.name,
        beam.section.sec_str,
        "Beam",
        loc_plac,
        prod_def_shp,
        beam.name,
        None,
    )
    beam._ifc_elem = ifc_beam

    # Add colour
    if beam.colour is not None:
        add_colour(f, extrude_area_solid, str(beam.colour), beam.colour)

    # Add penetrations
    for pen in beam.penetrations:
        f.createIfcRelVoidsElement(
            create_guid(),
            owner_history,
            None,
            None,
            ifc_beam,
            pen.ifc_opening,
        )

    f.createIfcRelDefinesByType(
        create_guid(),
        None,
        beam.section.type,
        None,
        [ifc_beam],
        beam_type,
    )

    add_multiple_props_to_elem(beam.metadata.get("props", dict()), ifc_beam, f)

    # Material
    mat_profile_set = add_material_assignment(f, beam, ifc_beam, owner_history, beam_type)

    # Cardinality
    mat_usage = f.createIfcMaterialProfileSetUsage(mat_profile_set, convert_bm_jusl_to_ifc(beam))
    f.createIfcRelAssociatesMaterial(create_guid(), owner_history, None, None, [ifc_beam], mat_usage)

    return ifc_beam


def add_material_assignment(f, beam: Beam, ifc_beam, owner_history, beam_type):
    sec = beam.section
    ifc_mat = beam.material.ifc_mat
    mat_profile = f.createIfcMaterialProfile(
        sec.name, "A material profile", ifc_mat, beam.section.ifc_profile, None, "LoadBearing"
    )
    mat_profile_set = f.createIfcMaterialProfileSet(sec.name, None, [mat_profile], None)

    f.createIfcRelAssociatesMaterial(create_guid(), owner_history, None, None, [beam_type], mat_profile_set)

    f.createIfcRelAssociatesMaterial(
        create_guid(),
        owner_history,
        beam.material.name,
        f"Associated Material to beam '{beam.name}'",
        [ifc_beam],
        mat_profile_set,
    )
    return mat_profile_set
