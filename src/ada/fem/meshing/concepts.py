from __future__ import annotations

import pathlib
import time
from dataclasses import dataclass, field
from typing import List, Union

import gmsh
import numpy as np

import ada
from ada import FEM, Beam, Pipe, Plate, Shape
from ada.api.containers import Nodes
from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.config import Config, logger
from ada.core.guid import create_guid
from ada.fem.containers import FemElements
from ada.fem.meshing.exceptions import BadJacobians
from ada.fem.shapes import ElemType


@dataclass
class GmshOptions:
    # https://gmsh.info/doc/texinfo/gmsh.html#Gmsh-options
    # search for Mesh.Algorithm3D
    Mesh_Algorithm: int = 6  #
    Mesh_ElementOrder: int = 1
    Mesh_Algorithm3D: int = 1
    Mesh_MeshSizeMin: float = None
    Mesh_MeshSizeMax: float = 0.1
    Mesh_SecondOrderIncomplete: int = 1
    Mesh_Smoothing: int = 3
    Mesh_RecombinationAlgorithm: int = None
    Mesh_SubdivisionAlgorithm: int = None
    # Curvature Options
    Mesh_MeshSizeFromCurvature: bool = False
    Mesh_MinimumElementsPerTwoPi: int = 12
    # Geometry
    Geometry_Tolerance: float = 1e-5
    Geometry_OCCImportLabels: int = 1
    Geometry_OCCMakeSolids: int = None
    # General (UI)
    General_ColorScheme: int = 3
    General_Orthographic: int = 0  # 0 Means perspective projection
    General_Terminal: int = 1

    def get_as_dict(self):
        return {key.replace("_", "."): value for key, value in vars(self).items() if value is not None}


@dataclass
class GmshTask:
    ada_obj: List[Union[Shape, Beam, Plate]]
    geom_repr: str
    mesh_size: float
    options: GmshOptions = field(default_factory=GmshOptions)


@dataclass
class CutPlane:
    origin: tuple
    dx: float = 10
    dy: float = 10
    plane: str = "XY"
    normal: tuple = None
    cut_objects: List[GmshData] = None
    gmsh_id: int = None


@dataclass
class GmshData:
    entities: list[tuple]
    geom_repr: str | GeomRepr
    order: int
    obj: Shape | Beam | Plate | Pipe
    mesh_size: float = None


class GmshSession:
    def __init__(self, silent=False, persist=True, options: GmshOptions = GmshOptions(), debug_mode=False):
        logger.debug("init method called")
        self._gmsh = None
        self.options = options
        self.cutting_planes: List[CutPlane] = []
        self.silent = silent
        self.debug_mode = debug_mode
        if silent is True:
            self.options.General_Terminal = 0
        self.persist = persist
        self.model_map: dict[Union[Shape, Beam, Plate, Pipe], GmshData] = dict()

    def add_obj(
        self,
        obj: BackendGeom | Shape | Beam | Plate | Pipe,
        geom_repr: GeomRepr | str = ElemType.SOLID,
        el_order=1,
        silent=True,
        mesh_size=None,
        build_native_lines=False,
        point_tol=Config().general_point_tol,
        use_native_pointer=True,
    ):
        from ada.core.utils import Counter

        from .utils import build_bm_lines

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        self.apply_settings()
        temp_dir = Config().general_temp_dir

        if build_native_lines is True and geom_repr == ElemType.LINE and type(obj) is Beam:
            entities = build_bm_lines(self.model, obj, point_tol)
        else:
            if use_native_pointer and hasattr(self.model.occ, "importShapesNativePointer"):
                # Use hasattr to ensure that it works for gmsh < 4.9.*
                if isinstance(obj, Pipe):
                    entities = []
                    for seg in obj.segments:
                        entities += import_into_gmsh_use_nativepointer(seg, geom_repr, self.model)
                else:
                    entities = import_into_gmsh_use_nativepointer(obj, geom_repr, self.model)
            else:
                entities = import_into_gmsh_using_step(obj, geom_repr, self.model, temp_dir, silent)

        obj_name = Counter(1, f"{obj.name}_")
        for dim, ent in entities:
            ent_name = next(obj_name)
            self.model.set_physical_name(dim, ent, ent_name)
            self.model.set_entity_name(dim, ent, ent_name)

        self.model.occ.synchronize()
        self.model.geo.synchronize()

        gmsh_data = GmshData(entities, geom_repr, el_order, obj, mesh_size=mesh_size)
        self.model_map[obj] = gmsh_data
        return gmsh_data

    def add_cutting_plane(self, cut_plane: CutPlane, cut_objects: List[GmshData] = None):
        if cut_objects is not None:
            if cut_plane.cut_objects is None:
                cut_plane.cut_objects = []
            cut_plane.cut_objects += cut_objects
        self.cutting_planes.append(cut_plane)

    def make_cuts(self, keep_cutting_planes=True):
        remove_tool = not keep_cutting_planes

        geom_repr_map = {ElemType.SOLID: 3, ElemType.SHELL: 2, ElemType.LINE: 1}
        for i, cut in enumerate(self.cutting_planes):
            x, y, z = cut.origin
            rect = self.model.occ.addRectangle(x, y, z, cut.dx, cut.dy)
            self.model.set_physical_name(2, rect, f"CuttingPlane{i}")

            logger.info(f"Cutting plane {cut.plane} at {cut.origin}")
            if cut.plane == "XZ":
                self.model.occ.synchronize()
                self.model.geo.synchronize()
                self.model.occ.rotate([(2, rect)], x, y, z, 1, 0, 0, np.deg2rad(90))
                self.model.occ.synchronize()
                self.model.geo.synchronize()

            cut.gmsh_id = rect
            for obj in cut.cut_objects:
                res, _ = self.model.occ.fragment(obj.entities, [(2, rect)], removeTool=remove_tool)
                cut_geom_dim = geom_repr_map[obj.geom_repr]
                replaced_entities = [(dim, r) for dim, r in res if r != rect and dim == cut_geom_dim]
                obj.entities = replaced_entities

            self.model.occ.remove([(2, rect)], True)

        if self.debug_mode:
            self.open_gui()
        self.model.occ.synchronize()
        self.model.geo.synchronize()

    def partition_beams(self):
        from ada.fem.meshing.partitioning.partition_beams import (
            split_crossing_beams,
            split_intersecting_beams,
        )

        plates = [obj for obj in self.model_map.keys() if type(obj) is Plate]

        if len(plates) == 0:  # For some reason this breaks the model whenever plates are in the model
            split_crossing_beams(self)
        else:
            split_intersecting_beams(self)

    def partition_plates(self):
        """Split plates that are intersecting each other"""
        from ada.core.clash_check import find_edge_connected_perpendicular_plates
        from ada.fem.meshing.partitioning.partition_plates import (
            fragment_plates,
            partition_intersected_plates,
            split_plates_by_beams,
        )

        split_plates_by_beams(self)

        self.check_model_entities()

        plates = [obj for obj in self.model_map.keys() if type(obj) is Plate]

        if len(plates) < 2:
            return

        plate_con = find_edge_connected_perpendicular_plates(plates)

        if len(plate_con.edge_connected) > 0:
            fragment_plates(plate_con, self)
            self.check_model_entities()

        partition_intersected_plates(plate_con, self)

        self.check_model_entities()

    def mesh(self, size: float = None, use_quads=False, use_hex=False, perform_quality_check=False):
        if self.silent is True:
            self.options.General_Terminal = 0

        if use_quads:
            self.make_quads()

        if use_hex:
            self.make_hex()

        if size is not None:
            self.options.Mesh_MeshSizeMax = size
            if self.options.Mesh_MeshSizeFromCurvature is False:
                self.options.Mesh_MeshSizeMin = size

        self.apply_settings()
        self.model.geo.synchronize()
        self.model.mesh.setRecombine(3, -1)

        br_names = Config().meshing_open_viewer_breakpoint_names
        if br_names is not None and "before_meshing" in br_names:
            self.open_gui()

        self.model.mesh.generate(3)

        self.model.mesh.removeDuplicateNodes()
        self.model.mesh.remove_duplicate_elements()

        if perform_quality_check:
            quality_check_mesh(self.model)

        if use_quads is True or use_hex is True:
            self.model.mesh.recombine()
            self.model.mesh.removeDuplicateNodes()
            self.model.mesh.remove_duplicate_elements()

    def make_quads(self):
        from ada.fem.meshing.partitioning.strategies import partition_objects_with_holes

        br_names = Config().meshing_open_viewer_breakpoint_names
        if br_names is not None and "pre_make_quads" in br_names:
            self.open_gui()

        ents = []
        for obj, model in self.model_map.items():
            if model.geom_repr != ElemType.SHELL:
                continue
            if len(obj.booleans) > 0:
                partition_objects_with_holes(model, self)
            else:
                for dim, tag in model.entities:
                    try:
                        self.model.mesh.set_transfinite_surface(tag)
                    except Exception as e:
                        logger.error(f"Error while setting transfinite surface: {e}")
                        continue
                    ents.append(tag)
                    self.model.mesh.setRecombine(dim, tag)

        if br_names is not None and "post_make_quads" in br_names:
            self.open_gui()

    def make_hex(self):
        from ada.fem.meshing.partitioning.strategies import partition_solid_beams

        for dim, tag in self.model.get_entities():
            if dim == 2:
                self.model.mesh.set_transfinite_surface(tag)
                self.model.mesh.setRecombine(dim, tag)

        for obj, model in self.model_map.items():
            if model.geom_repr != GeomRepr.SOLID:
                continue
            if isinstance(obj, Beam):
                partition_solid_beams(model, self)
            else:
                for dim, tag in model.entities:
                    self.model.mesh.set_transfinite_volume(tag)
                    self.model.mesh.setRecombine(dim, tag)

        self.model.mesh.recombine()

    def get_fem(self, name="AdaFEM") -> FEM:
        from ada.fem import Elem

        from .utils import (
            add_fem_sections,
            get_elements_from_entities,
            get_nodes_from_gmsh,
        )

        start = time.time()

        fem = FEM(name)

        gmsh_nodes = get_nodes_from_gmsh(self.model, fem)
        fem.nodes = Nodes(gmsh_nodes, parent=fem)
        end = time.time()
        logger.info(f"Time to get nodes: {end - start:.2f}s")

        def add_obj_to_elem_ref(el: Elem, obj: Shape | Beam | Plate | Pipe):
            el.refs.append(obj)

        # Get Elements
        start = time.time()
        elements = dict()

        for gmsh_data in self.model_map.values():
            entity_elements = get_elements_from_entities(self.model, gmsh_data.entities, fem)
            gmsh_data.obj.elem_refs = entity_elements
            [add_obj_to_elem_ref(el, gmsh_data.obj) for el in entity_elements]
            existing_el_ids = set(elements.keys())
            el_ids = set([el.id for el in entity_elements])

            # create a variable for all overlapping element ids
            overlapping_el_ids = existing_el_ids.intersection(el_ids)
            if overlapping_el_ids:
                logger.warning(f"Overlapping element ids found for {gmsh_data.obj.name}: {overlapping_el_ids}")

            elements.update({el.id: el for el in entity_elements})

        fem.elements = FemElements(elements.values(), fem_obj=fem)
        end = time.time()
        logger.info(f"Time to get elements: {end - start:.2f}s")

        # Add FEM sections
        for model_obj, gmsh_data in self.model_map.items():
            add_fem_sections(self.model, fem, model_obj, gmsh_data)

        fem.nodes.renumber()
        fem.elements.renumber()
        return fem

    def apply_settings(self):
        if self.options is not None:
            for setting, value in self.options.get_as_dict().items():
                self.gmsh.option.setNumber(setting, value)
        self.model.geo.synchronize()

    def open_gui(self):
        self.gmsh.fltk.run()

    def check_model_entities(self):
        from ada.fem.meshing.utils import check_entities_exist

        for map_obj in self.model_map.values():
            existing, nonexisting = check_entities_exist(map_obj.entities, self.model)
            if len(nonexisting) > 0:
                self.open_gui()
                raise ValueError(f"Entities not found in model for {map_obj}: {nonexisting}")

    def __enter__(self):
        logger.debug("Starting GMSH session")
        self._gmsh = gmsh
        self.gmsh.initialize()
        # self.model.add("ada")
        self.apply_settings()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        logger.debug("Closing GMSH")
        self.gmsh.finalize()

    @property
    def gmsh(self) -> "gmsh":
        return self._gmsh

    @property
    def model(self) -> "gmsh.model":
        return self.gmsh.model


def import_into_gmsh_using_step(
    obj, geom_repr: GeomRepr, model: gmsh.model, temp_dir: pathlib.Path, silent: bool
) -> List[tuple]:
    name = f"{obj.name}_{create_guid()}"
    obj.to_stp(temp_dir / name, geom_repr=geom_repr, silent=silent, fuse_piping=True)
    ents = model.occ.importShapes(str(temp_dir / f"{name}.stp"))
    return ents


def import_into_gmsh_use_nativepointer(obj: BackendGeom | Shape, geom_repr: GeomRepr, model: gmsh.model) -> List[tuple]:
    from OCC.Extend.TopologyUtils import TopologyExplorer

    from ada import PrimBox
    from ada.occ.utils import transform_shape

    abs_place = obj.placement.get_absolute_placement()

    def transform_occ(geo):
        if ada.Placement() != abs_place:
            geo = transform_shape(geo, transform=abs_place)
        return geo

    ents = []
    if geom_repr == GeomRepr.SOLID:
        geom = transform_occ(obj.solid_occ())
        t = TopologyExplorer(geom)
        geom_iter = t.solids()
    elif geom_repr == GeomRepr.SHELL:
        geom = obj.shell_occ() if type(obj) not in (PrimBox,) else obj.solid_occ()
        geom = transform_occ(geom)
        t = TopologyExplorer(geom)
        geom_iter = t.faces()
    else:
        geom = transform_occ(obj.line_occ())
        t = TopologyExplorer(geom)
        geom_iter = t.edges()

    for shp in geom_iter:
        ents += model.occ.importShapesNativePointer(int(shp.this))

    if len(ents) == 0:
        raise ValueError("No entities found")

    return ents


def quality_check_mesh(model: gmsh.model):
    element_types, element_tags, _ = model.mesh.getElements()

    for elementType, tags in zip(element_types, element_tags):
        if elementType == 15:
            continue
        qualities = gmsh.model.mesh.getElementQualities(tags, "minSJ")
        bad_indices = [i for i, q in enumerate(qualities) if q <= 0]
        if len(bad_indices) > 0:
            bad_elements = tags[bad_indices]
            bad_jacobis = qualities[bad_indices]
            raise BadJacobians(f"Bad elements (Jacobi<=0) found in mesh: {bad_elements}->{bad_jacobis}")

    logger.info("Mesh quality check passed")
