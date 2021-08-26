import logging
from itertools import chain

import numpy as np

from ada.base import Backend, BackendGeom
from ada.concepts.curves import CurvePoly
from ada.concepts.points import Node
from ada.concepts.primitives import PrimBox
from ada.config import Settings as _Settings
from ada.core.utils import (
    Counter,
    angle_between,
    calc_yvec,
    calc_zvec,
    roundoff,
    unit_vector,
    vector_length,
)
from ada.ifc.utils import create_guid
from ada.materials.metals import CarbonSteel
from ada.occ.utils import make_wire_from_points
from ada.sections import GeneralProperties, SectionCat

section_counter = Counter(1)
material_counter = Counter(1)


class Beam(BackendGeom):
    """
    The base Beam object

    :param n1: Start position of beam. List or Node object
    :param n2: End position of beam. List or Node object
    :param sec: Section definition. Str or Section Object
    :param mat: Material. Str or Material object. String: ['S355' & 'S420'] (default is 'S355' if None is parsed)
    :param name: Name of beam
    :param tap: Tapering of beam. Str or Section object
    :param jusl: Justification of Beam centreline
    :param curve: Curve
    """

    def __init__(
        self,
        name,
        n1=None,
        n2=None,
        sec=None,
        mat=None,
        tap=None,
        jusl="NA",
        up=None,
        angle=0.0,
        curve=None,
        e1=None,
        e2=None,
        colour=None,
        parent=None,
        metadata=None,
        ifc_geom=None,
        opacity=None,
        units="m",
        ifc_elem=None,
        guid=None,
    ):
        super().__init__(name, metadata=metadata, units=units, guid=guid, ifc_elem=ifc_elem)

        if ifc_elem is not None:
            props = self._import_from_ifc_beam(ifc_elem)
            self.name = props["name"]
            self.guid = props["guid"]
            n1 = props["n1"]
            n2 = props["n2"]
            sec = props["sec"]
            mat = props["mat"]
            up = props["up"]
            ifc_geom = props["ifc_geom"]
            colour = props["colour"]
            opacity = props["opacity"]
            self.metadata.update(props["props"])

        if curve is not None:
            curve.parent = self
            n1 = curve.points3d[0]
            n2 = curve.points3d[-1]
        self.colour = colour
        self._curve = curve
        self._n1 = n1 if type(n1) is Node else Node(n1, units=units)
        self._n2 = n2 if type(n2) is Node else Node(n2, units=units)
        self._jusl = jusl

        self._connected_to = []
        self._connected_end1 = None
        self._connected_end2 = None
        self._tos = None
        self._e1 = e1
        self._e2 = e2

        self._parent = parent
        self._bbox = None

        # Section setup
        if type(sec) is Section:
            self._section = sec
            self._taper = sec if tap is None else tap
        elif type(sec) is str:
            from ada.sections.utils import interpret_section_str

            self._section, self._taper = interpret_section_str(sec)
            if self._section is None:
                raise ValueError("Unable to find beam section based on input: {}".format(sec))
        else:
            raise ValueError(f'Unacceptable input type: "{type(sec)}"')

        self._section.parent = self
        self._taper.parent = self

        # Define orientations

        xvec = unit_vector(self.n2.p - self.n1.p)
        tol = 1e-3
        zvec = calc_zvec(xvec)
        gup = np.array(zvec)

        if up is None:
            if angle != 0.0 and angle is not None:
                from pyquaternion import Quaternion

                my_quaternion = Quaternion(axis=xvec, degrees=angle)
                rot_mat = my_quaternion.rotation_matrix
                up = np.array([roundoff(x) if abs(x) != 0.0 else 0.0 for x in np.matmul(gup, np.transpose(rot_mat))])
            else:
                up = np.array([roundoff(x) if abs(x) != 0.0 else 0.0 for x in gup])
            yvec = calc_yvec(xvec, up)
        else:
            if (len(up) == 3) is False:
                raise ValueError("Up vector must be length 3")
            if vector_length(xvec - up) < tol:
                raise ValueError("The assigned up vector is too close to your beam direction")
            yvec = calc_yvec(xvec, up)
            # TODO: Fix improper calculation of angle (e.g. xvec = [1,0,0] and up = [0, 1,0] should be 270?
            rad = angle_between(up, yvec)
            angle = np.rad2deg(rad)
            up = np.array(up)

        # lup = np.cross(xvec, yvec)
        self._xvec = xvec
        self._yvec = np.array([roundoff(x) for x in yvec])
        self._up = up
        self._angle = angle

        self._taper = self._section if self._taper is None else self._taper

        if isinstance(mat, Material):
            self._material = mat
        else:
            if mat is None:
                mat = "S355"
            self._material = Material(name=mat, mat_model=CarbonSteel(mat, plasticity_model=None))

        self._ifc_geom = ifc_geom
        self._opacity = opacity

    def get_outer_points(self):
        """

        :return:
        """
        from itertools import chain

        from ada.core.utils import local_2_global_nodes

        outer_curve, inner_curve, disconnected = self.section.cross_sec(False)
        if disconnected:
            ot = list(chain.from_iterable(outer_curve))
        else:
            ot = outer_curve

        if type(ot) is CurvePoly:
            assert isinstance(ot, CurvePoly)
            ot = ot.points2d

        yv = self.yvec
        xv = self.xvec
        p1 = self.n1.p
        p2 = self.n2.p

        nodes_p1 = local_2_global_nodes(ot, p1, yv, xv)
        nodes_p2 = local_2_global_nodes(ot, p2, yv, xv)

        return nodes_p1, nodes_p2

    def _calc_bbox(self):
        """
        Get the bounding box of a beam

        :param self:
        :return:
        """
        from ..sections import SectionCat

        if SectionCat.is_circular_profile(self.section.type) or SectionCat.is_tubular_profile(self.section.type):
            d = self.section.r * 2
            dummy_beam = Beam("dummy", self.n1.p, self.n2.p, Section("DummySec", "BG", h=d, w_btn=d, w_top=d))
            outer_curve = dummy_beam.get_outer_points()
        else:
            outer_curve = self.get_outer_points()

        points = np.array(list(chain.from_iterable(outer_curve)))
        xv = sorted([roundoff(p[0]) for p in points])
        yv = sorted([roundoff(p[1]) for p in points])
        zv = sorted([roundoff(p[2]) for p in points])
        xmin, xmax = xv[0], xv[-1]
        ymin, ymax = yv[0], yv[-1]
        zmin, zmax = zv[0], zv[-1]
        return (xmin, ymin, zmin), (xmax, ymax, zmax)

    def _generate_ifc_elem(self):
        from ada.config import Settings
        from ada.core.constants import O, X, Z
        from ada.core.utils import angle_between
        from ada.ifc.utils import (
            add_colour,
            add_multiple_props_to_elem,
            convert_bm_jusl_to_ifc,
            create_global_axes,
            create_ifcrevolveareasolid,
            create_local_placement,
        )

        sec = self.section
        if self.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = a.user.to_ifc()
        parent = self.parent.ifc_elem

        if Settings.include_ecc and self.e1 is not None:
            e1 = self.e1
        else:
            e1 = (0.0, 0.0, 0.0)

        if Settings.include_ecc and self.e2 is not None:
            e2 = self.e2
        else:
            e2 = (0.0, 0.0, 0.0)

        p1 = tuple([float(x) + float(e1[i]) for i, x in enumerate(self.n1.p)])
        p2 = tuple([float(x) + float(e2[i]) for i, x in enumerate(self.n2.p)])

        p1_ifc = f.createIfcCartesianPoint(p1)
        p2_ifc = f.createIfcCartesianPoint(p2)

        def to_real(v):
            return v.astype(float).tolist()

        xvec, yvec, zvec = to_real(self.xvec), to_real(self.yvec), to_real(self.up)
        beam_type = self.section.ifc_beam_type
        profile = self.section.ifc_profile

        if self.section != self.taper:
            profile_e = self.taper.ifc_profile
            # beam_type_e = self.taper.ifc_beam_type
        else:
            profile_e = None

        global_placement = create_local_placement(f, O, Z, X)

        if self.curve is not None:
            # TODO: Fix Sweeped Curve definition. Currently not working as intended (or maybe input is wrong.. )
            curve = self.curve.ifc_elem
            corigin = to_real(curve.rot_origin)
            # corigin_rel = to_real(self.n1.p + curve.rot_origin)
            corigin_ifc = f.createIfcCartesianPoint(corigin)
            # raxis = [float(x) for x in curve.rot_axis]
            v1 = np.array(self.n1.p) - np.array(curve.rot_origin)
            v2 = np.array(self.n2.p) - np.array(curve.rot_origin)
            v1u = unit_vector(v1)
            v2u = unit_vector(v2)
            profile_x = to_real(np.cross(v1u, zvec))
            profile_y = to_real(v1u)
            # ifc_px = f.createIfcDirection(profile_x)
            # ifc_py = f.createIfcDirection(profile_y)
            # a1 = angle_between((1, 0, 0), v1)
            # a2 = angle_between((1, 0, 0), v2)
            a3 = np.rad2deg(angle_between(v1u, v2u))
            # cangle1 = f.createIFCPARAMETERVALUE(np.rad2deg(a1))
            # cangle2 = f.createIFCPARAMETERVALUE(np.rad2deg(a2))

            curve_axis2plac3d = f.createIfcAxis2Placement3D(corigin_ifc)
            circle = f.createIfcCircle(curve_axis2plac3d, curve.radius)
            ifc_polyline = f.createIfcTrimmedCurve(circle, [p1_ifc], [p2_ifc], True, "CARTESIAN")

            revolve_placement = create_global_axes(f, p1, profile_x, profile_y)
            extrude_area_solid = create_ifcrevolveareasolid(f, profile, revolve_placement, corigin, xvec, a3)
            loc_plac = create_local_placement(f, O, Z, X, parent.ObjectPlacement)
        else:
            ifc_polyline = f.createIfcPolyLine([p1_ifc, p2_ifc])
            ifc_axis2plac3d = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint(O), None, None)
            extrude_dir = f.createIfcDirection((0.0, 0.0, 1.0))
            if profile_e is not None:
                extrude_area_solid = f.createIfcExtrudedAreaSolidTapered(
                    profile, ifc_axis2plac3d, extrude_dir, self.length, profile_e
                )
            else:
                extrude_area_solid = f.createIfcExtrudedAreaSolid(profile, ifc_axis2plac3d, extrude_dir, self.length)

            ax23d = f.createIfcAxis2Placement3D(
                p1_ifc,
                f.createIfcDirection(xvec),
                f.createIfcDirection(yvec),
            )
            loc_plac = f.createIfcLocalPlacement(global_placement, ax23d)

        body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [extrude_area_solid])
        axis = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [ifc_polyline])
        prod_def_shp = f.createIfcProductDefinitionShape(None, None, (axis, body))

        if "hidden" in self.metadata.keys():
            if self.metadata["hidden"] is True:
                a.presentation_layers.append(body)

        ifc_beam = f.createIfcBeam(
            self.guid,
            owner_history,
            self.name,
            self.section.sec_str,
            "Beam",
            loc_plac,
            prod_def_shp,
            self.name,
            None,
        )
        self._ifc_elem = ifc_beam

        # Add colour
        if self.colour is not None:
            add_colour(f, extrude_area_solid, str(self.colour), self.colour)

        # Add penetrations
        # elements = []
        for pen in self._penetrations:
            # elements.append(pen.ifc_opening)
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
            self.section.type,
            None,
            [ifc_beam],
            beam_type,
        )

        add_multiple_props_to_elem(self.metadata.get("props", dict()), ifc_beam, f)

        # Material
        ifc_mat = a.ifc_materials[self.material.name]
        mat_profile = f.createIfcMaterialProfile(sec.name, "A material profile", ifc_mat, profile, None, "LoadBearing")
        mat_profile_set = f.createIfcMaterialProfileSet(sec.name, None, [mat_profile], None)

        f.createIfcRelAssociatesMaterial(create_guid(), owner_history, None, None, [beam_type], mat_profile_set)

        f.createIfcRelAssociatesMaterial(
            create_guid(),
            owner_history,
            self.material.name,
            f"Associated Material to beam '{self.name}'",
            [ifc_beam],
            mat_profile_set,
        )

        # Cardinality
        mat_usage = f.createIfcMaterialProfileSetUsage(mat_profile_set, convert_bm_jusl_to_ifc(self))
        f.createIfcRelAssociatesMaterial(create_guid(), owner_history, None, None, [ifc_beam], mat_usage)

        return ifc_beam

    def _import_from_ifc_beam(self, ifc_elem):
        from ada.ifc.utils import (
            get_association,
            get_ifc_shape,
            get_name,
            getIfcPropertySets,
        )

        ass = get_association(ifc_elem)
        sec = Section(ass.Profile.ProfileName, ifc_elem=ass.Profile)
        mat = Material(ass.Material.Name, ifc_mat=ass.Material)

        axes = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Axis"]

        if len(axes) != 1:
            raise ValueError("Number of axis objects attached to element is not 1")
        if len(axes[0].Items) != 1:
            raise ValueError("Number of items objects attached to axis is not 1")

        axis = axes[0].Items[0]
        p1 = axis.Points[0].Coordinates
        p2 = axis.Points[1].Coordinates

        yvec = ifc_elem.ObjectPlacement.RelativePlacement.RefDirection.DirectionRatios
        xvec = unit_vector(np.array(p2) - np.array(p1))
        zvec = np.cross(xvec, yvec)

        # pdct_shape = ifcopenshell.geom.create_shape(self.settings, inst=ifc_elem)
        pdct_shape, colour, alpha = get_ifc_shape(ifc_elem, self.ifc_settings)

        bodies = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Body"]
        if len(bodies) != 1:
            raise ValueError("Number of body objects attached to element is not 1")
        if len(bodies[0].Items) != 1:
            raise ValueError("Number of items objects attached to body is not 1")
        body = bodies[0].Items[0]
        if len(body.StyledByItem) > 0:
            style = body.StyledByItem[0].Styles[0].Styles[0].Styles[0]
            colour = (
                int(style.SurfaceColour.Red),
                int(style.SurfaceColour.Green),
                int(style.SurfaceColour.Blue),
            )

        props = getIfcPropertySets(ifc_elem)

        return dict(
            name=get_name(ifc_elem),
            n1=p1,
            n2=p2,
            sec=sec,
            mat=mat,
            up=zvec,
            ifc_geom=pdct_shape,
            colour=colour,
            opacity=alpha,
            guid=ifc_elem.GlobalId,
            props=props,
        )

    def calc_con_points(self, point_tol=_Settings.point_tol):
        from ada.core.utils import sort_points_by_dist

        a = self.n1.p
        b = self.n2.p
        points = [tuple(con.centre) for con in self.connected_to]

        def is_mem_eccentric(mem, centre):
            is_ecc = False
            end = None
            if point_tol < vector_length(mem.n1.p - centre) < mem.length * 0.9:
                is_ecc = True
                end = mem.n1.p
            if point_tol < vector_length(mem.n2.p - centre) < mem.length * 0.9:
                is_ecc = True
                end = mem.n2.p
            return is_ecc, end

        if len(self.connected_to) == 1:
            con = self.connected_to[0]
            if con.main_mem == self:
                for m in con.beams:
                    if m != self:
                        is_ecc, end = is_mem_eccentric(m, con.centre)
                        if is_ecc:
                            logging.info(f'do something with end "{end}"')
                            points.append(tuple(end))

        midpoints = []
        prev_p = None
        for p in sort_points_by_dist(a, points):
            p = np.array(p)
            bmlen = self.length
            vlena = vector_length(p - a)
            vlenb = vector_length(p - b)

            if prev_p is not None:
                if vector_length(p - prev_p) < point_tol:
                    continue

            if vlena < point_tol:
                self._connected_end1 = self.connected_to[points.index(tuple(p))]
                prev_p = p
                continue

            if vlenb < point_tol:
                self._connected_end2 = self.connected_to[points.index(tuple(p))]
                prev_p = p
                continue

            if vlena > bmlen or vlenb > bmlen:
                prev_p = p
                continue

            midpoints += [p]
            prev_p = p

        return midpoints

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if self._units != value:
            self.n1.units = value
            self.n2.units = value
            self.section.units = value
            self.material.units = value
            for pen in self.penetrations:
                pen.units = value
            self._units = value

    @property
    def section(self):
        """

        :return:
        :rtype: Section
        """
        return self._section

    @section.setter
    def section(self, value):
        self._section = value

    @property
    def taper(self):
        """

        :return:
        :rtype: Section
        """
        return self._taper

    @taper.setter
    def taper(self, value):
        self._taper = value

    @property
    def material(self):
        """

        :return:
        :rtype: Material
        """
        return self._material

    @material.setter
    def material(self, value):
        self._material = value

    @property
    def member_type(self):
        from ada.core.utils import is_parallel

        xvec = self.xvec
        if is_parallel(xvec, [0.0, 0.0, 1.0], tol=1e-1):
            mtype = "Column"
        elif xvec[2] == 0.0:
            mtype = "Girder"
        else:
            mtype = "Brace"

        return mtype

    @property
    def connected_to(self):
        return self._connected_to

    @property
    def length(self):
        """
        This property returns the length of the beam

        :return: length of beam
        :rtype: float
        """
        p1 = self.n1.p
        p2 = self.n2.p

        if self.e1 is not None:
            p1 += self.e1
        if self.e2 is not None:
            p2 += self.e2
        return vector_length(p2 - p1)

    @property
    def jusl(self):
        """

        :return: Justification line
        """
        return self._jusl

    @property
    def ori(self):
        """
        Get the xvector, yvector and zvector of a given beam

        :param self:
        :return: xvec, yvec and up
        """

        return self.xvec, self.yvec, self.up

    @property
    def xvec(self):
        """

        :return: Local X-vector
        :rtype: np.ndarray
        """
        return self._xvec

    @property
    def yvec(self):
        """

        :return: Local Y-vector
        :rtype: np.ndarray
        """
        return self._yvec

    @property
    def up(self):
        return self._up

    @property
    def n1(self):
        """

        :return:
        :rtype: Node
        """
        return self._n1

    @n1.setter
    def n1(self, value):
        self._n1 = value

    @property
    def n2(self):
        """

        :return:
        :rtype: Node
        """
        return self._n2

    @n2.setter
    def n2(self, value):
        self._n2 = value

    @property
    def bbox(self):
        """
        Bounding Box of beam

        """
        if self._bbox is None:
            if _Settings.use_occ_bounding_box_algo:
                raise NotImplementedError()
                # from OCC.Core.Bnd import Bnd_OBB
                # from OCC.Core.BRepBndLib import brepbndlib_AddOBB
                #
                # obb = Bnd_OBB()
                # brepbndlib_AddOBB(self.solid, obb)
                # if _Settings.use_oriented_bbox:
                #     # converts the bounding box to a shape
                #     aBaryCenter = obb.Center()
                #     aXDir = obb.XDirection()
                #     aYDir = obb.YDirection()
                #     aZDir = obb.ZDirection()
                #     aHalfX = obb.XHSize()
                #     aHalfY = obb.YHSize()
                #     aHalfZ = obb.ZHSize()
                #
                #     self._bbox = (), ()

            else:
                self._bbox = self._calc_bbox()

        return self._bbox

    @property
    def e1(self):
        """

        :return:
        :rtype: numpy.ndarray
        """
        return self._e1

    @e1.setter
    def e1(self, value):
        self._e1 = np.array(value)

    @property
    def e2(self):
        """

        :return:
        :rtype: numpy.ndarray
        """
        return self._e2

    @e2.setter
    def e2(self, value):
        self._e2 = np.array(value)

    @property
    def opacity(self):
        return self._opacity

    @property
    def curve(self):
        """

        :return:
        :rtype: ada.core.containers.SweepCurve
        """
        return self._curve

    @property
    def line(self):
        return make_wire_from_points([self.n1.p, self.n2.p])

    @property
    def shell(self):
        """

        :return:
        :rtype: OCC.Core.TopoDS.TopoDS_Compound
        """
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        from ..sections import ProfileBuilder

        geom = ProfileBuilder.build_representation(self, False)

        for pen in self.penetrations:
            geom = BRepAlgoAPI_Cut(geom, pen.geom).Shape()

        return geom

    @property
    def solid(self):
        """

        :return:
        :rtype: OCC.Core.TopoDS.TopoDS_Compound
        """
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        from ..sections import ProfileBuilder

        geom = ProfileBuilder.build_representation(self, True)

        for pen in self.penetrations:
            geom = BRepAlgoAPI_Cut(geom, pen.geom).Shape()

        return geom

    def __hash__(self):
        return hash(self.guid)

    def __eq__(self, other):
        """

        :param other:
        :type other: ada.Beam
        """
        for key, val in self.__dict__.items():
            if "parent" in key or key in ["_ifc_settings", "_ifc_elem"]:
                continue
            oval = other.__dict__[key]

            if type(val) in (list, tuple, np.ndarray):
                if False in [x == y for x, y in zip(oval, val)]:
                    return False
            try:
                res = oval != val
            except ValueError as e:
                logging.error(e)
                return True

            if res is True:
                return False

        return True

    def __repr__(self):
        p1s = self.n1.p.tolist()
        p2s = self.n2.p.tolist()
        secn = self.section.sec_str
        matn = self.material.name
        return f'Beam("{self.name}", {p1s}, {p2s}, {secn}, {matn})'


class Plate(BackendGeom):
    """
    A plate object. The plate element covers all plate elements. Contains a dictionary with each point of the plate
    described by an id (index) and a Node object.

    :param name: Name of plate
    :param nodes: List of coordinates that make up the plate. Points can be Node, tuple or list
    :param t: Thickness of plate
    :param mat: Material. Can be either Material object or built-in materials ('S420' or 'S355')
    :param origin: Explicitly define origin of plate. If not set
    """

    def __init__(
        self,
        name,
        nodes,
        t,
        mat="S420",
        use3dnodes=False,
        origin=None,
        normal=None,
        xdir=None,
        pl_id=None,
        offset=None,
        colour=None,
        parent=None,
        ifc_geom=None,
        opacity=None,
        metadata=None,
        tol=None,
        units="m",
        ifc_elem=None,
        guid=None,
        **kwargs,
    ):
        # TODO: Support generation of plate object from IFC elem
        super().__init__(name, guid=guid, metadata=metadata, units=units, ifc_elem=ifc_elem)

        points2d = None
        points3d = None
        if ifc_elem is not None:
            props = self._import_from_ifc_plate(ifc_elem)
            self.name = props["name"]
            self.guid = ifc_elem.GlobalId
            t = props["t"]
            points2d = props["nodes2d"]
            origin = props["origin"]
            normal = props["normal"]
            xdir = props["xdir"]
            ifc_geom = props["ifc_geom"]
            colour = props["colour"]
            opacity = props["opacity"]
        else:
            if use3dnodes is True:
                points3d = nodes
            else:
                points2d = nodes

        self._pl_id = pl_id
        self._material = mat if isinstance(mat, Material) else Material(mat, mat_model=CarbonSteel(mat))
        self._t = t

        if tol is None:
            if units == "mm":
                tol = _Settings.mmtol
            elif units == "m":
                tol = _Settings.mtol
            else:
                raise ValueError(f'Unknown unit "{units}"')

        self._poly = CurvePoly(
            points3d=points3d,
            points2d=points2d,
            normal=normal,
            origin=origin,
            xdir=xdir,
            tol=tol,
            parent=self,
            **kwargs,
        )
        self.colour = colour
        self._offset = offset
        self._parent = parent
        self._ifc_geom = ifc_geom
        self._bbox = None
        self._opacity = opacity

    def _generate_ifc_elem(self):
        from ada.core.constants import O, X, Z
        from ada.ifc.utils import (
            add_colour,
            create_global_axes,
            create_ifcindexpolyline,
            create_ifcpolyline,
            create_local_placement,
            create_property_set,
        )

        if self.parent is None:
            raise ValueError("Ifc element cannot be built without any parent element")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = a.user.to_ifc()
        parent = self.parent.ifc_elem

        xvec = self.poly.xdir
        zvec = self.poly.normal
        yvec = np.cross(zvec, xvec)

        # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
        plate_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)
        tra_mat = np.array([xvec, yvec, zvec])
        t_vec = [0, 0, self.t]
        origin = np.array(self.poly.origin)
        res = origin + np.dot(tra_mat, t_vec)
        polyline = create_ifcpolyline(f, [origin.astype(float).tolist(), res.tolist()])
        axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve2D", [polyline])
        extrusion_placement = create_global_axes(f, O, Z, X)
        points = [(float(n[0]), float(n[1]), float(n[2])) for n in self.poly.seg_global_points]
        seg_index = self.poly.seg_index
        polyline = create_ifcindexpolyline(f, points, seg_index)
        # polyline = self.create_ifcpolyline(f, point_list)
        ifcclosedprofile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

        ifcdir = f.createIfcDirection(zvec.astype(float).tolist())
        ifcextrudedareasolid = f.createIfcExtrudedAreaSolid(ifcclosedprofile, extrusion_placement, ifcdir, self.t)

        body = f.createIfcShapeRepresentation(context, "Body", "SolidModel", [ifcextrudedareasolid])

        if "hidden" in self.metadata.keys():
            if self.metadata["hidden"] is True:
                a.presentation_layers.append(body)

        product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body])

        ifc_plate = f.createIfcPlate(
            self.guid,
            owner_history,
            self.name,
            self.name,
            None,
            plate_placement,
            product_shape,
            None,
        )

        self._ifc_elem = ifc_plate

        # Add colour
        if self.colour is not None:
            add_colour(f, ifcextrudedareasolid, str(self.colour), self.colour)

        # Add penetrations
        # elements = []
        for pen in self.penetrations:
            # elements.append(pen.ifc_opening)
            f.createIfcRelVoidsElement(
                create_guid(),
                owner_history,
                None,
                None,
                ifc_plate,
                pen.ifc_opening,
            )

        # if "props" in self.metadata.keys():
        props = create_property_set("Properties", f, self.metadata)
        f.createIfcRelDefinesByProperties(
            create_guid(),
            owner_history,
            "Properties",
            None,
            [ifc_plate],
            props,
        )

        return ifc_plate

    def _import_from_ifc_plate(self, ifc_elem, ifc_settings=None):
        from ada.ifc.utils import (
            get_ifc_shape,
            get_name,
            import_indexedpolycurve,
            import_polycurve,
        )

        a = self.get_assembly()
        if a is None:
            # use default ifc_settings
            ifc_settings = _Settings.default_ifc_settings()
        else:
            ifc_settings = a.ifc_settings

        pdct_shape, color, alpha = get_ifc_shape(ifc_elem, ifc_settings)
        atts = dict(ifc_geom=pdct_shape, colour=color, opacity=alpha)

        # TODO: Fix interpretation of IfcIndexedPolyCurve. Should pass origin to get actual 2d coordinates.

        # Adding Axis information
        axes = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Axis"]
        if len(axes) != 1:
            raise NotImplementedError("Geometry with multiple axis is not currently supported")
        axis = axes[0]
        origin = axis.Items[0].Points[0].Coordinates
        atts.update(origin=origin)

        # Adding Body
        bodies = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Body"]
        if len(bodies) != 1:
            raise NotImplementedError("Geometry with multiple bodies is not currently supported")
        if len(bodies[0].Items) != 1:
            raise NotImplementedError("Body with multiple Items is not currently supported")

        item = bodies[0].Items[0]
        t = item.Depth
        normal = item.ExtrudedDirection.DirectionRatios
        xdir = item.Position.RefDirection.DirectionRatios
        outer_curve = item.SweptArea.OuterCurve

        if outer_curve.is_a("IfcIndexedPolyCurve"):
            nodes2d = import_indexedpolycurve(outer_curve, normal, xdir, origin)
        else:
            nodes2d = import_polycurve(outer_curve, normal, xdir)

        atts.update(dict(normal=normal, xdir=xdir))

        if nodes2d is None or t is None:
            raise ValueError("Unable to get plate nodes or thickness")

        name = get_name(ifc_elem)
        if name is None:
            raise ValueError("Name cannot be none")
        return dict(name=name, nodes2d=nodes2d, t=t, use3dnodes=False, **atts)

    @property
    def id(self):
        return self._pl_id

    @id.setter
    def id(self, value):
        self._pl_id = value

    @property
    def offset(self):
        return self._offset

    @property
    def t(self):
        """

        :return: Plate thickness
        """
        return self._t

    @property
    def material(self):
        """

        :return:
        :rtype: Material
        """
        return self._material

    @material.setter
    def material(self, value):
        """

        :param value:
        :type value: Material
        """
        self._material = value

    @property
    def n(self):
        """


        :return: Normal vector
        :rtype: np.ndarray
        """
        return self.poly.normal

    @property
    def nodes(self):
        """

        :return:
        :rtype: list
        """
        return self.poly.nodes

    @property
    def poly(self):
        """

        :return:
        :rtype: ada.core.containers.PolyCurve
        """
        return self._poly

    @property
    def bbox(self):
        """

        :return: Bounding box of plate
        """
        if self._bbox is None:
            self._bbox = self.poly.calc_bbox(self.t)
        return self._bbox

    def volume_cog(self):
        """

        :return: Get a point in the plate's volumetric COG (based on bounding box).
        """

        return np.array(
            [
                (self.bbox[0][0] + self.bbox[0][1]) / 2,
                (self.bbox[1][0] + self.bbox[1][1]) / 2,
                (self.bbox[2][0] + self.bbox[2][1]) / 2,
            ]
        )

    @property
    def metadata(self):
        return self._metadata

    @property
    def line(self):
        return self._poly.wire

    @property
    def shell(self):
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        geom = self.poly.face
        for pen in self.penetrations:
            geom = BRepAlgoAPI_Cut(geom, pen.geom).Shape()
        return geom

    @property
    def solid(self):
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        geom = self._poly.make_extruded_solid(self.t)

        for pen in self.penetrations:
            geom = BRepAlgoAPI_Cut(geom, pen.geom).Shape()

        return geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if self._units != value:
            scale_factor = self._unit_conversion(self._units, value)
            tol = _Settings.mmtol if value == "mm" else _Settings.mtol
            self._t *= scale_factor
            self.poly.scale(scale_factor, tol)
            for pen in self.penetrations:
                pen.units = value
            self.material.units = value
            self._units = value

    def __repr__(self):
        return f"Plate({self.name}, t:{self.t}, {self.material})"


class Wall(BackendGeom):
    _valid_offset_str = ["CENTER", "LEFT", "RIGHT"]
    """
    A wall object representing

    :param points: Points making up wall
    :param height: Height
    :param thickness: Thickness
    :param origin: Origin
    :param offset: Wall offset from points making up the wall centerline. Accepts float | CENTER | LEFT | RIGHT
    """

    def __init__(
        self,
        name,
        points,
        height,
        thickness,
        origin=(0.0, 0.0, 0.0),
        offset="CENTER",
        metadata=None,
        colour=None,
        ifc_elem=None,
        units="m",
        guid=None,
    ):
        super().__init__(name, guid=guid, metadata=metadata, units=units, ifc_elem=ifc_elem)

        if ifc_elem is not None:
            self._import_from_ifc(ifc_elem)

        self._name = name
        self._origin = origin
        self.colour = colour
        new_points = []
        for p in points:
            np_ = [float(c) for c in p]
            if len(np_) == 2:
                np_ += [0.0]
            new_points.append(tuple(np_))
        self._points = new_points
        self._segments = list(zip(self._points[:-1], self.points[1:]))
        self._height = height
        self._thickness = thickness
        self._openings = []
        self._doors = []
        self._inserts = []
        if type(offset) is str:
            if offset not in Wall._valid_offset_str:
                raise ValueError(f'Unknown string input "{offset}" for offset')
            if offset == "CENTER":
                self._offset = 0.0
            elif offset == "LEFT":
                self._offset = -self._thickness / 2
            else:  # offset = RIGHT
                self._offset = self._thickness / 2
        else:
            if type(offset) not in (float, int):
                raise ValueError("Offset can only be string or float, int")
            self._offset = offset

    def add_insert(self, insert, wall_segment, off_x, off_z):
        """

        :param insert:
        :param wall_segment:
        :param off_x:
        :param off_z:
        :return:
        """
        from OCC.Extend.ShapeFactory import get_oriented_boundingbox

        xvec, yvec, zvec = self.get_segment_props(wall_segment)
        p1, p2 = self._segments[wall_segment]

        start = p1 + yvec * (self._thickness / 2 + self.offset) + xvec * off_x + zvec * off_z
        insert._depth = self._thickness
        insert._origin = start
        insert._lx = xvec
        insert._ly = zvec
        insert._lz = yvec
        insert.build_geom()

        frame = insert.shapes[0]
        center, dim, oobb_shp = get_oriented_boundingbox(frame.geom)
        x, y, z = center.X(), center.Y(), center.Z()
        dx, dy, dz = dim[0], dim[1], dim[2]

        x0 = x - abs(dx / 2)
        y0 = y - abs(dy / 2)
        z0 = z - abs(dz / 2)

        x1 = x + abs(dx / 2)
        y1 = y + abs(dy / 2)
        z1 = z + abs(dz / 2)

        self._inserts.append(insert)
        self._openings.append([wall_segment, insert, (x0, y0, z0), (x1, y1, z1)])

        tol = 0.4
        wi = insert

        p1 = wi.origin - yvec * (wi.depth / 2 + tol)
        p2 = wi.origin + yvec * (wi.depth / 2 + tol) + xvec * wi.width + zvec * wi.height

        self._penetrations.append(PrimBox("my_pen", p1, p2))

    def get_segment_props(self, wall_segment):
        """

        :param wall_segment:
        :return:
        """
        if wall_segment > len(self._segments):
            raise ValueError(f"Wall segment id should be equal or less than {len(self._segments)}")
        p1, p2 = self._segments[wall_segment]
        xvec = unit_vector(np.array(p2) - np.array(p1))
        zvec = np.array([0, 0, 1])
        yvec = unit_vector(np.cross(zvec, xvec))

        return xvec, yvec, zvec

    def _import_from_ifc(self, ifc_elem):
        raise NotImplementedError("Import of IfcWall is not yet supported")

    def _generate_ifc_elem(self):
        from ada.concepts.levels import Part
        from ada.core.constants import O, X, Z
        from ada.ifc.utils import (
            add_negative_extrusion,
            create_global_axes,
            create_ifcextrudedareasolid,
            create_ifcpolyline,
            create_local_placement,
            create_property_set,
        )

        if self.parent is None:
            raise ValueError("Ifc element cannot be built without any parent element")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = a.user.to_ifc()
        parent = self.parent.ifc_elem
        elevation = self.origin[2]

        # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
        wall_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

        # polyline = self.create_ifcpolyline(f, [(0.0, 0.0, 0.0), (5.0, 0.0, 0.0)])
        polyline = create_ifcpolyline(f, self.points)
        axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve2D", [polyline])

        extrusion_placement = create_global_axes(f, (0.0, 0.0, float(elevation)), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

        polyline = create_ifcpolyline(f, self.extrusion_area)
        profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

        solid = create_ifcextrudedareasolid(f, profile, extrusion_placement, (0.0, 0.0, 1.0), self.height)
        body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid])

        if "hidden" in self.metadata.keys():
            if self.metadata["hidden"] is True:
                a.presentation_layers.append(body)

        product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body])

        wall_el = f.createIfcWall(
            self.guid,
            owner_history,
            self.name,
            "An awesome wall",
            None,
            wall_placement,
            product_shape,
            None,
        )

        # Check for penetrations
        elements = []
        if len(self._inserts) > 0:
            for i, insert in enumerate(self._inserts):
                opening_element = add_negative_extrusion(
                    f, O, Z, X, insert.height, self.openings_extrusions[i], wall_el
                )
                if issubclass(type(insert), Part) is False:
                    raise ValueError(f'Unrecognized type "{type(insert)}"')
                insert_el = self._add_ifc_insert_elem(insert, opening_element, wall_el)
                elements.append(opening_element)
                elements.append(insert_el)

        f.createIfcRelContainedInSpatialStructure(
            create_guid(),
            owner_history,
            "Wall Elements",
            None,
            [wall_el] + elements,
            parent,
        )

        props = create_property_set("Properties", f, self.metadata)
        f.createIfcRelDefinesByProperties(
            create_guid(),
            owner_history,
            "Properties",
            None,
            [wall_el],
            props,
        )

        return wall_el

    def _add_ifc_insert_elem(self, insert, opening_element, wall_el):
        import ifcopenshell.geom

        from ada.core.constants import O, X, Z
        from ada.ifc.utils import create_local_placement

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = a.user.to_ifc()
        schema = a.ifc_file.wrapped_data.schema

        # Create a simplified representation for the Window
        insert_placement = create_local_placement(f, O, Z, X, wall_el.ObjectPlacement)
        if len(insert.shapes) > 1:
            raise ValueError("More than 1 shape is currently not allowed for Wall inserts")
        shape = insert.shapes[0].geom
        insert_shape = f.add(ifcopenshell.geom.serialise(schema=schema, string_or_shape=shape))

        # Link to representation context
        for rep in insert_shape.Representations:
            rep.ContextOfItems = context

        ifc_type = insert.metadata["ifc_type"]

        if ifc_type == "IfcWindow":
            ifc_insert = f.createIfcWindow(
                create_guid(),
                owner_history,
                "Window",
                "An awesome window",
                None,
                insert_placement,
                insert_shape,
                None,
                None,
            )
        elif ifc_type == "IfcDoor":
            ifc_insert = f.createIfcDoor(
                create_guid(),
                owner_history,
                "Door",
                "An awesome Door",
                None,
                insert_placement,
                insert_shape,
                None,
                None,
            )
        else:
            raise ValueError(f'Currently unsupported ifc_type "{ifc_type}"')

        # Relate the window to the opening element
        f.createIfcRelFillsElement(
            create_guid(),
            owner_history,
            None,
            None,
            opening_element,
            ifc_insert,
        )
        return ifc_insert

    @property
    def height(self):
        return self._height

    @property
    def thickness(self):
        return self._thickness

    @property
    def origin(self):
        return self._origin

    @property
    def points(self):
        return self._points

    @property
    def offset(self):
        """

        :return:
        :rtype: float
        """
        return self._offset

    @property
    def extrusion_area(self):
        from ada.core.utils import intersect_calc, is_parallel

        area_points = []
        vpo = [np.array(p) for p in self.points]
        p2 = None
        yvec = None
        prev_xvec = None
        prev_yvec = None
        zvec = np.array([0, 0, 1])
        # Inner line
        for p1, p2 in zip(vpo[:-1], vpo[1:]):
            xvec = p2 - p1
            yvec = unit_vector(np.cross(zvec, xvec))
            new_point = p1 + yvec * (self._thickness / 2) + yvec * self.offset
            if prev_xvec is not None:
                if is_parallel(xvec, prev_xvec) is False:
                    prev_p = area_points[-1]
                    # next_point = p2 + yvec * (self._thickness / 2) + yvec * self.offset
                    # c_p = prev_yvec * (self._thickness / 2) + prev_yvec * self.offset
                    AB = prev_xvec
                    CD = xvec
                    s, t = intersect_calc(prev_p, new_point, AB, CD)
                    sAB = prev_p + s * AB
                    new_point = sAB
            area_points.append(new_point)
            prev_xvec = xvec
            prev_yvec = yvec

        # Add last point
        area_points.append((p2 + yvec * (self._thickness / 2) + yvec * self.offset))
        area_points.append((p2 - yvec * (self._thickness / 2) + yvec * self.offset))

        reverse_points = []
        # Outer line
        prev_xvec = None
        prev_yvec = None
        for p1, p2 in zip(vpo[:-1], vpo[1:]):
            xvec = p2 - p1
            yvec = unit_vector(np.cross(xvec, np.array([0, 0, 1])))
            new_point = p1 - yvec * (self._thickness / 2) + yvec * self.offset
            if prev_xvec is not None:
                if is_parallel(xvec, prev_xvec) is False:
                    prev_p = reverse_points[-1]
                    c_p = prev_yvec * (self._thickness / 2) - prev_yvec * self.offset
                    new_point -= c_p
            reverse_points.append(new_point)
            prev_xvec = xvec
            prev_yvec = yvec

        reverse_points.reverse()
        area_points += reverse_points

        new_points = []
        for p in area_points:
            new_points.append(tuple([float(c) for c in p]))

        return new_points

    @property
    def openings_extrusions(self):
        from ada.concepts.levels import Part

        op_extrudes = []
        if self.units == "m":
            tol = 0.4
        else:
            tol = 400
        for op in self._openings:
            ws, wi, mi, ma = op
            xvec, yvec, zvec = self.get_segment_props(ws)
            assert issubclass(type(wi), Part)
            p1 = wi.origin - yvec * (wi.depth / 2 + tol)
            p2 = p1 + yvec * (wi.depth + tol * 2)
            p3 = p2 + xvec * wi.width
            p4 = p3 - yvec * (wi.depth + tol * 2)
            op_extrudes.append([p1.tolist(), p2.tolist(), p3.tolist(), p4.tolist(), p1.tolist()])
        return op_extrudes

    @property
    def metadata(self):
        return self._metadata

    @property
    def shell(self):
        poly = CurvePoly(points3d=self.extrusion_area, parent=self)
        return poly.face

    @property
    def solid(self):
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        poly = CurvePoly(points3d=self.extrusion_area, parent=self)
        geom = poly.make_extruded_solid(self.height)
        for pen in self.penetrations:
            geom = BRepAlgoAPI_Cut(geom, pen.geom).Shape()

        return geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            scale_factor = self._unit_conversion(self._units, value)
            self._height *= scale_factor
            self._thickness *= scale_factor
            self._offset *= scale_factor
            self._origin = tuple([x * scale_factor for x in self._origin])
            self._points = [tuple([x * scale_factor for x in p]) for p in self.points]
            self._segments = list(zip(self._points[:-1], self.points[1:]))
            for pen in self._penetrations:
                pen.units = value
            for opening in self._openings:
                opening[2] = tuple([x * scale_factor for x in opening[2]])
                opening[3] = tuple([x * scale_factor for x in opening[3]])

            for insert in self._inserts:
                insert.units = value

            self._units = value

    def __repr__(self):
        return f"Wall({self.name})"


class Section(Backend):
    """
    A Section object.

    See the

    :param name:
    :param h:
    :param w_top:
    :param w_btn:
    :param t_w:
    :param t_ftop:
    :param t_fbtn:
    :param r:
    :param wt:
    :param parent:
    :param sec_type:
    :param sec_str:
    :param from_str: Parse a
    :param genprops:
    :param metadata:
    """

    def __init__(
        self,
        name,
        sec_type=None,
        h=None,
        w_top=None,
        w_btn=None,
        t_w=None,
        t_ftop=None,
        t_fbtn=None,
        r=None,
        wt=None,
        sec_id=None,
        parent=None,
        sec_str=None,
        from_str=None,
        outer_poly=None,
        inner_poly=None,
        genprops=None,
        metadata=None,
        units="m",
        ifc_elem=None,
        guid=None,
    ):
        super(Section, self).__init__(name, guid, metadata, units, ifc_elem=ifc_elem)
        self._type = sec_type
        self._h = h
        self._w_top = w_top
        self._w_btn = w_btn
        self._t_w = t_w
        self._t_ftop = t_ftop
        self._t_fbtn = t_fbtn
        self._r = r
        self._wt = wt
        self._id = sec_id
        self._outer_poly = outer_poly
        self._inner_poly = inner_poly
        self._sec_str = sec_str
        self._parent = parent

        self._ifc_profile = None
        self._ifc_beam_type = None

        if ifc_elem is not None:
            props = self._import_from_ifc_profile(ifc_elem)
            self.__dict__.update(props.__dict__)

        if from_str is not None:
            from ada.sections.utils import interpret_section_str

            if units == "m":
                scalef = 0.001
            elif units == "mm":
                scalef = 1.0
            else:
                raise ValueError(f'Unknown units "{units}"')
            sec, tap = interpret_section_str(from_str, scalef, units=units)
            self.__dict__.update(sec.__dict__)
        elif outer_poly:
            self._type = "poly"

        self._genprops = GeneralProperties() if genprops is None else genprops
        self._genprops.edit(parent=self)

    def __eq__(self, other):
        for key, val in self.__dict__.items():
            if "parent" in key or "_ifc" in key or key in ["_sec_id", "_guid"]:
                continue
            oval = other.__dict__[key]
            if oval != val:
                return False

        return True

    def _generate_ifc_section_data(self):
        from ada.ifc.utils import create_ifcindexpolyline, create_ifcpolyline

        a = self.parent.parent.get_assembly()
        f = a.ifc_file

        sec_props = dict(ProfileType="AREA", ProfileName=self.name)

        if SectionCat.is_i_profile(self.type):
            if _Settings.use_param_profiles is False:
                outer_curve, inner_curve, disconnected = self.cross_sec(True)
                polyline = create_ifcpolyline(f, outer_curve)

                ifc_sec_type = "IfcArbitraryClosedProfileDef"
                sec_props.update(dict(OuterCurve=polyline))
            else:
                if SectionCat.is_strong_axis_symmetric(self) is False:
                    logging.error(
                        "Note! Not using IfcAsymmetricIShapeProfileDef as it is not supported by ifcopenshell v IFC4"
                    )
                    # ifc_sec_type = "IfcAsymmetricIShapeProfileDef"
                    # sec_props.update(
                    #     dict(
                    #         TopFlangeWidth=self.w_top,
                    #         BottomFlangeWidth=self.w_btn,
                    #         OverallDepth=self.h,
                    #         WebThickness=self.t_w,
                    #         TopFlangeThickness=self.t_ftop,
                    #         BottomFlangeThickness=self.t_fbtn,
                    #     )
                    # )

                ifc_sec_type = "IfcIShapeProfileDef"
                sec_props.update(
                    dict(
                        OverallWidth=self.w_top,
                        OverallDepth=self.h,
                        WebThickness=self.t_w,
                        FlangeThickness=self.t_ftop,
                    )
                )

        elif SectionCat.is_hp_profile(self.type):
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            points = [f.createIfcCartesianPoint(p) for p in outer_curve]
            ifc_polyline = f.createIfcPolyLine(points)
            ifc_sec_type = "IfcArbitraryClosedProfileDef"
            sec_props.update(dict(OuterCurve=ifc_polyline))

            if _Settings.use_param_profiles is True:
                logging.error(f'Export of "{self.type}" profile to parametric IFC profile is not yet added')

        elif SectionCat.is_box_profile(self.type):
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            outer_points = [f.createIfcCartesianPoint(p) for p in outer_curve + [outer_curve[0]]]
            inner_points = [f.createIfcCartesianPoint(p) for p in inner_curve + [inner_curve[0]]]
            inner_curve = f.createIfcPolyLine(inner_points)
            outer_curve = f.createIfcPolyLine(outer_points)
            ifc_sec_type = "IfcArbitraryProfileDefWithVoids"
            sec_props.update(dict(OuterCurve=outer_curve, InnerCurves=[inner_curve]))

            if _Settings.use_param_profiles is True:
                logging.error(f'Export of "{self.type}" profile to parametric IFC profile is not yet added')

        elif self.type in SectionCat.circular:
            ifc_sec_type = "IfcCircleProfileDef"
            sec_props.update(dict(Radius=self.r))
        elif self.type in SectionCat.tubular:
            ifc_sec_type = "IfcCircleHollowProfileDef"
            sec_props.update(dict(Radius=self.r, WallThickness=self.wt))
        elif self.type in SectionCat.general:
            logging.error("Note! Creating a Circle profile from general section (just for visual inspection as of now)")
            r = np.sqrt(self.properties.Ax / np.pi)
            ifc_sec_type = "IfcCircleProfileDef"
            sec_props.update(dict(Radius=r))
        elif self.type in SectionCat.flatbar:
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            polyline = create_ifcpolyline(f, outer_curve)
            ifc_sec_type = "IfcArbitraryClosedProfileDef"
            sec_props.update(dict(OuterCurve=polyline))

            if _Settings.use_param_profiles is True:
                logging.error(f'Export of "{self.type}" profile to parametric IFC profile is not yet added')

        elif self.type in SectionCat.channels:
            if _Settings.use_param_profiles is False:
                outer_curve, inner_curve, disconnected = self.cross_sec(True)
                polyline = create_ifcpolyline(f, outer_curve)
                ifc_sec_type = "IfcArbitraryClosedProfileDef"
                sec_props.update(dict(OuterCurve=polyline))
            else:
                ifc_sec_type = "IfcUShapeProfileDef"
                sec_props.update(
                    dict(Depth=self.h, FlangeWidth=self.w_top, WebThickness=self.t_w, FlangeThickness=self.t_ftop)
                )
        elif self.type == "poly":
            opoly = self.poly_outer
            opoints = [(float(n[0]), float(n[1]), float(n[2])) for n in opoly.seg_global_points]
            opolyline = create_ifcindexpolyline(f, opoints, opoly.seg_index)
            if self.poly_inner is None:
                ifc_sec_type = "IfcArbitraryClosedProfileDef"
                sec_props.update(dict(OuterCurve=opolyline))
            else:
                ipoly = self.poly_inner
                ipoints = [(float(n[0]), float(n[1]), float(n[2])) for n in ipoly.seg_global_points]
                ipolyline = create_ifcindexpolyline(f, ipoints, ipoly.seg_index)
                ifc_sec_type = "IfcArbitraryProfileDefWithVoids"
                sec_props.update(dict(OuterCurve=opolyline, InnerCurves=[ipolyline]))
        else:
            raise ValueError(f'Have yet to implement section type "{self.type}"')

        if self.name is None:
            raise ValueError("Name cannot be None!")

        profile = f.create_entity(ifc_sec_type, **sec_props)

        beamtype = f.createIfcBeamType(
            create_guid(),
            a.user.to_ifc(),
            self.name,
            self.sec_str,
            None,
            None,
            None,
            None,
            None,
            "BEAM",
        )
        return profile, beamtype

    def _import_from_ifc_profile(self, ifc_elem):
        from ada.sections.utils import interpret_section_str

        self._ifc_profile = ifc_elem
        try:
            sec, tap = interpret_section_str(ifc_elem.ProfileName)
        except ValueError as e:
            logging.debug(f'Unable to process section "{ifc_elem.ProfileName}" -> error: "{e}" ')
            sec = None
        if sec is None:
            if ifc_elem.is_a("IfcIShapeProfileDef"):
                sec = Section(
                    name=ifc_elem.ProfileName,
                    sec_type="IG",
                    h=ifc_elem.OverallDepth,
                    w_top=ifc_elem.OverallWidth,
                    w_btn=ifc_elem.OverallWidth,
                    t_w=ifc_elem.WebThickness,
                    t_ftop=ifc_elem.FlangeThickness,
                    t_fbtn=ifc_elem.FlangeThickness,
                )
            elif ifc_elem.is_a("IfcCircleHollowProfileDef"):
                sec = Section(
                    name=ifc_elem.ProfileName,
                    sec_type="TUB",
                    r=ifc_elem.Radius,
                    wt=ifc_elem.WallThickness,
                )
            elif ifc_elem.is_a("IfcUShapeProfileDef"):
                raise NotImplementedError(f'IFC section type "{ifc_elem}" is not yet implemented')
                # sec = Section(ifc_elem.ProfileName)
            else:
                raise NotImplementedError(f'IFC section type "{ifc_elem}" is not yet implemented')
        return sec

    @property
    def type(self):
        return self._type

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        if type(value) is not int:
            raise ValueError
        self._id = value

    @property
    def h(self):
        return self._h

    @property
    def w_top(self):
        return self._w_top

    @w_top.setter
    def w_top(self, value):
        self._w_top = value

    @property
    def w_btn(self):
        return self._w_btn

    @w_btn.setter
    def w_btn(self, value):
        self._w_btn = value

    @property
    def t_w(self):
        return self._t_w

    @property
    def t_ftop(self):
        return self._t_ftop

    @property
    def t_fbtn(self):
        """
        Thickness of bottom flange

        :return:
        """
        return self._t_fbtn

    @property
    def r(self):
        """
        Radius (Outer)

        :return:
        """
        return self._r

    @property
    def wt(self):
        """
        Wall thickness
        :return:
        """
        return self._wt

    @property
    def sec_str(self):
        def s(x):
            return x / 0.001

        if self.type in SectionCat.box + SectionCat.igirders + SectionCat.tprofiles + SectionCat.shs + SectionCat.rhs:
            sec_str = "{}{:g}x{:g}x{:g}x{:g}".format(self.type, s(self.h), s(self.w_top), s(self.t_w), s(self.t_ftop))
        elif self.type in SectionCat.tubular:
            sec_str = "{}{:g}x{:g}".format(self.type, s(self.r), s(self.wt))
        elif self.type in SectionCat.circular:
            sec_str = "{}{:g}".format(self.type, s(self.r))
        elif self.type in SectionCat.angular:
            sec_str = "{}{:g}x{:g}".format(self.type, s(self.h), s(self.t_w))
        elif self.type in SectionCat.iprofiles:
            sec_str = self._sec_str
        elif self.type in SectionCat.channels:
            sec_str = "{}{:g}".format(self.type, s(self.h))
        elif self.type in SectionCat.general:
            sec_str = "{}{}".format(self.type, self.id)
        elif self.type in SectionCat.flatbar:
            sec_str = f"{self.type}{s(self.h)}x{s(self.w_top)}"
        elif self.type == "poly":
            sec_str = "PolyCurve"
        else:
            raise ValueError(f'Section type "{self.type}" has not been given a section str')
        return sec_str.replace(".", "_") if sec_str is not None else None

    @property
    def properties(self):
        """

        :return:
        :rtype: GeneralProperties
        """
        return self._genprops

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if self._units != value:
            scale_factor = self._unit_conversion(self._units, value)

            if self.poly_inner is not None:
                self.poly_inner.scale(scale_factor, _Settings.point_tol)

            if self.poly_outer is not None:
                self.poly_outer.scale(scale_factor, _Settings.point_tol)

            vals = ["h", "w_top", "w_btn", "t_w", "t_ftop", "t_fbtn", "r", "wt"]

            for key in self.__dict__.keys():
                if self.__dict__[key] is not None:
                    if key[1:] in vals:
                        self.__dict__[key] *= scale_factor
            self._units = value

    @property
    def ifc_profile(self):
        if self._ifc_profile is None:
            self._ifc_profile, self._ifc_beam_type = self._generate_ifc_section_data()
        return self._ifc_profile

    @property
    def ifc_beam_type(self):
        if self._ifc_beam_type is None:
            self._ifc_profile, self._ifc_beam_type = self._generate_ifc_section_data()
        return self._ifc_beam_type

    @property
    def poly_outer(self):
        """

        :return:
        :rtype: CurvePoly
        """
        return self._outer_poly

    @property
    def poly_inner(self):
        """

        :return:
        :rtype: CurvePoly
        """
        return self._inner_poly

    def cross_sec(self, solid_repre=True):
        """

        :param solid_repre: Solid Representation
        :return:
        """
        from ada.sections import ProfileBuilder

        if self.type in SectionCat.angular:
            outer_curve, inner_curve, disconnected = ProfileBuilder.angular(self, solid_repre)
        elif self.type in SectionCat.iprofiles + SectionCat.igirders:
            outer_curve, inner_curve, disconnected = ProfileBuilder.iprofiles(self, solid_repre)
        elif self.type in SectionCat.box + SectionCat.rhs + SectionCat.shs:
            outer_curve, inner_curve, disconnected = ProfileBuilder.box(self, solid_repre)
        elif self.type in SectionCat.tubular:
            outer_curve, inner_curve, disconnected = ProfileBuilder.tubular(self, solid_repre)
        elif self.type in SectionCat.circular:
            outer_curve, inner_curve, disconnected = ProfileBuilder.circular(self, solid_repre)
        elif self.type in SectionCat.flatbar:
            outer_curve, inner_curve, disconnected = ProfileBuilder.flatbar(self, solid_repre)
        elif self.type in SectionCat.general:
            outer_curve, inner_curve, disconnected = ProfileBuilder.gensec(self, solid_repre)
        elif self.type in SectionCat.channels:
            outer_curve, inner_curve, disconnected = ProfileBuilder.channel(self, solid_repre)
        else:
            if self.poly_outer is not None:
                return self.poly_outer, None, None
            else:
                raise ValueError(
                    "Currently geometry build is unsupported for profile type {ptype}".format(ptype=self.type)
                )

        return outer_curve, inner_curve, disconnected

    def cross_sec_shape(
        self,
        solid_repre=True,
        origin=(0.0, 0.0, 0.0),
        xdir=(1.0, 0.0, 0.0),
        normal=(0.0, 0.0, 1.0),
    ):
        """

        :param solid_repre: Solid Representation
        :param origin:
        :param xdir:
        :param normal:
        :return:
        """
        from OCC.Extend.ShapeFactory import make_face, make_wire

        from ada.core.utils import local_2_global_nodes
        from ada.occ.utils import make_circle, make_face_w_cutout, make_wire_from_points

        def points2wire(curve):
            poly = CurvePoly(points2d=curve, origin=origin, xdir=xdir, normal=normal, parent=self)
            return poly.wire

        if self.type in SectionCat.tubular:
            outer_shape = make_wire([make_circle(origin, normal, self.r)])
            inner_shape = make_wire([make_circle(origin, normal, self.r - self.wt)])
        elif self.type in SectionCat.circular:
            outer_shape = make_wire([make_circle(origin, normal, self.r)])
            inner_shape = None
        else:
            outer_curve, inner_curve, disconnected = self.cross_sec(solid_repre)
            if type(outer_curve) is CurvePoly:
                assert isinstance(outer_curve, CurvePoly)
                outer_curve.origin = origin
                face = outer_curve.face
                return face
            if inner_curve is not None:
                # inner_shape = wp2.polyline(inner_curve).close().wire().toOCC()
                inner_shape = points2wire(inner_curve)
                # inner_poly = PolyCurve(points2d=inner_curve, origin=origin, xdir=xdir, normal=normal)
            else:
                inner_shape = None

            if disconnected is False:
                # outer_shape = wp.polyline(outer_curve).close().wire().toOCC()
                outer_shape = points2wire(outer_curve)
                # outer_shape = outer_poly.wire
            else:
                # outer_shape = [wp.polyline(wi).close().wire().toOCC() for wi in outer_curve]
                outer_shape = []
                for p1, p2 in outer_curve:
                    gp1 = local_2_global_nodes([p1], origin, xdir, normal)
                    gp2 = local_2_global_nodes([p2], origin, xdir, normal)
                    outer_shape.append(make_wire_from_points(gp1 + gp2))

        if inner_shape is not None and solid_repre is True:
            shape = make_face_w_cutout(make_face(outer_shape), inner_shape)
        else:
            shape = outer_shape

        return shape

    def _repr_html_(self):
        from IPython.display import display
        from ipywidgets import HBox

        from ada.visualize.renderer import SectionRenderer

        sec_render = SectionRenderer()
        fig, html = sec_render.build_display(self)
        display(HBox([fig, html]))

    def __hash__(self):
        return hash(self.guid)

    def __repr__(self):
        if self.type in SectionCat.circular + SectionCat.tubular:
            return f"Section({self.name}, {self.type}, r: {self.r}, wt: {self.wt})"
        elif self.type in SectionCat.general:
            p = self.properties
            return f"Section({self.name}, {self.type}, Ax: {p.Ax}, Ix: {p.Ix}, Iy: {p.Iy}, Iz: {p.Iz}, Iyz: {p.Iyz})"
        else:
            return (
                f"Section({self.name}, {self.type}, h: {self.h}, w_btn: {self.w_btn}, "
                f"w_top: {self.w_top}, t_fbtn: {self.t_fbtn}, t_ftop: {self.t_ftop}, t_w: {self.t_w})"
            )


class Material(Backend):
    """
    A basic material class


    :param name: Name of material
    :param mat_model: Material model. Default is ada.materials.metals.CarbonSteel
    :param mat_id: Material ID
    """

    def __init__(
        self,
        name,
        mat_model=CarbonSteel("S355"),
        mat_id=None,
        parent=None,
        metadata=None,
        units="m",
        ifc_mat=None,
        guid=None,
    ):
        super(Material, self).__init__(name, guid, metadata, units)
        self._mat_model = mat_model
        self._mat_id = mat_id
        self._parent = parent
        if ifc_mat is not None:
            props = self._import_from_ifc_mat(ifc_mat)
            self.__dict__.update(props)
        self._ifc_mat = None

    def __eq__(self, other):
        """
        Assuming uniqueness of Material Name and parent

        TODO: Make this check for same Material Model parameters

        :param other:
        :type other: Material
        :return:
        """
        # other_parent = other.__dict__['_parent']
        # other_name = other.__dict__['_name']
        # if self.name == other_name and other_parent == self.parent:
        #     return True
        # else:
        #     return False

        for key, val in self.__dict__.items():
            if "parent" in key or key == "_mat_id":
                continue
            if other.__dict__[key] != val:
                return False

        return True

    def _generate_ifc_mat(self):

        if self.parent is None:
            raise ValueError("Parent cannot be None")

        a = self.parent.get_assembly()
        f = a.ifc_file

        owner_history = a.user.to_ifc()

        ifc_mat = f.createIfcMaterial(self.name, None, "Steel")
        properties = []
        if type(self) is CarbonSteel:
            strength_grade = f.create_entity("IfcText", self.model.grade)
            properties.append(strength_grade)
        mass_density = f.create_entity("IfcMassDensityMeasure", float(self.model.rho))
        if self.model.sig_y is not None:
            yield_stress = f.create_entity("IfcPressureMeasure", float(self.model.sig_y))
            properties += [
                f.create_entity(
                    "IfcPropertySingleValue",
                    Name="YieldStress",
                    NominalValue=yield_stress,
                )
            ]
        young_modulus = f.create_entity("IfcModulusOfElasticityMeasure", float(self.model.E))
        poisson_ratio = f.create_entity("IfcPositiveRatioMeasure", float(self.model.v))
        therm_exp_coeff = f.create_entity("IfcThermalExpansionCoefficientMeasure", float(self.model.alpha))
        specific_heat = f.create_entity("IfcSpecificHeatCapacityMeasure", float(self.model.zeta))
        properties += [
            f.create_entity(
                "IfcPropertySingleValue",
                Name="YoungModulus",
                NominalValue=young_modulus,
            ),
            f.create_entity(
                "IfcPropertySingleValue",
                Name="PoissonRatio",
                NominalValue=poisson_ratio,
            ),
            f.create_entity(
                "IfcPropertySingleValue",
                Name="ThermalExpansionCoefficient",
                NominalValue=therm_exp_coeff,
            ),
            f.create_entity(
                "IfcPropertySingleValue",
                Name="SpecificHeatCapacity",
                NominalValue=specific_heat,
            ),
            f.create_entity("IfcPropertySingleValue", Name="MassDensity", NominalValue=mass_density),
        ]

        atts = {
            "GlobalId": create_guid(),
            "OwnerHistory": owner_history,
            "Name": self.name,
            "HasProperties": properties,
        }

        f.create_entity("IfcPropertySet", **atts)

        f.create_entity(
            "IfcMaterialProperties",
            **{
                "Name": "MaterialMechanical",
                "Description": "A Material property description",
                "Properties": properties,
                "Material": ifc_mat,
            },
        )
        return ifc_mat

    def _import_from_ifc_mat(self, ifc_mat):
        from ada.materials.metals import CarbonSteel, Metal

        mat_psets = ifc_mat.HasProperties
        scale_pascal = 1 if self.units == "mm" else 1e6
        scale_volume = 1 if self.units == "m" else 1e-9
        props = {entity.Name: entity.NominalValue[0] for entity in mat_psets[0].Properties}

        mat_props = dict(
            E=props.get("YoungModulus", 210000 * scale_pascal),
            sig_y=props.get("YieldStress", 355 * scale_pascal),
            rho=props.get("MassDensity", 7850 * scale_volume),
            v=props.get("PoissonRatio", 0.3),
            alpha=props.get("ThermalExpansionCoefficient", 1.2e-5),
            zeta=props.get("SpecificHeatCapacity", 1.15),
        )

        if "StrengthGrade" in props:
            mat_model = CarbonSteel(grade=props["StrengthGrade"], **mat_props)
        else:
            mat_model = Metal(sig_u=None, eps_p=None, sig_p=None, plasticitymodel=None, **mat_props)

        return dict(_name=ifc_mat.Name, _mat_model=mat_model)

    @property
    def id(self):
        return self._mat_id

    @id.setter
    def id(self, value):
        self._mat_id = value

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if value is None or any(x in value for x in [",", ".", "="]):
            raise ValueError("Material name cannot be None or contain special characters")
        self._name = value.strip()

    @property
    def model(self):
        return self._mat_model

    @model.setter
    def model(self, value):
        self._mat_model = value

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        self.model.units = value

    @property
    def ifc_mat(self):
        if self._ifc_mat is None:
            self._ifc_mat = self._generate_ifc_mat()
        return self._ifc_mat

    def __repr__(self):
        return f'Material(Name: "{self.name}" Material Model: "{self.model}'
