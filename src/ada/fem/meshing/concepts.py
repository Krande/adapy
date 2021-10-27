from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Iterable, List, Union

import gmsh

from ada import FEM, Beam, Pipe, Plate, Shape
from ada.base.physical_objects import BackendGeom
from ada.concepts.containers import Nodes
from ada.config import Settings
from ada.fem import Elem
from ada.fem.containers import FemElements
from ada.fem.shapes import ElemType
from ada.ifc.utils import create_guid


@dataclass
class GmshOptions:
    # Mesh
    Mesh_Algorithm: int = 6
    Mesh_ElementOrder: int = 1
    Mesh_Algorithm3D: int = 1
    Mesh_MeshSizeMin: float = None
    Mesh_MeshSizeMax: float = 0.1
    Mesh_SecondOrderIncomplete: int = 1
    Mesh_Smoothing: int = 3
    Mesh_RecombinationAlgorithm: int = None
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
    options: GmshOptions = GmshOptions()


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
    obj: Union[Shape, Beam, Plate, Pipe]
    mesh_size: float = None


class GmshSession:
    def __init__(self, silent=False, persist=True, options: GmshOptions = GmshOptions()):
        logging.debug("init method called")
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
        obj: Union[BackendGeom, Shape, Beam, Plate, Pipe],
        geom_repr=ElemType.SOLID,
        el_order=1,
        silent=True,
        mesh_size=None,
        build_native_lines=False,
        point_tol=Settings.point_tol,
    ):
        from ada.core.utils import Counter

        from .utils import build_bm_lines

        self.apply_settings()
        temp_dir = Settings.temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        name = f"{obj.name}_{create_guid()}"

        def export_as_step(export_obj):
            export_obj.to_stp(temp_dir / name, geom_repr=geom_repr, silent=silent, fuse_piping=True)
            ents = self.model.occ.importShapes(str(temp_dir / f"{name}.stp"))
            return ents

        if issubclass(type(obj), Shape) and geom_repr != ElemType.SOLID:
            logging.info(f"geom_repr for object type {type(obj)} must be solid. Changing to that now")
            geom_repr = ElemType.SOLID

        if build_native_lines is True and geom_repr == ElemType.LINE and type(obj) is Beam:
            # midpoints = obj.calc_con_points()
            entities = build_bm_lines(self.model, obj, point_tol)
            # if len(midpoints) > 0:
            #
            # else:
            #     entities = export_as_step(obj)
        else:
            entities = export_as_step(obj)
        #
        # self.model.geo.synchronize()
        # self.model.occ.synchronize()
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

    def mesh(self, size: float = None):
        if self.silent is True:
            self.options.General_Terminal = 0

        if size is not None:
            self.options.Mesh_MeshSizeMax = size
            if self.options.Mesh_MeshSizeFromCurvature is False:
                self.options.Mesh_MeshSizeMin = size

        self.apply_settings()
        self.model.geo.synchronize()
        self.model.mesh.setRecombine(3, -1)
        self.model.mesh.generate(3)
        self.model.mesh.removeDuplicateNodes()

    def get_fem(self) -> FEM:
        from .utils import (
            add_fem_sections,
            get_elements_from_entities,
            get_nodes_from_gmsh,
        )

        fem = FEM("AdaFEM")
        gmsh_nodes = get_nodes_from_gmsh(self.model, fem)
        fem.nodes = Nodes(gmsh_nodes, parent=fem)

        def add_obj_to_elem_ref(el: Elem, obj: Union[Shape, Beam, Plate, Pipe]):
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
        logging.debug("Starting GMSH session")

        self._gmsh = gmsh
        self.gmsh.initialize()
        # self.model.add("ada")
        self.apply_settings()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        logging.debug("Closing GMSH")
        self.gmsh.finalize()

    @property
    def gmsh(self) -> gmsh:
        return self._gmsh

    @property
    def model(self) -> "gmsh.model":
        return self.gmsh.model
