# coding=utf-8
import logging
import os
import pathlib
import traceback
from itertools import chain

import numpy as np

from .base import Backend, BackendGeom
from .config import Settings as _Settings
from .core.containers import Beams, Connections, Materials, Nodes, Plates, Sections
from .core.utils import (
    Counter,
    angle_between,
    create_guid,
    get_current_user,
    get_list_of_files,
    make_wire_from_points,
    roundoff,
    unit_vector,
    vector_length,
)
from .fem import FEM, Elem, FemSet, io
from .materials.metals import CarbonSteel
from .sections import GeneralProperties, SectionCat

__author__ = "Kristoffer H. Andersen"
__all__ = [
    "Assembly",
    "Part",
    "Beam",
    "Plate",
    "Pipe",
    "Wall",
    "Penetration",
    "Section",
    "Material",
    "Shape",
    "Node",
    "Connection",
    "PrimBox",
    "PrimCyl",
    "PrimExtrude",
    "PrimRevolve",
    "PrimSphere",
    "PrimSweep",
    "CurvePoly",
    "CurveRevolve",
    "LineSegment",
    "ArcSegment",
]


class Part(BackendGeom):
    """
    A Part superclass design to host all relevant information for cad and FEM modelling.

    :param name: Name of Part
    :param colour: Colour of Part.
    :param origin: Origin of part.
    :param lx: Local X
    :param ly: Local Y
    :param lz: Local Z
    :param props: A properties object
    :param metadata: A dict for containing metadata
    :type props: Settings
    :type fem: FEM
    """

    def __init__(
        self,
        name,
        colour=None,
        origin=(0, 0, 0),
        lx=(1, 0, 0),
        ly=(0, 1, 0),
        lz=(0, 0, 1),
        fem=None,
        props=_Settings(),
        metadata=None,
        parent=None,
        units="m",
        ifc_elem=None,
        guid=None,
    ):
        super().__init__(name, guid=guid, metadata=metadata, units=units, parent=parent)
        from ada.fem.io.mesh import GMesh

        self._nodes = Nodes(parent=self)
        self._beams = Beams(parent=self)
        self._plates = Plates(parent=self)
        self._pipes = list()
        self._walls = list()
        self._connections = Connections(parent=self)
        self._materials = Materials(parent=self)
        self._sections = Sections(parent=self)
        self._gmsh = GMesh(self)
        self._colour = colour
        self._origin = origin
        self._lx = lx
        self._ly = ly
        self._lz = lz
        self._shapes = []
        self._parts = dict()

        if ifc_elem is not None:
            self.metadata["ifctype"] = self._import_part_from_ifc(ifc_elem)
        else:
            if hasattr(metadata, "ifctype") is False:
                self.metadata["ifctype"] = "site" if type(self) is Assembly else "storey"

        self._ifc_elem = None

        self._props = props
        if fem is not None:
            fem.edit(parent=self)

        self._fem = FEM(name + "-1", parent=self) if fem is None else fem

    def add_beam(self, beam):
        """
        Add beam to the assembly

        :param beam: Beam object
        :type beam: Beam
        """
        if beam.units != self.units:
            beam.units = self.units
        beam.parent = self
        mat = self.add_material(beam.material)
        if mat is not None:
            beam.material = mat

        sec = self.add_section(beam.section)
        if sec is not None:
            beam.section = sec

        tap = self.add_section(beam.taper)
        if tap is not None:
            beam.taper = tap

        old_node = self.nodes.add(beam.n1)
        if old_node is not None:
            beam.n1 = old_node

        old_node = self.nodes.add(beam.n2)
        if old_node is not None:
            beam.n2 = old_node

        self.beams.add(beam)
        return beam

    def add_plate(self, plate):
        """
        Add a plate

        :param plate:
        :type plate: Plate
        """
        if plate.units != self.units:
            plate.units = self.units

        plate.parent = self

        mat = self.add_material(plate.material)
        if mat is not None:
            plate.material = mat

        self._plates.add(plate)

    def add_pipe(self, pipe):
        """

        :param pipe:
        :type pipe: ada.Pipe
        """
        if pipe.units != self.units:
            pipe.units = self.units
        pipe.parent = self

        mat = self.add_material(pipe.material)
        if mat is not None:
            pipe.material = mat

        self._pipes.append(pipe)

    def add_wall(self, wall):
        """

        :param wall:
        :type wall: ada.Wall
        """
        if wall.units != self.units:
            wall.units = self.units
        wall.parent = self
        self._walls.append(wall)

    def add_shape(self, shape):
        """
        Add a shape

        :param shape:
        :type shape: ada.Shape
        """
        if shape.units != self.units:
            logging.info(f'shape "{shape}" has different units. changing from "{shape.units}" to "{self.units}"')
            shape.units = self.units
        shape.parent = self
        self._shapes.append(shape)

    def add_part(self, part, auto_merge=True):
        """

        :param part:
        :param auto_merge: Automatically merge parts with existing name
        :type part: Part
        """
        if issubclass(type(part), Part) is False:
            raise ValueError("Added Part must be a subclass or instance of Part")
        if part.units != self.units:
            part.units = self.units
        part.parent = self
        if part.name in self._parts.keys():
            raise ValueError(f'Part name "{part.name}" already exists and cannot be overwritten')
        self._parts[part.name] = part
        return part

    def add_joint(self, joint):
        """
        This method takes a Joint element containing two intersecting beams. It will check with the existing
        list of joints to see whether or not it is part of a larger more complex joint. It usese primarily
        two criteria.

        Criteria 1: If both elements are in an existing joint already, it will u

        Criteria 2: If the intersecting point coincides within a specified tolerance (currently 10mm)
        with an exisiting joint intersecting point. If so it will add the elements to this joint.
        If not it will create a new joint based on these two members.

        :param joint:
        :type joint: Connection
        """

        """
        Notes
        Evaluate if possible to have an additional self check on parallel beam if either of the two end nodes
        are in proximity to already established joint. If so move the Centre Point to one of the two ends!

        A Counter-point would be that it would depend on when the intersection was made. Maybe it is best to do this
        on an algorithm running after

        """
        if joint.units != self.units:
            joint.units = self.units
        self._connections.add(joint)

    def add_material(self, material):
        """
        This method will add a Material element. First it will check if the material exists in the db already.

        :param material:
        :type material: Material
        """
        if material.units != self.units:
            material.units = self.units
        material.parent = self
        return self._materials.add(material)

    def add_section(self, section):
        """
        This method will add a Section element. First it will check if the section exists in the db already.

        :param section:
        :type section: Section
        """
        if section.units != self.units:
            section.units = self.units
        return self._sections.add(section)

    def add_penetration(self, pen, include_subparts=True):
        """

        :param pen:
        :param include_subparts:
        :return:
        """
        if type(pen) in (PrimExtrude, PrimRevolve, PrimCyl, PrimBox):
            pen = Penetration(pen, parent=self)

        for bm in self.beams:
            bm.add_penetration(pen)

        for pl in self.plates:
            pl.add_penetration(pen)

        for shp in self.shapes:
            shp.add_penetration(pen)

        for pipe in self.pipes:
            pipe.add_penetration(pen)

        for wall in self.walls:
            wall.add_penetration(pen)

        for p in self.get_all_subparts():
            p.add_penetration(pen, False)

    def add_elements_from_ifc(self, ifc_file, data_only=False):
        """

        :param ifc_file:
        :param data_only:
        :return:
        """
        a = Assembly("temp")
        a.read_ifc(ifc_file, data_only=data_only)
        all_shapes = [shp for p in a.get_all_subparts() for shp in p.shapes] + a.shapes
        for shp in all_shapes:
            self.add_shape(shp)

        all_beams = [bm for p in a.get_all_subparts() for bm in p.beams] + [bm for bm in a.beams]
        for bm in all_beams:
            ids = self.beams.dmap.keys()
            names = [b.name for b in self.beams.dmap.values()]
            if bm.guid in ids:
                raise NotImplementedError("Have not considered merging ifc elements with identical IDs yet.")
            if bm.name in names:
                start = max(ids) + 1
                bm_name = f"bm{start}"
                if bm_name not in names:
                    bm.name = bm_name
                else:
                    while bm_name in names:
                        bm_name = f"bm{start}"
                        bm.name = bm_name
                        start += 1
            self.add_beam(bm)

        all_plates = [pl for p in a.get_all_subparts() for pl in p.plates] + [pl for pl in a.plates]
        for pl in all_plates:
            self.add_plate(pl)

        all_pipes = [pipe for p in a.get_all_subparts() for pipe in p.pipes] + a.pipes
        for pipe in all_pipes:
            self.add_pipe(pipe)

        all_walls = [wall for p in a.get_all_subparts() for wall in p.walls] + a.walls
        for wall in all_walls:
            self.add_wall(wall)

    def read_step_file(
        self, cad_ref, name=None, scale=None, transform=None, rotate=None, colour=None, opacity=1.0, units="m"
    ):
        """

        :param cad_ref:
        :param name:
        :param scale:
        :param transform:
        :param rotate:
        :param colour:
        :param opacity:
        :return:
        """
        import math

        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
        from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
        from OCC.Extend.DataExchange import read_step_file
        from OCC.Extend.TopologyUtils import TopologyExplorer

        def transform_shape(shp_):
            trsf = gp_Trsf()
            if scale is not None:
                trsf.SetScaleFactor(scale)
            if transform is not None:
                trsf.SetTranslation(gp_Vec(transform[0], transform[1], transform[2]))
            if rotate is not None:
                pt = gp_Pnt(rotate[0][0], rotate[0][1], rotate[0][2])
                dire = gp_Dir(rotate[1][0], rotate[1][1], rotate[1][2])
                revolve_axis = gp_Ax1(pt, dire)
                trsf.SetRotation(revolve_axis, math.radians(rotate[2]))
            return BRepBuilderAPI_Transform(shp_, trsf, True).Shape()

        def walk_shapes(dir_path):
            shps = []
            for stp_file in get_list_of_files(dir_path, ".stp"):
                shps += extract_subshapes(read_step_file(stp_file))
            return shps

        def extract_subshapes(shp_):
            s = []
            t = TopologyExplorer(shp_)
            for solid in t.solids():
                s.append(solid)
            return s

        shapes = []
        if type(cad_ref) is str and ".stp" in cad_ref:
            cad_file_path = pathlib.Path(cad_ref)
            if cad_file_path.is_file():
                shapes += extract_subshapes(read_step_file(str(cad_file_path)))
            elif cad_file_path.is_dir():
                shapes += walk_shapes(cad_file_path)
            else:
                raise Exception(
                    'step_ref "{}" does not represent neither file or folder found on system'.format(cad_ref)
                )
        else:
            raise Exception('step_ref type "{}" is not recognized'.format(type(cad_ref)))

        shapes = [transform_shape(s) for s in shapes]
        if len(shapes) > 0:
            ada_name = name if name is not None else "CAD" + str(len(self.shapes) + 1)
            for i, shp in enumerate(shapes):
                ada_shape = Shape(ada_name + "_" + str(i), shp, colour, opacity, units=units)
                self.add_shape(ada_shape)

    def create_objects_from_fem(self, skip_plates=False, skip_beams=False):
        """
        Build Beams and PLates from the contents of the local FEM object

        :return:
        """

        from .core.utils import is_coplanar

        def convert_shell_elements_to_object(elem, parent):
            """
            TODO: Evaluate merging elements by proximity and equal section and normals.

            :param elem: Finite Element object
            :param parent: Parent Part object
            :type elem: ada.fem.Elem
            """
            plates = []
            fem_sec = elem.fem_sec
            fem_sec.material.parent = parent
            if len(elem.nodes) == 4:
                if is_coplanar(
                    *elem.nodes[0].p,
                    *elem.nodes[1].p,
                    *elem.nodes[2].p,
                    *elem.nodes[3].p,
                ):
                    plates.append(Plate(f"sh{elem.id}", elem.nodes, fem_sec.thickness, use3dnodes=True, parent=parent))
                else:
                    plates.append(
                        Plate(f"sh{elem.id}", elem.nodes[:2], fem_sec.thickness, use3dnodes=True, parent=parent)
                    )
                    plates.append(
                        Plate(
                            f"sh{elem.id}_1",
                            [elem.nodes[0], elem.nodes[2], elem.nodes[3]],
                            fem_sec.thickness,
                            use3dnodes=True,
                            parent=parent,
                        )
                    )
            else:
                plates.append(Plate(f"sh{elem.id}", elem.nodes, fem_sec.thickness, use3dnodes=True, parent=parent))
            return plates

        def elem_to_beam(elem, parent):
            """
            TODO: Evaluate merging elements by proximity and equal section and normals.

            :param elem:
            :param parent: Parent Part object
            :type elem: ada.fem.Elem
            """

            n1 = elem.nodes[0]
            n2 = elem.nodes[-1]
            offset = elem.fem_sec.offset
            e1 = None
            e2 = None
            elem.fem_sec.material.parent = parent
            if offset is not None:
                for no, ecc in offset:
                    if no.id == n1.id:
                        e1 = ecc
                    if no.id == n2.id:
                        e2 = ecc

            if elem.fem_sec.section.type == "GENBEAM":
                logging.error(f"Beam elem {elem.id}  uses a GENBEAM which might not represent an actual cross section")

            return Beam(
                f"bm{elem.id}",
                n1,
                n2,
                elem.fem_sec.section,
                elem.fem_sec.material,
                up=elem.fem_sec.local_z,
                e1=e1,
                e2=e2,
                parent=parent,
            )

        def convert_part_objects(p):
            """

            :param p:
            :type p: Part
            """
            if skip_plates is False:
                p._plates = Plates(
                    list(chain.from_iterable([convert_shell_elements_to_object(sh, p) for sh in p.fem.elements.shell]))
                )
            if skip_beams is False:
                p._beams = Beams([elem_to_beam(bm, p) for bm in p.fem.elements.beams])

        if type(self) is Assembly:
            for p_ in self.get_all_parts_in_assembly():
                logging.info(f'Beginning conversion from fem to structural objects for "{p_.name}"')
                convert_part_objects(p_)
        else:
            logging.info(f'Beginning conversion from fem to structural objects for "{self.name}"')
            convert_part_objects(self)
        logging.info("Conversion complete")

    def create_fem_from_obj(self, obj, el_type=None):
        """

        :param obj: ADA object. Currently only BEAM is supported
        :param el_type:
        :return:
        """
        from ada.fem import FemSection

        if type(obj) is Beam:
            el_type = "B31" if el_type is None else el_type

            res = self.fem.nodes.add(obj.n1)
            if res is not None:
                obj.n1 = res
            res = self.fem.nodes.add(obj.n2)
            if res is not None:
                obj.n2 = res

            elem = Elem(None, [obj.n1, obj.n2], el_type)
            self.fem.add_elem(elem)
            femset = FemSet(f"{obj.name}_set", [elem.id], "elset")
            self.fem.add_set(femset)
            self.fem.add_section(
                FemSection(
                    f"d{obj.name}_sec",
                    "beam",
                    femset,
                    obj.material,
                    obj.section,
                    obj.ori[1],
                )
            )
        else:
            raise NotImplementedError(f'Object type "{type(obj)}" is not yet supported')

    def get_part(self, name):
        """

        :param name: Name of part
        :return:
        :rtype: Part
        """
        return self.parts[name]

    def get_by_name(self, name):
        """
        Get element of any type by its name.

        :param name:
        :return:
        """
        for p in self.get_all_subparts() + [self]:
            if p.name == name:
                return p
            for bm in p.beams:
                if bm.name == name:
                    return bm
            for pl in p.plates:
                if pl.name == name:
                    return pl
            for shp in p.shapes:
                if shp.name == name:
                    return shp

    def get_all_parts_in_assembly(self, include_self=False):
        parent = self.get_assembly()
        list_of_ps = []
        self._flatten_list_of_subparts(parent, list_of_ps)
        if include_self:
            list_of_ps += [self]
        return list_of_ps

    def get_all_subparts(self):
        list_of_parts = []
        self._flatten_list_of_subparts(self, list_of_parts)
        return list_of_parts

    def beam_clash_check(self, margins=5e-5):
        """
        For all beams in a Assembly get all beams touching or within the beam. Essentially a clash check is performed
        and it returns a dictionary of all beam ids and the touching beams. A margin to the beam volume can be included.

        :param margins: Add margins to the volume box (equal in all directions). Input is in meters. Can be negative.
        :return: A map generator for the list of beams and resulting intersecting beams
        """
        all_parts = self.get_all_subparts() + [self]
        all_beams = [bm for p in all_parts for bm in p.beams]

        def intersect(bm):
            """

            :param bm:
            :type bm: ada.Beam
            """
            if bm.section.type == "gensec":
                return bm, []
            try:
                vol = bm.bbox
            except ValueError as e:
                logging.error(f"Intersect bbox skipped: {e}\n{traceback.format_exc()}")
                return None
            vol_in = [x for x in zip(vol[0], vol[1])]
            beams = filter(
                lambda x: x != bm,
                chain.from_iterable([p.beams.get_beams_within_volume(vol_in, margins=margins) for p in all_parts]),
            )
            return bm, beams

        return filter(None, map(intersect, all_beams))

    def _flatten_list_of_subparts(self, p, list_of_parts=None):
        for value in p.parts.values():
            list_of_parts.append(value)
            self._flatten_list_of_subparts(value, list_of_parts)

    def _generate_ifc_elem(self):
        from ada.core.ifc_utils import create_ifclocalplacement, create_property_set

        if self.parent is None:
            raise ValueError("Cannot build ifc element without parent")

        a = self.get_assembly()
        f = a.ifc_file

        owner_history = f.by_type("IfcOwnerHistory")[0]
        itype = self.metadata["ifctype"]
        parent = self.parent.ifc_elem
        placement = create_ifclocalplacement(
            f,
            origin=self.origin,
            loc_x=self._lx,
            loc_z=self._lz,
            relative_to=parent.ObjectPlacement,
        )

        if itype == "building":
            ifc_elem = f.createIfcBuilding(
                self.guid,
                owner_history,
                self.name,
                None,
                None,
                placement,
                None,
                None,
                "ELEMENT",
                None,
                None,
                None,
            )
        elif itype == "space":
            ifc_elem = f.createIfcSpace(
                self.metadata["guid"],
                owner_history,
                self.name,
                "Description",
                None,
                placement,
                None,
                None,
                "ELEMENT",
                None,
                None,
            )
        elif itype == "spatial":
            ifc_elem = f.createIfcSpatialZone(
                self.metadata["guid"],
                owner_history,
                self.name,
                "Description",
                None,
                placement,
                None,
                None,
                None,
            )
        elif itype == "storey":
            elevation = self.origin[2]
            ifc_elem = f.createIfcBuildingStorey(
                self.guid,
                owner_history,
                self.name,
                None,
                None,
                placement,
                None,
                None,
                "ELEMENT",
                elevation,
            )
        else:
            raise ValueError(f'Currently not supported "{itype}"')

        f.createIfcRelAggregates(
            create_guid(),
            owner_history,
            "Site Container",
            None,
            parent,
            [ifc_elem],
        )

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

    def _import_part_from_ifc(self, ifc_elem):
        convert = dict(
            site="IfcSite",
            space="IfcSpace",
            building="IfcBuilding",
            storey="IfcBuildingStorey",
            spatial="IfcSpatialZone",
        )
        opposite = {val: key for key, val in convert.items()}
        pr_type = ifc_elem.is_a()
        return opposite[pr_type]

    @property
    def parts(self):
        """

        :return: Dictionary of parts belonging to this part
        """
        return self._parts

    @property
    def shapes(self):
        return self._shapes

    @property
    def beams(self):
        """

        :rtype: Beams
        """
        return self._beams

    @property
    def plates(self):
        """

        :rtype: Plates
        """
        return self._plates

    @property
    def pipes(self):
        return self._pipes

    @property
    def walls(self):
        return self._walls

    @property
    def nodes(self):
        """

        :return:
        :rtype: Nodes
        """
        return self._nodes

    @property
    def fem(self):
        """

        :return:
        :rtype: FEM
        """
        return self._fem

    @property
    def gmsh(self):
        """

        :return:
        :rtype: ada.fem.io.mesh.GMesh
        """
        return self._gmsh

    @property
    def connections(self):
        """

        :rtype: Connections
        """
        return self._connections

    @property
    def sections(self):
        """

        :return:
        :rtype: Sections
        """
        return self._sections

    @property
    def materials(self):
        """

        :return:
        :rtype: Materials
        """
        return self._materials

    @property
    def bbox(self):
        if len(self.fem.nodes) != 0:
            return self.fem.nodes.bbox
        elif len(self.nodes) != 0:
            return self.nodes.bbox
        else:
            raise ValueError("This part contains no Nodes")

    @property
    def colour(self):
        if self._colour is None:
            from random import randint

            return randint(0, 255) / 255, randint(0, 255) / 255, randint(0, 255) / 255
        else:
            return self._colour

    @colour.setter
    def colour(self, value):
        self._colour = value

    @property
    def properties(self):
        return self._props

    @property
    def origin(self):
        return self._origin

    @origin.setter
    def origin(self, value):
        self._origin = value

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            for bm in self.beams:
                assert isinstance(bm, Beam)
                bm.units = value

            for pl in self.plates:
                assert isinstance(pl, Plate)
                pl.units = value

            for pipe in self._pipes:
                assert isinstance(pipe, Pipe)
                pipe.units = value

            for shp in self._shapes:
                assert isinstance(shp, Shape)
                shp.units = value

            for wall in self.walls:
                assert isinstance(wall, Wall)
                wall.units = value

            for pen in self.penetrations:
                pen.units = value

            for p in self.get_all_subparts():
                p.units = value

            self.sections.units = value
            self.materials.units = value
            self._units = value
            if type(self) is Assembly:
                from ada.core.ifc_utils import generate_tpl_ifc_file

                self._ifc_file = generate_tpl_ifc_file(
                    self.name,
                    self.metadata["project"],
                    self.metadata["organization"],
                    self.metadata["creator"],
                    self.metadata["schema"],
                    value,
                )

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()
        return self._ifc_elem

    def __truediv__(self, other_object):
        if type(other_object) in [list, tuple]:
            for obj in other_object:
                if type(obj) is Beam:
                    self.add_beam(obj)
                elif type(obj) is Plate:
                    self.add_plate(obj)
                elif type(obj) is Pipe:
                    self.add_pipe(obj)
                elif issubclass(type(obj), Part):
                    self.add_part(obj)
                elif issubclass(type(obj), Shape):
                    self.add_shape(obj)
                else:
                    raise NotImplementedError(f'"{type(obj)}" is not yet supported for smart append')
        elif issubclass(type(other_object), Part):
            self.add_part(other_object)
        elif type(other_object) is Beam:
            self.add_beam(other_object)
        elif type(other_object) is Plate:
            self.add_plate(other_object)
        elif type(other_object) is Pipe:
            self.add_pipe(other_object)
        elif issubclass(type(other_object), Shape):
            self.add_shape(other_object)
        else:
            raise NotImplementedError(f'"{type(other_object)}" is not yet supported for smart append')
        return self

    def __repr__(self):
        nbms = len(self.beams) + len([bm for p in self.get_all_subparts() for bm in p.beams])
        npls = len(self.plates) + len([pl for p in self.get_all_subparts() for pl in p.plates])
        nshps = len(self.shapes) + len([shp for p in self.get_all_subparts() for shp in p.shapes])
        nels = len(self.fem.elements) + len([el for p in self.get_all_subparts() for el in p.fem.elements])
        nnodes = len(self.fem.nodes) + len([no for p in self.get_all_subparts() for no in p.fem.nodes])
        return f'Part("{self.name}": Beams: {nbms}, Plates: {npls}, Shapes: {nshps}, Elements: {nels}, Nodes: {nnodes})'


class Assembly(Part):
    """
    The Assembly object. A top level container of parts, beams, plates, shapes and FEM.


    """

    def __init__(
        self,
        name="Ada",
        project="AdaProject",
        organization="AdaOrganization",
        creator="AdaCreator",
        schema="IFC4",
        props=_Settings(),
        metadata=None,
        units="m",
        ifc_settings=None,
    ):

        from ada.core.ifc_utils import generate_tpl_ifc_file

        creator = get_current_user() if creator is None else creator
        self._ifc_file = generate_tpl_ifc_file(name, project, organization, creator, schema, units)
        if metadata is None:
            metadata = dict()
        metadata["project"] = project
        metadata["organization"] = organization
        metadata["creator"] = creator
        metadata["schema"] = schema
        self._ifc_sections = None
        self._ifc_materials = None
        self._source_ifc_files = dict()
        self._ifc_settings = ifc_settings
        self._presentation_layers = []

        Part.__init__(self, name=name, props=props, metadata=metadata, units=units)

    def read_ifc(self, ifc_file, data_only=False, elements2part=None):
        """
        Import from IFC file.


        Note! Currently only geometry is imported into individual shapes.

        :param ifc_file:
        :param data_only: Set True if data is relevant, not geometry
        :param elements2part: Grab all physical elements from ifc and import it to the parsed in Part object.
        """
        import ifcopenshell

        from ada.core.ifc_utils import (
            calculate_unit_scale,
            get_name,
            get_parent,
            getIfcPropertySets,
            scale_ifc_file_object,
        )

        f = ifcopenshell.open(ifc_file)

        oval = calculate_unit_scale(self.ifc_file)
        nval = calculate_unit_scale(f)
        if oval != nval:
            logging.debug("Running Unit Conversion. This is still highly unstable")
            new_file = scale_ifc_file_object(f, nval)
            f = new_file

        # Get hierarchy
        if elements2part is None:
            for product in f.by_type("IfcProduct"):
                pr_type = product.is_a()
                pp = get_parent(product)
                if pp is None:
                    continue
                parent_type = pp.is_a()
                name = get_name(product)
                if pr_type in [
                    "IfcBuilding",
                    "IfcSpace",
                    "IfcBuildingStorey",
                    "IfcSpatialZone",
                ]:
                    new_part = Part(name, ifc_elem=product)
                    if parent_type in [
                        "IfcSite",
                        "IfcSpace",
                        "IfcBuilding",
                        "IfcBuildingStorey",
                        "IfcSpatialZone",
                    ]:
                        self.add_part(new_part)
                        # self.ifc_file.add(new_part.ifc_elem)
                    else:
                        for p in self.get_all_parts_in_assembly():
                            if p.name == pp.Name:
                                p.add_part(new_part)

        # Get physical elements
        for product in f.by_type("IfcProduct"):
            if product.Representation is not None and data_only is False:
                pr_type = product.is_a()
                pp = get_parent(product)
                props = getIfcPropertySets(product)
                name = get_name(product)
                logging.info(f"importing {name}")
                if pr_type in ["IfcBeamStandardCase", "IfcBeam"]:
                    try:
                        bm = Beam(name, ifc_elem=product)
                    except NotImplementedError as e:
                        logging.error(e)
                        continue
                    bm.metadata["props"] = props
                    imported = False
                    if elements2part is not None:
                        elements2part.add_beam(bm)
                        imported = True
                    else:
                        for p in self.get_all_parts_in_assembly():
                            if p.name == pp.Name:
                                p.add_beam(bm)
                                imported = True
                                break
                    if imported is False:
                        raise ValueError(f'Unable to import beam "{bm.name}"')
                elif pr_type in ["IfcPlateStandardCase", "IfcPlate"]:
                    try:
                        pl = Plate(name, None, None, ifc_elem=product)
                    except np.linalg.LinAlgError as g:
                        logging.error(g)
                        continue
                    except IndexError as f:
                        logging.error(f)
                        continue
                    except ValueError as e:
                        logging.error(e)
                        continue
                    pl.metadata["props"] = props
                    imported = False
                    if elements2part is not None:
                        elements2part.add_plate(pl)
                        imported = True
                    else:
                        all_parts = self.get_all_parts_in_assembly()
                        for p in all_parts:
                            if p.name == pp.Name:
                                p.add_plate(pl)
                                imported = True
                                break
                    if imported is False:
                        raise ValueError(f'Unable to import plate "{pl.name}"')
                else:
                    if product.is_a("IfcOpeningElement") is True:
                        continue
                    # new_product = self.ifc_file.add(product)
                    props = getIfcPropertySets(product)

                    shp = Shape(
                        name,
                        None,
                        guid=product.GlobalId,
                        # ifc_elem=product,
                        metadata=dict(ifc_file=ifc_file, props=props),
                    )
                    if shp is None:
                        continue
                    imported = False
                    if elements2part is not None:
                        elements2part.add_shape(shp)
                        imported = True
                    else:
                        if pp is not None:
                            for p in self.get_all_parts_in_assembly():
                                if p.name == pp.Name:
                                    p.add_shape(shp)
                                    imported = True
                    if imported is False:
                        self.add_shape(shp)
                        logging.debug(f'Shape "{product.Name}" was added below Assembly Level -> No owner found')
        print(f'Import of IFC file "{ifc_file}" is complete')

    @io.femio
    def read_fem(
        self,
        fem_file,
        fem_format=None,
        fem_name=None,
        fem_converter="default",
        convert_func=None,
    ):
        """
        Import a Finite Element model.

        Currently supported FEM formats: Abaqus, Sesam and Calculix

        :param fem_file: A
        :param fem_format:
        :param fem_name:
        :param fem_converter: Set desired fem converter. Use either 'default' or 'meshio'.
        :param convert_func:
        :type fem_file: str
        :type fem_format: str
        :type fem_name: str

        Note! The meshio fem converter implementation currently only supports reading elements and nodes.
        """

        fem_file = pathlib.Path(fem_file)
        if fem_file.exists() is False:
            raise FileNotFoundError(fem_file)

        convert_func(self, fem_file, fem_name)

    @io.femio
    def to_fem(
        self,
        name,
        fem_format,
        scratch_dir=None,
        metadata=None,
        execute=False,
        run_ext=False,
        cpus=2,
        gpus=None,
        overwrite=False,
        fem_converter="default",
        convert_func=None,
        exit_on_complete=True,
    ):
        """
        Create a FEM input file deck for executing fem analysis in a specified FEM format.
        Currently there is limited write support for the following FEM formats are:

        Open Source

        * Calculix
        * Code_Aster

        Proprietary

        * Abaqus
        * Usfos
        * Sesam


        Write support is added on a need-only-basis. Any contributions are welcome!


        :param name: Name of FEM analysis input deck
        :param fem_format: Desired fem format
        :param scratch_dir: Output directory for analysis input deck
        :param metadata: Parse additional commands to FEM solver not supported by the generalized classes
        :param execute: Execute analysis on complete
        :param run_ext: Run analysis externally or wait for complete
        :param cpus: Number of cpus for running the analysis
        :param gpus: Number of gpus for running the analysis (wherever relevant)
        :param overwrite: Overwrite existing input file deck
        :param fem_converter: Set desired fem converter. Use either 'default' or 'meshio'.
        :param convert_func:
        :param exit_on_complete:


            Note! Meshio implementation currently only supports reading & writing elements and nodes.

        Abaqus Metadata:

            'ecc_to_mpc': Runs the method :func:`~ada.fem.FEM.convert_ecc_to_mpc` . Default is True
            'hinges_to_coupling': Runs the method :func:`~ada.fem.FEM.convert_hinges_2_couplings` . Default is True

            Important Note! The the ecc_to_mpc and hinges_to_coupling will make permanent modifications to the model.
            If this proves to create issues regarding performance this should be evaluated further.

        """
        print(f'Exporting to "{fem_format}" using {convert_func.__name__}')

        # Update all global materials and sections before writing input file
        # self.materials
        # self.sections

        convert_func(
            self,
            name,
            scratch_dir,
            metadata,
            execute,
            run_ext,
            cpus,
            gpus,
            overwrite,
            exit_on_complete,
        )

    def to_ifc(self, destination_file):
        """
        Export Model Assembly to a specified IFC file.

        :param destination_file:
        """

        f = self.ifc_file
        owner_history = f.by_type("IfcOwnerHistory")[0]

        dest = pathlib.Path(destination_file).with_suffix(".ifc")

        # TODO: Consider having all of these operations happen upon import of elements as opposed to one big operation
        #  on export

        for s in self.sections.nmap.values():
            f.add(s.ifc_profile)
            f.add(s.ifc_beam_type)

        for p in self.get_all_parts_in_assembly(include_self=True):
            physical_objects = []
            for m in p.materials.dmap.values():
                f.add(m.ifc_mat)

            for bm in p.beams:
                assert isinstance(bm, Beam)
                f.add(bm.ifc_elem)
                physical_objects.append(bm.ifc_elem)

            for pl in p.plates:
                assert isinstance(pl, Plate)
                f.add(pl.ifc_elem)
                physical_objects.append(pl.ifc_elem)

            for pi in p.pipes:
                for pi_seg in pi.ifc_elem:
                    f.add(pi_seg)
                    physical_objects.append(pi_seg)

            for wall in p.walls:
                f.add(wall.ifc_elem)
                physical_objects.append(wall.ifc_elem)

            for shp in p.shapes:
                assert isinstance(shp, Shape)
                if "ifc_file" in shp.metadata.keys():
                    ifc_file = shp.metadata["ifc_file"]
                    ifc_f = self.get_ifc_source_by_name(ifc_file)
                    ifc_elem = ifc_f.by_guid(shp.guid)
                    f.add(ifc_elem)
                    physical_objects.append(ifc_elem)
                else:
                    f.add(shp.ifc_elem)
                    physical_objects.append(shp.ifc_elem)

            f.createIfcRelContainedInSpatialStructure(
                create_guid(),
                owner_history,
                "Physical model",
                None,
                physical_objects,
                p.ifc_elem,
            )

        f.createIfcPresentationLayerWithStyle(
            "HiddenLayers", "Hidden Layers (ADA)", self.presentation_layers, "10", False
        )

        os.makedirs(dest.parent, exist_ok=True)
        self.ifc_file.write(str(dest))
        self._source_ifc_files = dict()
        print(f'ifc file created at "{dest}"')

    def push(
        self,
        comment,
        bimserver_url,
        username,
        password,
        project,
        merge=False,
        sync=False,
    ):
        """
        Push current assembly to BimServer with a comment tag that defines the revision name

        :param comment: A comment describing the model changes
        :param bimserver_url:
        :param username:
        :param password:
        :param project:
        :param merge:
        :param sync:
        """
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.push(project, comment, merge, sync)

    def pull(self, bimserver_url, username, password, project, checkout=False):
        """

        :param bimserver_url:
        :param username:
        :param password:
        :param project:
        :param checkout:
        :return:
        """
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.pull(project, checkout)

    def _generate_ifc_elem(self):
        from ada.core.ifc_utils import create_ifclocalplacement, create_property_set

        f = self.ifc_file
        owner_history = f.by_type("IfcOwnerHistory")[0]
        site_placement = create_ifclocalplacement(f)
        site = f.createIfcSite(
            self.guid,
            owner_history,
            self.name,
            None,
            None,
            site_placement,
            None,
            None,
            "ELEMENT",
            None,
            None,
            None,
            None,
            None,
        )
        f.createIfcRelAggregates(
            create_guid(),
            owner_history,
            "Project Container",
            None,
            f.by_type("IfcProject")[0],
            [site],
        )

        props = create_property_set("Properties", f, self.metadata)
        f.createIfcRelDefinesByProperties(
            create_guid(),
            owner_history,
            "Properties",
            None,
            [site],
            props,
        )

        return site

    def get_ifc_source_by_name(self, ifc_file):
        """

        :param ifc_file:
        :return:
        """
        import ifcopenshell

        if ifc_file not in self._source_ifc_files.keys():
            ifc_f = ifcopenshell.open(ifc_file)
            self._source_ifc_files[ifc_file] = ifc_f
        else:
            ifc_f = self._source_ifc_files[ifc_file]

        return ifc_f

    @property
    def materials(self):
        mat_db = Materials([mat for mat in self._materials], parent=self)
        for mat in list(chain.from_iterable([p.materials for p in self.get_all_parts_in_assembly()])):
            mat_db.add(mat)
        return mat_db

    @property
    def sections(self):
        sec_db = Sections([sec for sec in self._sections], parent=self)
        for sec in list(chain.from_iterable([p.sections for p in self.get_all_parts_in_assembly()])):
            sec_db.add(sec)

        return sec_db

    @property
    def ifc_sections(self):
        if self._ifc_sections is None:
            secrel = dict()
            for sec in self.sections.nmap.values():
                secrel[sec.name] = sec.ifc_profile, sec.ifc_beam_type
            self._ifc_sections = secrel
        return self._ifc_sections

    @property
    def ifc_materials(self):
        if self._ifc_materials is None:
            matrel = dict()
            for mat in self.materials.dmap.values():
                matrel[mat.name] = mat.ifc_mat
            self._ifc_materials = matrel
        return self._ifc_materials

    @property
    def ifc_file(self):
        return self._ifc_file

    @property
    def presentation_layers(self):
        return self._presentation_layers

    def __repr__(self):
        nm = self.name
        nbms = len([bm for p in self.get_all_subparts() for bm in p.beams]) + len(self.beams)
        npls = len([pl for p in self.get_all_subparts() for pl in p.plates]) + len(self.plates)
        nshps = len([shp for p in self.get_all_subparts() for shp in p.shapes]) + len(self.shapes)
        nels = len(self.fem.elements) + len([el for p in self.get_all_subparts() for el in p.fem.elements])
        nns = len(self.fem.nodes) + len([no for p in self.get_all_subparts() for no in p.fem.nodes])
        return f'Assembly("{nm}": Beams: {nbms}, Plates: {npls}, Shapes: {nshps}, Elements: {nels}, Nodes: {nns})'


class Beam(BackendGeom):
    """
    The base StruBeam object

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
        super().__init__(name, metadata=metadata, units=units, guid=guid)
        self._ifc_elem = None

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
        self._connected_from = []
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
            raise ValueError("Unacceptable input type: {}".format(type(sec)))

        self._section.parent = self
        self._taper.parent = self

        # Define orientations

        xvec = unit_vector(self.n2.p - self.n1.p)
        tol = 1e-3
        from ada.core.constants import Y, Z

        zvec = np.array(Z)
        dvec = xvec - zvec
        vlen = vector_length(dvec)
        if vlen < tol:
            gup = np.array(Y)
        else:
            gup = np.array(Z)

        if up is None:
            if angle != 0.0 and angle is not None:
                from pyquaternion import Quaternion

                my_quaternion = Quaternion(axis=xvec, degrees=angle)
                rot_mat = my_quaternion.rotation_matrix
                up = np.array([roundoff(x) if abs(x) != 0.0 else 0.0 for x in np.matmul(gup, np.transpose(rot_mat))])
            else:
                up = np.array([roundoff(x) if abs(x) != 0.0 else 0.0 for x in gup])
            yvec = np.cross(up, xvec)
        else:
            if (len(up) == 3) is False:
                raise ValueError("Up vector must be length 3")
            if vector_length(xvec - up) < tol:
                raise ValueError("The assigned up vector is too close to your beam direction")
            yvec = np.cross(up, xvec)
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
            self._material = Material(name=name + "_mat", mat_model=CarbonSteel(mat, plasticity_model=None))

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
        from .sections import SectionCat

        if SectionCat.is_circular_profile(self.section.type):
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

    def _generate_ifc_beam(self):
        from ada.config import Settings
        from ada.core.constants import O, X, Z
        from ada.core.ifc_utils import (
            add_colour,
            create_ifcaxis2placement,
            create_ifclocalplacement,
            create_ifcrevolveareasolid,
            create_property_set,
        )
        from ada.core.utils import angle_between

        sec = self.section
        if self.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]
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

        global_placement = create_ifclocalplacement(f, O, Z, X)

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

            revolve_placement = create_ifcaxis2placement(f, p1, profile_x, profile_y)
            extrude_area_solid = create_ifcrevolveareasolid(f, profile, revolve_placement, corigin, xvec, a3)
            loc_plac = create_ifclocalplacement(f, O, Z, X, parent.ObjectPlacement)
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

        if "hidden" in self.metadata.keys():
            if self.metadata["hidden"] is True:
                a.presentation_layers.append(body)

        prod_def_shp = f.createIfcProductDefinitionShape(None, None, (axis, body))

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
        elements = []
        for pen in self._penetrations:
            elements.append(pen.ifc_opening)

        f.createIfcRelDefinesByType(
            create_guid(),
            None,
            self.section.type,
            None,
            [ifc_beam] + elements,
            beam_type,
        )

        props = create_property_set("Properties", f, self.metadata)
        f.createIfcRelDefinesByProperties(
            create_guid(),
            owner_history,
            "Properties",
            None,
            [ifc_beam] + elements,
            props,
        )

        ifc_mat = a.ifc_materials[self.material.name]
        mat_profile = f.createIfcMaterialProfile(sec.name, "A material profile", ifc_mat, profile, None, "LoadBearing")
        mat_profile_set = f.createIfcMaterialProfileSet(sec.name, None, [mat_profile], None)
        f.createIfcRelAssociatesMaterial(create_guid(), None, None, None, [beam_type], mat_profile_set)
        mat_profile_set = f.createIfcMaterialProfileSetUsage(mat_profile_set, 8, None)
        f.createIfcRelAssociatesMaterial(
            create_guid(),
            owner_history,
            self.material.name,
            f"Associated Material to beam '{self.name}'",
            [ifc_beam],
            mat_profile_set,
        )

        return ifc_beam

    def _import_from_ifc_beam(self, ifc_elem):
        from ada.core.ifc_utils import get_association, get_name, get_representation

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
        pdct_shape, colour, alpha = get_representation(ifc_elem, self.ifc_settings)

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
        )

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
        xvec = self.xvec
        if list(xvec) == [0.0, 0.0, 1.0] or list(xvec) == [0.0, 0.0, -1.0]:
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
    def connected_from(self):
        return self._connected_from

    @property
    def length(self):
        """
        This property returns the length of the beam

        :return: length of beam
        :rtype: float
        """
        return vector_length(self.n2.p - self.n1.p)

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
        :rtype: Node
        """
        return self._e1

    @property
    def e2(self):
        """

        :return:
        :rtype: Node
        """
        return self._e2

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

        from .sections import ProfileBuilder

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

        from .sections import ProfileBuilder

        geom = ProfileBuilder.build_representation(self, True)

        for pen in self.penetrations:
            geom = BRepAlgoAPI_Cut(geom, pen.geom).Shape()

        return geom

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_beam()

        return self._ifc_elem

    def __repr__(self):
        return "Beam(id: {}, Name: {}\nN1: {}, N2: {}\nSection: {}\nMaterial: {})".format(
            self.guid, self.name, self.n1, self.n2, self.section, self.material
        )


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
        super().__init__(name, guid=guid, metadata=metadata, units=units)
        self._ifc_elem = None
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

    def _generate_ifc_plate(self):
        from ada.core.constants import O, X, Z
        from ada.core.ifc_utils import (
            add_colour,
            create_ifcaxis2placement,
            create_ifcindexpolyline,
            create_ifclocalplacement,
            create_ifcpolyline,
            create_property_set,
        )

        if self.parent is None:
            raise ValueError("Ifc element cannot be built without any parent element")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]
        parent = self.parent.ifc_elem

        xvec = self.poly.xdir
        zvec = self.poly.normal
        yvec = np.cross(zvec, xvec)

        # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
        plate_placement = create_ifclocalplacement(f, relative_to=parent.ObjectPlacement)
        tra_mat = np.array([xvec, yvec, zvec])
        t_vec = [0, 0, self.t]
        origin = np.array(self.poly.origin)
        res = origin + np.dot(tra_mat, t_vec)
        polyline = create_ifcpolyline(f, [origin.astype(float).tolist(), res.tolist()])
        axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve2D", [polyline])
        extrusion_placement = create_ifcaxis2placement(f, O, Z, X)
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
        elements = []
        for pen in self.penetrations:
            elements.append(pen.ifc_opening)

        # if "props" in self.metadata.keys():
        props = create_property_set("Properties", f, self.metadata)
        f.createIfcRelDefinesByProperties(
            create_guid(),
            owner_history,
            "Properties",
            None,
            [ifc_plate] + elements,
            props,
        )

        return ifc_plate

    def _import_from_ifc_plate(self, ifc_elem, ifc_settings=None):
        from ada.core.ifc_utils import (
            get_name,
            get_representation,
            import_indexedpolycurve,
            import_polycurve,
        )

        a = self.get_assembly()
        if a is None:
            # use default ifc_settings
            ifc_settings = _Settings.default_ifc_settings()
        else:
            ifc_settings = a.ifc_settings

        pdct_shape, color, alpha = get_representation(ifc_elem, ifc_settings)
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

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_plate()
        return self._ifc_elem

    def __repr__(self):
        return f"Plate({self.name}, t:{self.t}, {self.material})"


class Pipe(BackendGeom):
    """

    :param name:
    :param points:
    :param sec:
    :param mat:
    :param content:
    :param metadata:
    :param colour:
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
    ):
        super().__init__(name, guid=guid, metadata=metadata, units=units)

        self._ifc_elem = None

        self._section = sec
        self._material = mat if isinstance(mat, Material) else Material(name=name + "_mat", mat_model=CarbonSteel(mat))
        self._content = content
        self.colour = colour
        self._n1 = points[0] if type(points[0]) is Node else Node(points[0])
        self._n2 = points[-1] if type(points[-1]) is Node else Node(points[-1])
        self._points = [Node(n) if type(n) is not Node else n for n in points]
        self._segments = None
        self._elbows = None
        self._swept_solids = None
        self._edges = None
        self._fillets = None
        self._build_pipe()

    def _build_pipe(self):

        from OCC.Core.BRep import BRep_Tool_Pnt
        from OCC.Extend.TopologyUtils import TopologyExplorer

        segs = []
        for p1, p2 in zip(self.points[:-1], self.points[1:]):
            segs.append([p1, p2])
        self._segments = segs
        pipe_bend_radius = self.pipe_bend_radius

        # Make elbows and adjust segments
        edges = []
        fillets = []
        swept_solids = []
        self._elbows = []
        new_segments = []
        for i, (seg1, seg2) in enumerate(zip(self.segments[:-1], self.segments[1:])):
            p11, p12 = seg1
            p21, p22 = seg2
            xvec1 = p12.p - p11.p
            xvec2 = p22.p - p21.p
            normal = unit_vector(np.cross(xvec1, xvec2))
            if i == 0:
                edge1 = Pipe.make_edge(seg1)
            else:
                edge1 = edges[-1]
            edge2 = Pipe.make_edge(seg2)
            ed1, ed2, fillet = self.make_fillet(edge1, edge2, normal, pipe_bend_radius)
            fillets.append((xvec1, fillet))
            edges.append(ed1)
            edges.append(ed2)
            t = TopologyExplorer(fillet)
            ps = [BRep_Tool_Pnt(v) for v in t.vertices()]
            ns = Node([ps[0].X(), ps[0].Y(), ps[0].Z()])
            ne = Node([ps[-1].X(), ps[-1].Y(), ps[-1].Z()])
            if i == 0:
                s1n = [seg1[0], ns]
                s2n = [ne, seg2[1]]
                new_segments.append(s1n)
                new_segments.append(s2n)
            else:
                s2n = [ne, seg2[1]]
                new_segments[-1][1] = ns
                new_segments.append(s2n)
            swept_solids.append(self.sweep_pipe(ed1, xvec1))
            if i == len(self._segments) - 2:
                swept_solids.append(self.sweep_pipe(ed2, xvec2))
            elbow = self.sweep_pipe(fillet, xvec1)
            self._elbows.append(elbow)
        if len(self._segments) > 1:
            self._segments = new_segments
        else:
            if len(self._segments) == 1:
                seg = self._segments[0]
                p1, p2 = seg
                xvec = p2.p - p1.p
                edge = Pipe.make_edge(seg)
                edges.append(edge)
                swept_solids.append(self.sweep_pipe(edge, xvec))
        self._swept_solids = swept_solids
        self._edges = edges
        self._fillets = fillets

    def sweep_pipe(self, edge, xvec):
        from OCC.Core.BRep import BRep_Tool_Pnt
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeWire
        from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakePipe
        from OCC.Core.gp import gp_Dir
        from OCC.Extend.TopologyUtils import TopologyExplorer

        t = TopologyExplorer(edge)
        points = [v for v in t.vertices()]
        point = BRep_Tool_Pnt(points[0])
        # x, y, z = point.X(), point.Y(), point.Z()
        direction = gp_Dir(*unit_vector(xvec).astype(float).tolist())
        o = self.make_sec_face(point, direction, self.section.r)
        i = self.make_sec_face(point, direction, self.section.r - self.section.wt)

        # pipe
        makeWire = BRepBuilderAPI_MakeWire()
        makeWire.Add(edge)
        makeWire.Build()
        wire = makeWire.Wire()
        elbow_o = BRepOffsetAPI_MakePipe(wire, o).Shape()
        elbow_i = BRepOffsetAPI_MakePipe(wire, i).Shape()
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut

        boolean_result = BRepAlgoAPI_Cut(elbow_o, elbow_i).Shape()
        return boolean_result

    def _generate_ifc_pipe_segments(self):
        import ifcopenshell.geom

        from ada.core.ifc_utils import (  # create_ifcrevolveareasolid,
            create_ifcaxis2placement,
            create_ifclocalplacement,
            create_ifcpolyline,
            to_real,
        )

        if self.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]
        parent = self.parent.ifc_elem
        schema = a.ifc_file.wrapped_data.schema

        segments = []
        polylines = []
        pipe_segments = []

        profile = f.createIfcCircleHollowProfileDef("AREA", self.section.name, None, self.section.r, self.section.wt)
        ifcdir = f.createIfcDirection((0.0, 0.0, 1.0))

        for i, (p1, p2) in enumerate(self.segments):
            rp1 = to_real(p1.p)
            rp2 = to_real(p2.p)
            xvec = unit_vector(p2.p - p1.p)
            vlen = vector_length(xvec - np.array([0, 0, 1]))
            vlen2 = vector_length(xvec + np.array([0, 0, 1]))
            if vlen != 0.0 and vlen2 != 0.0:
                yvec = unit_vector(np.cross(xvec, np.array([0, 0, 1])))
            else:
                yvec = unit_vector(np.cross(xvec, np.array([1, 0, 0])))
            seg_l = vector_length(p2.p - p1.p)

            extrusion_placement = create_ifcaxis2placement(f, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

            solid = f.createIfcExtrudedAreaSolid(profile, extrusion_placement, ifcdir, seg_l)

            polyline = create_ifcpolyline(f, [rp1, rp2])

            segments.append(solid)
            polylines.append(polyline)

            axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve3D", [polyline])
            body_representation = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid])

            product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body_representation])

            origin = f.createIfcCartesianPoint((0.0, 0.0, 0.0))
            local_z = f.createIfcDirection((0.0, 0.0, 1.0))
            local_x = f.createIfcDirection((1.0, 0.0, 0.0))
            d237 = f.createIfcLocalPlacement(None, f.createIfcAxis2Placement3D(origin, local_z, local_x))

            d256 = f.createIfcCartesianPoint(rp1)
            d257 = f.createIfcDirection(to_real(xvec))
            d258 = f.createIfcDirection(to_real(yvec))
            d236 = f.createIfcAxis2Placement3D(d256, d257, d258)
            local_placement = f.createIfcLocalPlacement(d237, d236)

            pipe_segment = f.createIfcPipeSegment(
                create_guid(),
                owner_history,
                f"{self.name}{i}",
                "An awesome pipe",
                None,
                local_placement,
                product_shape,
                None,
            )
            pipe_segments.append(pipe_segment)

            ifc_mat = self.material.ifc_mat
            mat_profile = f.createIfcMaterialProfile(self.material.name, None, ifc_mat, profile, None, None)
            mat_profile_set = f.createIfcMaterialProfileSet(None, None, [mat_profile], None)
            mat_profile_set = f.createIfcMaterialProfileSetUsage(mat_profile_set, 8, None)
            f.createIfcRelAssociatesMaterial(create_guid(), None, None, None, [pipe_segment], mat_profile_set)

        # Add elbows
        # TODO: Make this parametric by using swept geom
        for i, elbow_shape in enumerate(self.elbows):
            pfitting_placement = create_ifclocalplacement(
                f,
                (0.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                (1.0, 0.0, 0.0),
                parent.ObjectPlacement,
            )

            # ifc_elbow = f.add(ifcopenshell_geom.serialise(schema=self.schema, string_or_shape=elbow_shape))
            ifc_elbow = f.add(ifcopenshell.geom.tesselate(schema, elbow_shape, self.faceted_tol))
            #
            # # Link to representation context
            for rep in ifc_elbow.Representations:
                rep.ContextOfItems = context

            pfitting = f.createIfcPipeFitting(
                create_guid(),
                owner_history,
                f"Elbow{i}",
                "An awesome Elbow",
                None,
                pfitting_placement,
                ifc_elbow,
                None,
                None,
            )
            # pfitting.Representation = ifc_elbow
            pipe_segments.append(pfitting)

        return pipe_segments

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
    def segments(self):
        return self._segments

    @property
    def elbows(self):
        return self._elbows

    @property
    def metadata(self):
        return self._metadata

    @property
    def geometries(self):
        return self._elbows + self._swept_solids

    @property
    def pipe_bend_radius(self):
        if self.section.type != "PIPE":
            return None

        wt = self.section.wt
        R = self.section.r
        D = R * 2
        w_tol = 0.125
        cor_tol = 0.003
        corr_T = (wt - (wt * w_tol)) - cor_tol
        d = D - 2.0 * corr_T

        return roundoff(d + corr_T / 2.0)

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

    @staticmethod
    def make_edge(segment):
        """

        :param segment:
        :return:
        :rtype: OCC.Core.TopoDS.TopoDS_Edge
        """
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCC.Core.gp import gp_Pnt

        p1, p2 = segment
        return BRepBuilderAPI_MakeEdge(gp_Pnt(*p1.p.tolist()), gp_Pnt(*p2.p.tolist())).Edge()

    @staticmethod
    def make_fillet(edge1, edge2, normal, bend_radius):
        from OCC.Core.BRep import BRep_Tool_Pnt
        from OCC.Core.ChFi2d import ChFi2d_AnaFilletAlgo
        from OCC.Core.gp import gp_Dir, gp_Pln, gp_Vec
        from OCC.Extend.TopologyUtils import TopologyExplorer

        from ada.core.utils import is_edges_ok

        f = ChFi2d_AnaFilletAlgo()
        plane_normal = gp_Dir(gp_Vec(*normal[:3]))
        t = TopologyExplorer(edge1)
        apt = None
        for v in t.vertices():
            apt = BRep_Tool_Pnt(v)
        f.Init(edge1, edge2, gp_Pln(apt, plane_normal))
        f.Perform(bend_radius * 0.999)
        fillet2d = f.Result(edge1, edge2)
        if is_edges_ok(edge1, fillet2d, edge2) is False:
            logging.debug("Is Edges algorithm fails on edges")

        return edge1, edge2, fillet2d

    @staticmethod
    def make_sec_face(point, direction, radius):
        from OCC.Core.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakeWire,
        )
        from OCC.Core.gp import gp_Ax2, gp_Circ

        circle = gp_Circ(gp_Ax2(point, direction), radius)
        profile_edge = BRepBuilderAPI_MakeEdge(circle).Edge()
        profile_wire = BRepBuilderAPI_MakeWire(profile_edge).Wire()
        profile_face = BRepBuilderAPI_MakeFace(profile_wire).Face()
        return profile_face

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
            for p in self.points:
                p.units = value
            self._build_pipe()
            self._units = value

    @property
    def faceted_tol(self):
        if self.units == "m":
            return 1e-2
        else:
            return 1

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_pipe_segments()
        return self._ifc_elem

    def __repr__(self):
        return f"Pipe({self.name}, {self.section})"


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
        super().__init__(name, guid=guid, metadata=metadata, units=units)

        self._ifc_elem = ifc_elem
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
        yvec = unit_vector(np.cross(xvec, zvec))

        return xvec, yvec, zvec

    def _import_from_ifc(self, ifc_elem):
        raise NotImplementedError("Import of IfcWall is not yet supported")

    def _generate_ifc_elem(self):
        from ada.core.constants import O, X, Z
        from ada.core.ifc_utils import (
            add_negative_extrusion,
            create_ifcaxis2placement,
            create_ifcextrudedareasolid,
            create_ifclocalplacement,
            create_ifcpolyline,
            create_property_set,
        )

        if self.parent is None:
            raise ValueError("Ifc element cannot be built without any parent element")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]
        parent = self.parent.ifc_elem
        elevation = self.origin[2]

        # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
        wall_placement = create_ifclocalplacement(f, relative_to=parent.ObjectPlacement)

        # polyline = self.create_ifcpolyline(f, [(0.0, 0.0, 0.0), (5.0, 0.0, 0.0)])
        polyline = create_ifcpolyline(f, self.points)
        axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve2D", [polyline])

        extrusion_placement = create_ifcaxis2placement(
            f, (0.0, 0.0, float(elevation)), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
        )

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
            "Physical model",
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
        from ada.core.ifc_utils import create_ifclocalplacement

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]
        schema = a.ifc_file.wrapped_data.schema

        # Create a simplified representation for the Window
        insert_placement = create_ifclocalplacement(f, O, Z, X, wall_el.ObjectPlacement)
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
        from ada.core.utils import intersect_calc, parallel_check

        area_points = []
        vpo = [np.array(p) for p in self.points]
        p2 = None
        yvec = None
        prev_xvec = None
        prev_yvec = None

        # Inner line
        for p1, p2 in zip(vpo[:-1], vpo[1:]):
            xvec = p2 - p1
            yvec = unit_vector(np.cross(xvec, np.array([0, 0, 1])))
            new_point = p1 + yvec * (self._thickness / 2) + yvec * self.offset
            if prev_xvec is not None:
                if parallel_check(xvec, prev_xvec) is False:
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
                if parallel_check(xvec, prev_xvec) is False:
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

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()
        return self._ifc_elem

    def __repr__(self):
        return f"Wall({self.name})"


class Shape(BackendGeom):
    """
    A shape object


    :param name:
    :param geom:
    :param colour:
    :param opacity:
    :param metadata:
    """

    def __init__(
        self,
        name,
        geom,
        colour=None,
        opacity=1.0,
        metadata=None,
        units="m",
        ifc_elem=None,
        guid=None,
    ):

        super().__init__(name, guid=guid, metadata=metadata, units=units)
        if type(geom) is str:
            from OCC.Extend.DataExchange import read_step_file

            geom = read_step_file(geom)

        self._ifc_elem = ifc_elem
        if ifc_elem is not None:
            self.guid = ifc_elem.GlobalId
            self._import_from_ifc_elem(ifc_elem)

        self._geom = geom
        self.colour = colour
        self._opacity = opacity

    def generate_parametric_solid(self, ifc_file):
        from ada.core.constants import O, X, Z
        from ada.core.ifc_utils import (
            create_ifcaxis2placement,
            create_ifcextrudedareasolid,
            create_IfcFixedReferenceSweptAreaSolid,
            create_ifcindexpolyline,
            create_ifcpolyline,
            create_ifcrevolveareasolid,
            to_real,
        )

        f = ifc_file
        context = f.by_type("IfcGeometricRepresentationContext")[0]

        opening_axis_placement = create_ifcaxis2placement(f, O, Z, X)

        if type(self) is PrimBox:
            box = self
            assert isinstance(box, PrimBox)
            p1 = box.p1
            p2 = box.p2
            points = [
                p1,
                (p1[0], p2[1], p1[2]),
                (p2[0], p2[1], p1[2]),
                (p2[0], p1[1], p1[2]),
            ]
            depth = p2[2] - p1[2]
            polyline = create_ifcpolyline(f, points)
            profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
            solid_geom = create_ifcextrudedareasolid(f, profile, opening_axis_placement, (0.0, 0.0, 1.0), depth)
        elif type(self) is PrimCyl:
            cyl = self
            assert isinstance(cyl, PrimCyl)
            p1 = cyl.p1
            p2 = cyl.p2
            r = cyl.r

            vec = np.array(p2) - np.array(p1)
            uvec = unit_vector(vec)
            vecdir = to_real(uvec)

            cr_dir = np.array([0, 0, 1])

            if vector_length(abs(uvec) - abs(cr_dir)) == 0.0:
                cr_dir = np.array([1, 0, 0])

            perp_dir = np.cross(uvec, cr_dir)

            if vector_length(perp_dir) == 0.0:
                raise ValueError("Perpendicular dir cannot be zero")

            create_ifcaxis2placement(f, to_real(p1), vecdir, to_real(perp_dir))

            opening_axis_placement = create_ifcaxis2placement(f, to_real(p1), vecdir, to_real(perp_dir))

            depth = vector_length(vec)
            profile = f.createIfcCircleProfileDef("AREA", self.name, None, r)
            solid_geom = create_ifcextrudedareasolid(f, profile, opening_axis_placement, Z, depth)
        elif type(self) is PrimExtrude:
            extrude = self
            assert isinstance(extrude, PrimExtrude)
            # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
            # polyline = self.create_ifcpolyline(self.file, [p[:3] for p in points])
            normal = extrude.poly.normal
            h = extrude.extrude_depth
            points = [tuple(x.astype(float).tolist()) for x in extrude.poly.seg_global_points]
            seg_index = extrude.poly.seg_index
            polyline = create_ifcindexpolyline(f, points, seg_index)
            profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
            solid_geom = create_ifcextrudedareasolid(f, profile, opening_axis_placement, [float(n) for n in normal], h)
        elif type(self) is PrimRevolve:
            rev = self
            assert isinstance(rev, PrimRevolve)
            # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/link/annex-e.htm
            # 8.8.3.28 IfcRevolvedAreaSolid

            revolve_axis = [float(n) for n in rev.revolve_axis]
            revolve_origin = [float(x) for x in rev.revolve_origin]
            revolve_angle = rev.revolve_angle
            points = [tuple(x.astype(float).tolist()) for x in rev.poly.seg_global_points]
            seg_index = rev.poly.seg_index
            polyline = create_ifcindexpolyline(f, points, seg_index)
            profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
            solid_geom = create_ifcrevolveareasolid(
                f,
                profile,
                opening_axis_placement,
                revolve_origin,
                revolve_axis,
                revolve_angle,
            )
        elif type(self) is PrimSphere:
            sphere = self
            assert isinstance(sphere, PrimSphere)
            opening_axis_placement = create_ifcaxis2placement(f, to_real(sphere.pnt), Z, X)
            solid_geom = f.createIfcSphere(opening_axis_placement, float(sphere.radius))
        elif type(self) is PrimSweep:
            sweep = self
            assert isinstance(sweep, PrimSweep)
            sweep_curve = sweep.sweep_curve.ifc_elem
            profile = f.createIfcArbitraryClosedProfileDef("AREA", None, sweep.profile_curve_outer.ifc_elem)
            ifc_xdir = f.createIfcDirection([float(x) for x in sweep.profile_curve_outer.xdir])
            solid_geom = create_IfcFixedReferenceSweptAreaSolid(
                f, sweep_curve, profile, opening_axis_placement, None, None, ifc_xdir
            )
        else:
            raise ValueError(f'Penetration type "{self}" is not yet supported')

        shape_representation = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid_geom])
        ifc_shape = f.createIfcProductDefinitionShape(None, None, [shape_representation])

        # Link to representation context
        for rep in ifc_shape.Representations:
            rep.ContextOfItems = context

        return ifc_shape

    def _generate_ifc_elem(self):
        import ifcopenshell.geom

        from ada.core.ifc_utils import add_colour, create_ifclocalplacement

        if self.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        a = self.parent.get_assembly()
        f = a.ifc_file

        context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]
        parent = self.parent.ifc_elem
        schema = a.ifc_file.wrapped_data.schema

        shape_placement = create_ifclocalplacement(f, relative_to=parent.ObjectPlacement)
        if type(self) is not Shape:
            ifc_shape = self.generate_parametric_solid(f)
        else:
            occ_string = ifcopenshell.geom.occ_utils.serialize_shape(self.geom)
            serialized_geom = ifcopenshell.geom.serialise(schema, occ_string)

            if serialized_geom is None:
                if a.units == "mm":
                    tol = _Settings.mmtol
                elif a.units == "m":
                    tol = _Settings.mtol
                else:
                    raise ValueError(f'Unrecognized unit "{a.units}"')
                logging.debug("Starting serialization of geometry")
                serialized_geom = ifcopenshell.geom.tesselate(schema, occ_string, tol)
            ifc_shape = f.add(serialized_geom)

        # Link to representation context
        for rep in ifc_shape.Representations:
            rep.ContextOfItems = context

        if "guid" in self.metadata.keys():
            guid = self.metadata["guid"]
        else:
            guid = create_guid()

        if "description" in self.metadata.keys():
            description = self.metadata["description"]
        else:
            description = None

        if "hidden" in self.metadata.keys():
            if self.metadata["hidden"] is True:
                a.presentation_layers.append(ifc_shape)

        # Add colour
        if self.colour is not None:
            add_colour(f, ifc_shape.Representations[0].Items[0], str(self.colour), self.colour)

        ifc_elem = f.createIfcBuildingElementProxy(
            guid,
            owner_history,
            self.name,
            description,
            None,
            shape_placement,
            ifc_shape,
            None,
            None,
        )

        return ifc_elem

    def _import_from_ifc_elem(self, ifc_elem):
        from ada.core.ifc_utils import getIfcPropertySets

        props = getIfcPropertySets(ifc_elem)
        if props is None:
            return None
        product_name = ifc_elem.Name
        if "NAME" in props.keys():
            name = props["NAME"] if product_name is None else product_name
        else:
            name = product_name if product_name is not None else "Test"

        if name is None or len(props.keys()) == 0:
            return None

        return Shape(
            name,
            None,
            guid=ifc_elem.GlobalId,
            metadata=dict(props=props, ifc_source=True),
        )

    @property
    def type(self):
        return type(self.geom)

    @property
    def transparent(self):
        if self.opacity == 1.0:
            return False
        else:
            return True

    @property
    def opacity(self):
        return self._opacity

    @opacity.setter
    def opacity(self, value):
        if 0.0 <= value <= 1.0:
            self._opacity = value
        else:
            raise ValueError("Opacity is only valid between 1 and 0")

    @property
    def bbox(self):
        """return the bounding box of the TopoDS_Shape `shape`

        returns xmin, ymin, zmin, xmax, ymax, zmax, xmax - xmin, ymax - ymin, zmax - zmin
        """
        from ada.core.utils import get_boundingbox

        return get_boundingbox(self.geom, use_mesh=True)

    @property
    def point_on(self):
        return self.bbox[3:6]

    @property
    def geom(self):
        """

        :return:
        :rtype:
        """
        if self._geom is None:
            from ada.core.ifc_utils import get_representation

            if self._ifc_elem is not None:
                ifc_elem = self._ifc_elem
            elif "ifc_file" in self.metadata.keys():
                a = self.parent.get_assembly()
                ifc_file = self.metadata["ifc_file"]
                ifc_f = a.get_ifc_source_by_name(ifc_file)
                ifc_elem = ifc_f.by_guid(self.guid)
            else:
                raise ValueError("No geometry information attached to this element")
            geom, color, alpha = get_representation(ifc_elem, self.ifc_settings)
            self._geom = geom
            self._colour = color
            self._opacity = alpha
        return self._geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            scale_factor = self._unit_conversion(self._units, value)
            if self._geom is not None:
                from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
                from OCC.Core.gp import gp_Trsf

                trsf = gp_Trsf()
                trsf.SetScaleFactor(scale_factor)
                self._geom = BRepBuilderAPI_Transform(self.geom, trsf, True).Shape()
            if self.metadata.get("ifc_source") is True:
                logging.info("do something")

            self._units = value

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()

        return self._ifc_elem


class PrimSphere(Shape):
    def __init__(self, name, pnt, radius, colour=None, opacity=1.0, metadata=None, units="m"):
        from ada.core.utils import make_sphere

        self.pnt = pnt
        self.radius = radius
        super(PrimSphere, self).__init__(
            name=name,
            geom=make_sphere(pnt, radius),
            colour=colour,
            opacity=opacity,
            metadata=metadata,
            units=units,
        )

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import make_sphere

            scale_factor = self._unit_conversion(self._units, value)
            self.pnt = tuple([x * scale_factor for x in self.pnt])
            self.radius = self.radius * scale_factor
            self._geom = make_sphere(self.pnt, self.radius)
            self._units = value


class PrimBox(Shape):
    def __init__(self, name, p1, p2, colour=None, opacity=1.0, metadata=None, units="m"):
        from ada.core.utils import make_box_by_points

        self.p1 = p1
        self.p2 = p2
        super(PrimBox, self).__init__(
            name=name,
            geom=make_box_by_points(p1, p2),
            colour=colour,
            opacity=opacity,
            metadata=metadata,
            units=units,
        )

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            from ada.core.utils import make_box_by_points

            scale_factor = self._unit_conversion(self._units, value)
            self.p1 = tuple([x * scale_factor for x in self.p1])
            self.p2 = tuple([x * scale_factor for x in self.p2])
            self._geom = make_box_by_points(self.p1, self.p2)
            self._units = value


class PrimCyl(Shape):
    def __init__(self, name, p1, p2, r, colour=None, opacity=1.0, metadata=None, units="m"):
        from ada.core.utils import make_cylinder_from_points

        self.p1 = np.array(p1)
        self.p2 = np.array(p2)
        self.r = r
        super(PrimCyl, self).__init__(name, make_cylinder_from_points(p1, p2, r), colour, opacity, metadata, units)

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        from ada.core.utils import make_cylinder_from_points

        if value != self._units:
            scale_factor = self._unit_conversion(self._units, value)
            self.p1 = [x * scale_factor for x in self.p1]
            self.p2 = [x * scale_factor for x in self.p2]
            self.r = self.r * scale_factor
            self._geom = make_cylinder_from_points(self.p1, self.p2, self.r)


class PrimExtrude(Shape):
    def __init__(
        self,
        name,
        points2d,
        h,
        normal,
        origin,
        xdir,
        tol=1e-3,
        colour=None,
        opacity=1.0,
        metadata=None,
        units="m",
    ):
        self._name = name
        poly = CurvePoly(
            points2d=points2d,
            normal=normal,
            origin=origin,
            xdir=xdir,
            tol=tol,
            parent=self,
        )
        self._poly = poly
        self._extrude_depth = h

        super(PrimExtrude, self).__init__(
            name,
            self._poly.make_extruded_solid(self._extrude_depth),
            colour,
            opacity,
            metadata,
            units,
        )

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            scale_factor = self._unit_conversion(self._units, value)
            self.poly.origin = [x * scale_factor for x in self.poly.origin]
            self._extrude_depth = self._extrude_depth * scale_factor
            self._units = value

    @property
    def poly(self):
        """

        :return:
        :rtype: CurvePoly
        """
        return self._poly

    @property
    def extrude_depth(self):
        return self._extrude_depth


class PrimRevolve(Shape):
    """
    Primitive Revolved

    """

    def __init__(
        self,
        name,
        points2d,
        origin,
        xdir,
        normal,
        rev_angle,
        tol=1e-3,
        colour=None,
        opacity=1.0,
        metadata=None,
        units="m",
    ):
        self._name = name
        poly = CurvePoly(
            points2d=points2d,
            normal=[roundoff(x) for x in normal],
            origin=origin,
            xdir=[roundoff(x) for x in xdir],
            tol=tol,
            parent=self,
        )
        self._poly = poly
        self._revolve_angle = rev_angle
        self._revolve_axis = [roundoff(x) for x in poly.ydir]
        self._revolve_origin = origin
        super(PrimRevolve, self).__init__(
            name,
            self._poly.make_revolve_solid(
                self._revolve_axis,
                self._revolve_angle,
                self._revolve_origin,
            ),
            colour,
            opacity,
            metadata,
            units,
        )

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            raise NotImplementedError()

    @property
    def poly(self):
        return self._poly

    @property
    def revolve_origin(self):
        return self._revolve_origin

    @property
    def revolve_axis(self):
        return self._revolve_axis

    @property
    def revolve_angle(self):
        return self._revolve_angle


class PrimSweep(Shape):
    def __init__(
        self,
        name,
        sweep_curve,
        normal,
        xdir,
        profile_curve_outer,
        profile_curve_inner=None,
        origin=None,
        tol=1e-3,
        colour=None,
        opacity=1.0,
        metadata=None,
        units="m",
    ):
        if type(sweep_curve) is list:
            sweep_curve = CurvePoly(points3d=sweep_curve, is_closed=False)

        if type(profile_curve_outer) is list:
            origin = sweep_curve.origin if origin is None else origin
            profile_curve_outer = CurvePoly(profile_curve_outer, origin=origin, normal=normal, xdir=xdir)

        sweep_curve.parent = self
        profile_curve_outer.parent = self

        self._ifc_elem = None
        self._sweep_curve = sweep_curve
        self._profile_curve_outer = profile_curve_outer
        self._profile_curve_inner = profile_curve_inner

        super(PrimSweep, self).__init__(
            name,
            self._sweep_geom(),
            colour,
            opacity,
            metadata,
            units,
        )

    def _sweep_geom(self):
        from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakePipe

        pipe = BRepOffsetAPI_MakePipe(self.sweep_curve.wire, self.profile_curve_outer.wire).Shape()
        return pipe

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            raise NotImplementedError()

    @property
    def sweep_curve(self):
        return self._sweep_curve

    @property
    def profile_curve_outer(self):
        return self._profile_curve_outer

    @property
    def profile_curve_inner(self):
        return self._profile_curve_inner


class Penetration(BackendGeom):
    _name_gen = Counter(1, "Pen")

    """
    A penetration object. Wraps around a primitive. TODO: Maybe this should be evaluated for removal?

    :param primitive: Takes any Prim<> Class in ada.
    """

    def __init__(self, primitive, metadata=None, parent=None, units="m", guid=None):
        if type(primitive) not in [PrimRevolve, PrimCyl, PrimExtrude, PrimBox]:
            raise ValueError(f'Unsupported primitive "{type(primitive)}"')

        super(Penetration, self).__init__(primitive.name, guid=guid, metadata=metadata, units=units)
        self._primitive = primitive
        self._parent = parent
        self._ifc_opening = None

    def _generate_ifc_opening(self):
        from ada.core.constants import O, X, Z
        from ada.core.ifc_utils import add_properties_to_elem, create_ifclocalplacement

        if self.parent is None:
            raise ValueError("This penetration has no parent")

        a = self.parent.parent.get_assembly()
        f = a.ifc_file

        ifc_geom = self.parent.ifc_elem
        geom_parent = self.parent.parent.ifc_elem
        # context = f.by_type("IfcGeometricRepresentationContext")[0]
        owner_history = f.by_type("IfcOwnerHistory")[0]

        # Create and associate an opening for the window in the wall
        opening_placement = create_ifclocalplacement(f, O, Z, X, geom_parent.ObjectPlacement)
        opening_shape = self.primitive.generate_parametric_solid(f)

        opening_element = f.createIfcOpeningElement(
            create_guid(),
            owner_history,
            self.name,
            self.name + " (Opening)",
            None,
            opening_placement,
            opening_shape,
            None,
        )

        if "props" in self.metadata.keys():
            pro = self.metadata["props"]
            if len(pro.keys()) > 0:
                if type(list(pro.values())[0]) is dict:
                    for pro_id, prop_ in pro.items():
                        add_properties_to_elem(pro_id, f, opening_element, prop_)
                else:
                    add_properties_to_elem("Properties", f, opening_element, pro)

        f.createIfcRelVoidsElement(
            create_guid(),
            owner_history,
            None,
            None,
            ifc_geom,
            opening_element,
        )

        return opening_element

    @property
    def primitive(self):
        return self._primitive

    @property
    def geom(self):
        return self.primitive.geom

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            self.primitive.units = value
            self._units = value

    @property
    def ifc_opening(self):
        if self._ifc_opening is None:
            self._ifc_opening = self._generate_ifc_opening()
        return self._ifc_opening


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
        super(Section, self).__init__(name, guid, metadata, units)
        self._type = sec_type
        self._h = h
        self._w_top = w_top
        self._w_btn = w_btn
        self._t_w = t_w
        self._t_ftop = t_ftop
        self._t_fbtn = t_fbtn
        self._r = r
        self._wt = wt
        self._sec_id = sec_id
        self._outer_poly = outer_poly
        self._inner_poly = inner_poly
        self._sec_str = sec_str
        self._parent = parent

        self._ifc_profile = None
        self._ifc_beam_type = None

        if ifc_elem is not None:
            props = self._import_from_ifc_beam(ifc_elem)
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
            if "parent" in key or key in ["_sec_id"]:
                continue
            if other.__dict__[key] != val:
                return False

        return True

    def edit(self, sec_id=None, parent=None):
        """

        :param sec_id:
        :param parent:
        :return:
        """
        self._sec_id = sec_id if sec_id is not None else self._sec_id
        self._parent = parent if parent is not None else self._parent

    def _generate_ifc_section_data(self):
        from ada.core.ifc_utils import create_ifcindexpolyline, create_ifcpolyline

        a = self.parent.parent.get_assembly()
        f = a.ifc_file

        if SectionCat.is_i_profile(self.type):
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            polyline = create_ifcpolyline(f, outer_curve)
            profile = f.createIfcArbitraryClosedProfileDef("AREA", self.name, polyline)
            # profile = f.createIfcIShapeProfileDef('AREA', self.name, None, self.w_top, self.h, self.t_w, self.t_ftop)
        elif SectionCat.is_hp_profile(self.type):
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            points = [f.createIfcCartesianPoint(p) for p in outer_curve]
            ifc_polyline = f.createIfcPolyLine(points)
            profile = f.createIfcArbitraryClosedProfileDef("AREA", self.name, ifc_polyline)
        elif SectionCat.is_box_profile(self.type):
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            outer_points = [f.createIfcCartesianPoint(p) for p in outer_curve + [outer_curve[0]]]
            inner_points = [f.createIfcCartesianPoint(p) for p in inner_curve + [inner_curve[0]]]
            inner_curve = f.createIfcPolyLine(inner_points)
            outer_curve = f.createIfcPolyLine(outer_points)
            profile = f.createIfcArbitraryProfileDefWithVoids("AREA", self.name, outer_curve, [inner_curve])
        elif self.type in SectionCat.circular:
            profile = f.createIfcCircleProfileDef("AREA", self.name, None, self.r)
        elif self.type in SectionCat.tubular:
            profile = f.createIfcCircleHollowProfileDef("AREA", self.name, None, self.r, self.wt)
        elif self.type in SectionCat.general:
            r = np.sqrt(self.properties.Ax / np.pi)
            profile = f.createIfcCircleProfileDef("AREA", self.name, None, r)
        elif self.type in SectionCat.flatbar:
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            polyline = create_ifcpolyline(f, outer_curve)
            profile = f.createIfcArbitraryClosedProfileDef("AREA", self.name, polyline)
        elif self.type in SectionCat.channels:
            outer_curve, inner_curve, disconnected = self.cross_sec(True)
            polyline = create_ifcpolyline(f, outer_curve)
            profile = f.createIfcArbitraryClosedProfileDef("AREA", self.name, polyline)
        elif self.type == "poly":
            opoly = self.poly_outer
            opoints = [(float(n[0]), float(n[1]), float(n[2])) for n in opoly.seg_global_points]
            opolyline = create_ifcindexpolyline(f, opoints, opoly.seg_index)
            if self.poly_inner is None:
                profile = f.createIfcArbitraryClosedProfileDef("AREA", self.name, opolyline)
            else:
                ipoly = self.poly_inner
                ipoints = [(float(n[0]), float(n[1]), float(n[2])) for n in ipoly.seg_global_points]
                ipolyline = create_ifcindexpolyline(f, ipoints, ipoly.seg_index)
                profile = f.createIfcArbitraryProfileDefWithVoids("AREA", self.name, opolyline, [ipolyline])
        else:
            raise ValueError(f'Have yet to implement section type "{self.type}"')
        if self.name is None:
            raise ValueError("Name cannot be None!")

        beamtype = f.createIfcBeamType(
            create_guid(),
            f.by_type("IfcOwnerHistory")[0],
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

    def _import_from_ifc_beam(self, ifc_elem):
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
            else:
                raise NotImplementedError(f'IFC section type "{ifc_elem}" is not yet implemented')
        return sec

    @property
    def type(self):
        return self._type

    @property
    def id(self):
        return self._sec_id

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
        :rtype: PolyCurve
        """
        return self._outer_poly

    @property
    def poly_inner(self):
        """

        :return:
        :rtype: PolyCurve
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

        from ada.core.utils import (
            local_2_global_nodes,
            make_circle,
            make_face_w_cutout,
            make_wire_from_points,
        )

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
        from .base.renderer import SectionRenderer

        sec_render = SectionRenderer()
        sec_render.display(self)

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

        owner_history = f.by_type("IfcOwnerHistory")[0]

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
        yield_stress = None
        props = {entity.Name: entity.NominalValue[0] for entity in mat_psets[0].Properties}

        mat_props = dict(
            E=props["YoungModulus"],
            v=props["PoissonRatio"],
            rho=props["MassDensity"],
            alpha=props["ThermalExpansionCoefficient"],
            zeta=props["SpecificHeatCapacity"],
            sig_y=yield_stress,
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


class Node:
    """
    Base node object

    :param p: Array of [x, y, z] coords
    :param nid: Node id
    :param bc: Boundary condition
    :param r: Radius
    :param parent: Parent object
    """

    def __init__(self, p, nid=None, bc=None, r=None, parent=None, units="m"):
        """

        :param p:
        :param nid:
        :param bc:
        :param r:
        :param parent:
        :param units:
        """
        self._id = nid
        self.p = np.array([p[0], p[1], p[2]], dtype=np.float64) if type(p) != np.ndarray else p
        if len(self.p) != 3:
            raise ValueError("Node object must have exactly 3 coordinates (x, y, z).")
        self._bc = bc
        self._r = r
        self._parent = parent
        self._units = units
        self._refs = []

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        self._id = value

    @property
    def x(self):
        return self.p[0]

    @property
    def y(self):
        return self.p[1]

    @property
    def z(self):
        return self.p[2]

    @property
    def bc(self):
        """

        :return:
        :rtype: ada.fem.Bc
        """
        return self._bc

    @bc.setter
    def bc(self, value):
        self._bc = value

    @property
    def r(self):
        return self._r

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            scale_factor = Backend._unit_conversion(self._units, value)
            self.p = np.array(
                [
                    self.p[0] * scale_factor,
                    self.p[1] * scale_factor,
                    self.p[2] * scale_factor,
                ]
            )
            if self._r is not None:
                self._r *= scale_factor
            self._units = value

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def refs(self):
        return self._refs

    def __getitem__(self, index):
        return self.p[index]

    def __gt__(self, other):
        return tuple(self.p) > tuple(other.p)

    def __lt__(self, other):
        return tuple(self.p) < tuple(other.p)

    def __ge__(self, other):
        return tuple(self.p) >= tuple(other.p)

    def __le__(self, other):
        return tuple(self.p) <= tuple(other.p)

    def __eq__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return tuple(self.p) == tuple(other.p)

    def __ne__(self, other):
        if not isinstance(other, Node):
            return NotImplemented
        return tuple(self.p) != tuple(other.p)

    def __repr__(self):
        return f"Node([{self.x}, {self.y}, {self.z}], {self.id})"


class Connection(Part):
    """
    A basic Connection class

    """

    def __init__(self, name, incoming_beams, wp=None):
        super(Connection, self).__init__(name)
        self._beams = incoming_beams
        self._clines = list()
        self._wp = wp

    def edit(self, colour=None, parent=None, name=None, wp=None):
        """
        Sets the joint work point

        :param colour:
        :param parent:
        :param name:
        :param wp:
        """
        self._colour = colour if colour is not None else self._colour
        self._parent = parent if parent is not None else self._parent
        self._name = name if name is not None else self._name
        self._wp = wp if wp is not None else self._wp

    def add_cline(self, cline):
        self._clines.append(cline)

    @property
    def wp(self):
        return self._wp

    def __repr__(self):
        return 'Joint Name: "{}", center: "{}"'.format(self._name, self._wp)


class CurveRevolve:
    def __init__(
        self,
        curve_type,
        p1,
        p2,
        radius=None,
        rot_axis=None,
        point_on=None,
        rot_origin=None,
        parent=None,
    ):
        self._p1 = p1
        self._p2 = p2
        self._type = curve_type
        self._radius = radius
        self._rot_axis = rot_axis
        self._parent = parent
        self._point_on = point_on
        self._rot_origin = rot_origin

        if self._point_on is not None:
            from ada.core.constants import O, X, Y, Z
            from ada.core.utils import (
                calc_arc_radius_center_from_3points,
                global_2_local_nodes,
                local_2_global_nodes,
            )

            p1, p2 = self.p1, self.p2

            csys0 = [X, Y, Z]
            res = global_2_local_nodes(csys0, O, [p1, self._point_on, p2])
            lcenter, radius = calc_arc_radius_center_from_3points(res[0][:2], res[1][:2], res[2][:2])
            if True in np.isnan(lcenter) or np.isnan(radius):
                raise ValueError("Curve is not valid. Please check your input")
            res2 = local_2_global_nodes([lcenter], O, X, Z)
            center = res2[0]
            self._radius = radius
            self._rot_origin = center

    def edit(self, parent=None):
        if parent is not None:
            self._parent = parent

    @property
    def type(self):
        return self._type

    @property
    def p1(self):
        return self._p1

    @property
    def p2(self):
        return self._p2

    @property
    def radius(self):
        return self._radius

    @property
    def point_on(self):
        return self._point_on

    @property
    def rot_axis(self):
        return self._rot_axis

    @property
    def rot_origin(self):
        return np.array(self._rot_origin)

    @property
    def parent(self):
        """

        :return:
        :rtype: ada.Beam
        """
        return self._parent


class CurvePoly:
    """
    TODO: Simplify this class.

    :param points3d:
    :param points2d: Input of points
    :param origin: Origin of Polycurve (only applicable if using points2D)
    :param normal: Local Normal direction (only applicable if using points2D)
    :param xdir: Local X-Direction (only applicable if using points2D)
    :param flip_normal:
    """

    def __init__(
        self,
        points2d=None,
        origin=None,
        normal=None,
        xdir=None,
        points3d=None,
        flip_normal=False,
        tol=1e-3,
        is_closed=True,
        parent=None,
        debug=False,
    ):
        self._tol = tol
        self._parent = parent
        self._ifc_elem = None
        self._is_closed = is_closed
        self._debug = debug

        from ada.core.utils import (
            clockwise,
            global_2_local_nodes,
            local_2_global_nodes,
            normal_to_points_in_plane,
            unit_vector,
        )

        if points2d is None and points3d is None:
            raise ValueError("Either points2d or points3d must be set")

        if points2d is not None:
            if origin is None or normal is None or xdir is None:
                raise ValueError("You must supply origin, xdir and normal when passing in 2d points")
            points2d_no_r = [n[:2] for n in points2d]
            points3d = local_2_global_nodes(points2d_no_r, origin, xdir, normal)
            for i, p in enumerate(points2d):
                if len(p) == 3:
                    points3d[i] = (
                        points3d[i][0],
                        points3d[i][1],
                        points3d[i][2],
                        p[-1],
                    )
                else:
                    points3d[i] = tuple(points3d[i].tolist())
            self._xdir = xdir
            self._normal = np.array(normal)
            self._origin = np.array(origin).astype(float)
            self._ydir = np.cross(self._normal, self._xdir)
        else:
            self._normal = normal_to_points_in_plane([np.array(x[:3]) for x in points3d])
            self._origin = np.array(points3d[0][:3]).astype(float)
            self._xdir = unit_vector(np.array(points3d[1][:3]) - np.array(points3d[0][:3]))
            self._ydir = np.cross(self._normal, self._xdir)
            csys = [self._xdir, self._ydir]
            points2d = global_2_local_nodes(csys, self._origin, [np.array(x[:3]) for x in points3d])
            points3d = [x.p if type(x) is Node else x for x in points3d]
            for i, p in enumerate(points3d):
                if len(p) == 4:
                    points2d[i] = (points2d[i][0], points2d[i][1], p[-1])
                else:
                    points2d[i] = (points2d[i][0], points2d[i][1])

        if clockwise(points2d) is False:
            if is_closed:
                points2d = [points2d[0]] + [p for p in reversed(points2d[1:])]
                points3d = [points3d[0]] + [p for p in reversed(points3d[1:])]
            else:
                points2d = [p for p in reversed(points2d)]
                points3d = [p for p in reversed(points3d)]

        self._points3d = points3d
        self._points2d = points2d

        if flip_normal:
            self._normal *= -1

        self._seg_list = None
        self._seg_index = None
        self._face = None
        self._wire = None
        self._edges = None
        self._seg_global_points = None
        self._nodes = None

        self._local2d_to_polycurve(points2d, tol)

    def _to_ifc_elem(self):
        a = self.parent.parent.get_assembly()
        f = a.ifc_file

        ifc_segments = []
        for seg_ind in self.seg_index:
            if len(seg_ind) == 2:
                ifc_segments.append(f.createIfcLineIndex(seg_ind))
            elif len(seg_ind) == 3:
                ifc_segments.append(f.createIfcArcIndex(seg_ind))
            else:
                raise ValueError("Unrecognized number of values")

        # TODO: Investigate using 2DLists instead is it could reduce complexity?
        # ifc_point_list = ifcfile.createIfcCartesianPointList2D(points)
        points = [tuple(x.astype(float).tolist()) for x in self.seg_global_points]
        ifc_point_list = f.createIfcCartesianPointList3D(points)
        segindex = f.createIfcIndexedPolyCurve(ifc_point_list, ifc_segments)
        return segindex

    def _segments_2_edges(self, segments):
        from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCC.Core.GC import GC_MakeArcOfCircle
        from OCC.Core.gp import gp_Pnt

        from ada.core.utils import make_edge

        edges = []
        for seg in segments:
            if type(seg) is ArcSegment:
                aArcOfCircle = GC_MakeArcOfCircle(
                    gp_Pnt(*list(seg.p1)),
                    gp_Pnt(*list(seg.midpoint)),
                    gp_Pnt(*list(seg.p2)),
                )
                aEdge2 = BRepBuilderAPI_MakeEdge(aArcOfCircle.Value()).Edge()
                edges.append(aEdge2)
            else:
                edge = make_edge(seg.p1, seg.p2)
                edges.append(edge)

        return edges

    def _local2d_to_polycurve(self, local_points2d, tol=1e-3):
        """

        :param local_points2d:
        :param tol:
        :return:
        """
        from ada.core.utils import (
            build_polycurve,
            local_2_global_nodes,
            segments_to_indexed_lists,
        )

        debug_name = self._parent.name if self._parent is not None else "PolyCurveDebugging"

        seg_list = build_polycurve(local_points2d, tol, self._debug, debug_name)

        # # Convert from local to global coordinates
        for i, seg in enumerate(seg_list):
            if type(seg) is ArcSegment:
                lpoints = [seg.p1, seg.p2, seg.midpoint]
                gp = local_2_global_nodes(lpoints, self.origin, self.xdir, self.normal)
                seg.p1 = gp[0]
                seg.p2 = gp[1]
                seg.midpoint = gp[2]
            else:
                lpoints = [seg.p1, seg.p2]
                gp = local_2_global_nodes(lpoints, self.origin, self.xdir, self.normal)
                seg.p1 = gp[0]
                seg.p2 = gp[1]

        self._seg_list = seg_list
        self._seg_global_points, self._seg_index = segments_to_indexed_lists(seg_list)
        self._nodes = [Node(p) if len(p) == 3 else Node(p[:3], r=p[3]) for p in self._points3d]

    def make_extruded_solid(self, height):
        """

        :param height:
        :return:
        """
        from OCC.Core.gp import gp_Pnt, gp_Vec
        from OCC.Extend.ShapeFactory import make_extrusion, make_face

        p1 = self.origin + self.normal * height
        olist = self.origin
        starting_point = gp_Pnt(olist[0], olist[1], olist[2])
        end_point = gp_Pnt(*p1.tolist())
        vec = gp_Vec(starting_point, end_point)

        solid = make_extrusion(make_face(self.wire), height, vec)

        return solid

    def make_revolve_solid(self, axis, angle, origin):
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeRevol
        from OCC.Core.gp import gp_Ax1, gp_Dir, gp_Pnt

        revolve_axis = gp_Ax1(gp_Pnt(origin[0], origin[1], origin[2]), gp_Dir(axis[0], axis[1], axis[2]))
        face = self.face
        revolved_shape_ = BRepPrimAPI_MakeRevol(face, revolve_axis, np.deg2rad(angle)).Shape()
        return revolved_shape_

    def make_shell(self):
        from OCC.Core.BRepFill import BRepFill_Filling
        from OCC.Core.GeomAbs import GeomAbs_C0

        n_sided = BRepFill_Filling()
        for edg in self.edges:
            n_sided.Add(edg, GeomAbs_C0)
        n_sided.Build()
        face = n_sided.Face()
        return face

    def calc_bbox(self, thick):
        """
        Calculate the Bounding Box of the plate

        :return: Bounding Box of the plate
        :rtype: tuple
        """
        xs = []
        ys = []
        zs = []

        for pt in self.nodes:
            xs.append(pt.x)
            ys.append(pt.y)
            zs.append(pt.z)

        bbox_min = np.array([min(xs), min(ys), min(zs)]).astype(np.float64)
        bbox_max = np.array([max(xs), max(ys), max(zs)]).astype(np.float64)
        n = self.normal.astype(np.float64)

        pv = np.nonzero(n)[0]
        matr = {0: "X", 1: "Y", 2: "Z"}
        orient = matr[pv[0]]
        if orient == "X" or orient == "Y":
            delta_vec = abs(n * thick / 2.0)
            bbox_min -= delta_vec
            bbox_max += delta_vec
        elif orient == "Z":
            delta_vec = abs(n * thick).astype(np.float64)
            bbox_min -= delta_vec

        else:
            raise ValueError(f"Error in {orient}")

        return tuple([(x, y) for x, y in zip(list(bbox_min), list(bbox_max))])

    def scale(self, scale_factor, tol):
        self._origin = np.array([x * scale_factor for x in self.origin])
        self._points2d = [tuple([x * scale_factor for x in p]) for p in self._points2d]
        self._points3d = [tuple([x * scale_factor for x in p]) for p in self._points3d]
        self._local2d_to_polycurve(self.points2d, tol=tol)

    @property
    def origin(self):
        return self._origin

    @origin.setter
    def origin(self, value):
        from ada.core.utils import local_2_global_nodes

        self._origin = value
        points2d_no_r = [n[:2] for n in self.points2d]
        points3d = local_2_global_nodes(points2d_no_r, self._origin, self.xdir, self.normal)
        for i, p in enumerate(self.points2d):
            if len(p) == 3:
                points3d[i] = (points3d[i][0], points3d[i][1], points3d[i][2], p[-1])
            else:
                points3d[i] = tuple(points3d[i].tolist())
        self._points3d = points3d
        self._local2d_to_polycurve(self.points2d, tol=self._tol)

    @property
    def seg_global_points(self):
        return self._seg_global_points

    @property
    def points2d(self):
        return self._points2d

    @property
    def points3d(self):
        return self._points3d

    @property
    def nodes(self):
        return self._nodes

    @property
    def normal(self):
        return self._normal

    @property
    def xdir(self):
        return self._xdir

    @property
    def ydir(self):
        return self._ydir

    @property
    def edges(self):
        # if self._edges is None:
        #     self._edges = self._segments_2_edges(self.seg_list)
        return self._segments_2_edges(self.seg_list)

    @property
    def wire(self):
        from OCC.Extend.ShapeFactory import make_wire

        # if self._wire is None:
        #     self._wire = make_wire(self.edges)
        return make_wire(self.edges)

    @property
    def face(self):
        # if self._face is None:
        #     self._face = self.make_shell()
        return self.make_shell()

    @property
    def seg_index(self):
        return self._seg_index

    @property
    def seg_list(self):
        return self._seg_list

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._to_ifc_elem()
        return self._ifc_elem


class LineSegment:
    def __init__(self, p1, p2):
        self._p1 = p1
        self._p2 = p2

    @property
    def p1(self):
        if type(self._p1) is not np.ndarray:
            self._p1 = np.array(self._p1)
        return self._p1

    @p1.setter
    def p1(self, value):
        self._p1 = value

    @property
    def p2(self):
        if type(self._p2) is not np.ndarray:
            self._p2 = np.array(self._p2)
        return self._p2

    @p2.setter
    def p2(self, value):
        self._p2 = value

    def __repr__(self):
        return f"LineSegment({self.p1}, {self.p2})"


class ArcSegment(LineSegment):
    def __init__(self, p1, p2, midpoint=None, radius=None, center=None, intersection=None):
        super(ArcSegment, self).__init__(p1, p2)
        self._midpoint = midpoint
        self._radius = radius
        self._center = center
        self._intersection = intersection

    @property
    def midpoint(self):
        return self._midpoint

    @midpoint.setter
    def midpoint(self, value):
        self._midpoint = value

    @property
    def radius(self):
        return self._radius

    @radius.setter
    def radius(self, value):
        self._radius = value

    @property
    def center(self):
        return self._center

    @property
    def intersection(self):
        return self._intersection

    def __repr__(self):
        return f"ArcSegment({self.p1}, {self.midpoint}, {self.p2})"
