from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, List, Tuple, Union

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.geom
import ifcopenshell.util.element
import numpy as np
from ifcopenshell.util.unit import get_prefix_multiplier

import ada.core.constants as ifco
from ada.api.transforms import Transform
from ada.cadit.ifc.write.geom.points import cpt
from ada.config import Config, logger
from ada.core.file_system import get_list_of_files
from ada.core.guid import create_guid
from ada.core.utils import to_real
from ada.visit.colors import Color

if TYPE_CHECKING:
    from ada import Assembly, Beam


def create_reference_subrep(f, global_axes):
    contexts = list(f.by_type("IfcGeometricRepresentationContext"))
    list(f.by_type("IfcGeometricRepresentationSubContext"))
    context_map = {c.ContextIdentifier: c for c in contexts}
    model_rep = context_map.get("Model")
    body_sub_rep = context_map.get("Body")
    if model_rep is None:
        model_rep = f.create_entity("IfcGeometricRepresentationContext", None, "Model", 3, 1.0e-05, global_axes, None)
    if body_sub_rep is None:
        body_sub_rep = f.create_entity(
            "IfcGeometricRepresentationSubContext",
            "Body",
            "Model",
            None,
            None,
            None,
            None,
            model_rep,
            None,
            "MODEL_VIEW",
            None,
        )
    ref_sub_rep = f.create_entity(
        "IfcGeometricRepresentationSubContext",
        "Reference",
        "Model",
        None,
        None,
        None,
        None,
        model_rep,
        None,
        "GRAPH_VIEW",
        None,
    )

    return {"model": model_rep, "body": body_sub_rep, "reference": ref_sub_rep}


def ifc_dir(f: ifcopenshell.file, vec: Tuple[float, float, float]):
    return f.create_entity("IfcDirection", to_real(vec))


def ensure_guid_consistency(a: Assembly, project_prefix):
    """Function to edit the global IDs of your elements when they are arbitrarily created from upstream data dump"""
    a.ifc_store.sync()
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


def create_ifc_placement(f: ifcopenshell.file, origin=ifco.O, loc_z=ifco.Z, loc_x=ifco.X):
    """
    Creates an IfcAxis2Placement3D from Location, Axis and RefDirection specified as Python tuples

    :param f:
    :param origin:
    :param loc_z:
    :param loc_x:
    :return:
    """

    ifc_loc_z = f.create_entity("IfcDirection", to_real(loc_z))
    ifc_loc_x = f.create_entity("IfcDirection", to_real(loc_x))
    return f.create_entity("IfcAxis2Placement3D", cpt(f, origin), ifc_loc_z, ifc_loc_x)


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


def assembly_to_ifc_file(a: "Assembly"):
    schema = a.metadata["schema"]
    f = ifcopenshell.api.run("project.create_file", version=schema)
    project = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name=a.metadata["project"])
    f.add(project)

    if a.units == "mm":
        prefix = dict(prefix="MILLI")
    else:
        prefix = dict()

    # ifcopenshell.api.run("unit.assign_unit", f, **{"length": {"is_metric": True, "raw": "METERS"}})
    # Let's create a modeling geometry context, so we can store 3D geometry (note: IFC supports 2D too!)
    # context = ifcopenshell.api.run("context.add_context", f, context_type="Model")
    length_unit = ifcopenshell.api.run("unit.add_si_unit", f, unit_type="LENGTHUNIT", **prefix)
    area_unit = ifcopenshell.api.run("unit.add_si_unit", f, unit_type="AREAUNIT", **prefix)
    volume_unit = ifcopenshell.api.run("unit.add_si_unit", f, unit_type="VOLUMEUNIT", **prefix)
    planeangle_unit = ifcopenshell.api.run("unit.add_si_unit", f, unit_type="PLANEANGLEUNIT")
    # planeangle_unit = ifcopenshell.api.run("unit.add_conversion_based_unit", f, name="degree")

    ifcopenshell.api.run("unit.assign_unit", f, units=[length_unit, area_unit, volume_unit, planeangle_unit])
    # In particular, in this example we want to store the 3D "body" geometry of objects, i.e. the body shape
    ifcopenshell.api.run(
        "context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW"
    )
    f.wrapped_data.header.file_name.author = ("AdaUser",)
    f.wrapped_data.header.file_name.organization = ("AdaOrg",)
    return f


def create_ifcpolyline(ifcfile, point_list):
    """
    Creates an IfcPolyLine from a list of points, specified as Python tuples

    :param ifcfile:
    :param point_list:
    :return:
    """
    ifcpts = []
    for p_in in point_list:
        point = cpt(ifcfile, p_in)
        ifcpts.append(point)
    polyline = ifcfile.createIfcPolyLine(ifcpts)
    return polyline


def create_axis(f, points, context):
    polyline = create_ifcpolyline(f, points)
    return f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [polyline])


def create_ifcindexpolyline(ifcfile, points3d, seg_index):
    """
    Assumes a point list whereas all points that are to be used for creating arc-segments will have 4 values
    (x,y,z,r) instead of 3 (x,y,z)

    #https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm

    :param ifcfile:
    :param points3d:
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

    ifc_point_list = ifcfile.createIfcCartesianPointList3D(points3d)
    segindex = ifcfile.createIfcIndexedPolyCurve(ifc_point_list, ifc_segments, False)
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
    segindex = ifcfile.createIfcIndexedPolyCurve(ifc_point_list, ifc_segments, False)
    return segindex


def create_ifcrevolveareasolid(f, profile, ifcaxis2placement, origin, revolve_axis, revolve_angle):
    """Creates an IfcExtrudedAreaSolid from a list of points, specified as Python tuples"""
    ifcorigin = f.create_entity("IfcCartesianPoint", to_real(origin))
    ifcaxis1dir = f.create_entity(
        "IfcAxis1Placement", ifcorigin, f.create_entity("IfcDirection", to_real(revolve_axis))
    )

    return f.create_entity("IfcRevolvedAreaSolid", profile, ifcaxis2placement, ifcaxis1dir, revolve_angle)


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


_value_map = {str: "IfcText", float: "IfcReal", int: "IfcInteger", bool: "IfcBoolean"}


def ifc_value_map(f, value):
    if value is None:
        return f.create_entity("IfcText", "")

    if type(value) in (np.float64,):
        value = float(value)
    if type(value) in (np.int64,):
        value = int(value)
    if isinstance(value, Enum):
        value = value.value

    ifc_type = _value_map.get(type(value), None)
    if ifc_type is None:
        # Check if the value is a class instance
        if hasattr(value, "__class__") and hasattr(value.__class__, "__name__"):
            value = "Ifc" + value.__class__.__name__
        logger.warning(f'Unable to find suitable IFC type for "{type(value)}". Will convert it to string')
        return f.create_entity("IfcText", str(value))

    return f.create_entity(ifc_type, value)


def ifc_list_value(f, name: str, list_value: list, owner_history):
    list_values = []
    for x in list_value:
        if isinstance(x, (list, tuple, np.ndarray)):
            list_values.append(ifc_list_value(f, f"{name}_sub", x, owner_history))
        elif isinstance(x, dict):
            list_values.append(create_property_set(f"{name}_sub", f, x, owner_history))
        else:
            list_values.append(ifc_value_map(f, x))

    return f.create_entity("IfcPropertyListValue", Name=name, ListValues=list_values)


def create_property_set(name, ifc_file, metadata_props, owner_history):
    properties = []

    for key, value in metadata_props.items():
        if isinstance(value, (list, tuple, np.ndarray)):
            properties.append(ifc_list_value(ifc_file, key, value, owner_history))
        elif isinstance(value, dict):
            if len(value.keys()) == 0:
                continue
            properties.append(create_property_set(f"{name}_sub", ifc_file, value, owner_history))
        else:
            properties.append(
                ifc_file.create_entity(
                    "IfcPropertySingleValue",
                    Name=key,
                    NominalValue=ifc_value_map(ifc_file, value),
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
    logger.info(f'Adding "{name}" properties to IFC Element "{ifc_elem}"')

    props = create_property_set(name, ifc_file, elem_props, owner_history=owner_history)
    ifc_file.createIfcRelDefinesByProperties(
        create_guid(),
        owner_history,
        "Properties",
        None,
        [ifc_elem],
        props,
    )


def write_elem_property_sets(metadata_props, elem, f, owner_history) -> None:
    if len(metadata_props.keys()) == 0 or Config().ifc_export_props is False:
        return None

    if isinstance(list(metadata_props.values())[0], dict):
        for pro_id, prop_ in metadata_props.items():
            add_properties_to_elem(pro_id, f, elem, prop_, owner_history=owner_history)
    else:
        add_properties_to_elem("Properties", f, elem, metadata_props, owner_history=owner_history)


def add_negative_extrusion(f, origin, loc_z, loc_x, depth, points, parent):
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
    color: Color,
    use_surface_style_rendering=False,
) -> None:
    """Add IFcSurfaceStyle using either IfcSurfaceStyleRendering or IfcSurfaceStyleShading"""
    if color is None:
        return None
    # check if color is already defined
    existing_ifc_colors = f.by_type("IfcColourRgb")
    # make a map of existing colors
    existing_colors = {(c.Red, c.Green, c.Blue): c for c in existing_ifc_colors}
    new_rgb = (color.red, color.green, color.blue)
    if new_rgb in existing_colors.keys():
        ifc_color = existing_colors[new_rgb]
    else:
        ifc_color = f.create_entity("IfcColourRgb", name, color.red, color.green, color.blue)

    if use_surface_style_rendering:
        surface_style_shading = f.create_entity("IFCSURFACESTYLERENDERING", ifc_color, color.transparency)
    else:
        surface_style_shading = f.create_entity(
            "IfcSurfaceStyleShading", SurfaceColour=ifc_color, Transparency=color.transparency
        )

    surface_style = f.create_entity(
        "IfcSurfaceStyle", Name=ifc_color.Name, Side="BOTH", Styles=(surface_style_shading,)
    )
    if type(ifc_body) in [list, tuple]:
        for ifc_b in ifc_body:
            f.create_entity("IfcStyledItem", ifc_b, (surface_style,), ifc_color.Name)
    else:
        f.createIfcStyledItem(ifc_body, (surface_style,), ifc_color.Name)


def calculate_unit_scale(file) -> float:
    units = file.by_type("IfcUnitAssignment")
    if len(units) == 0:
        logger.warning("No unit assignment found in file. Assuming meters")
        return 1.0

    units = units[0]
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


def get_unit_type(file: ifcopenshell.file):
    from ada.base.units import Units

    value = calculate_unit_scale(file)
    if value == 0.001:
        return Units.MM
    elif value == 1:
        return Units.M
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
                        conv_unit = 1 / sf**2
                        obj_.wrappedValue = obj_.wrappedValue * conv_unit
                        return obj_
                    elif obj_.is_a("IfcMassDensityMeasure"):
                        conv_unit = 1 / sf**3
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
        print(f'merging products ({i + 1} of {len(files) - 1}) from "{fp}"')
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


def convert_bm_jusl_to_ifc(bm: Beam) -> int:
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
    from ada.api.beams.helpers import Justification as jt
    from ada.api.beams.helpers import get_justification

    just = get_justification(bm)

    jusl_map = {jt.NA: 5, jt.TOS: 8}

    jusl_val = jusl_map.get(just, None)

    if jusl_val is None:
        if just != jt.NA:
            logger.info(f'Unknown JUSL value "{just}". Using NA')
        return 5

    return jusl_val


def scale_ifc_file(current_ifc, new_ifc):
    oval = calculate_unit_scale(current_ifc)
    nval = calculate_unit_scale(new_ifc)
    if oval != nval:
        logger.error("Running Unit Conversion on IFC import. This is still highly unstable")
        # length_unit = f.createIfcSIUnit(None, "LENGTHUNIT", None, "METRE")
        # unit_assignment = f.createIfcUnitAssignment((length_unit,))
        new_file = scale_ifc_file_object(new_ifc, nval)
        return new_file


def tesselate_shape(shape, schema, tol):
    occ_string = ifcopenshell.geom.occ_utils.serialize_shape(shape)
    serialized_geom = ifcopenshell.geom.serialise(schema, occ_string)

    if serialized_geom is None:
        logger.debug("Starting serialization of geometry")
        serialized_geom = ifcopenshell.geom.tesselate(schema, occ_string, tol)

    return serialized_geom


def default_settings():
    settings = ifcopenshell.geom.settings()
    settings.set("mesher-linear-deflection", 0.001)
    settings.set("mesher-angular-deflection", 0.5)
    settings.set("apply-default-materials", False)
    settings.set("keep-bounding-boxes", True)
    settings.set("layerset-first", True)
    settings.set("use-world-coords", True)
    # Wire intersection checks is prohibitively slow on advanced breps. See bug #5999.
    settings.set("no-wire-intersection-check", True)
    # settings.set("triangulation-type", ifcopenshell.ifcopenshell_wrapper.POLYHEDRON_WITHOUT_HOLES)
    # settings.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)

    return settings


def export_transform(f: ifcopenshell.file, transform: Transform):
    from ada.core.constants import X

    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/annex/annex-e/mapped-shape-with-multiple-items.ifc
    # axis1 = f.create_entity("")
    f.create_entity(
        "IfcCartesianTransformationOperator",
        ifc_dir(f, X),
    )
    raise NotImplementedError()


def get_all_subtypes(entity: ifcopenshell.ifcopenshell_wrapper.entity, subtypes=None):
    subtypes = [] if subtypes is None else subtypes
    for subtype in entity.subtypes():
        if subtype.is_abstract() is False:
            subtypes.append(subtype.name())
        get_all_subtypes(subtype, subtypes)
    return subtypes


def get_representation_items(f: ifcopenshell.file, ifc_elem: ifcopenshell.entity_instance):
    geom_items = [
        "IfcTriangulatedFaceSet",
        "IfcExtrudedAreaSolid",
        "IfcExtrudedAreaSolid",
        "IfcExtrudedAreaSolidTapered",
        "IfcRevolvedAreaSolid",
        "IfcClosedShell",
    ]
    geom_lower = [i.lower() for i in geom_items]
    return list(
        filter(
            lambda x: hasattr(x, "StyledByItem") and x.is_a().lower() in geom_lower,
            f.traverse(ifc_elem),
        )
    )
