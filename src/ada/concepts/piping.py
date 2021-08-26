import logging

import numpy as np

from ada.ifc.utils import create_guid

from ..base import BackendGeom
from ..config import Settings as _Settings
from ..core.utils import Counter, angle_between, roundoff, unit_vector, vector_length
from ..materials.metals import CarbonSteel
from .curves import ArcSegment
from .points import Node
from .structural import Material


class Pipe(BackendGeom):
    """

    :param name:
    :param points:
    :param sec:
    :param mat:
    :param content:
    :param metadata:
    :param colour:
    :param units:
    :param guid:
    :param ifc_elem:
    """

    def __init__(
        self,
        name,
        points,
        sec,
        mat="S355",
        content=None,
        metadata=None,
        colour=None,
        units="m",
        guid=None,
        ifc_elem=None,
    ):
        super().__init__(name, guid=guid, metadata=metadata, units=units, ifc_elem=ifc_elem)

        self._section = sec
        sec.parent = self
        self._material = mat if isinstance(mat, Material) else Material(name=name + "_mat", mat_model=CarbonSteel(mat))
        self._content = content
        self.colour = colour

        self._n1 = points[0] if type(points[0]) is Node else Node(points[0], units=units)
        self._n2 = points[-1] if type(points[-1]) is Node else Node(points[-1], units=units)
        self._points = [Node(n, units=units) if type(n) is not Node else n for n in points]
        self._segments = []
        self._build_pipe()

    def _build_pipe(self):
        """

        :return:
        """
        from ada.core.curve_utils import make_arc_segment

        segs = []
        for p1, p2 in zip(self.points[:-1], self.points[1:]):
            if vector_length(p2.p - p1.p) == 0.0:
                logging.info("skipping zero length segment")
                continue
            segs.append([p1, p2])
        segments = segs

        seg_names = Counter(prefix=self.name + "_")

        # Make elbows and adjust segments
        props = dict(section=self.section, material=self.material, parent=self, units=self.units)
        angle_tol = 1e-1
        len_tol = _Settings.point_tol if self.units == "m" else _Settings.point_tol * 1000
        for i, (seg1, seg2) in enumerate(zip(segments[:-1], segments[1:])):
            p11, p12 = seg1
            p21, p22 = seg2
            vlen1 = vector_length(seg1[1].p - seg1[0].p)
            vlen2 = vector_length(seg2[1].p - seg2[0].p)

            if vlen1 < len_tol or vlen2 == len_tol:
                logging.error(f'Segment Length is below point tolerance for unit "{self.units}". Skipping')
                continue
            xvec1 = unit_vector(p12.p - p11.p)
            xvec2 = unit_vector(p22.p - p21.p)
            a = angle_between(xvec1, xvec2)
            res = True if abs(abs(a) - abs(np.pi)) < angle_tol or abs(abs(a) - 0.0) < angle_tol else False

            if res is True:
                self._segments.append(PipeSegStraight(next(seg_names), p11, p12, **props))
            else:
                if p12 != p21:
                    logging.error("No shared point found")

                if i != 0 and len(self._segments) > 0:
                    pseg = self._segments[-1]
                    prev_p = (pseg.p1.p, pseg.p2.p)
                else:
                    prev_p = (p11.p, p12.p)
                try:
                    seg1, arc, seg2 = make_arc_segment(prev_p[0], prev_p[1], p22.p, self.pipe_bend_radius * 0.99)
                except ValueError as e:
                    logging.error(f"Error: {e}")  # , traceback: "{traceback.format_exc()}"')
                    continue
                except RuntimeError as e:
                    logging.error(f"Error: {e}")  # , traceback: "{traceback.format_exc()}"')
                    continue

                if i == 0 or len(self._segments) == 0:
                    self._segments.append(
                        PipeSegStraight(
                            next(seg_names), Node(seg1.p1, units=self.units), Node(seg1.p2, units=self.units), **props
                        )
                    )
                else:
                    if len(self._segments) == 0:
                        continue
                    pseg = self._segments[-1]
                    pseg.p2 = Node(seg1.p2, units=self.units)

                self._segments.append(
                    PipeSegElbow(
                        next(seg_names) + "_Elbow",
                        Node(seg1.p1, units=self.units),
                        Node(p21.p, units=self.units),
                        Node(seg2.p2, units=self.units),
                        arc.radius,
                        **props,
                        arc_seg=arc,
                    )
                )
                self._segments.append(
                    PipeSegStraight(
                        next(seg_names), Node(seg2.p1, units=self.units), Node(seg2.p2, units=self.units), **props
                    )
                )

    @property
    def segments(self):
        """

        :return: List of either PipeSegStraight or PipeSegElbow
        :rtype: list
        """
        return self._segments

    @property
    def material(self):
        return self._material

    @material.setter
    def material(self, value):
        self._material = value

    @property
    def points(self):
        return self._points

    @property
    def start(self):
        return self.points[0]

    @property
    def end(self):
        return self.points[-1]

    @property
    def metadata(self):
        return self._metadata

    @property
    def geometries(self):
        return [x.geom for x in self._segments]

    @property
    def pipe_bend_radius(self):
        if self.section.type != "PIPE":
            return None

        wt = self.section.wt
        r = self.section.r
        d = r * 2
        w_tol = 0.125 if self.units == "m" else 125
        cor_tol = 0.003 if self.units == "m" else 3
        corr_t = (wt - (wt * w_tol)) - cor_tol
        d -= 2.0 * corr_t

        return roundoff(d + corr_t / 2.0)

    @property
    def section(self):
        """

        :return:
        :rtype: Section
        """
        return self._section

    @property
    def n1(self):
        return self._n1

    @property
    def n2(self):
        return self._n2

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            self.n1.units = value
            self.n2.units = value
            self.section.units = value
            self.material.units = value
            self._segments = []
            for p in self.points:
                p.units = value
            self._build_pipe()
            self._units = value

    def _generate_ifc_pipe(self):
        from ada.core.constants import X, Z
        from ada.ifc.utils import create_local_placement, create_property_set

        if self.parent is None:
            raise ValueError("Cannot build ifc element without parent")

        a = self.get_assembly()
        f = a.ifc_file

        owner_history = a.user.to_ifc()
        parent = self.parent.ifc_elem

        placement = create_local_placement(
            f,
            origin=self.n1.p.astype(float).tolist(),
            loc_x=X,
            loc_z=Z,
            relative_to=parent.ObjectPlacement,
        )

        ifc_elem = f.createIfcSpace(
            self.guid,
            owner_history,
            self.name,
            "Description",
            None,
            placement,
            None,
            None,
            None,
        )

        f.createIfcRelAggregates(
            create_guid(),
            owner_history,
            "Site Container",
            None,
            parent,
            [ifc_elem],
        )
        if len(self.metadata.keys()) > 0:
            props = create_property_set("Properties", f, self.metadata)
            f.createIfcRelDefinesByProperties(
                create_guid(),
                owner_history,
                "Properties",
                None,
                [ifc_elem],
                props,
            )

        return ifc_elem

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_pipe()

            a = self.get_assembly()
            f = a.ifc_file

            owner_history = a.user.to_ifc()

            segments = []
            for param_seg in self._segments:
                if type(param_seg) is PipeSegStraight:
                    assert isinstance(param_seg, PipeSegStraight)
                    res = param_seg.ifc_elem
                else:
                    assert isinstance(param_seg, PipeSegElbow)
                    res = param_seg.ifc_elem
                if res is None:
                    logging.error(f'Branch "{param_seg.name}" was not converted to ifc element')
                f.add(res)
                segments += [res]

            f.createIfcRelContainedInSpatialStructure(
                create_guid(),
                owner_history,
                "Pipe Segments",
                None,
                segments,
                self._ifc_elem,
            )

        return self._ifc_elem

    def __repr__(self):
        return f"Pipe({self.name}, {self.section})"


class PipeSegStraight(BackendGeom):
    def __init__(
        self,
        name,
        p1,
        p2,
        section,
        material,
        parent=None,
        guid=None,
        metadata=None,
        units="m",
        colour=None,
        ifc_elem=None,
    ):
        super(PipeSegStraight, self).__init__(name, guid, metadata, units, parent, colour, ifc_elem=ifc_elem)
        self.p1 = p1
        self.p2 = p2
        self.section = section
        self.material = material

    @property
    def xvec1(self):
        return self.p2.p - self.p1.p

    @property
    def geom(self):
        from ada.occ.utils import make_edge, sweep_pipe

        edge = make_edge(self.p1, self.p2)

        return sweep_pipe(edge, self.xvec1, self.section.r, self.section.wt)

    def _generate_ifc_elem(self):
        from ada.core.constants import O, X, Z
        from ada.ifc.utils import (  # create_ifcrevolveareasolid,
            create_global_axes,
            create_ifcpolyline,
            to_real,
        )

        if self.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = a.user.to_ifc()

        p1 = self.p1
        p2 = self.p2

        ifcdir = f.createIfcDirection((0.0, 0.0, 1.0))

        rp1 = to_real(p1.p)
        rp2 = to_real(p2.p)
        xvec = unit_vector(p2.p - p1.p)
        a = angle_between(xvec, np.array([0, 0, 1]))
        zvec = np.array([0, 0, 1]) if a != np.pi and a != 0 else np.array([1, 0, 0])
        yvec = unit_vector(np.cross(zvec, xvec))
        seg_l = vector_length(p2.p - p1.p)

        extrusion_placement = create_global_axes(f, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

        solid = f.createIfcExtrudedAreaSolid(self.section.ifc_profile, extrusion_placement, ifcdir, seg_l)

        polyline = create_ifcpolyline(f, [rp1, rp2])

        axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [polyline])
        body_representation = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid])

        product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body_representation])

        origin = f.createIfcCartesianPoint(O)
        local_z = f.createIfcDirection(Z)
        local_x = f.createIfcDirection(X)
        d237 = f.createIfcLocalPlacement(None, f.createIfcAxis2Placement3D(origin, local_z, local_x))

        d256 = f.createIfcCartesianPoint(rp1)
        d257 = f.createIfcDirection(to_real(xvec))
        d258 = f.createIfcDirection(to_real(yvec))
        d236 = f.createIfcAxis2Placement3D(d256, d257, d258)
        local_placement = f.createIfcLocalPlacement(d237, d236)

        pipe_segment = f.createIfcPipeSegment(
            create_guid(),
            owner_history,
            self.name,
            "An awesome pipe",
            None,
            local_placement,
            product_shape,
            None,
        )

        ifc_mat = self.material.ifc_mat
        mat_profile = f.createIfcMaterialProfile(
            self.material.name, None, ifc_mat, self.section.ifc_profile, None, None
        )
        mat_profile_set = f.createIfcMaterialProfileSet(None, None, [mat_profile], None)
        mat_profile_set = f.createIfcMaterialProfileSetUsage(mat_profile_set, 8, None)
        f.createIfcRelAssociatesMaterial(create_guid(), None, None, None, [pipe_segment], mat_profile_set)

        return pipe_segment


class PipeSegElbow(BackendGeom):
    def __init__(
        self,
        name,
        p1,
        p2,
        p3,
        bend_radius,
        section,
        material,
        parent=None,
        guid=None,
        metadata=None,
        units="m",
        colour=None,
        arc_seg=None,
    ):
        super(PipeSegElbow, self).__init__(name, guid, metadata, units, parent, colour)
        self.p1 = p1
        self.p2 = p2
        self.p3 = p3
        self.bend_radius = bend_radius
        self.section = section
        self.material = material
        self._arc_seg = arc_seg

    @property
    def parent(self) -> Pipe:
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def xvec1(self):
        return self.p2.p - self.p1.p

    @property
    def xvec2(self):
        return self.p3.p - self.p2.p

    @property
    def geom(self):
        from ada.core.curve_utils import make_edges_and_fillet_from_3points
        from ada.occ.utils import sweep_pipe

        i = self.parent.segments.index(self)
        if i != 0:
            pseg = self.parent.segments[i - 1]
            xvec = pseg.xvec1
        else:
            xvec = self.xvec1

        if self.arc_seg.edge_geom is None:
            _, _, fillet = make_edges_and_fillet_from_3points(self.p1, self.p2, self.p3, self.bend_radius)
            edge = fillet
        else:
            edge = self.arc_seg.edge_geom

        return sweep_pipe(edge, xvec, self.section.r, self.section.wt)

    @property
    def arc_seg(self) -> ArcSegment:
        return self._arc_seg

    def _elbow_tesselated(self, f, schema, a):
        from ada.ifc.utils import get_tolerance, tesselate_shape

        shape = self.geom

        if shape is None:
            logging.error(f"Unable to create geometry for Branch {self.name}")
            return None

        serialized_geom = tesselate_shape(shape, schema, get_tolerance(a.units))
        ifc_shape = f.add(serialized_geom)

        return ifc_shape

    def _elbow_revolved_solid(self, f, context):
        from ada.core.constants import O, X, Z
        from ada.core.curve_utils import get_center_from_3_points_and_radius
        from ada.core.utils import normal_to_points_in_plane
        from ada.ifc.utils import create_global_axes

        center, _, _, _ = get_center_from_3_points_and_radius(self.p1.p, self.p2.p, self.p3.p, self.bend_radius)

        opening_axis_placement = create_global_axes(f, O, Z, X)

        profile = self.section.ifc_profile
        normal = normal_to_points_in_plane([self.arc_seg.p1, self.arc_seg.p2, self.arc_seg.midpoint])
        revolve_axis = self.arc_seg.center + normal
        revolve_angle = 10

        ifcorigin = f.createIfcCartesianPoint(self.arc_seg.p1.astype(float).tolist())
        ifcaxis1dir = f.createIfcAxis1Placement(ifcorigin, f.createIfcDirection(revolve_axis.astype(float).tolist()))

        ifc_shape = f.createIfcRevolvedAreaSolid(profile, opening_axis_placement, ifcaxis1dir, revolve_angle)

        curve = f.createIfcTrimmedCurve()

        body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [ifc_shape])
        axis = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [curve])
        prod_def_shp = f.createIfcProductDefinitionShape(None, None, (axis, body))

        return prod_def_shp

    def _generate_ifc_elem(self):
        from ada.ifc.utils import create_local_placement

        if self.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = a.user.to_ifc()
        schema = a.ifc_file.wrapped_data.schema

        if _Settings.make_param_elbows is False:
            ifc_elbow = self._elbow_tesselated(f, schema, a)
            # Link to representation context
            for rep in ifc_elbow.Representations:
                rep.ContextOfItems = context
        else:
            ifc_elbow = self._elbow_revolved_solid(f, context)

        pfitting_placement = create_local_placement(f)

        pfitting = f.createIfcPipeFitting(
            create_guid(),
            owner_history,
            self.name,
            "An awesome Elbow",
            None,
            pfitting_placement,
            ifc_elbow,
            None,
            None,
        )

        ifc_mat = self.material.ifc_mat
        mat_profile = f.createIfcMaterialProfile(
            self.material.name, None, ifc_mat, self.section.ifc_profile, None, None
        )
        mat_profile_set = f.createIfcMaterialProfileSet(None, None, [mat_profile], None)
        mat_profile_set = f.createIfcMaterialProfileSetUsage(mat_profile_set, 8, None)
        f.createIfcRelAssociatesMaterial(create_guid(), None, None, None, [pfitting], mat_profile_set)

        return pfitting
