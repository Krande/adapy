from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Tuple, Union

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import numpy as np
from ifcopenshell.util.unit import get_prefix_multiplier

import ada.core.constants as ifco
from ada.concepts.transforms import Transform
from ada.config import Settings
from ada.core.file_system import get_list_of_files
from ada.core.utils import roundoff

if TYPE_CHECKING:
    from ada import Assembly, Beam


def ifc_dir(f: ifcopenshell.file, vec: Tuple[float, float, float]):
    return f.create_entity("IfcDirection", to_real(vec))


def get_tolerance(units):
    tol_map = dict(m=Settings.mtol, mm=Settings.mmtol)
    if units not in tol_map.keys():
        raise ValueError(f'Unrecognized unit "{units}"')
    return tol_map[units]


def ensure_guid_consistency(a: Assembly, project_prefix):
    """Function to edit the global IDs of your elements when they are arbitrarily created from upstream data dump"""
    ensure_uniqueness = dict()
    for p in a.get_all_parts_in_assembly():
        p.guid = create_guid(project_prefix + p.name)

        if p.guid in ensure_uniqueness.keys():
            # p_other = ensure_uniqueness.get(p.guid)
            # ancestors1 = p.get_ancestors()
            # ancestors2 = p_other.get_ancestors()
            raise ValueError(f"GUID Uniqueness not maintained for {p.name}")
        ensure_uniqueness[p.guid] = p
        for obj in p.get_all_physical_objects(sub_elements_only=True):
            obj.guid = create_guid(project_prefix + obj.name)

            if obj.guid in ensure_uniqueness.keys():
                conflicting_obj = ensure_uniqueness[obj.guid]
                # Handle special case BIM software outputs elements of same name
                # but one of them are opaque obstruction vols
                if conflicting_obj.opacity != 0.0:
                    conflicting_obj.name = conflicting_obj.name + "_INSU"
                    conflicting_obj.guid = create_guid(project_prefix + obj.name)
                    ensure_uniqueness[conflicting_obj.guid] = conflicting_obj
                elif obj.opacity != 0.0:
                    obj.name = obj.name + "_INSU"
                    obj.guid = create_guid(project_prefix + obj.name)
                else:
                    raise ValueError(f"GUID Uniqueness not maintained for '{obj}' in [{a.name}]")

            ensure_uniqueness[obj.guid] = obj


def create_guid(name=None):
    """Creates a guid from a random name or bytes or generates a random guid"""
    import hashlib
    import uuid

    if name is None:
        hexdig = uuid.uuid1().hex
    else:
        if type(name) != bytes:
            n = name.encode()
        else:
            n = name
        hexdig = hashlib.md5(n).hexdigest()
    result = ifcopenshell.guid.compress(hexdig)
    return result


def ifc_p(f: ifcopenshell.file, p):
    return f.create_entity("IfcCartesianPoint", to_real(p))


def create_ifc_placement(f: ifcopenshell.file, origin=ifco.O, loc_z=ifco.Z, loc_x=ifco.X):
    """
    Creates an IfcAxis2Placement3D from Location, Axis and RefDirection specified as Python tuples

    :param f:
    :param origin:
    :param loc_z:
    :param loc_x:
    :return:
    """

    ifc_loc_z = f.createIfcDirection(to_real(loc_z))
    ifc_loc_x = f.createIfcDirection(to_real(loc_x))
    return f.createIfcAxis2Placement3D(ifc_p(f, origin), ifc_loc_z, ifc_loc_x)


def create_local_placement(f: ifcopenshell.file, origin=ifco.O, loc_z=ifco.Z, loc_x=ifco.X, relative_to=None):
    """
    Creates an IfcLocalPlacement from Location, Axis and RefDirection,
    specified as Python tuples, and relative placement

    :param f:
    :param origin:
    :param loc_z:
    :param loc_x:
    :param relative_to:
    :return: IFC local placement
    """

    axis2placement = create_ifc_placement(f, origin, loc_z, loc_x)
    ifclocalplacement2 = f.create_entity(
        "IfcLocalPlacement", PlacementRelTo=relative_to, RelativePlacement=axis2placement
    )
    return ifclocalplacement2


def create_new_ifc_file(file_name, schema):
    import datetime

    from ..core.utils import get_version

    f = ifcopenshell.file(schema=schema)
    ada_ver = get_version()
    f.wrapped_data.header.file_name.name = file_name
    f.wrapped_data.header.file_name.time_stamp = (
        datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).astimezone().replace(microsecond=0).isoformat()
    )

    ver_str = f'IfcOpenShell: "{ifcopenshell.version}", ADA: "{ada_ver}"'

    f.wrapped_data.header.file_name.preprocessor_version = ver_str
    f.wrapped_data.header.file_name.originating_system = ver_str
    f.wrapped_data.header.file_name.authorization = "Nobody"
    length_unit = f.createIfcSIUnit(None, "LENGTHUNIT", None, "METRE")
    f.createIfcUnitAssignment((length_unit,))
    # f.wrapped_data.header.file_description.description = ("ViewDefinition[DesignTransferView]",)
    return f


def assembly_to_ifc_file(a: "Assembly"):
    return generate_tpl_ifc_file(a.name, a.metadata["project"], a.metadata["schema"], a.units, a.user)


def generate_tpl_ifc_file(file_name, project, schema, units, user):
    """

    :param file_name:
    :param project:
    :param schema:
    :param units:
    :param user:
    :type user: ada.config.User
    :return:
    """

    import time

    from .ifc_template import tpl_create

    timestamp = time.time()
    timestring = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(timestamp))
    application, application_version = "IfcOpenShell", "0.6"
    project_globalid = create_guid()
    if units == "m":
        units_str = "$,.METRE."
    elif units == "mm":
        units_str = ".MILLI.,.METRE."
    else:
        raise ValueError(f'Unrecognized unit prefix "{units}"')
    ifc_file = tpl_create(
        file_name + ".ifc",
        timestring,
        user.org_name,
        user.user_id,
        schema,
        application_version,
        int(timestamp),
        application,
        project_globalid,
        project,
        units_str,
        user.org_name,
    )

    return ifc_file


def create_ifcpolyline(ifcfile, point_list):
    """
    Creates an IfcPolyLine from a list of points, specified as Python tuples

    :param ifcfile:
    :param point_list:
    :return:
    """
    ifcpts = []
    for p_in in point_list:
        p = [float(x) for x in p_in]
        point = ifcfile.createIfcCartesianPoint(p)
        ifcpts.append(point)
    polyline = ifcfile.createIfcPolyLine(ifcpts)
    return polyline


def create_ifcindexpolyline(ifcfile, points, seg_index):
    """
    Assumes a point list whereas all points that are to be used for creating arc-segments will have 4 values
    (x,y,z,r) instead of 3 (x,y,z)

    #https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm

    :param ifcfile:
    :param points:
    :param seg_index:
    :return:
    """
    ifc_segments = []
    for seg_ind in seg_index:
        if len(seg_ind) == 2:
            ifc_segments.append(ifcfile.createIfcLineIndex(seg_ind))
        elif len(seg_ind) == 3:
            ifc_segments.append(ifcfile.createIfcArcIndex(seg_ind))
        else:
            raise ValueError("Unrecognized number of values")

    # TODO: Investigate using 2DLists instead is it could reduce complexity?
    # ifc_point_list = ifcfile.createIfcCartesianPointList2D(points)

    ifc_point_list = ifcfile.createIfcCartesianPointList3D(points)
    segindex = ifcfile.createIfcIndexedPolyCurve(ifc_point_list, ifc_segments)
    return segindex


def create_ifcindexpolyline2d(ifcfile, points2d, seg_index):
    """
    Assumes a point list whereas all points that are to be used for creating arc-segments will have 4 values
    (x,y,z,r) instead of 3 (x,y,z)

    #https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm

    :param ifcfile:
    :param points2d:
    :param seg_index:
    :return:
    """
    ifc_segments = []
    for seg_ind in seg_index:
        if len(seg_ind) == 2:
            ifc_segments.append(ifcfile.createIfcLineIndex(seg_ind))
        elif len(seg_ind) == 3:
            ifc_segments.append(ifcfile.createIfcArcIndex(seg_ind))
        else:
            raise ValueError("Unrecognized number of values")

    # TODO: Investigate using 2DLists instead is it could reduce complexity?
    # ifc_point_list = ifcfile.createIfcCartesianPointList2D(points)

    ifc_point_list = ifcfile.createIfcCartesianPointList2D(points2d)
    segindex = ifcfile.createIfcIndexedPolyCurve(ifc_point_list, ifc_segments)
    return segindex


def create_ifcrevolveareasolid(f, profile, ifcaxis2placement, origin, revolve_axis, revolve_angle):
    """Creates an IfcExtrudedAreaSolid from a list of points, specified as Python tuples"""
    ifcorigin = f.create_entity("IfcCartesianPoint", to_real(origin))
    ifcaxis1dir = f.create_entity(
        "IfcAxis1Placement", ifcorigin, f.create_entity("IfcDirection", to_real(revolve_axis))
    )

    return f.create_entity("IfcRevolvedAreaSolid", profile, ifcaxis2placement, ifcaxis1dir, revolve_angle)


def create_IfcFixedReferenceSweptAreaSolid(f, curve, profile, ifcaxis2placement, start_param, end_param, fixed_ref):
    """

    :param f:
    :param profile:
    :param ifcaxis2placement:
    :param curve:
    :param start_param:
    :param end_param:
    :param fixed_ref:
    :return:
    """
    return f.create_entity(
        "IfcFixedReferenceSweptAreaSolid", profile, ifcaxis2placement, curve, start_param, end_param, fixed_ref
    )


def create_ifcextrudedareasolid(ifc_file, profile, ifcaxis2placement, extrude_dir, extrusion):
    """
    Creates an IfcExtrudedAreaSolid from a list of points, specified as Python tuples

    :param profile:
    :param ifcaxis2placement:
    :param extrude_dir:
    :param extrusion:
    :return:
    """

    ifcdir = ifc_file.createIfcDirection(extrude_dir)
    ifcextrudedareasolid = ifc_file.create_entity("IfcExtrudedAreaSolid", profile, ifcaxis2placement, ifcdir, extrusion)
    return ifcextrudedareasolid


def create_ifcrightcylinder(ifc_file, ifcaxis2placement, height, radius):
    """

    :param ifc_file:
    :param ifcaxis2placement:
    :param height:
    :param radius:
    :return:
    """
    ifcextrudedareasolid = ifc_file.createIfcRightCircularCylinder(ifcaxis2placement, height, radius)
    return ifcextrudedareasolid


def create_property_set(name, ifc_file, metadata_props, owner_history):

    properties = []

    def ifc_value(v_):
        return ifc_file.create_entity("IfcText", str(v_))

    def to_str(in_enum):
        return (
            f"{in_enum}".replace("(", "")
            .replace(")", "")
            .replace(" ", "")
            .replace(",", ";")
            .replace("[", "")
            .replace("]", "")
        )

    for key, value in metadata_props.items():
        if type(value) in (tuple, list):
            if type(value[0]) in (list, tuple, np.ndarray):
                for i, v in enumerate(value):
                    if type(v) is np.ndarray:
                        v = [roundoff(x) for x in v]
                    properties.append(
                        ifc_file.create_entity(
                            "IfcPropertySingleValue",
                            Name=f"{key}_{i}",
                            NominalValue=ifc_value(to_str(v)),
                        )
                    )
            else:
                properties.append(
                    ifc_file.create_entity(
                        "IfcPropertySingleValue",
                        Name=key,
                        NominalValue=ifc_value(to_str(value)),
                    )
                )
        else:
            properties.append(
                ifc_file.create_entity(
                    "IfcPropertySingleValue",
                    Name=key,
                    NominalValue=ifc_value(value),
                )
            )

    atts = {
        "GlobalId": ifcopenshell.guid.new(),
        "OwnerHistory": owner_history,
        "Name": name,
        "HasProperties": properties,
    }

    return ifc_file.create_entity("IfcPropertySet", **atts)


def add_properties_to_elem(name, ifc_file, ifc_elem, elem_props, owner_history):
    logging.info(f'Adding "{name}" properties to IFC Element "{ifc_elem}"')

    props = create_property_set(name, ifc_file, elem_props, owner_history=owner_history)
    ifc_file.createIfcRelDefinesByProperties(
        create_guid(),
        owner_history,
        "Properties",
        None,
        [ifc_elem],
        props,
    )


def add_multiple_props_to_elem(metadata_props, elem, f, owner_history):
    if len(metadata_props.keys()) > 0:
        if type(list(metadata_props.values())[0]) is dict:
            for pro_id, prop_ in metadata_props.items():
                add_properties_to_elem(pro_id, f, elem, prop_, owner_history=owner_history)
        else:
            add_properties_to_elem("Properties", f, elem, metadata_props, owner_history=owner_history)


def to_real(v) -> Union[float, List[float]]:
    from ada import Node

    if type(v) is float:
        return v
    elif type(v) is tuple:
        return [float(x) for x in v]
    elif type(v) is list:
        if type(v[0]) is float:
            return v
        else:
            return [float(x) for x in v]
    elif type(v) is Node:
        return v.p.astype(float).tolist()
    else:
        return v.astype(float).tolist()


def add_negative_extrusion(f, origin, loc_z, loc_x, depth, points, parent):
    """

    :param f:
    :param origin:
    :param loc_z:
    :param loc_x:
    :param depth:
    :param points:
    :param parent:
    :return:
    """

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = f.by_type("IfcOwnerHistory")[0]

    # Create and associate an opening for the window in the wall
    opening_placement = create_local_placement(f, origin, loc_z, loc_x, parent.ObjectPlacement)
    opening_axis_placement = create_ifc_placement(f, origin, loc_z, loc_x)
    polyline = create_ifcpolyline(f, points)
    ifcclosedprofile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

    opening_solid = create_ifcextrudedareasolid(f, ifcclosedprofile, opening_axis_placement, loc_z, depth)
    opening_representation = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [opening_solid])
    opening_shape = f.createIfcProductDefinitionShape(None, None, [opening_representation])
    opening_element = f.createIfcOpeningElement(
        create_guid(),
        owner_history,
        "Opening",
        "Door opening",
        None,
        opening_placement,
        opening_shape,
        None,
    )
    f.createIfcRelVoidsElement(create_guid(), owner_history, None, None, parent, opening_element)

    return opening_element


def add_colour(
    f,
    ifc_body: Union[List[ifcopenshell.entity_instance], ifcopenshell.entity_instance],
    name,
    colour,
    transparency=0.0,
    use_surface_style_rendering=False,
) -> None:
    """Add IFcSurfaceStyle using either IfcSurfaceStyleRendering or IfcSurfaceStyleShading"""
    if colour is None:
        return None
    colour = f.createIfcColourRgb(name, colour[0], colour[1], colour[2])

    if use_surface_style_rendering:
        surfaceStyleShading = f.createIFCSURFACESTYLERENDERING(colour, transparency)
    else:
        surfaceStyleShading = f.createIfcSurfaceStyleShading()
        surfaceStyleShading.SurfaceColour = colour

    surfaceStyle = f.createIfcSurfaceStyle(colour.Name, "BOTH", (surfaceStyleShading,))
    presStyleAssign = f.createIfcPresentationStyleAssignment((surfaceStyle,))
    if type(ifc_body) in [list, tuple]:
        for ifc_b in ifc_body:
            f.createIfcStyledItem(ifc_b, (presStyleAssign,), colour.Name)
    else:
        f.createIfcStyledItem(ifc_body, (presStyleAssign,), colour.Name)


def calculate_unit_scale(file):
    units = file.by_type("IfcUnitAssignment")[0]
    unit_scale = 1
    for unit in units.Units:
        if not hasattr(unit, "UnitType") or unit.UnitType != "LENGTHUNIT":
            continue
        while unit.is_a("IfcConversionBasedUnit"):
            unit_scale *= unit.ConversionFactor.ValueComponent.wrappedValue
            unit = unit.ConversionFactor.UnitComponent
        if unit.is_a("IfcSIUnit"):
            unit_scale *= get_prefix_multiplier(unit.Prefix)

    return unit_scale


def get_unit_type(file):
    value = calculate_unit_scale(file)
    if value == 0.001:
        return "mm"
    elif value == 1:
        return "m"
    else:
        raise NotImplementedError(f'Unit scale of "{value}" is not yet supported')


def scale_ifc_file_object(ifc_file, scale_factor):
    """
    Scale length factor to meter

    :param ifc_file:
    :return:
    """

    s = ifcopenshell.ifcopenshell_wrapper.schema_by_name("IFC4")
    classes_to_modify = {}
    for d in s.declarations():
        if not hasattr(d, "all_attributes") or "IfcLength" not in str(d.all_attributes()):
            continue
        attributes_to_modify = []
        for attribute in d.all_attributes():
            if "IfcLength" in str(attribute):
                attributes_to_modify.append(attribute.name())
        classes_to_modify[d.name()] = attributes_to_modify

    def scale_all(obj, sf):
        def serialize(obj_):
            """Recursively walk object's hierarchy."""
            if isinstance(obj_, (int, float)):
                return obj_ * sf
            elif isinstance(obj_, list):
                return [serialize(item) for item in obj_]
            elif isinstance(obj_, tuple):
                return tuple(serialize([item for item in obj_]))
            else:
                try:
                    if obj_.is_a("IfcLengthMeasure") is True:
                        obj_.wrappedValue = obj_.wrappedValue * sf
                        return obj_
                    elif obj_.is_a("IfcReal") is True:
                        obj_.wrappedValue = obj_.wrappedValue * sf
                        return obj_
                    elif obj_.is_a("IfcInteger") is True:
                        obj_.wrappedValue = int(obj_.wrappedValue * sf)
                        return obj_
                    elif obj_.is_a("IfcPlaneAngleMeasure") is True:
                        return obj_
                    elif obj_.is_a("IfcPressureMeasure") or obj_.is_a("IfcModulusOfElasticityMeasure"):
                        # sf is a length unit.
                        conv_unit = 1 / sf ** 2
                        obj_.wrappedValue = obj_.wrappedValue * conv_unit
                        return obj_
                    elif obj_.is_a("IfcMassDensityMeasure"):
                        conv_unit = 1 / sf ** 3
                        obj_.wrappedValue = obj_.wrappedValue * conv_unit
                        return obj_
                    # Unit-less
                    elif obj_.is_a("IfcText") is True or obj_.is_a("IfcPositiveRatioMeasure") is True:
                        return obj_
                    elif obj_.is_a("IfcThermalExpansionCoefficientMeasure") or obj_.is_a(
                        "IfcSpecificHeatCapacityMeasure"
                    ):
                        return obj_
                    elif obj_.is_a("IfcLogical") is True:
                        return obj_
                except Exception as er:
                    raise ValueError(f"Error {er}")

                raise ValueError(f'Unknown entity "{type(obj_)}", "{obj_}"')

        return serialize(obj)

    for ifc_class, attributes in classes_to_modify.items():
        for element in ifc_file.by_type(ifc_class):
            for attribute in attributes:

                old_val = getattr(element, attribute)
                if old_val is None:
                    continue
                # setattr(element, attribute, scale_all(old_val, scale_factor))
                # new_val = getattr(element, attribute)
    return ifc_file


def merge_existing(original_file, source_file, new_file):
    source = ifcopenshell.open(source_file)
    f = ifcopenshell.open(original_file)
    original_project = f.by_type("IfcProject")[0]
    merged_project = f.add(source.by_type("IfcProject")[0])
    for element in source.by_type("IfcRoot"):
        f.add(element)
    for inverse in f.get_inverse(merged_project):
        ifcopenshell.util.element.replace_attribute(inverse, merged_project, original_project)
    f.remove(merged_project)
    f.write(str(new_file))


def patch(f, source_file):
    """

    :param f:
    :param source_file:
    :return:
    """
    source = ifcopenshell.open(source_file)
    original_project = f.by_type("IfcProject")[0]
    merged_project = f.add(source.by_type("IfcProject")[0])
    for element in source.by_type("IfcRoot"):
        f.add(element)
    for inverse in f.get_inverse(merged_project):
        ifcopenshell.util.element.replace_attribute(inverse, merged_project, original_project)
    f.remove(merged_project)


def merge_ifc_files(parent_dir, output_file_name, clean_files=False, include_elements=None, exclude_elements=None):
    """

    :param parent_dir:
    :param output_file_name:
    :param clean_files:
    :param include_elements:
    :param exclude_elements:
    :return:
    """
    import pathlib
    import time

    parent_dir = pathlib.Path(parent_dir)
    files = get_list_of_files(parent_dir, ".ifc")
    f = ifcopenshell.open(files[0])
    for i, fp in enumerate(files[1:]):
        checkpoint = time.time()
        print(f'merging products ({i+1} of {len(files)-1}) from "{fp}"')
        fn = ifcopenshell.open(fp)
        print(f"file opened in {time.time() - checkpoint:.2f} seconds")
        checkpoint = time.time()
        for product in fn.by_type("IfcProduct"):
            f.add(product)
        print(f"Products added in {time.time() - checkpoint:.2f} seconds")
        del fn
    out_file_name = str((parent_dir / output_file_name).with_suffix(".ifc"))
    print(f'Writing file "{out_file_name}"')
    checkpoint = time.time()
    f.write(out_file_name)
    print(f"File written in {time.time() - checkpoint:.2f} seconds")


def convert_bm_jusl_to_ifc(bm: "Beam") -> int:
    """
    IfcCardinalPointReference


    1.      bottom left
    2.      bottom centre
    3.      bottom right
    4.      mid-depth left
    5.      mid-depth centre
    6.      mid-depth right
    7.      top left
    8.      top centre
    9.      top right
    10.     geometric centroid
    11.     bottom in line with the geometric centroid
    12.     left in line with the geometric centroid
    13.     right in line with the geometric centroid
    14.     top in line with the geometric centroid
    15.     shear centre
    16.     bottom in line with the shear centre
    17.     left in line with the shear centre
    18.     right in line with the shear centre
    19.     top in line with the shear centre

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcmaterialresource/lexical/ifccardinalpointreference.htm
    """
    jusl = bm.jusl
    jt = bm.JUSL_TYPES

    jusl_map = {jt.NA: 5, jt.TOS: 8}

    jusl_val = jusl_map.get(jusl, None)

    if jusl_val is None:
        if jusl != jt.NA:
            logging.error(f'Unknown JUSL value "{jusl}". Using NA')
        return 5

    return jusl_val


def scale_ifc_file(current_ifc, new_ifc):
    oval = calculate_unit_scale(current_ifc)
    nval = calculate_unit_scale(new_ifc)
    if oval != nval:
        logging.error("Running Unit Conversion on IFC import. This is still highly unstable")
        # length_unit = f.createIfcSIUnit(None, "LENGTHUNIT", None, "METRE")
        # unit_assignment = f.createIfcUnitAssignment((length_unit,))
        new_file = scale_ifc_file_object(new_ifc, nval)
        return new_file


def tesselate_shape(shape, schema, tol):
    occ_string = ifcopenshell.geom.occ_utils.serialize_shape(shape)
    serialized_geom = ifcopenshell.geom.serialise(schema, occ_string)

    if serialized_geom is None:
        logging.debug("Starting serialization of geometry")
        serialized_geom = ifcopenshell.geom.tesselate(schema, occ_string, tol)

    return serialized_geom


def default_settings():
    ifc_settings = ifcopenshell.geom.settings()
    ifc_settings.set(ifc_settings.USE_PYTHON_OPENCASCADE, True)
    ifc_settings.set(ifc_settings.SEW_SHELLS, True)
    ifc_settings.set(ifc_settings.WELD_VERTICES, True)
    ifc_settings.set(ifc_settings.INCLUDE_CURVES, True)
    ifc_settings.set(ifc_settings.USE_WORLD_COORDS, True)
    ifc_settings.set(ifc_settings.VALIDATE_QUANTITIES, True)
    return ifc_settings


def export_transform(f: ifcopenshell.file, transform: Transform):
    from ada.core.constants import X

    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/annex/annex-e/mapped-shape-with-multiple-items.ifc
    # axis1 = f.create_entity("")
    f.create_entity(
        "IfcCartesianTransformationOperator",
        ifc_dir(f, X),
    )
    raise NotImplementedError()


def get_representation_items(f: ifcopenshell.file, ifc_elem: ifcopenshell.entity_instance):
    geom_items = ["IfcTriangulatedFaceSet", "IfcExtrudedAreaSolid", "IfcRevolvedAreaSolid"]
    return list(filter(lambda x: hasattr(x, "StyledByItem") and x.is_a() in geom_items, f.traverse(ifc_elem)))
