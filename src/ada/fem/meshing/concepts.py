from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field
from typing import Iterable, List, Union

import gmsh
import numpy as np

from ada import FEM, Beam, Pipe, Plate, Shape
from ada.base.physical_objects import BackendGeom
from ada.base.types import GeomRepr
from ada.concepts.containers import Nodes
from ada.config import Settings, get_logger
from ada.fem import Elem
from ada.fem.containers import FemElements
from ada.fem.shapes import ElemType
from ada.ifc.utils import create_guid

logger = get_logger()


@dataclass
class GmshOptions:
    # Mesh
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
    entities: Iterable
    geom_repr: str
    order: int
    obj: Shape | Beam | Plate | Pipe
    mesh_size: float = None


class GmshSession:
    def __init__(self, silent=False, persist=True, options: GmshOptions = GmshOptions()):
        logger.debug("init method called")
        self._gmsh = None
        self.options = options
        self.cutting_planes: List[CutPlane] = []
        self.silent = silent
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
        point_tol=Settings.point_tol,
        use_native_pointer=True,
    ):
        from ada.core.utils import Counter

        from .utils import build_bm_lines

        if isinstance(geom_repr, str):
            geom_repr = GeomRepr.from_str(geom_repr)

        self.apply_settings()
        temp_dir = Settings.temp_dir
        os.makedirs(temp_dir, exist_ok=True)

        if build_native_lines is True and geom_repr == ElemType.LINE and type(obj) is Beam:
            entities = build_bm_lines(self.model, obj, point_tol)
        else:
            if use_native_pointer and hasattr(self.model.occ, "importShapesNativePointer"):
                # Use hasattr to ensure that it works for gmsh < 4.9.*
                if type(obj) is Pipe:
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

    def make_cuts(self):
        geom_repr_map = {ElemType.SOLID: 3, ElemType.SHELL: 2, ElemType.LINE: 1}

        for cut in self.cutting_planes:
            x, y, z = cut.origin
            rect = self.model.occ.addRectangle(x, y, z, cut.dx, cut.dy)

            if cut.plane == "XZ":
                self.model.occ.synchronize()
                self.model.geo.synchronize()
                self.model.occ.rotate([(2, rect)], x, y, z, 1, 0, 0, np.deg2rad(90))
                self.model.occ.synchronize()
                self.model.geo.synchronize()

            cut.gmsh_id = rect
            for obj in cut.cut_objects:
                res, _ = self.model.occ.fragment(obj.entities, [(2, rect)], removeTool=True)
                cut_geom_dim = geom_repr_map[obj.geom_repr]
                replaced_entities = [(dim, r) for dim, r in res if r != rect and dim == cut_geom_dim]
                obj.entities = replaced_entities
            self.model.occ.remove([(2, rect)], True)

        # rem_ids = [(2, c.gmsh_id) for c in self.cutting_planes]
        # self.model.occ.remove(rem_ids, True)

        self.model.occ.synchronize()
        self.model.geo.synchronize()

    def split_crossing_beams(self):
        # Todo: base this algo on beams that are actually clashing

        beams = [obj for obj in self.model_map.keys() if type(obj) is Beam]
        if len(beams) == 1:
            return None

        intersecting_beams = []
        int_bm_map = dict()
        for bm in beams:
            bm_gmsh_obj = self.model_map[bm]
            for li_dim, li_ent in bm_gmsh_obj.entities:
                intersecting_beams.append((li_dim, li_ent))
                int_bm_map[(li_dim, li_ent)] = bm_gmsh_obj

        res, res_map = self.model.occ.fragment(intersecting_beams, intersecting_beams)

        for i, int_bm in enumerate(intersecting_beams):
            bm_gmsh_obj = int_bm_map[int_bm]
            new_ents = res_map[i]
            bm_gmsh_obj.entities = new_ents

        self.model.occ.synchronize()

    def split_plates_by_beams(self):
        from ada.core.clash_check import (
            filter_away_beams_along_plate_edges,
            find_beams_connected_to_plate,
        )

        beams = [obj for obj in self.model_map.keys() if type(obj) is Beam]
        plates = [obj for obj in self.model_map.keys() if type(obj) is Plate]
        for pl in plates:
            pl_gmsh_obj = self.model_map[pl]
            for pl_dim, pl_ent in pl_gmsh_obj.entities:
                intersecting_beams = []
                int_bm_map = dict()
                all_contained_beams = find_beams_connected_to_plate(pl, beams)
                inside_beams = filter_away_beams_along_plate_edges(pl, all_contained_beams)
                for bm in inside_beams:
                    bm_gmsh_obj = self.model_map[bm]
                    for li_dim, li_ent in bm_gmsh_obj.entities:
                        intersecting_beams.append((li_dim, li_ent))
                        int_bm_map[(li_dim, li_ent)] = bm_gmsh_obj
                # Using Embed fails during meshing

                # res = self.model.mesh.embed(1, [t for e,t in intersecting_beams], 2, pl_ent)

                res, res_map = self.model.occ.fragment(intersecting_beams, [(pl_dim, pl_ent)])
                replaced_pl_entities = [(dim, r) for dim, r in res if dim == 2]
                for i, int_bm in enumerate(intersecting_beams):
                    bm_gmsh_obj = int_bm_map[int_bm]
                    new_ents = res_map[i]
                    bm_gmsh_obj.entities = new_ents
                pl_gmsh_obj.entities = replaced_pl_entities

                self.model.occ.synchronize()

    def mesh(self, size: float = None, use_quads=False, use_hex=False):
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
        self.model.mesh.generate(3)
        self.model.mesh.removeDuplicateNodes()

        if use_quads is True or use_hex is True:
            self.model.mesh.recombine()

    def make_quads(self):
        from ada.fem.meshing.partitioning.strategies import partition_objects_with_holes

        ents = []
        for obj, model in self.model_map.items():
            if model.geom_repr == ElemType.SHELL:
                if len(obj.penetrations) > 0:
                    partition_objects_with_holes(model, self)
                else:
                    for dim, tag in model.entities:
                        ents.append(tag)
                        self.model.mesh.set_transfinite_surface(tag)
                        self.model.mesh.setRecombine(dim, tag)

    def make_hex(self):
        from ada.fem.meshing.partitioning.strategies import partition_solid_beams

        for dim, tag in self.model.get_entities():
            if dim == 2:
                self.model.mesh.set_transfinite_surface(tag)
                self.model.mesh.setRecombine(dim, tag)

        for obj, model in self.model_map.items():
            if model.geom_repr == GeomRepr.SOLID:
                if isinstance(obj, Beam):
                    partition_solid_beams(model, self)
                else:
                    for dim, tag in model.entities:
                        self.model.mesh.set_transfinite_volume(tag)
                        self.model.mesh.setRecombine(dim, tag)

        self.model.mesh.recombine()

    def get_fem(self, name="AdaFEM") -> FEM:
        from .utils import (
            add_fem_sections,
            get_elements_from_entities,
            get_nodes_from_gmsh,
        )

        fem = FEM(name)
        gmsh_nodes = get_nodes_from_gmsh(self.model, fem)
        fem.nodes = Nodes(gmsh_nodes, parent=fem)

        def add_obj_to_elem_ref(el: Elem, obj: Shape | Beam | Plate | Pipe):
            el.refs.append(obj)

        # Get Elements
        elements = []
        for gmsh_data in self.model_map.values():
            entity_elements = get_elements_from_entities(self.model, gmsh_data.entities, fem)
            gmsh_data.obj.elem_refs = entity_elements
            [add_obj_to_elem_ref(el, gmsh_data.obj) for el in entity_elements]
            elements += entity_elements

        fem.elements = FemElements(elements, fem_obj=fem)

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

    ents = []
    if geom_repr == GeomRepr.SOLID:
        geom = obj.solid()
        t = TopologyExplorer(geom)
        geom_iter = t.solids()
    elif geom_repr == GeomRepr.SHELL:
        geom = obj.shell() if type(obj) not in (PrimBox,) else obj.geom()
        t = TopologyExplorer(geom)
        geom_iter = t.faces()
    else:
        geom = obj.line()
        t = TopologyExplorer(geom)
        geom_iter = t.edges()

    for shp in geom_iter:
        ents += model.occ.importShapesNativePointer(int(shp.this))

    if len(ents) == 0:
        raise ValueError("No entities found")

    return ents
