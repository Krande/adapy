from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass
from io import StringIO
from itertools import chain
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Union

from ada.base.physical_objects import BackendGeom
from ada.concepts.connections import JointBase
from ada.concepts.containers import (
    Beams,
    Connections,
    Materials,
    Nodes,
    Plates,
    Sections,
)
from ada.concepts.piping import Pipe
from ada.concepts.points import Node
from ada.concepts.primitives import (
    Penetration,
    PrimBox,
    PrimCyl,
    PrimExtrude,
    PrimRevolve,
    Shape,
)
from ada.concepts.transforms import Instance, Placement
from ada.config import Settings, User
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
from ada.fem.elements import ElemType
from ada.ifc.utils import create_guid

if TYPE_CHECKING:
    from ada import Beam, Material, Plate, Section, Wall
    from ada.fem.meshing import GmshOptions
    from ada.fem.results import Results
    from ada.ifc.concepts import IfcRef
    from ada.visualize.concept import VisMesh
    from ada.visualize.config import ExportConfig

_step_types = Union[StepSteadyState, StepEigen, StepImplicit, StepExplicit]

logger = logging.getLogger(__name__)


@dataclass
class _ConvertOptions:
    ecc_to_mpc: bool = True
    hinges_to_coupling: bool = True

    # From FEM to concepts
    fem2concepts_include_ecc = False


class Part(BackendGeom):
    """A Part superclass design to host all relevant information for cad and FEM modelling."""

    def __init__(
        self,
        name,
        colour=None,
        placement=Placement(),
        fem: FEM = None,
        settings: Settings = Settings(),
        metadata=None,
        parent=None,
        units="m",
        ifc_elem=None,
        guid=None,
        ifc_ref: IfcRef = None,
    ):
        super().__init__(
            name, guid=guid, metadata=metadata, units=units, parent=parent, ifc_elem=ifc_elem, ifc_ref=ifc_ref
        )
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
        self._instances: Dict[Any, Instance] = dict()
        self._shapes = []
        self._parts = dict()
        self._groups: Dict[str, Group] = dict()

        if ifc_elem is not None:
            self.metadata["ifctype"] = self._import_part_from_ifc(ifc_elem)
        else:
            if self.metadata.get("ifctype") is None:
                self.metadata["ifctype"] = "site" if type(self) is Assembly else "storey"

        self._props = settings
        if fem is not None:
            fem.parent = self

        self.fem = FEM(name + "-1", parent=self) if fem is None else fem

    def add_beam(self, beam: Beam) -> Beam:
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

        self.beams.add(beam)
        return beam

    def add_plate(self, plate: Plate) -> Plate:
        if plate.units != self.units:
            plate.units = self.units

        plate.parent = self

        mat = self.add_material(plate.material)
        if mat is not None:
            plate.material = mat

        for n in plate.nodes:
            self.nodes.add(n)

        self._plates.add(plate)
        return plate

    def add_pipe(self, pipe: Pipe) -> Pipe:
        if pipe.units != self.units:
            pipe.units = self.units
        pipe.parent = self

        mat = self.add_material(pipe.material)
        if mat is not None:
            pipe.material = mat

        self._pipes.append(pipe)
        return pipe

    def add_wall(self, wall: Wall) -> Wall:
        if wall.units != self.units:
            wall.units = self.units
        wall.parent = self
        self._walls.append(wall)
        return wall

    def add_shape(self, shape: Shape) -> Shape:
        if shape.units != self.units:
            logger.info(f'shape "{shape}" has different units. changing from "{shape.units}" to "{self.units}"')
            shape.units = self.units
        shape.parent = self

        mat = self.add_material(shape.material)
        if mat != shape.material:
            shape.material = mat

        self._shapes.append(shape)
        return shape

    def add_part(self, part: Part) -> Part:
        if issubclass(type(part), Part) is False:
            raise ValueError("Added Part must be a subclass or instance of Part")
        if part.units != self.units:
            part.units = self.units
        part.parent = self
        if part.name in self._parts.keys():
            raise ValueError(f'Part name "{part.name}" already exists and cannot be overwritten')
        self._parts[part.name] = part
        try:
            part._on_import()
        except NotImplementedError:
            logger.info(f'Part "{part}" has not defined its "on_import()" method')
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

    def add_material(self, material: Material) -> Material:
        if material.units != self.units:
            material.units = self.units
        material.parent = self
        return self._materials.add(material)

    def add_section(self, section: Section) -> Section:
        if section.units != self.units:
            section.units = self.units
        return self._sections.add(section)

    def add_object(self, obj: Union[Part, Beam, Plate, Wall, Pipe, Shape]):
        from ada import Beam

        if isinstance(obj, Part):
            self.add_part(obj)
        elif isinstance(obj, Beam):
            self.add_beam(obj)
        else:
            raise NotImplementedError()

    def add_penetration(
        self, pen: Union[Penetration, PrimExtrude, PrimRevolve, PrimCyl, PrimBox], add_pen_to_subparts=True
    ) -> Penetration:
        if type(pen) in (PrimExtrude, PrimRevolve, PrimCyl, PrimBox):
            pen = Penetration(pen, parent=self)

        for bm in self.beams:
            bm.add_penetration(pen)

        for pl in self.plates:
            pl.add_penetration(pen)

        for shp in self.shapes:
            shp.add_penetration(pen)

        for pipe in self.pipes:
            for seg in pipe.segments:
                seg.add_penetration(pen)

        for wall in self.walls:
            wall.add_penetration(pen)

        if add_pen_to_subparts:
            for p in self.get_all_subparts():
                p.add_penetration(pen, False)
        return pen

    def add_instance(self, element, placement: Placement):
        if element not in self._instances.keys():
            self._instances[element] = Instance(element)
        self._instances[element].placements.append(placement)

    def add_set(self, name, set_members: List[Union[Part, Beam, Plate, Wall, Pipe, Shape]]) -> Group:
        if name not in self.groups.keys():
            self.groups[name] = Group(name, set_members, parent=self)
        else:
            logger.info(f'Appending set "{name}"')
            for mem in set_members:
                if mem not in self.groups[name].members:
                    self.groups[name].members.append(mem)

        return self.groups[name]

    def add_elements_from_ifc(self, ifc_file_path: os.PathLike, data_only=False):
        a = Assembly("temp")
        a.read_ifc(ifc_file_path, data_only=data_only)
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
        self,
        step_path,
        name=None,
        scale=None,
        transform=None,
        rotate=None,
        colour=None,
        opacity=1.0,
        source_units="m",
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

    def get_by_name(self, name) -> Union[Part, Plate, Beam, Shape, Material, Pipe, None]:
        """Get element of any type by its name."""
        pmap = {p.name: p for p in self.get_all_subparts() + [self]}
        result = pmap.get(name)
        if result is not None:
            return result

        for p in self.get_all_subparts() + [self]:
            for stru_cont in [p.beams, p.plates]:
                res = stru_cont.from_name(name)
                if res is not None:
                    return res

            for shp in p.shapes:
                if shp.name == name:
                    return shp

            for pi in p.pipes:
                if pi.name == name:
                    return pi

            for mat in p.materials:
                if mat.name == name:
                    return mat

        logger.debug(f'Unable to find"{name}". Check if the element type is evaluated in the algorithm')
        return None

    def get_all_parts_in_assembly(self, include_self=False) -> List[Part]:
        parent = self.get_assembly()
        list_of_ps = []
        self._flatten_list_of_subparts(parent, list_of_ps)
        if include_self:
            list_of_ps += [self]
        return list_of_ps

    def get_all_subparts(self, include_self=False) -> List[Part]:
        list_of_parts = [] if include_self is False else [self]
        self._flatten_list_of_subparts(self, list_of_parts)
        return list_of_parts

    def get_all_physical_objects(
        self, sub_elements_only=False, by_type=None, filter_by_guids: Union[List[str]] = None
    ) -> Iterable[Union[Beam, Plate, Wall, Pipe, Shape]]:
        physical_objects = []
        if sub_elements_only:
            iter_parts = iter([self])
        else:
            iter_parts = iter(self.get_all_subparts(include_self=True))

        for p in iter_parts:
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

    def get_ifc_elem(self):
        if self._ifc_elem is None:
            self._ifc_elem = self._generate_ifc_elem()
        return self._ifc_elem

    def _generate_ifc_elem(self):
        from ada.ifc.write.write_levels import write_ifc_part

        return write_ifc_part(self)

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

    def _on_import(self):
        """A method call that will be triggered when a Part is imported into an existing Assembly/Part"""
        raise NotImplementedError()

    def to_fem_obj(
        self,
        mesh_size: float,
        bm_repr=ElemType.LINE,
        pl_repr=ElemType.SHELL,
        shp_repr=ElemType.SOLID,
        options: GmshOptions = None,
        silent=True,
        interactive=False,
        use_quads=False,
        use_hex=False,
    ) -> FEM:
        from ada import Beam, Plate, Shape
        from ada.fem.meshing import GmshOptions, GmshSession

        options = GmshOptions(Mesh_Algorithm=8) if options is None else options
        masses: List[Shape] = []
        with GmshSession(silent=silent, options=options) as gs:
            for obj in self.get_all_physical_objects(sub_elements_only=False):
                if type(obj) is Beam:
                    gs.add_obj(obj, geom_repr=bm_repr.upper(), build_native_lines=False)
                elif type(obj) is Plate:
                    gs.add_obj(obj, geom_repr=pl_repr.upper())
                elif issubclass(type(obj), Shape) and obj.mass is not None:
                    masses.append(obj)
                elif issubclass(type(obj), Shape):
                    gs.add_obj(obj, geom_repr=shp_repr.upper())
                else:
                    logger.error(f'Unsupported object type "{obj}". Should be either plate or beam objects')

            # if interactive is True:
            #     gs.open_gui()

            gs.split_plates_by_beams()
            gs.mesh(mesh_size, use_quads=use_quads, use_hex=use_hex)

            if interactive is True:
                gs.open_gui()

            fem = gs.get_fem()

        for mass_shape in masses:
            cog_absolute = mass_shape.placement.absolute_placement() + mass_shape.cog
            n = fem.nodes.add(Node(cog_absolute))
            fem.add_mass(Mass(f"{mass_shape.name}_mass", [n], mass_shape.mass))

        return fem

    def to_vis_mesh(
        self,
        export_config: ExportConfig = None,
        auto_merge_by_color=True,
        opt_func: Callable = None,
        overwrite_cache=False,
        use_disk_io=False,
    ) -> VisMesh:
        from ada.visualize.concept import PartMesh, VisMesh
        from ada.visualize.config import ExportConfig
        from ada.visualize.formats.assembly_mesh.write_objects_to_mesh import (
            filter_mesh_objects,
            obj_to_mesh,
        )
        from ada.visualize.formats.assembly_mesh.write_part_to_mesh import generate_meta
        from ada.visualize.utils import from_cache

        if export_config is None:
            export_config = ExportConfig()

        all_obj_num = len(list(self.get_all_physical_objects(sub_elements_only=False)))
        print(f"Exporting {all_obj_num} physical objects to custom json format.")

        obj_num = 1
        subgeometries = dict()
        part_array = []
        for p in self.get_all_subparts(include_self=True):
            if export_config.max_convert_objects is not None and obj_num > export_config.max_convert_objects:
                break
            obj_list = filter_mesh_objects(p.get_all_physical_objects(sub_elements_only=True), export_config)
            if obj_list is None:
                continue
            id_map = dict()
            for obj in obj_list:
                print(f'Exporting "{obj.name}" [{obj.get_assembly().name}] ({obj_num} of {all_obj_num})')
                cache_file = pathlib.Path(f".cache/{self.name}.h5")
                if export_config.use_cache is True and cache_file.exists():
                    res = from_cache(cache_file, obj.guid)
                    if res is None:
                        res = obj_to_mesh(obj, export_config, opt_func=opt_func)
                else:
                    res = obj_to_mesh(obj, export_config, opt_func=opt_func)
                if res is None:
                    continue
                if type(res) is list:
                    for i, obj_mesh in enumerate(res):
                        if i > 0:
                            name = f"{obj.name}_{i}"
                            guid = create_guid(export_config.name_prefix + name)
                            obj_mesh.guid = guid
                            subgeometries[obj_mesh.guid] = (name, obj.parent.guid)
                        id_map[obj_mesh.guid] = obj_mesh
                else:
                    id_map[obj.guid] = res
                obj_num += 1
                if export_config.max_convert_objects is not None and obj_num >= export_config.max_convert_objects:
                    print(f'Maximum number of converted objects of "{export_config.max_convert_objects}" reached')
                    break

            if id_map is None:
                print(f'Part "{p.name}" has no physical members. Skipping.')
                continue

            for inst in p.instances.values():
                id_map[inst.instance_ref.guid].instances = inst.to_list_of_custom_json_matrices()

            part_array.append(PartMesh(name=p.name, id_map=id_map))

        amesh = VisMesh(
            name=self.name,
            project=self.metadata.get("project", "DummyProject"),
            world=part_array,
            meta=generate_meta(self, export_config, sub_geometries=subgeometries),
        )
        if export_config.use_cache:
            amesh.to_cache(overwrite_cache)

        if auto_merge_by_color:
            return amesh.merge_objects_in_parts_by_color()

        return amesh

    @property
    def parts(self) -> dict[str, Part]:
        return self._parts

    @property
    def shapes(self) -> List[Shape]:
        return self._shapes

    @shapes.setter
    def shapes(self, value: List[Shape]):
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
    def pipes(self) -> List[Pipe]:
        return self._pipes

    @pipes.setter
    def pipes(self, value: List[Pipe]):
        self._pipes = value

    @property
    def walls(self) -> List[Wall]:
        return self._walls

    @walls.setter
    def walls(self, value: List[Wall]):
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
    def instances(self) -> Dict[Any, Instance]:
        return self._instances

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
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

            if type(self) is Assembly:
                assert isinstance(self, Assembly)
                from ada.ifc.utils import assembly_to_ifc_file

                self._ifc_file = assembly_to_ifc_file(self)

    @property
    def groups(self) -> Dict[str, Group]:
        return self._groups

    def __truediv__(self, other_object):
        from ada import Beam, Part, Pipe, Plate, Shape, Wall

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
                elif type(obj) is Wall:
                    self.add_wall(obj)
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
        units="m",
        ifc_settings=None,
        clear_cache=False,
        enable_experimental_cache=None,
    ):
        from ada.ifc.utils import assembly_to_ifc_file

        metadata = dict() if metadata is None else metadata
        metadata["project"] = project
        metadata["schema"] = schema
        super(Assembly, self).__init__(name=name, settings=settings, metadata=metadata, units=units)
        self.fem.parent = self
        user.parent = self
        self._user = user

        self._ifc_file = assembly_to_ifc_file(self)
        self._convert_options = _ConvertOptions()
        self._ifc_sections = None
        self._ifc_materials = None
        self._source_ifc_files = dict()
        self._ifc_settings = ifc_settings
        self._presentation_layers = []

        # Model Cache
        if enable_experimental_cache is None:
            enable_experimental_cache = Settings.use_experimental_cache
        self._enable_experimental_cache = enable_experimental_cache

        state_path = pathlib.Path("").parent.resolve().absolute() / ".state" / self.name
        self._state_file = state_path.with_suffix(".json")
        self._cache_file = state_path.with_suffix(".h5")

        if self._enable_experimental_cache is True:
            if self._cache_file.exists() and clear_cache:
                os.remove(self._cache_file)
            if self._state_file.exists() and clear_cache:
                os.remove(self._state_file)

            self._cache_loaded = False
            self._from_cache()

    def is_cache_outdated(self, input_file=None):
        is_cache_outdated = False
        state = self._get_file_state()

        for name, props in state.items():
            in_file = pathlib.Path(props.get("fp"))
            last_modified_state = props.get("lm")
            if in_file.exists() is False:
                is_cache_outdated = True
                break

            last_modified = os.path.getmtime(in_file)
            if last_modified != last_modified_state:
                is_cache_outdated = True
                break

        if self._cache_file.exists() is False:
            logger.debug("Cache file not found")
            is_cache_outdated = True

        if input_file is not None:
            curr_in_file = pathlib.Path(input_file)
            if curr_in_file.name not in state.keys():
                is_cache_outdated = True

        return is_cache_outdated

    def reset_ifc_file(self):
        from ada.ifc.utils import assembly_to_ifc_file

        self._ifc_file = assembly_to_ifc_file(self)

        for p in self.get_all_parts_in_assembly(True):
            p._ifc_elem = None
            for bm in p.beams:
                bm._ifc_elem = None

    def _from_cache(self, input_file=None):
        is_cache_outdated = self.is_cache_outdated(input_file)
        if input_file is None and is_cache_outdated is False:
            self._read_cache()
            return True

        if is_cache_outdated is False and self._cache_loaded is False:
            self._read_cache()
            return True
        elif is_cache_outdated is False and self._cache_loaded is True:
            return True
        else:
            return False

    def _get_file_state(self):
        state_file = self._state_file
        if state_file.exists() is True:
            with open(state_file, "r") as f:
                state = json.load(f)
                return state
        return dict()

    def _update_file_state(self, input_file=None):
        in_file = pathlib.Path(input_file)
        fna = in_file.name
        last_modified = os.path.getmtime(in_file)
        state_file = self._state_file
        state = self._get_file_state()

        state.get(fna, dict())
        state[fna] = dict(lm=last_modified, fp=str(in_file))

        os.makedirs(state_file.parent, exist_ok=True)

        with open(state_file, "w") as f:
            json.dump(state, f, indent=4)

    def _to_cache(self, input_file, write_to_cache: bool):
        self._update_file_state(input_file)
        if write_to_cache:
            self.update_cache()

    def _read_cache(self):
        from ada.cache.reader import read_assembly_from_cache

        read_assembly_from_cache(self._cache_file, self)
        self._cache_loaded = True
        print(f"Finished Loading model from cache {self._cache_file}")

    def update_cache(self):
        from ada.cache.writer import write_assembly_to_cache

        write_assembly_to_cache(self, self._cache_file)

    def read_ifc(
        self, ifc_file: Union[str, os.PathLike, StringIO], data_only=False, elements2part=None, cache_model_now=False
    ):
        """
        Import from IFC file.


        Note! Currently only geometry is imported into individual shapes.

        :param ifc_file:
        :param data_only: Set True if data is relevant, not geometry
        :param elements2part: Grab all physical elements from ifc and import it to the parsed in Part object.
        :param cache_model_now:
        """
        from ada.ifc.read.read_ifc import read_ifc_file

        if self._enable_experimental_cache is True and type(ifc_file) is not StringIO:
            if self._from_cache(ifc_file) is True:
                return None

        settings = self.ifc_settings
        a = read_ifc_file(ifc_file, settings, elements2part, data_only)

        self.__add__(a)

        if self._enable_experimental_cache is True:
            self._to_cache(ifc_file, cache_model_now)

    def read_fem(
        self,
        fem_file: Union[str, os.PathLike],
        fem_format: str = None,
        name: str = None,
        fem_converter="default",
        cache_model_now=False,
    ):
        """
        Import a Finite Element model.

        Currently supported FEM formats: Abaqus, Sesam and Calculix

        :param fem_file: Path to fem file
        :param fem_format: Fem Format
        :param name:
        :param fem_converter: Set desired fem converter. Use either 'default' or 'meshio'.
        :param cache_model_now:

        Note! The meshio fem converter implementation currently only supports reading elements and nodes.
        """
        from ada.fem.formats.general import get_fem_converters

        fem_file = pathlib.Path(fem_file)
        if fem_file.exists() is False:
            raise FileNotFoundError(fem_file)

        if self._enable_experimental_cache is True:
            if self._from_cache(fem_file) is True:
                return None

        fem_importer, _ = get_fem_converters(fem_file, fem_format, fem_converter)

        temp_assembly: Assembly = fem_importer(fem_file, name)
        self.__add__(temp_assembly)

        if self._enable_experimental_cache is True:
            self._to_cache(fem_file, cache_model_now)

    def to_fem(
        self,
        name: str,
        fem_format: str,
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
        import_result_mesh=False,
        writable_obj: StringIO = None,
    ) -> Results:
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
        :param import_result_mesh: Automatically import the result mesh into

            Note! Meshio implementation currently only supports reading & writing elements and nodes.

        Abaqus Metadata:

            'ecc_to_mpc': Runs the method :func:`~ada.fem.FEM.convert_ecc_to_mpc` . Default is True
            'hinges_to_coupling': Runs the method :func:`~ada.fem.FEM.convert_hinges_2_couplings` . Default is True

            Important Note! The the ecc_to_mpc and hinges_to_coupling will make permanent modifications to the model.
            If this proves to create issues regarding performance this should be evaluated further.

        """
        from ada.fem.formats.general import fem_executables, get_fem_converters
        from ada.fem.formats.utils import (
            default_fem_inp_path,
            default_fem_res_path,
            folder_prep,
            should_convert,
        )
        from ada.fem.results import Results

        scratch_dir = Settings.scratch_dir if scratch_dir is None else pathlib.Path(scratch_dir)
        fem_res_files = default_fem_res_path(name, scratch_dir=scratch_dir)

        res_path = fem_res_files.get(fem_format, None)
        metadata = dict() if metadata is None else metadata
        metadata["fem_format"] = fem_format

        out = None
        if should_convert(res_path, overwrite):
            analysis_dir = folder_prep(scratch_dir, name, overwrite)
            _, fem_exporter = get_fem_converters("", fem_format, fem_converter)

            if fem_exporter is None:
                raise ValueError(f'FEM export for "{fem_format}" using "{fem_converter}" is currently not supported')

            fem_inp_files = default_fem_inp_path(name, scratch_dir)
            fem_exporter(self, name, analysis_dir, metadata)

            if make_zip_file is True:
                import shutil

                shutil.make_archive(name, "zip", str(analysis_dir))

            if execute is True:
                exe_func = fem_executables.get(fem_format, None)
                inp_path = fem_inp_files.get(fem_format, None)
                if exe_func is None:
                    raise NotImplementedError(f'The FEM format "{fem_format}" has no execute function')
                if inp_path is None:
                    raise ValueError("")
                out = exe_func(
                    inp_path=inp_path,
                    cpus=cpus,
                    gpus=gpus,
                    run_ext=run_ext,
                    metadata=metadata,
                    execute=execute,
                    exit_on_complete=exit_on_complete,
                    run_in_shell=run_in_shell,
                )
        else:
            print(f'Result file "{res_path}" already exists.\nUse "overwrite=True" if you wish to overwrite')

        if out is None and res_path is None:
            logger.info("No Result file is created")
            return None

        return Results(
            res_path,
            name,
            fem_format=fem_format,
            assembly=self,
            output=out,
            overwrite=overwrite,
            import_mesh=import_result_mesh,
        )

    def to_ifc(
        self,
        destination_file=None,
        include_fem=False,
        override_skip_props=False,
        return_file_obj=False,
        create_new_ifc_file=False,
    ) -> Union[None, StringIO]:
        from ada.ifc.write.write_ifc import write_to_ifc

        if override_skip_props is True:
            for p in self.get_all_subparts():
                for obj in p.get_all_physical_objects(True):
                    obj.ifc_options.export_props = override_skip_props
        if destination_file is None or return_file_obj is True:
            destination_file = "object"
        else:
            destination_file = pathlib.Path(destination_file).resolve().absolute()

        print(f'Beginning writing to IFC file "{destination_file}" using IfcOpenShell')
        file_obj = write_to_ifc(
            destination_file,
            self,
            include_fem,
            return_file_obj=return_file_obj,
            create_new_ifc_file=create_new_ifc_file,
        )
        print("IFC file creation complete")
        return file_obj

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
        """Push current assembly to BimServer with a comment tag that defines the revision name"""
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.push(project, comment, merge, sync)

    def pull(self, bimserver_url, username, password, project, checkout=False):
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.pull(project, checkout)

    def _generate_ifc_elem(self):
        from ada.ifc.write.write_levels import write_ifc_assembly

        return write_ifc_assembly(self)

    def get_ifc_source_by_name(self, ifc_file):
        from ada.ifc.read.reader_utils import open_ifc

        if ifc_file not in self._source_ifc_files.keys():

            ifc_f = open_ifc(ifc_file)
            self._source_ifc_files[ifc_file] = ifc_f
        else:
            ifc_f = self._source_ifc_files[ifc_file]

        return ifc_f

    @property
    def ifc_sections(self):
        if self._ifc_sections is None:
            secrel = dict()
            for sec in self.sections:
                secrel[sec.name] = sec.ifc_profile, sec.ifc_beam_type
            self._ifc_sections = secrel
        return self._ifc_sections

    @property
    def ifc_materials(self):
        if self._ifc_materials is None:
            matrel = dict()
            for mat in self.materials.name_map.values():
                matrel[mat.name] = mat.ifc_mat
            self._ifc_materials = matrel
        return self._ifc_materials

    @property
    def ifc_file(self):
        return self._ifc_file

    @property
    def presentation_layers(self):
        return self._presentation_layers

    @property
    def user(self) -> User:
        return self._user

    @property
    def convert_options(self) -> _ConvertOptions:
        return self._convert_options

    def __add__(self, other: Union[Assembly, Part]):
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


@dataclass
class Group:
    name: str
    members: List[Union[Part, Beam, Plate, Wall, Pipe, Shape]]
    parent: Union[Part, Assembly]
    description: str = ""
    ifc_elem = None

    def _generate_ifc_elem(self, f):
        a = self.parent.get_assembly()
        owner_history = a.user.to_ifc()
        return f.create_entity("IfcGroup", create_guid(), owner_history, self.name, self.description)

    def to_ifc(self, f):
        a = self.parent.get_assembly()
        owner_history = a.user.to_ifc()
        if self.ifc_elem is None:
            self.ifc_elem = self._generate_ifc_elem(f)

        relating_objects = []
        for m in self.members:
            relating_objects.append(m.get_ifc_elem())

        f.create_entity(
            "IfcRelAssignsToGroup",
            create_guid(),
            owner_history,
            self.name,
            self.description,
            RelatedObjects=relating_objects,
            RelatingGroup=self.ifc_elem,
        )

    def to_part(self, name: str):
        p = Part(name)
        for mem in self.members:
            p.add_object(mem)
        return p
