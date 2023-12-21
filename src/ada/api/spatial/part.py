from __future__ import annotations

import os
import pathlib
from itertools import chain
from typing import TYPE_CHECKING, Any, Callable, Iterable

from ada import Node, Pipe, PrimBox, PrimCyl, PrimExtrude, PrimRevolve, Shape
from ada.api.beams.base_bm import Beam, BeamTapered
from ada.api.connections import JointBase
from ada.api.containers import Beams, Connections, Materials, Nodes, Plates, Sections
from ada.api.groups import Group
from ada.base.changes import ChangeAction
from ada.base.ifc_types import SpatialTypes
from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.base.units import Units
from ada.config import Settings, logger
from ada.visit.gltf.graph import GraphNode, GraphStore

if TYPE_CHECKING:
    from ada import (
        FEM,
        Boolean,
        Instance,
        Material,
        Placement,
        Plate,
        Section,
        Wall,
        Weld,
    )
    from ada.cadit.ifc.store import IfcStore
    from ada.fem.containers import COG
    from ada.fem.meshing import GmshOptions


class Part(BackendGeom):
    """A Part superclass design to host all relevant information for cad and FEM modelling."""

    IFC_CLASSES = SpatialTypes

    def __init__(
        self,
        name,
        color=None,
        placement=None,
        fem: FEM = None,
        settings: Settings = Settings(),
        metadata=None,
        parent=None,
        units: Units = Units.M,
        guid=None,
        ifc_store: IfcStore = None,
        ifc_class: SpatialTypes = SpatialTypes.IfcBuildingStorey,
    ):
        from ada import FEM

        super().__init__(
            name, guid=guid, metadata=metadata, units=units, parent=parent, ifc_store=ifc_store, placement=placement
        )
        self._nodes = Nodes(parent=self)
        self._beams = Beams(parent=self)
        self._plates = Plates(parent=self)
        self._pipes = list()
        self._walls = list()
        self._connections = Connections(parent=self)
        self._materials = Materials(parent=self)
        self._sections = Sections(parent=self)
        self._colour = color

        self._instances: dict[Any, Instance] = dict()
        self._shapes = []
        self._welds = []
        self._parts = dict()
        self._groups: dict[str, Group] = dict()
        self._ifc_class = ifc_class
        self._props = settings
        if fem is not None:
            fem.parent = self

        self.fem = FEM(name + "-1", parent=self) if fem is None else fem

    def add_beam(self, beam: Beam, add_to_layer: str = None) -> Beam:
        if beam.units != self.units:
            beam.units = self.units
        beam.parent = self

        mat = self.add_material(beam.material)
        if mat != beam.material:
            beam.material = mat

        sec = self.add_section(beam.section)
        if sec != beam.section:
            beam.section = sec

        if isinstance(beam, BeamTapered):
            tap = self.add_section(beam.taper)
            if tap != beam.taper:
                beam.taper = tap

        old_node = self.nodes.add(beam.n1)
        if old_node != beam.n1:
            beam.n1 = old_node

        old_node = self.nodes.add(beam.n2)
        if old_node != beam.n2:
            beam.n2 = old_node

        beam.change_type = beam.change_type.ADDED
        self.beams.add(beam)

        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(beam, add_to_layer)

        return beam

    def add_plate(self, plate: Plate, add_to_layer: str = None) -> Plate:
        if plate.units != self.units:
            plate.units = self.units

        plate.parent = self

        mat = self.add_material(plate.material)
        if mat is not None:
            plate.material = mat

        for n in plate.nodes:
            self.nodes.add(n)

        plate.change_type = plate.change_type.ADDED
        self._plates.add(plate)

        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(plate, add_to_layer)

        return plate

    def add_pipe(self, pipe: Pipe, add_to_layer: str = None) -> Pipe:
        if pipe.units != self.units:
            pipe.units = self.units
        pipe.parent = self

        mat = self.add_material(pipe.material)
        if mat != pipe.material:
            pipe.material = mat
            mat.refs.append(pipe)

        sec = self.add_section(pipe.section)
        if sec != pipe.section:
            pipe.section = sec
            sec.refs.append(pipe)

        for seg in pipe.segments:
            mat = self.add_material(seg.material)
            if mat != seg.material:
                seg.material = mat
                mat.refs.append(seg)

            sec = self.add_section(seg.section)
            if sec != seg.section:
                seg.section = sec
                sec.refs.append(seg)

        pipe.change_type = pipe.change_type.ADDED
        self.pipes.append(pipe)

        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(pipe, add_to_layer)

        return pipe

    def add_wall(self, wall: Wall) -> Wall:
        if wall.units != self.units:
            wall.units = self.units
        wall.parent = self
        self._walls.append(wall)
        return wall

    def add_shape(self, shape: Shape, change_type: ChangeAction = ChangeAction.ADDED) -> Shape:
        if shape.units != self.units:
            logger.info(f'shape "{shape}" has different units. changing from "{shape.units}" to "{self.units}"')
            shape.units = self.units
        shape.parent = self

        mat = self.add_material(shape.material)
        if mat != shape.material:
            shape.material = mat

        shape.change_type = change_type
        self._shapes.append(shape)
        return shape

    def add_part(self, part: Part, overwrite: bool = False, add_to_layer: str = None) -> Part:
        if issubclass(type(part), Part) is False:
            raise ValueError("Added Part must be a subclass or instance of Part")

        if part.units != self.units:
            part.units = self.units
        part.parent = self

        if part.name in self._parts.keys() and overwrite is False:
            raise ValueError(f'Part name "{part.name}" already exists. Pass "overwrite=True" to replace existing part.')

        self._parts[part.name] = part
        try:
            part._on_import()
        except NotImplementedError:
            logger.info(f'Part "{part}" has not defined its "on_import()" method')

        part.change_type = part.change_type.ADDED
        if add_to_layer is not None:
            a = self.get_assembly()
            a.presentation_layers.add_object(part, add_to_layer)

        return part

    def add_joint(self, joint: JointBase) -> JointBase:
        """
        This method takes a Joint element containing two intersecting beams. It will check with the existing
        list of joints to see whether or not it is part of a larger more complex joint. It usese primarily
        two criteria.

        Criteria 1: If both elements are in an existing joint already, it will u

        Criteria 2: If the intersecting point coincides within a specified tolerance (currently 10mm)
        with an exisiting joint intersecting point. If so it will add the elements to this joint.
        If not it will create a new joint based on these two members.
        """
        if joint.units != self.units:
            joint.units = self.units
        self._connections.add(joint)
        return joint

    def add_weld(self, weld: Weld) -> Weld:
        weld.parent = self
        self._welds.append(weld)

        return weld

    def add_material(self, material: Material) -> Material:
        if material.units != self.units:
            material.units = self.units
        material.parent = self
        return self._materials.add(material)

    def add_section(self, section: Section) -> Section:
        if section.units != self.units:
            section.units = self.units
        return self._sections.add(section)

    def add_object(self, obj: Part | Beam | Plate | Wall | Pipe | Shape | Weld | Section):
        from ada import Beam, Part, Pipe, Plate, Section, Shape, Wall, Weld

        if isinstance(obj, Beam):
            return self.add_beam(obj)
        elif isinstance(obj, Plate):
            return self.add_plate(obj)
        elif isinstance(obj, Pipe):
            return self.add_pipe(obj)
        elif issubclass(type(obj), Part):
            return self.add_part(obj)
        elif issubclass(type(obj), Shape):
            return self.add_shape(obj)
        elif isinstance(obj, Wall):
            return self.add_wall(obj)
        elif isinstance(obj, Weld):
            return self.add_weld(obj)
        elif isinstance(obj, Section):
            return self.add_section(obj)
        else:
            raise NotImplementedError(f'"{type(obj)}" is not yet supported for smart append')

    def add_boolean(
        self,
        boolean: Boolean | PrimExtrude | PrimRevolve | PrimCyl | PrimBox,
        add_pen_to_subparts=True,
        add_to_layer: str = None,
    ) -> Boolean:
        def create_pen(pen_):
            if isinstance(pen_, (PrimExtrude, PrimRevolve, PrimCyl, PrimBox)):
                return Boolean(pen_, parent=self)
            return pen_

        for bm in self.beams:
            bm.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for pl in self.plates:
            pl.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for shp in self.shapes:
            shp.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for pipe in self.pipes:
            for seg in pipe.segments:
                seg.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        for wall in self.walls:
            wall.add_boolean(create_pen(boolean), add_to_layer=add_to_layer)

        if add_pen_to_subparts:
            for p in self.get_all_subparts():
                p.add_boolean(boolean, False, add_to_layer=add_to_layer)

        return boolean

    def add_instance(self, element, placement: Placement):
        from ada import Instance

        if element not in self._instances.keys():
            self._instances[element] = Instance(element)
        self._instances[element].placements.append(placement)

    def add_set(self, name, set_members: list[Part | Beam | Plate | Wall | Pipe | Shape]) -> Group:
        if name not in self.groups.keys():
            self.groups[name] = Group(name, set_members, parent=self)
        else:
            logger.info(f'Appending set "{name}"')
            for mem in set_members:
                if mem not in self.groups[name].members:
                    self.groups[name].members.append(mem)

        return self.groups[name]

    def add_elements_from_ifc(self, ifc_file_path: os.PathLike | str, data_only=False):
        from ada import Assembly, Beam, Pipe, Plate, Shape, Wall

        a = Assembly("temp")
        a.read_ifc(ifc_file_path, data_only=data_only)
        for sec in a.get_all_sections():
            res = self.add_section(sec)
            if res == sec:
                sec.change_type = ChangeAction.ADDED

        for mat in a.get_all_materials():
            res = self.add_material(mat)
            if res == mat:
                mat.change_type = ChangeAction.ADDED

        for obj in a.get_all_physical_objects():
            if issubclass(type(obj), Shape):
                self.add_shape(obj)
            elif isinstance(obj, Beam):
                self.add_beam(obj)
            elif isinstance(obj, Plate):
                self.add_plate(obj)
            elif isinstance(obj, Wall):
                self.add_wall(obj)
            elif isinstance(obj, Pipe):
                self.add_pipe(obj)
            else:
                raise ValueError(f"Unrecognized {type(obj)=}")

    def read_step_file(
        self,
        step_path,
        name=None,
        scale=None,
        transform=None,
        rotate=None,
        colour=None,
        opacity=1.0,
        source_units=Units.M,
        include_shells=False,
    ):
        """

        :param step_path: Can be path to stp file or path to directory of step files.
        :param name: Desired name of destination Shape object
        :param scale: Scale the step content upon import
        :param transform: Transform the step content upon import
        :param rotate: Rotate step content upon import
        :param colour: Assign a specific colour upon import
        :param opacity: Assign Opacity upon import
        :param source_units: Unit of the imported STEP file. Default is 'm'
        """
        from ada.occ.utils import extract_shapes

        shapes = extract_shapes(step_path, scale, transform, rotate, include_shells=include_shells)

        if len(shapes) > 0:
            ada_name = name if name is not None else "CAD" + str(len(self.shapes) + 1)
            for i, shp in enumerate(shapes):
                ada_shape = Shape(ada_name + "_" + str(i), shp, colour, opacity, units=source_units)
                self.add_shape(ada_shape)

    def calculate_cog(self) -> COG:
        import numpy as np

        from ada import Beam, Plate, Shape
        from ada.core.vector_transforms import local_2_global_points
        from ada.core.vector_utils import poly2d_center_of_gravity, poly_area_from_list
        from ada.fem.containers import COG

        tot_mass = 0
        cogs = []
        for obj in self.get_all_physical_objects():
            if issubclass(type(obj), Shape):  # Assuming Mass & COG is manually assigned to arbitrary shape
                cogs.append(np.array(obj.cog) * obj.mass)
                tot_mass += obj.mass
            elif isinstance(obj, Beam):
                rho = obj.material.model.rho
                area = obj.section.properties.Ax
                length = obj.length
                mass = rho * area * length
                cog = (obj.n1.p + obj.n2.p) / 2
                cogs.append(cog * mass)
                tot_mass += mass
            elif isinstance(obj, Plate):
                rho = obj.material.model.rho
                positions = np.array(obj.poly.points2d)
                place = obj.poly.placement

                area = poly_area_from_list(obj.poly.points2d)
                cog2d = poly2d_center_of_gravity(positions)
                cog = local_2_global_points([cog2d], place.origin, place.xdir, place.zdir)[0]
                mass = rho * obj.t * area
                cogs.append(cog * mass)
                tot_mass += mass

        for mass in self.fem.masses.values():
            cogs.append(np.array(mass.nodes[0].p) * mass.mass)
            tot_mass += mass.mass

        cog = sum(cogs) / tot_mass
        return COG(cog, tot_mass)

    def create_objects_from_fem(self, skip_plates=False, skip_beams=False) -> None:
        """Build Beams and Plates from the contents of the local FEM object"""
        from ada import Assembly
        from ada.fem.formats.utils import convert_part_objects

        if isinstance(self, Assembly):
            for p_ in self.get_all_parts_in_assembly():
                logger.info(f'Beginning conversion from fem to structural objects for "{p_.name}"')
                convert_part_objects(p_, skip_plates, skip_beams)
        else:
            logger.info(f'Beginning conversion from fem to structural objects for "{self.name}"')
            convert_part_objects(self, skip_plates, skip_beams)
        logger.info("Conversion complete")

    def get_part(self, name: str) -> Part:
        key_map = {key.lower(): key for key in self.parts.keys()}
        return self.parts[key_map[name.lower()]]

    def _get_by_prop(self, value: str, prop: str) -> Part | Plate | Beam | Shape | Material | Pipe | None:
        pmap = {getattr(p, prop): p for p in self.get_all_subparts() + [self]}
        result = pmap.get(value)
        if result is not None:
            return result

        for p in self.get_all_subparts() + [self]:
            for stru_cont in [p.beams, p.plates]:
                if prop == "guid":
                    res = stru_cont.from_guid(value)
                else:
                    res = stru_cont.from_name(value)
                if res is not None:
                    return res

            for shp in p.shapes:
                if getattr(shp, prop) == value:
                    return shp

            for pi in p.pipes:
                if getattr(pi, prop) == value:
                    return pi
                for seg in pi.segments:
                    if getattr(seg, prop) == value:
                        return seg

            for mat in p.materials:
                if getattr(mat, prop) == value:
                    return mat

        logger.debug(f'Unable to find"{value}". Check if the element type is evaluated in the algorithm')
        return None

    def get_by_guid(self, guid) -> Part | Plate | Beam | Shape | Material | Pipe | None:
        return self._get_by_prop(guid, "guid")

    def get_by_name(self, name) -> Part | Plate | Beam | Shape | Material | Pipe | None:
        """Get element of any type by its name."""
        return self._get_by_prop(name, "name")

    def get_all_materials(self, include_self=True) -> list[Material]:
        materials = []

        for part in filter(lambda x: len(x.materials) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            materials += part.materials.materials

        return materials

    def get_all_sections(self, include_self=True) -> list[Section]:
        sections = []
        for sec in filter(lambda x: len(x.sections) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            sections += sec.sections.sections
        return sections

    def consolidate_sections(self, include_self=True):
        """Moves all sections from all sub-parts to this part"""
        from ada import Beam
        from ada.fem import FemSection

        new_sections = Sections(parent=self)
        refs_num = 0

        for sec in self.get_all_sections(include_self=include_self):
            res = new_sections.add(sec)
            if res.guid == sec.guid:
                continue
            refs = [r for r in sec.refs]
            for elem in refs:
                refs_num += 1
                sec.refs.pop(sec.refs.index(elem))
                if elem not in res.refs:
                    res.refs.append(elem)
                if isinstance(elem, (Beam, FemSection)):
                    if isinstance(elem, BeamTapered) and sec.guid == elem.taper.guid:
                        elem.taper = res
                    elem.section = res
                else:
                    raise NotImplementedError(f"Not yet support section {type(elem)=}")

        for part in filter(lambda x: len(x.sections) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            part.sections = Sections(parent=part)

        sec_map = {sec.guid: sec for sec in new_sections.sections}

        not_found = []
        for beam in self.get_all_physical_objects(by_type=Beam):
            if isinstance(beam, BeamTapered) and beam.taper.guid not in sec_map.keys():
                not_found.append((beam, "taper", beam.taper))
            if beam.section.guid not in sec_map.keys():
                not_found.append((beam, "section", beam.section))

        if len(not_found) > 0:
            raise ValueError(f"The following beam sections are not consolidated {not_found}")

        self.sections = new_sections

        return self.sections.sections

    def consolidate_materials(self, include_self=True):
        from ada import Beam, Pipe, PipeSegElbow, PipeSegStraight, Plate
        from ada.fem import FemSection

        # Copy all materials assigned to fem sections objects to their parent parts
        for part in filter(lambda x: not x.fem.is_empty(), self.get_all_parts_in_assembly(include_self=include_self)):
            for sec in part.fem.sections:
                ext_mat = part.materials.add(sec.material)
                sec.material = ext_mat

        num_elem_changed = 0
        new_materials = Materials(parent=self)
        for mat in self.get_all_materials(include_self=include_self):
            res = new_materials.add(mat)
            if res.guid == mat.guid:
                continue
            refs = [r for r in mat.refs]
            for elem in refs:
                mat.refs.pop(mat.refs.index(elem))
                if elem not in res.refs:
                    res.refs.append(elem)
                if isinstance(elem, (Beam, Plate, FemSection, PipeSegStraight, PipeSegElbow, Pipe)):
                    elem.material = res
                    num_elem_changed += 1
                elif issubclass(type(elem), Shape):
                    elem.material = res
                    num_elem_changed += 1
                else:
                    raise NotImplementedError(f"Not yet support section {type(elem)=}")

        for part in filter(lambda x: len(x.materials) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            part.materials = Materials(parent=part)

        for i, mat in enumerate(new_materials.materials, start=1):
            mat.id = i

        self.materials = new_materials

        return self.materials.materials

    def get_all_parts_in_assembly(self, include_self=False, by_type=None) -> list[Part]:
        parent = self.get_assembly()
        list_of_ps = []
        self._flatten_list_of_subparts(parent, list_of_ps)
        if include_self:
            list_of_ps += [self]

        if by_type is not None:
            return list(filter(lambda x: issubclass(type(x), by_type), list_of_ps))

        return list_of_ps

    def get_all_subparts(self, include_self=False) -> list[Part]:
        list_of_parts = [] if include_self is False else [self]
        self._flatten_list_of_subparts(self, list_of_parts)
        return list_of_parts

    def get_all_physical_objects(
        self, sub_elements_only=False, by_type=None, filter_by_guids: list[str] = None, pipe_to_segments=False
    ) -> Iterable[Beam | Plate | Wall | Pipe | Shape]:
        physical_objects = []
        if sub_elements_only:
            iter_parts = iter([self])
        else:
            iter_parts = iter(self.get_all_subparts(include_self=True))

        for p in iter_parts:
            if pipe_to_segments:
                segments = chain.from_iterable([pipe.segments for pipe in p.pipes])
                all_as_iterable = chain(p.plates, p.beams, p.shapes, segments, p.walls)
            else:
                all_as_iterable = chain(p.plates, p.beams, p.shapes, p.pipes, p.walls)
            physical_objects.append(all_as_iterable)

        if by_type is not None:
            res = filter(lambda x: type(x) is by_type, chain.from_iterable(physical_objects))
        else:
            res = chain.from_iterable(physical_objects)

        if filter_by_guids is not None:
            res = filter(lambda x: x.guid in filter_by_guids, res)

        return res

    def get_graph_store(self) -> GraphStore:
        root = GraphNode(self.name, self.guid)
        graph: dict[str, GraphNode] = {self.guid: root}
        objects = self.get_all_physical_objects(pipe_to_segments=True)
        containers = self.get_all_parts_in_assembly()
        for p in chain.from_iterable([containers, objects]):
            if p.guid in graph.keys():
                continue
            parent_node = graph.get(p.parent.guid)
            n = GraphNode(p.name, hash=p.guid)
            if parent_node is not None:
                n.parent = parent_node
                parent_node.children.append(n)
            graph[p.guid] = n
        return GraphStore(root, graph)

    def beam_clash_check(self, margins=5e-5):
        """
        For all beams in a Assembly get all beams touching or within the beam. Essentially a clash check is performed
        and it returns a dictionary of all beam ids and the touching beams. A margin to the beam volume can be included.

        :param margins: Add margins to the volume box (equal in all directions). Input is in meters. Can be negative.
        :return: A map generator for the list of beams and resulting intersecting beams
        """
        from ada.core.clash_check import basic_intersect

        all_parts = self.get_all_subparts() + [self]
        all_beams = [bm for p in all_parts for bm in p.beams]

        return filter(None, [basic_intersect(bm, margins, all_parts) for bm in all_beams])

    def move_all_mats_and_sec_here_from_subparts(self):
        for p in self.get_all_subparts():
            self._materials += p.materials
            self._sections += p.sections
            p._materials = Materials(parent=p)
            p._sections = Sections(parent=p)

        self.sections.merge_sections_by_properties()
        self.materials.merge_materials_by_properties()

    def _flatten_list_of_subparts(self, p, list_of_parts=None):
        for value in p.parts.values():
            list_of_parts.append(value)
            self._flatten_list_of_subparts(value, list_of_parts)

    def _on_import(self):
        """A method call that will be triggered when a Part is imported into an existing Assembly/Part"""
        raise NotImplementedError()

    def to_fem_obj(
        self,
        mesh_size: float,
        bm_repr: GeomRepr = GeomRepr.LINE,
        pl_repr: GeomRepr = GeomRepr.SHELL,
        shp_repr: GeomRepr = GeomRepr.SOLID,
        options: GmshOptions = None,
        silent=True,
        interactive=False,
        use_quads=False,
        use_hex=False,
        experimental_bm_splitting=True,
        experimental_pl_splitting=True,
        name=None,
    ) -> FEM:
        from ada import Beam, Plate, Shape
        from ada.fem.elements import Mass
        from ada.fem.meshing import GmshOptions, GmshSession

        if isinstance(bm_repr, str):
            bm_repr = GeomRepr.from_str(bm_repr)
        if isinstance(pl_repr, str):
            pl_repr = GeomRepr.from_str(pl_repr)
        if isinstance(shp_repr, str):
            shp_repr = GeomRepr.from_str(shp_repr)

        options = GmshOptions(Mesh_Algorithm=8) if options is None else options
        masses: list[Shape] = []
        with GmshSession(silent=silent, options=options) as gs:
            for obj in self.get_all_physical_objects(sub_elements_only=False):
                if isinstance(obj, Beam):
                    gs.add_obj(obj, geom_repr=bm_repr, build_native_lines=False)
                elif isinstance(obj, Plate):
                    gs.add_obj(obj, geom_repr=pl_repr)
                elif issubclass(type(obj), Shape) and obj.mass is not None:
                    masses.append(obj)
                elif issubclass(type(obj), Shape):
                    gs.add_obj(obj, geom_repr=shp_repr)
                else:
                    logger.error(f'Unsupported object type "{obj}". Should be either plate or beam objects')

            if interactive is True:
                gs.open_gui()

            gs.split_plates_by_beams()

            num_plates = len(list(self.get_all_physical_objects(by_type=Plate)))
            if experimental_pl_splitting is True and num_plates > 1:
                gs.split_plates_by_plates()

            if experimental_bm_splitting is True and num_plates == 0:
                gs.split_crossing_beams()

            if interactive is True:
                gs.open_gui()

            gs.mesh(mesh_size, use_quads=use_quads, use_hex=use_hex)

            if interactive is True:
                gs.open_gui()

            fem = gs.get_fem(name=name if name is not None else f"{self.name}-FEM")

        for mass_shape in masses:
            cog_absolute = mass_shape.placement.get_absolute_placement().origin + mass_shape.cog
            n = fem.nodes.add(Node(cog_absolute))
            fem.add_mass(Mass(f"{mass_shape.name}_mass", [n], mass_shape.mass))

        return fem

    def to_gltf(self, gltf_file: str | pathlib.Path, **kwargs):
        if isinstance(gltf_file, str):
            gltf_file = pathlib.Path(gltf_file)
        gltf_file.parent.mkdir(parents=True, exist_ok=True)

        def post_pro(buffer_items, tree):
            pass

        self.to_trimesh_scene(**kwargs).export(gltf_file, buffer_postprocessor=post_pro)

    def to_trimesh_scene(
        self, render_override: dict[str, GeomRepr | str] = None, filter_by_guids=None, merge_meshes=True
    ):
        from ada.occ.tessellating import BatchTessellator

        bt = BatchTessellator()
        return bt.tessellate_part(
            self, merge_meshes=merge_meshes, render_override=render_override, filter_by_guids=filter_by_guids
        )

    def to_stp(
        self,
        destination_file,
        geom_repr: GeomRepr = GeomRepr.SOLID,
        progress_callback: Callable[
            [int, int],
            None,
        ] = None,
        geom_repr_override: dict[str, GeomRepr] = None,
    ):
        from ada.occ.store import OCCStore

        step_writer = OCCStore.get_step_writer()

        num_shapes = len(list(self.get_all_physical_objects()))
        shape_iter = OCCStore.shape_iterator(self, geom_repr=geom_repr, render_override=geom_repr_override)
        for i, (obj, shape) in enumerate(shape_iter, start=1):
            step_writer.add_shape(shape, obj.name, rgb_color=obj.color.rgb)
            if progress_callback is not None:
                progress_callback(i, num_shapes)

        step_writer.export(destination_file)

    def to_viewer(self, **kwargs):
        from ada.visit.comms import send_to_viewer

        send_to_viewer(self, **kwargs)

    def show(self):
        from ada.occ.tessellating import BatchTessellator

        bt = BatchTessellator()
        scene = bt.tessellate_part(self)

        return scene.show("notebook")

    @property
    def parts(self) -> dict[str, Part]:
        return self._parts

    @property
    def shapes(self) -> list[Shape]:
        return self._shapes

    @shapes.setter
    def shapes(self, value: list[Shape]):
        self._shapes = value

    @property
    def beams(self) -> Beams:
        return self._beams

    @beams.setter
    def beams(self, value: Beams):
        self._beams = value

    @property
    def plates(self) -> Plates:
        return self._plates

    @plates.setter
    def plates(self, value: Plates):
        self._plates = value

    @property
    def pipes(self) -> list[Pipe]:
        return self._pipes

    @pipes.setter
    def pipes(self, value: list[Pipe]):
        self._pipes = value

    @property
    def welds(self) -> list[Weld]:
        return self._welds

    @property
    def walls(self) -> list[Wall]:
        return self._walls

    @walls.setter
    def walls(self, value: list[Wall]):
        self._walls = value

    @property
    def nodes(self) -> Nodes:
        return self._nodes

    @property
    def fem(self) -> FEM:
        return self._fem

    @fem.setter
    def fem(self, value: FEM):
        value.parent = self
        self._fem = value

    @property
    def connections(self) -> Connections:
        return self._connections

    @property
    def sections(self) -> Sections:
        return self._sections

    @sections.setter
    def sections(self, value: Sections):
        self._sections = value

    @property
    def materials(self) -> Materials:
        return self._materials

    @materials.setter
    def materials(self, value: Materials):
        self._materials = value

    @property
    def colour(self):
        if self._colour is None:
            from random import randint

            self._colour = randint(0, 255) / 255, randint(0, 255) / 255, randint(0, 255) / 255

        return self._colour

    @colour.setter
    def colour(self, value):
        self._colour = value

    @property
    def properties(self):
        return self._props

    @property
    def placement(self) -> Placement:
        return self._placement

    @placement.setter
    def placement(self, value: Placement):
        self._placement = value

    @property
    def instances(self) -> dict[Any, Instance]:
        return self._instances

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        from ada import Assembly

        if isinstance(value, str):
            value = Units.from_str(value)

        if value != self._units:
            for bm in self.beams:
                bm.units = value

            for pl in self.plates:
                pl.units = value

            for pipe in self._pipes:
                pipe.units = value

            for shp in self._shapes:
                shp.units = value

            for wall in self.walls:
                wall.units = value

            for pen in self.booleans:
                pen.units = value

            for p in self.get_all_subparts():
                p.units = value

            self.sections.units = value
            self.materials.units = value
            self._units = value
            if isinstance(self, Assembly):
                f = self.ifc_store.f

                res = {x.UnitType.upper(): x for x in f.by_type("IFCSIUNIT")}
                length_unit = res.get("LENGTHUNIT", None)
                area_unit = res.get("AREAUNIT", None)
                volume_unit = res.get("VOLUMEUNIT", None)
                prefix = None if value == Units.M else "MILLI"

                if length_unit:
                    length_unit.Prefix = prefix
                if area_unit:
                    area_unit.Prefix = prefix
                if volume_unit:
                    volume_unit.Prefix = prefix

    @property
    def groups(self) -> dict[str, Group]:
        return self._groups

    @property
    def ifc_class(self) -> SpatialTypes:
        return self._ifc_class

    def __truediv__(self, other_object):
        if type(other_object) in [list, tuple]:
            for obj in other_object:
                self.add_object(obj)
        else:
            self.add_object(other_object)

        return self

    def __repr__(self):
        nbms = len(self.beams) + len([bm for p in self.get_all_subparts() for bm in p.beams])
        npls = len(self.plates) + len([pl for p in self.get_all_subparts() for pl in p.plates])
        npipes = len(self.pipes) + len([pl for p in self.get_all_subparts() for pl in p.pipes])
        nshps = len(self.shapes) + len([shp for p in self.get_all_subparts() for shp in p.shapes])
        nels = len(self.fem.elements) + len([el for p in self.get_all_subparts() for el in p.fem.elements])
        nnodes = len(self.fem.nodes) + len([no for p in self.get_all_subparts() for no in p.fem.nodes])
        return (
            f'Part("{self.name}": Beams: {nbms}, Plates: {npls}, '
            f"Pipes: {npipes}, Shapes: {nshps}, Elements: {nels}, Nodes: {nnodes})"
        )
