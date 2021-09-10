from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass, field
from itertools import chain
from typing import List, Union

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
from ada.concepts.structural import Beam, Material, Plate, Section, Wall
from ada.concepts.transforms import Transform
from ada.config import Settings, User
from ada.fem import (
    Amplitude,
    Bc,
    Connector,
    ConnectorSection,
    Constraint,
    Csys,
    Elem,
    FemSection,
    FemSet,
    Interaction,
    InteractionProperty,
    Mass,
    PredefinedField,
    Spring,
    Step,
    Surface,
)
from ada.fem.containers import FemElements, FemSections, FemSets
from ada.ifc.utils import create_guid


class Part(BackendGeom):
    """A Part superclass design to host all relevant information for cad and FEM modelling."""

    def __init__(
        self,
        name,
        colour=None,
        origin=(0, 0, 0),
        lx=(1, 0, 0),
        ly=(0, 1, 0),
        lz=(0, 0, 1),
        fem: FEM = None,
        settings: Settings = Settings(),
        metadata=None,
        parent=None,
        units="m",
        ifc_elem=None,
        guid=None,
    ):
        super().__init__(name, guid=guid, metadata=metadata, units=units, parent=parent, ifc_elem=ifc_elem)
        from ada.fem.meshing import GMesh

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
        self._instances = []
        self._lx = lx
        self._ly = ly
        self._lz = lz
        self._shapes = []
        self._parts = dict()

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
            logging.info(f'shape "{shape}" has different units. changing from "{shape.units}" to "{self.units}"')
            shape.units = self.units
        shape.parent = self
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
            pipe.add_penetration(pen)

        for wall in self.walls:
            wall.add_penetration(pen)

        if add_pen_to_subparts:
            for p in self.get_all_subparts():
                p.add_penetration(pen, False)
        return pen

    def add_instance(self, element, transform: Transform):
        self._instances[element] = transform

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
        self, step_path, name=None, scale=None, transform=None, rotate=None, colour=None, opacity=1.0, source_units="m"
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

        shapes = extract_shapes(step_path, scale, transform, rotate)
        if len(shapes) > 0:
            ada_name = name if name is not None else "CAD" + str(len(self.shapes) + 1)
            for i, shp in enumerate(shapes):
                ada_shape = Shape(ada_name + "_" + str(i), shp, colour, opacity, units=source_units)
                self.add_shape(ada_shape)

    def create_objects_from_fem(self, skip_plates=False, skip_beams=False) -> None:
        """Build Beams and Plates from the contents of the local FEM object"""
        from ada.fem.io.utils import convert_part_objects

        if type(self) is Assembly:
            for p_ in self.get_all_parts_in_assembly():
                logging.info(f'Beginning conversion from fem to structural objects for "{p_.name}"')
                convert_part_objects(p_, skip_plates, skip_beams)
        else:
            logging.info(f'Beginning conversion from fem to structural objects for "{self.name}"')
            convert_part_objects(self, skip_plates, skip_beams)
        logging.info("Conversion complete")

    def get_part(self, name) -> Part:
        return self.parts[name]

    def get_by_name(self, name) -> Union[Part, Plate, Beam, Shape, Material, None]:
        """Get element of any type by its name."""
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

            for mat in p.materials:
                if mat.name == name:
                    return mat

        logging.debug(f'Unable to find"{name}". Check if the element type is evaluated in the algorithm')
        return None

    def get_all_parts_in_assembly(self, include_self=False) -> List[Part]:
        parent = self.get_assembly()
        list_of_ps = []
        self._flatten_list_of_subparts(parent, list_of_ps)
        if include_self:
            list_of_ps += [self]
        return list_of_ps

    def get_all_subparts(self) -> List[Part]:
        list_of_parts = []
        self._flatten_list_of_subparts(self, list_of_parts)
        return list_of_parts

    def get_all_physical_objects(self, sub_elements_only=True) -> List[Union[Beam, Plate, Wall, Pipe, Shape]]:
        physical_objects = []
        if sub_elements_only:
            iter_parts = iter(self.get_all_subparts() + [self])
        else:
            iter_parts = iter(self.get_all_parts_in_assembly(True))

        for p in iter_parts:
            physical_objects += list(p.plates) + list(p.beams) + list(p.shapes) + list(p.pipes) + list(p.walls)
        return physical_objects

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

    def _flatten_list_of_subparts(self, p, list_of_parts=None):
        for value in p.parts.values():
            list_of_parts.append(value)
            self._flatten_list_of_subparts(value, list_of_parts)

    def _generate_ifc_elem(self):
        from ada.ifc.utils import add_multiple_props_to_elem, create_local_placement

        if self.parent is None:
            raise ValueError("Cannot build ifc element without parent")

        a = self.get_assembly()
        f = a.ifc_file

        owner_history = a.user.to_ifc()

        itype = self.metadata["ifctype"]
        parent = self.parent.ifc_elem
        placement = create_local_placement(
            f,
            origin=self.origin,
            loc_x=self._lx,
            loc_z=self._lz,
            relative_to=parent.ObjectPlacement,
        )
        type_map = dict(building="IfcBuilding", space="IfcSpace", spatial="IfcSpatialZone", storey="IfcBuildingStorey")

        if itype not in type_map.keys() and itype not in type_map.values():
            raise ValueError(f'Currently not supported "{itype}"')

        ifc_type = type_map[itype] if itype not in type_map.values() else itype

        props = dict(
            GlobalId=self.guid,
            OwnerHistory=owner_history,
            Name=self.name,
            Description=self.metadata.get("Description", None),
            ObjectType=None,
            ObjectPlacement=placement,
            Representation=None,
            LongName=self.metadata.get("LongName", None),
        )

        if ifc_type not in ["IfcSpatialZone"]:
            props["CompositionType"] = self.metadata.get("CompositionType", "ELEMENT")

        if ifc_type == "IfcBuildingStorey":
            props["Elevation"] = self.origin[2]

        ifc_elem = f.create_entity(ifc_type, **props)

        f.createIfcRelAggregates(
            create_guid(),
            owner_history,
            "Site Container",
            None,
            parent,
            [ifc_elem],
        )

        add_multiple_props_to_elem(self.metadata.get("props", dict()), ifc_elem, f)

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
    def parts(self) -> dict[str, Part]:
        return self._parts

    @property
    def shapes(self) -> List[Shape]:
        return self._shapes

    @property
    def beams(self) -> Beams:
        return self._beams

    @property
    def plates(self) -> Plates:
        return self._plates

    @property
    def pipes(self) -> List[Pipe]:
        return self._pipes

    @property
    def walls(self) -> List[Wall]:
        return self._walls

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
    def gmsh(self):
        """:rtype: ada.fem.meshing.GMesh"""
        return self._gmsh

    @property
    def connections(self) -> Connections:
        return self._connections

    @property
    def sections(self) -> Sections:
        return self._sections

    @property
    def materials(self) -> Materials:
        return self._materials

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
                assert isinstance(self, Assembly)
                from ada.ifc.utils import assembly_to_ifc_file

                self._ifc_file = assembly_to_ifc_file(self)

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
        schema="IFC4",
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

        Part.__init__(self, name=name, settings=settings, metadata=metadata, units=units)

        user.parent = self
        self._user = user

        self._ifc_file = assembly_to_ifc_file(self)

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
            logging.debug("Cache file not found")
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

    def read_ifc(self, ifc_file: Union[str, os.PathLike], data_only=False, elements2part=None, cache_model_now=False):
        """
        Import from IFC file.


        Note! Currently only geometry is imported into individual shapes.

        :param ifc_file:
        :param data_only: Set True if data is relevant, not geometry
        :param elements2part: Grab all physical elements from ifc and import it to the parsed in Part object.
        :param cache_model_now:
        """
        from ada.ifc.utils import (
            add_to_assembly,
            get_parent,
            import_ifc_hierarchy,
            import_physical_ifc_elem,
            open_ifc,
            scale_ifc_file,
        )

        if self._enable_experimental_cache is True:
            if self._from_cache(ifc_file) is True:
                return None

        f = open_ifc(ifc_file)

        scaled_ifc = scale_ifc_file(self.ifc_file, f)
        if scaled_ifc is not None:
            f = scaled_ifc

        # Get hierarchy
        if elements2part is None:
            for product in f.by_type("IfcProduct"):
                res, new_part = import_ifc_hierarchy(self, product)
                if new_part is None:
                    continue
                if res is None:
                    self.add_part(new_part)
                elif type(res) is not Part:
                    raise NotImplementedError()
                else:
                    res.add_part(new_part)

        # Get physical elements
        for product in f.by_type("IfcProduct"):
            if product.Representation is not None and data_only is False:
                parent = get_parent(product)
                obj = import_physical_ifc_elem(product)
                obj.metadata["ifc_file"] = ifc_file
                if obj is not None:
                    add_to_assembly(self, obj, parent, elements2part)

        print(f'Import of IFC file "{ifc_file}" is complete')

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
        from ada.fem.io import get_fem_converters

        fem_file = pathlib.Path(fem_file)
        if fem_file.exists() is False:
            raise FileNotFoundError(fem_file)

        if self._enable_experimental_cache is True:
            if self._from_cache(fem_file) is True:
                return None

        fem_importer, _ = get_fem_converters(fem_file, fem_format, fem_converter)
        fem_importer(self, fem_file, name)

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
        :param exit_on_complete:
        :rtype: ada.fem.results.Results

            Note! Meshio implementation currently only supports reading & writing elements and nodes.

        Abaqus Metadata:

            'ecc_to_mpc': Runs the method :func:`~ada.fem.FEM.convert_ecc_to_mpc` . Default is True
            'hinges_to_coupling': Runs the method :func:`~ada.fem.FEM.convert_hinges_2_couplings` . Default is True

            Important Note! The the ecc_to_mpc and hinges_to_coupling will make permanent modifications to the model.
            If this proves to create issues regarding performance this should be evaluated further.

        """
        from ada.fem.io import fem_executables, get_fem_converters
        from ada.fem.io.utils import default_fem_res_path, folder_prep, should_convert
        from ada.fem.results import Results

        fem_res_files = default_fem_res_path(name, Settings.scratch_dir)

        res_path = fem_res_files.get(fem_format, None)
        metadata = dict() if metadata is None else metadata
        metadata["fem_format"] = fem_format

        out = None
        if should_convert(res_path, overwrite):
            analysis_dir = folder_prep(scratch_dir, name, overwrite)
            _, fem_exporter = get_fem_converters("", fem_format, fem_converter)

            if fem_exporter is None:
                raise ValueError(f'FEM export for "{fem_format}" using "{fem_converter}" is currently not supported')
            fem_inp_files = dict(
                code_aster=(analysis_dir / name).with_suffix(".export"),
                abaqus=(analysis_dir / name).with_suffix(".inp"),
                calculix=(analysis_dir / name).with_suffix(".inp"),
                sesam=(analysis_dir / name).with_suffix(".FEM"),
            )

            fem_exporter(self, name, analysis_dir, metadata)

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
            return None
        return Results(name, res_path, fem_format=fem_format, assembly=self, output=out, overwrite=overwrite)

    def to_ifc(self, destination_file, include_fem=False) -> None:
        from ada.ifc.export import add_part_objects_to_ifc

        f = self.ifc_file

        dest = pathlib.Path(destination_file).with_suffix(".ifc")

        for s in self.sections:
            f.add(s.ifc_profile)
            f.add(s.ifc_beam_type)

        for p in self.get_all_parts_in_assembly(include_self=True):
            add_part_objects_to_ifc(p, f, self, include_fem)

        if len(self.presentation_layers) > 0:
            presentation_style = f.createIfcPresentationStyle("HiddenLayers")
            f.createIfcPresentationLayerWithStyle(
                "HiddenLayers",
                "Hidden Layers (ADA)",
                self.presentation_layers,
                "10",
                False,
                False,
                False,
                [presentation_style],
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
        """Push current assembly to BimServer with a comment tag that defines the revision name"""
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.push(project, comment, merge, sync)

    def pull(self, bimserver_url, username, password, project, checkout=False):
        from ada.core.bimserver import BimServerConnect

        bimcon = BimServerConnect(bimserver_url, username, password, self)
        bimcon.pull(project, checkout)

    def _generate_ifc_elem(self):
        from ada.ifc.utils import create_local_placement, create_property_set

        f = self.ifc_file
        owner_history = self.user.to_ifc()
        site_placement = create_local_placement(f)
        site = f.create_entity(
            "IfcSite",
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
        from ada.ifc.utils import open_ifc

        if ifc_file not in self._source_ifc_files.keys():

            ifc_f = open_ifc(ifc_file)
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
            for sec in self.sections:
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

    @property
    def user(self) -> User:
        return self._user

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
class FEM:
    name: str
    metadata: dict = field(default_factory=dict)
    parent: Part = field(init=True, default=None)

    masses: dict[str, Mass] = field(init=False, default_factory=dict)
    surfaces: dict[str, Surface] = field(init=False, default_factory=dict)
    amplitudes: dict[str, Amplitude] = field(init=False, default_factory=dict)
    connectors: dict[str, Connector] = field(init=False, default_factory=dict)
    connector_sections: dict[str, ConnectorSection] = field(init=False, default_factory=dict)
    springs: dict[str, Spring] = field(init=False, default_factory=dict)
    intprops: dict[str, InteractionProperty] = field(init=False, default_factory=dict)
    interactions: dict[str, Interaction] = field(init=False, default_factory=dict)
    predefined_fields: dict[str, PredefinedField] = field(init=False, default_factory=dict)
    lcsys: dict[str, Csys] = field(init=False, default_factory=dict)

    bcs: List[Bc] = field(init=False, default_factory=list)
    constraints: List[Constraint] = field(init=False, default_factory=list)
    steps: List[Step] = field(init=False, default_factory=list)

    nodes: Nodes = field(default_factory=Nodes, init=True)
    elements: FemElements = field(default_factory=FemElements, init=True)
    sets: FemSets = field(default_factory=FemSets, init=True)
    sections: FemSections = field(default_factory=FemSections, init=True)
    initial_state: PredefinedField = field(default=None, init=True)
    subroutine: str = field(default=None, init=True)

    def __post_init__(self):
        self.nodes.parent = self
        self.elements.parent = self
        self.sets.parent = self
        self.sections.parent = self

    def add_elem(self, elem: Elem):
        elem.parent = self
        self.elements.add(elem)

    def add_section(self, section: FemSection):
        section.parent = self
        self.sections.add(section)

    def add_bc(self, bc: Bc):
        if bc.name in [b.name for b in self.bcs]:
            raise ValueError(f'BC with name "{bc.name}" already exists')

        bc.parent = self
        if bc.fem_set.parent is None:
            logging.debug("Bc FemSet has no parent. Adding to self")
            self.sets.add(bc.fem_set)

        self.bcs.append(bc)

    def add_mass(self, mass: Mass):
        mass.parent = self
        self.masses[mass.name] = mass

    def add_set(
        self,
        fem_set: FemSet,
        ids=None,
        p=None,
        vol_box=None,
        vol_cyl=None,
        single_member=False,
        tol=1e-4,
    ) -> FemSet:
        """
        Simple method that creates a set string based on a set name, node or element ids and adds it to the assembly str

        :param fem_set: A fem set object
        :param ids: List of integers
        :param p: Single point (x,y,z)
        :param vol_box: Search by quadratic volume. Where p is (xmin, ymin, zmin) and vol_box is (xmax, ymax, zmax)
        :param vol_cyl: Search by cylindrical volume. Used together with p to find
                        nodes within cylinder inputted by [radius, height, thickness]
        :param single_member: Set True if you wish to keep only a single member
        :param tol: Point Tolerances. Default is 1e-4
        """
        if ids is not None:
            fem_set.add_members(ids)

        fem_set.parent = self

        def append_members(nodelist):
            if single_member is True:
                fem_set.add_members([nodelist[0]])
            else:
                fem_set.add_members(nodelist)

        if fem_set.type == "nset":
            if p is not None or vol_box is not None or vol_cyl is not None:
                nodes = self.nodes.get_by_volume(p, vol_box, vol_cyl, tol)
                if len(nodes) == 0 and self.parent is not None:
                    assembly = self.parent.get_assembly()
                    list_of_ps = assembly.get_all_subparts() + [assembly]
                    for part in list_of_ps:
                        nodes = part.fem.nodes.get_by_volume(p, vol_box, vol_cyl, tol)
                        if len(nodes) > 0:
                            fem_set.parent = part.fem
                            append_members(nodes)
                            part.fem.add_set(fem_set)
                            return fem_set

                    raise Exception(f'No nodes found for fem set "{fem_set.name}"')
                elif nodes is not None and len(nodes) > 0:
                    append_members(nodes)
                else:
                    raise Exception(f'No nodes found for femset "{fem_set.name}"')

        self.sets.add(fem_set)
        return fem_set

    def add_step(self, step: Step) -> Step:
        """Add an analysis step to the assembly"""
        if len(self.steps) > 0:
            if self.steps[-1].type != "eigenfrequency" and step.type == "complex_eig":
                raise Exception(
                    "complex eigenfrequency analysis step needs to follow eigenfrequency step. Check your input"
                )
        step.parent = self
        self.steps.append(step)

        return step

    def add_interaction_property(self, int_prop: InteractionProperty) -> InteractionProperty:
        int_prop.parent = self
        self.intprops[int_prop.name] = int_prop
        return int_prop

    def add_interaction(self, interaction: Interaction) -> Interaction:
        interaction.parent = self
        self.interactions[interaction.name] = interaction
        return interaction

    def add_constraint(self, constraint: Constraint) -> Constraint:
        constraint.parent = self
        self.constraints.append(constraint)
        return constraint

    def add_lcsys(self, lcsys: Csys) -> Csys:
        if lcsys.name in self.lcsys.keys():
            raise ValueError("Local Coordinate system cannot have duplicate name")
        lcsys.parent = self
        self.lcsys[lcsys.name] = lcsys
        return lcsys

    def add_connector_section(self, connector_section: ConnectorSection):
        connector_section.parent = self
        self.connector_sections[connector_section.name] = connector_section

    def add_connector(self, connector: Connector):
        connector.parent = self
        self.connectors[connector.name] = connector
        connector.csys.parent = self
        self.elements.add(connector)
        self.add_set(FemSet(name=connector.name, members=[connector.id], set_type="elset"))

    def add_rp(self, name, node: Node):
        """Adds a reference point in assembly with a specific name"""
        node.parent = self
        self.nodes.add(node)
        fem_set = self.add_set(FemSet(name, [node], "nset"))
        return node, fem_set

    def add_surface(self, surface: Surface):
        surface.parent = self
        self.surfaces[surface.name] = surface

    def add_amplitude(self, amplitude: Amplitude):
        amplitude.parent = self
        self.amplitudes[amplitude.name] = amplitude

    def add_predefined_field(self, pre_field: PredefinedField):
        pre_field.parent = self

        self.predefined_fields[pre_field.name] = pre_field

    def add_spring(self, spring: Spring):
        # self.elements.add(spring)

        if spring.fem_set.parent is None:
            self.sets.add(spring.fem_set)
        self.springs[spring.name] = spring

    def create_fem_elem_from_obj(self, obj, el_type=None) -> Elem:
        """Converts structural object to FEM elements. Currently only BEAM is supported"""
        from ada.fem.shapes import ElemType

        if type(obj) is not Beam:
            raise NotImplementedError(f'Object type "{type(obj)}" is not yet supported')

        el_type = "B31" if el_type is None else el_type

        res = self.nodes.add(obj.n1)
        if res is not None:
            obj.n1 = res
        res = self.nodes.add(obj.n2)
        if res is not None:
            obj.n2 = res

        elem = Elem(None, [obj.n1, obj.n2], el_type)
        self.add_elem(elem)
        femset = FemSet(f"{obj.name}_set", [elem.id], "elset")
        self.add_set(femset)
        self.add_section(
            FemSection(
                f"d{obj.name}_sec",
                ElemType.LINE,
                femset,
                obj.material,
                obj.section,
                obj.ori[1],
            )
        )
        return elem

    @property
    def instance_name(self):
        return self.name if self.name is not None else f"{self.parent.name}-1"

    @property
    def nsets(self):
        return self.sets.nodes

    @property
    def elsets(self):
        return self.sets.elements

    def __add__(self, other: FEM):
        # Nodes
        nodid_max = self.nodes.max_nid if len(self.nodes) > 0 else 0
        if nodid_max > other.nodes.min_nid:
            other.nodes.renumber(int(nodid_max + 10))

        self.nodes += other.nodes

        # Elements
        elid_max = self.elements.max_el_id if len(self.elements) > 0 else 0

        if elid_max > other.elements.min_el_id:
            other.elements.renumber(int(elid_max + 10))

        logging.info("FEM operand type += is still ")

        self.elements += other.elements
        self.sections += other.sections
        self.sets += other.sets
        self.lcsys.update(other.lcsys)

        return self

    def __repr__(self):
        return f"FEM({self.name}, Elements: {len(self.elements)}, Nodes: {len(self.nodes)})"
