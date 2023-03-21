from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Any, Callable, Iterable, Union

from ada.base.changes import ChangeAction
from ada.base.ifc_types import SpatialTypes
from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.base.units import Units
from ada.cache.store import CacheStore
from ada.concepts.connections import JointBase
from ada.concepts.containers import (
    Beams,
    Connections,
    Materials,
    Nodes,
    Plates,
    Sections,
)
from ada.concepts.groups import Group
from ada.concepts.piping import Pipe
from ada.concepts.points import Node
from ada.concepts.presentation_layers import PresentationLayers
from ada.concepts.primitives import (
    Penetration,
    PrimBox,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    Shape,
)
from ada.concepts.transforms import Instance, Placement
from ada.concepts.user import User
from ada.config import Settings, get_logger
from ada.fem import (
    Connector,
    Csys,
    FemSet,
    Mass,
    StepEigen,
    StepExplicit,
    StepImplicit,
    StepSteadyState,
)
from ada.fem.concept import FEM

if TYPE_CHECKING:
    import ifcopenshell

    from ada import Beam, Material, Plate, Section, Wall, Weld
    from ada.fem.containers import COG
    from ada.fem.formats.general import FEATypes, FemConverters
    from ada.fem.meshing import GmshOptions
    from ada.fem.results.common import FEAResult
    from ada.ifc.store import IfcStore
    from ada.visualize.concept import VisMesh
    from ada.visualize.config import ExportConfig

_step_types = Union[StepSteadyState, StepEigen, StepImplicit, StepExplicit]

logger = get_logger()


class FormatNotSupportedException(Exception):
    pass


@dataclass
class _ConvertOptions:
    ecc_to_mpc: bool = True
    hinges_to_coupling: bool = True

    # From FEM to concepts
    fem2concepts_include_ecc = False


class Part(BackendGeom):
    """A Part superclass design to host all relevant information for cad and FEM modelling."""

    IFC_CLASSES = SpatialTypes

    def __init__(
        self,
        name,
        colour=None,
        placement=Placement(),
        fem: FEM = None,
        settings: Settings = Settings(),
        metadata=None,
        parent=None,
        units: Units = Units.M,
        guid=None,
        ifc_store: IfcStore = None,
        ifc_class: SpatialTypes = SpatialTypes.IfcBuildingStorey,
    ):
        super().__init__(name, guid=guid, metadata=metadata, units=units, parent=parent, ifc_store=ifc_store)
        self._nodes = Nodes(parent=self)
        self._beams = Beams(parent=self)
        self._plates = Plates(parent=self)
        self._pipes = list()
        self._walls = list()
        self._connections = Connections(parent=self)
        self._materials = Materials(parent=self)
        self._sections = Sections(parent=self)
        self._colour = colour
        self._placement = placement
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

    def add_object(self, obj: Part | Beam | Plate | Wall | Pipe | Shape | Weld):
        from ada import Beam, Part, Pipe, Plate, Shape, Wall, Weld

        if isinstance(obj, Beam):
            self.add_beam(obj)
        elif isinstance(obj, Plate):
            self.add_plate(obj)
        elif isinstance(obj, Pipe):
            self.add_pipe(obj)
        elif issubclass(type(obj), Part):
            self.add_part(obj)
        elif issubclass(type(obj), Shape):
            self.add_shape(obj)
        elif isinstance(obj, Wall):
            self.add_wall(obj)
        elif isinstance(obj, Weld):
            self.add_weld(obj)
        else:
            raise NotImplementedError(f'"{type(obj)}" is not yet supported for smart append')

    def add_penetration(
        self,
        pen: Penetration | PrimExtrude | PrimRevolve | PrimCyl | PrimBox,
        add_pen_to_subparts=True,
        add_to_layer: str = None,
    ) -> Penetration:
        def create_pen(pen_):
            if isinstance(pen_, (PrimExtrude, PrimRevolve, PrimCyl, PrimBox)):
                return Penetration(pen_, parent=self)
            return pen_

        for bm in self.beams:
            bm.add_penetration(create_pen(pen), add_to_layer=add_to_layer)

        for pl in self.plates:
            pl.add_penetration(create_pen(pen), add_to_layer=add_to_layer)

        for shp in self.shapes:
            shp.add_penetration(create_pen(pen), add_to_layer=add_to_layer)

        for pipe in self.pipes:
            for seg in pipe.segments:
                seg.add_penetration(create_pen(pen), add_to_layer=add_to_layer)

        for wall in self.walls:
            wall.add_penetration(create_pen(pen), add_to_layer=add_to_layer)

        if add_pen_to_subparts:
            for p in self.get_all_subparts():
                p.add_penetration(pen, False, add_to_layer=add_to_layer)

        return pen

    def add_instance(self, element, placement: Placement):
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
        from ada import Beam, Pipe, Plate, Shape, Wall

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
        from ada.core.vector_utils import (
            local_2_global_points,
            poly2d_center_of_gravity,
            poly_area_from_list,
        )
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
        from ada.fem.formats.utils import convert_part_objects

        if type(self) is Assembly:
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
            if res.guid != sec.guid:
                refs = [r for r in sec.refs]
                for elem in refs:
                    refs_num += 1
                    sec.refs.pop(sec.refs.index(elem))
                    if elem not in res.refs:
                        res.refs.append(elem)
                    if isinstance(elem, (Beam, FemSection)):
                        if isinstance(elem, Beam) and sec.guid == elem.taper.guid:
                            elem.taper = res
                        elem.section = res
                    else:
                        raise NotImplementedError(f"Not yet support section {type(elem)=}")

        for part in filter(lambda x: len(x.sections) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            part.sections = Sections(parent=part)

        sec_map = {sec.guid: sec for sec in new_sections.sections}

        not_found = []
        for beam in self.get_all_physical_objects(by_type=Beam):
            if beam.taper.guid not in sec_map.keys():
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

        num_elem_changed = 0
        new_materials = Materials(parent=self)
        for mat in self.get_all_materials(include_self=include_self):
            res = new_materials.add(mat)
            if res.guid != mat.guid:
                refs = [r for r in mat.refs]
                for elem in refs:
                    mat.refs.pop(mat.refs.index(elem))
                    if elem not in res.refs:
                        res.refs.append(elem)
                    if isinstance(elem, (Beam, Plate, FemSection, PipeSegStraight, PipeSegElbow, Pipe)):
                        elem.material = res
                        num_elem_changed += 1
                    else:
                        raise NotImplementedError(f"Not yet support section {type(elem)=}")

        for part in filter(lambda x: len(x.materials) > 0, self.get_all_parts_in_assembly(include_self=include_self)):
            part.materials = Materials(parent=part)

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
    ) -> FEM:
        from ada import Beam, Plate, Shape
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

            if experimental_bm_splitting is True and len(list(self.get_all_physical_objects(by_type=Plate))) == 0:
                gs.split_crossing_beams()

            gs.mesh(mesh_size, use_quads=use_quads, use_hex=use_hex)

            if interactive is True:
                gs.open_gui()

            fem = gs.get_fem()

        for mass_shape in masses:
            cog_absolute = mass_shape.placement.absolute_placement() + mass_shape.cog
            n = fem.nodes.add(Node(cog_absolute))
            fem.add_mass(Mass(f"{mass_shape.name}_mass", [n], mass_shape.mass))

        # Move FEM mesh to match part placement origin
        x, y, z = self.placement.origin
        if x != 0.0 or y != 0.0 or z == 0.0:
            fem.nodes.move(self.placement.origin)

        return fem

    def to_vis_mesh(
        self,
        export_config: ExportConfig = None,
        merge_by_color=True,
        opt_func: Callable = None,
        overwrite_cache=False,
        auto_sync_ifc_store=True,
        use_experimental=True,
        cpus: int = None,
    ) -> VisMesh:
        from ada.visualize.interface import part_to_vis_mesh2

        return part_to_vis_mesh2(self, auto_sync_ifc_store, cpus=cpus)

    def to_gltf(
        self,
        gltf_file: str | pathlib.Path,
        auto_sync_ifc_store=True,
        cpus=None,
        limit_to_guids=None,
        embed_meta=False,
        merge_by_color=False,
    ):
        from ada.visualize.interface import part_to_vis_mesh2

        vm = part_to_vis_mesh2(self, auto_sync_ifc_store, cpus=cpus)
        if merge_by_color:
            vm = vm.merge_objects_in_parts_by_color()

        vm.to_gltf(gltf_file, only_these_guids=limit_to_guids, embed_meta=embed_meta)

    def to_stp(
        self,
        destination_file,
        geom_repr: GeomRepr = GeomRepr.SOLID,
        progress_callback: Callable[
            [int, int],
            None,
        ] = None,
    ):
        from ada.occ.store import OCCStore

        step_writer = OCCStore.get_writer()

        num_shapes = len(list(self.get_all_physical_objects()))
        for i, (obj, shape) in enumerate(OCCStore.shape_iterator(self, geom_repr=geom_repr), start=1):
            step_writer.add_shape(shape, obj.name, rgb_color=obj.colour_norm)
            if progress_callback is not None:
                progress_callback(i, num_shapes)

        step_writer.export(destination_file)

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

            for pen in self.penetrations:
                pen.units = value

            for p in self.get_all_subparts():
                p.units = value

            self.sections.units = value
            self.materials.units = value
            self._units = value

            if isinstance(self, Assembly):
                from ada.ifc.utils import assembly_to_ifc_file

                self._ifc_file = assembly_to_ifc_file(self)

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


class Assembly(Part):
    """The Assembly object. A top level container of parts, beams, plates, shapes and FEM."""

    def __init__(
        self,
        name="Ada",
        project="AdaProject",
        user: User = User(),
        schema="IFC4X1",
        settings=Settings(),
        metadata=None,
        units: Units | str = Units.M,
        ifc_settings=None,
        enable_cache: bool = False,
        clear_cache: bool = False,
        ifc_class: SpatialTypes = SpatialTypes.IfcSite,
    ):
        from ada.ifc.store import IfcStore
        from ada.ifc.utils import assembly_to_ifc_file

        metadata = dict() if metadata is None else metadata
        metadata["project"] = project
        metadata["schema"] = schema
        super(Assembly, self).__init__(name=name, settings=settings, metadata=metadata, units=units)
        self.fem.parent = self
        user.parent = self
        self._user = user

        self._ifc_class = ifc_class
        self._ifc_file = assembly_to_ifc_file(self)
        self._convert_options = _ConvertOptions()
        self._ifc_sections = None
        self._ifc_materials = None
        self._source_ifc_files = dict()
        self._ifc_settings = ifc_settings
        self._presentation_layers = PresentationLayers()

        self._ifc_store = IfcStore(assembly=self)
        self._cache_store = None
        if enable_cache:
            self._cache_store = CacheStore(name)
            self.cache_store.sync(self, clear_cache=clear_cache)

    def read_ifc(
        self, ifc_file: str | os.PathLike | ifcopenshell.file, data_only=False, elements2part=None, create_cache=False
    ):
        """Import from IFC file."""

        if self.cache_store is not None and isinstance(ifc_file, ifcopenshell.file) is False:
            if self.cache_store.from_cache(self, ifc_file) is True:
                return None

        self.ifc_store.load_ifc_content_from_file(ifc_file, data_only=data_only, elements2part=elements2part)

        if self.cache_store is not None:
            self.cache_store.to_cache(self, ifc_file, create_cache)

    def read_fem(
        self,
        fem_file: str | os.PathLike,
        fem_format: FEATypes | str = None,
        name: str = None,
        fem_converter: FemConverters | str = "default",
        cache_model_now=False,
    ):
        """Import a Finite Element model. Currently supported FEM formats: Abaqus, Sesam and Calculix"""
        from ada.fem.formats.general import get_fem_converters

        fem_file = pathlib.Path(fem_file)
        if fem_file.exists() is False:
            raise FileNotFoundError(fem_file)

        if self.cache_store is not None:
            if self.cache_store.from_cache(self, fem_file) is True:
                return None

        fem_importer, _ = get_fem_converters(fem_file, fem_format, fem_converter)
        if fem_importer is None:
            suffix = fem_file.suffix
            raise FormatNotSupportedException(f'File "{fem_file.name}" [{suffix}] is not a supported FEM format.')

        temp_assembly: Assembly = fem_importer(fem_file, name)
        self.__add__(temp_assembly)

        if self.cache_store is not None:
            self.cache_store.to_cache(self, fem_file, cache_model_now)

    def to_fem(
        self,
        name: str,
        fem_format: FEATypes | str,
        scratch_dir=None,
        metadata=None,
        execute=False,
        run_ext=False,
        cpus=2,
        gpus=None,
        overwrite=False,
        fem_converter="default",
        exit_on_complete=True,
        run_in_shell=False,
        make_zip_file=False,
        return_fea_results=True,
    ) -> FEAResult | None:
        """
        Create a FEM input file deck for executing fem analysis in a specified FEM format.
        Currently there is limited write support for the following FEM formats:

        Open Source

        * Calculix
        * Code_Aster

        not open source

        * Abaqus
        * Usfos
        * Sesam


        Write support is added on a need-only-basis. Any contributions are welcomed!

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
        :param exit_on_complete:
        :param run_in_shell:
        :param make_zip_file:
        :param return_fea_results: Automatically import the result mesh into

            Note! Meshio implementation currently only supports reading & writing elements and nodes.

        Abaqus Metadata:

            'ecc_to_mpc': Runs the method :func:`~ada.fem.FEM.convert_ecc_to_mpc` . Default is True
            'hinges_to_coupling': Runs the method :func:`~ada.fem.FEM.convert_hinges_2_couplings` . Default is True

            Important Note! The ecc_to_mpc and hinges_to_coupling will make permanent modifications to the model.
            If this proves to create issues regarding performance this should be evaluated further.

        """
        from ada.fem.formats.execute import execute_fem
        from ada.fem.formats.general import FEATypes, write_to_fem
        from ada.fem.formats.postprocess import postprocess
        from ada.fem.formats.utils import default_fem_res_path

        if isinstance(fem_format, str):
            fem_format = FEATypes.from_str(fem_format)

        scratch_dir = Settings.scratch_dir if scratch_dir is None else pathlib.Path(scratch_dir)

        write_to_fem(self, name, fem_format, overwrite, fem_converter, scratch_dir, metadata, make_zip_file)

        if execute:
            execute_fem(
                name, fem_format, scratch_dir, cpus, gpus, run_ext, metadata, execute, exit_on_complete, run_in_shell
            )

        fem_res_files = default_fem_res_path(name, scratch_dir=scratch_dir)
        res_path = fem_res_files.get(fem_format, None)

        if res_path.exists() is False or return_fea_results is False:
            return None

        return postprocess(res_path, fem_format=fem_format)

    def to_ifc(
        self,
        destination=None,
        include_fem=False,
        file_obj_only=False,
        validate=False,
        progress_callback: Callable[[int, int], None] = None,
    ) -> ifcopenshell.file:
        import ifcopenshell.validate

        if destination is None or file_obj_only is True:
            destination = "object"
        else:
            destination = pathlib.Path(destination).resolve().absolute()

        print(f'Beginning writing to IFC file "{destination}" using IfcOpenShell')

        self.ifc_store.sync(include_fem=include_fem, progress_callback=progress_callback)

        if file_obj_only is False:
            os.makedirs(destination.parent, exist_ok=True)
            self.ifc_store.save_to_file(destination)

        if validate:
            ifcopenshell.validate.validate(self.ifc_store.f, logging)

        print("IFC file creation complete")
        return self.ifc_store.f

    def to_genie_xml(self, destination_xml):
        from ada.fem.formats.sesam.xml.write.write_xml import write_xml

        write_xml(self, destination_xml)

    def push(self, comment, bimserver_url, username, password, project, merge=False, sync=False):
        """Push current assembly to BimServer with a comment tag that defines the revision name"""
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.push(project, comment, merge, sync)

    def pull(self, bimserver_url, username, password, project, checkout=False):
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.pull(project, checkout)

    def get_ifc_source_by_name(self, ifc_file):
        from ada.ifc.read.reader_utils import open_ifc

        if ifc_file not in self._source_ifc_files.keys():
            ifc_f = open_ifc(ifc_file)
            self._source_ifc_files[ifc_file] = ifc_f
        else:
            ifc_f = self._source_ifc_files[ifc_file]

        return ifc_f

    @property
    def ifc_store(self) -> IfcStore:
        return self._ifc_store

    @ifc_store.setter
    def ifc_store(self, value):
        self._ifc_store = value

    @property
    def presentation_layers(self) -> PresentationLayers:
        return self._presentation_layers

    @presentation_layers.setter
    def presentation_layers(self, value):
        self._presentation_layers = value

    @property
    def user(self) -> User:
        return self._user

    @property
    def convert_options(self) -> _ConvertOptions:
        return self._convert_options

    @property
    def cache_store(self) -> CacheStore:
        return self._cache_store

    def __add__(self, other: Assembly | Part):
        if other.units != self.units:
            other.units = self.units

        for interface_n in other.fem.interface_nodes:
            n = interface_n.node
            for p in self.get_all_parts_in_assembly(True):
                res = p.fem.nodes.get_by_volume(n.p)
                if res is not None and len(res) > 0:
                    replace_node = res[0]
                    for ref in n.refs:
                        if isinstance(ref, Connector):
                            if n == ref.n1:
                                ref.n1 = replace_node
                            elif n == ref.n2:
                                ref.n2 = replace_node
                            else:
                                logger.warning(f'No matching node found for either n1 or n2 of "{ref}"')
                        elif isinstance(ref, Csys):
                            index = ref.nodes.index(n)
                            ref.nodes.pop(index)
                            ref.nodes.insert(index, replace_node)
                        elif isinstance(ref, FemSet):
                            index = ref.members.index(n)
                            ref.members.pop(index)
                            ref.members.insert(index, replace_node)
                        else:
                            raise NotImplementedError(f'Unsupported type "{type(ref)}"')
                    break

        self.fem += other.fem

        for p in other.parts.values():
            p.parent = self
            self.add_part(p)

        for mat in other.materials:
            if mat not in self.materials:
                self.materials.add(mat)

        self.sections += other.sections
        self.shapes += other.shapes
        self.beams += other.beams
        self.plates += other.plates
        self.pipes += other.pipes
        self.walls += other.walls
        return self

    def __repr__(self):
        nbms = len([bm for p in self.get_all_subparts() for bm in p.beams]) + len(self.beams)
        npls = len([pl for p in self.get_all_subparts() for pl in p.plates]) + len(self.plates)
        nshps = len([shp for p in self.get_all_subparts() for shp in p.shapes]) + len(self.shapes)
        npipes = len(self.pipes) + len([pl for p in self.get_all_subparts() for pl in p.pipes])
        nels = len(self.fem.elements) + len([el for p in self.get_all_subparts() for el in p.fem.elements])
        nns = len(self.fem.nodes) + len([no for p in self.get_all_subparts() for no in p.fem.nodes])
        return (
            f'Assembly("{self.name}": Beams: {nbms}, Plates: {npls}, Pipes: {npipes}, '
            f"Shapes: {nshps}, Elements: {nels}, Nodes: {nns})"
        )
