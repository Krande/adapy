import logging

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element
import numpy as np
from ifcopenshell.util.unit import get_prefix_multiplier

import ada.core.constants as ifco
from ada.core.utils import Counter, create_guid, get_list_of_files, roundoff

name_gen = Counter(1, "IfcEl")


def create_ifcaxis2placement(ifcfile, origin=ifco.O, loc_z=ifco.Z, loc_x=ifco.X):
    """
    Creates an IfcAxis2Placement3D from Location, Axis and RefDirection specified as Python tuples

    :param ifcfile:
    :param origin:
    :param loc_z:
    :param loc_x:
    :return:
    """

    ifc_origin = ifcfile.createIfcCartesianPoint(to_real(origin))
    ifc_loc_z = ifcfile.createIfcDirection(to_real(loc_z))
    ifc_loc_x = ifcfile.createIfcDirection(to_real(loc_x))
    axis2placement = ifcfile.createIfcAxis2Placement3D(ifc_origin, ifc_loc_z, ifc_loc_x)
    return axis2placement


def create_ifclocalplacement(ifcfile, origin=ifco.O, loc_z=ifco.Z, loc_x=ifco.X, relative_to=None):
    """
    Creates an IfcLocalPlacement from Location, Axis and RefDirection,
    specified as Python tuples, and relative placement

    :param ifcfile:
    :param origin:
    :param loc_z:
    :param loc_x:
    :param relative_to:
    :return: IFC local placement
    """

    axis2placement = create_ifcaxis2placement(ifcfile, origin, loc_z, loc_x)
    ifclocalplacement2 = ifcfile.createIfcLocalPlacement(relative_to, axis2placement)
    return ifclocalplacement2


def generate_tpl_ifc_file(file_name, project, organization, creator, schema, units):
    """

    :param file_name:
    :param project:
    :param organization:
    :param creator:
    :param schema:
    :param units:
    :return:
    """

    import time

    from ada.core.ifc_template import tpl_create

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
        organization,
        creator,
        schema,
        application_version,
        int(timestamp),
        application,
        project_globalid,
        project,
        units_str,
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


def create_ifcrevolveareasolid(ifc_file, profile, ifcaxis2placement, origin, revolve_axis, revolve_angle):
    """
    Creates an IfcExtrudedAreaSolid from a list of points, specified as Python tuples
    :param ifc_file:
    :param profile:
    :param ifcaxis2placement:
    :param origin:
    :param revolve_axis:
    :param revolve_angle:
    :return:
    """
    ifcorigin = ifc_file.createIfcCartesianPoint(origin)
    ifcaxis1dir = ifc_file.createIfcAxis1Placement(ifcorigin, ifc_file.createIfcDirection(revolve_axis))

    ifcextrudedareasolid = ifc_file.createIfcRevolvedAreaSolid(profile, ifcaxis2placement, ifcaxis1dir, revolve_angle)
    return ifcextrudedareasolid


def create_IfcFixedReferenceSweptAreaSolid(
    ifc_file, curve, profile, ifcaxis2placement, start_param, end_param, fixed_ref
):
    """

    :param ifc_file:
    :param profile:
    :param ifcaxis2placement:
    :param curve:
    :param start_param:
    :param end_param:
    :param fixed_ref:
    :return:
    """
    ifcextrudedareasolid = ifc_file.createIfcFixedReferenceSweptAreaSolid(
        profile, ifcaxis2placement, curve, start_param, end_param, fixed_ref
    )
    return ifcextrudedareasolid


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
    ifcextrudedareasolid = ifc_file.createIfcExtrudedAreaSolid(profile, ifcaxis2placement, ifcdir, extrusion)
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


def create_property_set(name, ifc_file, metadata_props):
    owner_history = ifc_file.by_type("IfcOwnerHistory")[0]
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


def add_properties_to_elem(name, ifc_file, ifc_elem, elem_props):
    """

    :param ifc_file:
    :param ifc_elem:
    :param elem_props:
    :return:
    """
    owner_history = ifc_file.by_type("IfcOwnerHistory")[0]
    props = create_property_set(name, ifc_file, elem_props)
    ifc_file.createIfcRelDefinesByProperties(
        create_guid(),
        owner_history,
        "Properties",
        None,
        [ifc_elem],
        props,
    )


def to_real(v):
    """

    :param v:
    :return:
    """
    if type(v) is tuple:
        return [float(x) for x in v]
    elif type(v) is list:
        if type(v[0]) is float:
            return v
        else:
            return [float(x) for x in v]
    else:
        return v.astype(float).tolist()


def getIfcPropertySets(ifcelem):
    """Returns a dictionary of {pset_id:[prop_id, prop_id...]} for an IFC object"""
    props = dict()
    # get psets for this pid
    for definition in ifcelem.IsDefinedBy:
        # To support IFC2X3, we need to filter our results.
        if definition.is_a("IfcRelDefinesByProperties"):
            property_set = definition.RelatingPropertyDefinition
            pset_name = property_set.Name.split(":")[0].strip()
            props[pset_name] = dict()
            if property_set.is_a("IfcElementQuantity"):
                continue
            for prop in property_set.HasProperties:
                if prop.is_a("IfcPropertySingleValue"):
                    props[pset_name][prop.Name] = prop.NominalValue.wrappedValue
            # Returning first instance of RelDefines
            # return props (Why?)
    return props


def get_parent(instance):
    """

    :param instance:
    :return:
    """
    if instance.is_a("IfcOpeningElement"):
        return instance.VoidsElements[0].RelatingBuildingElement
    if instance.is_a("IfcElement"):
        fills = instance.FillsVoids
        if len(fills):
            return fills[0].RelatingOpeningElement
        containments = instance.ContainedInStructure
        if len(containments):
            return containments[0].RelatingStructure
    if instance.is_a("IfcObjectDefinition"):
        decompositions = instance.Decomposes
        if len(decompositions):
            return decompositions[0].RelatingObject


def get_association(ifc_elem):
    """

    :param ifc_elem:
    :return:
    """
    c = None
    for association in ifc_elem.HasAssociations:
        if association.is_a("IfcRelAssociatesMaterial"):
            material = association.RelatingMaterial
            if material.is_a("IfcMaterialProfileSet"):
                # For now, we only deal with a single profile
                c = material.MaterialProfiles[0]
            if material.is_a("IfcMaterialProfileSetUsage"):
                c = material.ForProfileSet.MaterialProfiles[0]
            if material.is_a("IfcRelAssociatesMaterial"):
                c = material.RelatingMaterial
            if material.is_a("IfcMaterial"):
                c = material
    if c is None:
        raise ValueError(f'IfcElem "{ifc_elem.Name}" lacks associated Material properties')

    return c


def get_name(ifc_elem):
    """

    :param ifc_elem:
    :return:
    """
    props = getIfcPropertySets(ifc_elem)
    product_name = ifc_elem.Name
    if hasattr(props, "NAME") and product_name is None:
        name = props["NAME"]
    else:
        name = product_name
    if name is None:
        name = next(name_gen)
    return name


def get_representation(ifc_elem, settings):
    """

    :param ifc_elem:
    :param settings:
    :return:
    """
    pdct_shape = ifcopenshell.geom.create_shape(settings, inst=ifc_elem)

    if pdct_shape is None:
        print(f'Unable to import geometry for ifc element "{ifc_elem}"')
        return pdct_shape, None, None

    geom = get_geom(ifc_elem, settings)
    r, g, b, alpha = pdct_shape.styles[0]  # the shape color
    return geom, (r, g, b), alpha


def get_geom(ifc_elem, settings):
    """

    :param ifc_elem:
    :param settings:
    :return:
    """
    from ifcopenshell.geom.occ_utils import shape_tuple
    from OCC.Core import BRepTools
    from OCC.Core.TopoDS import TopoDS_Compound

    try:
        pdct_shape = ifcopenshell.geom.create_shape(settings, inst=ifc_elem)
    except RuntimeError:
        print(f'unable to parse ifc_elem "{ifc_elem}"')
        return

    if type(pdct_shape) is shape_tuple:
        shape = pdct_shape[1]
    else:
        shape = pdct_shape.solid

    if type(shape) is not TopoDS_Compound:
        brep_data = pdct_shape.solid.brep_data
        ss = BRepTools.BRepTools_ShapeSet()
        ss.ReadFromString(brep_data)
        nb_shapes = ss.NbShapes()
        occ_shape = ss.Shape(nb_shapes)
    else:
        occ_shape = shape
    return occ_shape


def import_indexedpolycurve(ipoly, normal, xdir, origin):
    """

    :param ipoly: IFC element
    :param normal:
    :param xdir:
    :param origin:
    :return:
    """
    from ada import ArcSegment, LineSegment
    from ada.core.utils import global_2_local_nodes, segments_to_local_points

    ydir = np.cross(normal, xdir)
    nodes3d = [p for p in ipoly.Points.CoordList]
    nodes2d = global_2_local_nodes([xdir, ydir], origin, nodes3d)
    nodes2d = [np.array([n[0], n[1], 0.0]) for n in nodes2d]
    seg_list = []
    for i, seg in enumerate(ipoly.Segments):
        if seg.is_a("IfcLineIndex"):
            v = seg.wrappedValue
            p1 = nodes2d[v[0] - 1]
            p2 = nodes2d[v[1] - 1]
            seg_list.append(LineSegment(p1=p1, p2=p2))
        elif seg.is_a("IfcArcIndex"):
            v = seg.wrappedValue
            p1 = nodes2d[v[0] - 1]
            p2 = nodes2d[v[1] - 1]
            p3 = nodes2d[v[2] - 1]
            seg_list.append(ArcSegment(p1, p3, midpoint=p2))
        else:
            raise ValueError("Unrecognized type")

    local_points = [(roundoff(x[0]), roundoff(x[1])) for x in segments_to_local_points(seg_list)]
    return local_points


def import_polycurve(poly, normal, xdir):
    from ada.core.utils import global_2_local_nodes

    ydir = np.cross(normal, xdir)
    nodes3d = [p for p in poly.Points]
    nodes2d = global_2_local_nodes([xdir, ydir], (0, 0, 0), nodes3d)

    return nodes2d


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
    opening_placement = create_ifclocalplacement(f, origin, loc_z, loc_x, parent.ObjectPlacement)
    opening_axis_placement = create_ifcaxis2placement(f, origin, loc_z, loc_x)
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


def add_colour(f, ifc_body, name, colour):
    """

    :param f:
    :param ifc_body:
    :param name:
    :param colour:
    :return:
    """
    colour = f.createIfcColourRgb(name, colour[0], colour[1], colour[2])
    surfaceStyleShading = f.createIfcSurfaceStyleShading()
    surfaceStyleShading.SurfaceColour = colour
    surfaceStyle = f.createIfcSurfaceStyle(colour.Name, "BOTH", (surfaceStyleShading,))
    presStyleAssign = f.createIfcPresentationStyleAssignment((surfaceStyle,))
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
                try:
                    setattr(element, attribute, scale_all(old_val, scale_factor))
                except Exception as e:
                    raise ValueError(e)
                # new_val = getattr(element, attribute)
    return ifc_file


def add_fem(ifc_file, part):
    """
    :param ifc_file:
    :param part:
    :type part: ada.Part
    :return:
    """
    from itertools import groupby
    from operator import attrgetter

    from ada.fem import ElemShapes

    ifc_file.createIfcStructuralAnalysisModel(
        create_guid(),
        ifc_file.by_type("IfcOwnerHistory")[0],
        "Structural Analysis",
        "Example analysis",
        ".NOTDEFINED.",
        "LOADING_3D",
    )
    fem = part.fem

    for el_type, elements in groupby(fem.elements, key=attrgetter("type")):

        if el_type in ElemShapes.beam:
            for elem in elements:
                # TODO: add_fem_elem_beam(elem, parent)
                pass


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


def convert_bm_jusl_to_ifc(bm):
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

    :param bm:
    :type bm: ada.Beam
    :return:
    """
    jusl = bm.jusl
    jusl_map = dict(NA=5, TOP=8)

    jusl_val = jusl_map.get(jusl, None)

    if jusl_val is None:
        if jusl != "NA":
            logging.error(f'Unknown JUSL value "{jusl}". Using NA')
        return 5

    return jusl_val
