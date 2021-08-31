from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from itertools import chain
from typing import Iterable, List, Union

import gmsh
import numpy as np

from ada.concepts.containers import Nodes
from ada.concepts.piping import Pipe
from ada.concepts.points import Node
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam, Plate
from ada.config import Settings
from ada.core.utils import make_name_fem_ready
from ada.fem import FEM, Elem, FemSection, FemSet
from ada.fem.containers import FemElements
from ada.fem.io_meshio import ada_to_meshio_type, gmsh_to_meshio_ordering
from ada.ifc.utils import create_guid

from .gmshapi import eval_thick_normal_from_cog_of_beam_plate, gmsh_map


@dataclass
class GmshOptions:
    Mesh_Algorithm: int = 1
    Mesh_MeshSizeMin: float = None
    Mesh_MeshSizeMax: float = 0.1
    Mesh_ElementOrder: int = 1
    Mesh_SecondOrderIncomplete: int = 1
    Mesh_Smoothing: int = 3
    Mesh_RecombinationAlgorithm: int = None
    Geometry_Tolerance: float = 1e-5
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

    def add_obj(self, obj: Union[Shape, Beam, Plate, Pipe], geom_repr="solid", el_order=1, silent=True, mesh_size=None):
        self._add_settings()
        temp_dir = Settings.temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        name = f"{obj.name}_{create_guid()}"

        if issubclass(type(obj), Shape) and geom_repr != "solid":
            logging.info(f"geom_repr for object type {type(obj)} must be solid. Changing to that now")
            geom_repr = "solid"

        obj.to_stp(temp_dir / name, geom_repr=geom_repr, silent=silent, fuse_piping=True)
        entities = self.model.occ.importShapes(str(temp_dir / f"{name}.stp"))
        self.model.occ.synchronize()

        gmsh_data = GmshData(entities, geom_repr, el_order, mesh_size=mesh_size)
        self.model_map[obj] = gmsh_data
        return gmsh_data

    def add_cutting_plane(self, cut_plane: CutPlane, cut_objects: List[GmshData] = None):
        if cut_objects is not None:
            if cut_plane.cut_objects is None:
                cut_plane.cut_objects = []
            cut_plane.cut_objects += cut_objects
        self.cutting_planes.append(cut_plane)

    def make_cuts(self):
        for cut in self.cutting_planes:
            x, y, z = cut.origin
            rect = self.model.occ.addRectangle(x, y, z, cut.dx, cut.dy)
            cut.gmsh_id = rect
            for obj in cut.cut_objects:
                res = self.model.occ.fragment(obj.entities, [(2, rect)], removeTool=True)
                obj.entities = [(dim, r) for dim, r in res[0] if dim == 3]
            self.model.occ.remove([(2, rect)], True)

        rem_ids = [(2, c.gmsh_id) for c in self.cutting_planes]
        self.model.occ.remove(rem_ids, True)
        self.model.occ.synchronize()

    def mesh(self, size: float = None):
        if self.silent is True:
            self.options.General_Terminal = 0
        self._add_settings()
        if size is not None:
            self.gmsh.option.setNumber("Mesh.MeshSizeMax", size)

        model = self.model
        model.geo.synchronize()
        model.mesh.setRecombine(3, 1)
        model.mesh.generate(3)
        model.mesh.removeDuplicateNodes()

    def get_fem(self) -> FEM:
        fem = FEM("AdaFEM")
        gmsh_nodes = get_nodes_from_gmsh(self.model, fem)
        fem._nodes = Nodes(gmsh_nodes, parent=fem)

        # Get Elements
        elements = []
        for gmsh_data in self.model_map.values():
            elements += get_elements_from_entities(self.model, gmsh_data.entities, fem)
        fem._elements = FemElements(elements, fem_obj=fem)

        # Add FEM sections
        for model_obj, gmsh_data in self.model_map.items():
            add_fem_sections(self.model, fem, model_obj, gmsh_data)

        return fem

    def _add_settings(self):
        if self.options is not None:
            for setting, value in self.options.get_as_dict().items():
                self.gmsh.option.setNumber(setting, value)

    def open_gui(self):
        self.gmsh.fltk.run()

    def __enter__(self):
        logging.debug("Starting GMSH session")

        self._gmsh = gmsh
        self.gmsh.initialize()
        self._add_settings()
        self.model.add("ada")
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


def add_fem_sections(model: gmsh.model, fem: FEM, model_obj: Union[Beam, Plate, Pipe], gmsh_data: GmshData):
    for _, ent in gmsh_data.entities:
        if gmsh_data.geom_repr == "shell":
            get_sh_sections(model, model_obj, ent, fem)
        elif gmsh_data.geom_repr == "solid":
            get_so_sections(model, model_obj, ent, fem)
        else:  # beam
            get_bm_sections(model, model_obj, ent, fem)


def get_sh_sections(model: gmsh.model, model_obj: Union[Beam, Plate, Pipe], ent, fem: FEM):
    _, tags, _ = model.mesh.getElements(2, ent)
    r = model.occ.getCenterOfMass(2, ent)
    if type(model_obj) is Beam:
        t, n, c = eval_thick_normal_from_cog_of_beam_plate(model_obj, r)
    elif type(model_obj) is Pipe:
        t, n, c = model_obj.section.wt, model_obj.segments[0].zvec, "pipe"
    else:
        t, n, c = model_obj.t, model_obj.n, "pl"

    set_name = make_name_fem_ready(f"el{model_obj.name}_e{ent}_{c}_sh")
    fem_sec_name = make_name_fem_ready(f"d{model_obj.name}_e{ent}_{c}_sh")

    fem_set = FemSet(set_name, [fem.elements.from_id(x) for x in chain.from_iterable(tags)], "elset")
    props = dict(local_z=n, thickness=t, int_points=5)
    fem_sec = FemSection(fem_sec_name, "shell", fem_set, model_obj.material, **props)
    add_sec_to_fem(fem, fem_sec, fem_set)


def get_bm_sections(model: gmsh.model, beam: Beam, ent, fem: FEM):

    elem_types, elem_tags, elem_node_tags = model.mesh.getElements(1, ent)
    elements = [fem.elements.from_id(tag) for tag in elem_tags[0]]

    set_name = make_name_fem_ready(f"el{beam.name}_set_bm")
    fem_sec_name = make_name_fem_ready(f"d{beam.name}_sec_bm")
    fem_set = FemSet(set_name, elements, "elset", parent=fem)
    fem_sec = FemSection(fem_sec_name, "beam", fem_set, beam.material, beam.section, beam.ori[2])

    add_sec_to_fem(fem, fem_sec, fem_set)


def get_so_sections(model: gmsh.model, beam: Beam, ent, fem: FEM):
    _, tags, _ = model.mesh.getElements(3, ent)

    set_name = make_name_fem_ready(f"el{beam.name}_e{ent}_so")
    fem_sec_name = make_name_fem_ready(f"d{beam.name}_e{ent}_so")

    elements = [fem.elements.from_id(tag) for tag in tags[0]]

    fem_set = FemSet(set_name, elements, "elset", parent=fem)
    fem_sec = FemSection(fem_sec_name, "solid", fem_set, beam.material)

    add_sec_to_fem(fem, fem_sec, fem_set)


def add_sec_to_fem(fem: FEM, fem_section: FemSection, fem_set: FemSet):
    fem_set_ = fem.sets.add(fem_set)
    fem_section.elset = fem_set_
    fem.add_section(fem_section)


def get_point(gmsh_session: gmsh, p, tol):
    tol_vec = np.array([tol, tol, tol])
    lower = np.array(p) - tol_vec
    upper = np.array(p) + tol_vec
    return gmsh_session.model.getEntitiesInBoundingBox(*lower.tolist(), *upper.tolist(), 0)


def get_nodes_from_gmsh(model: gmsh.model, fem: FEM) -> List[Node]:
    nodes = list(model.mesh.getNodes(-1, -1))
    node_ids = nodes[0]
    node_coords = nodes[1].reshape(len(node_ids), 3)
    return [Node(coord, nid, parent=fem) for nid, coord in zip(node_ids, node_coords)]


def get_elements_from_entity(model: gmsh.model, ent, fem: FEM, dim) -> List[Elem]:
    elem_types, elem_tags, elem_node_tags = model.mesh.getElements(dim, ent)
    elements = []
    for k, element_list in enumerate(elem_tags):
        el_name, _, _, numv, _, _ = model.mesh.getElementProperties(elem_types[k])
        if el_name == "Point":
            continue
        elem_type = gmsh_map[el_name]
        for j, eltag in enumerate(element_list):
            nodes = []
            for i in range(numv):
                idtag = numv * j + i
                p1 = elem_node_tags[k][idtag]
                nodes.append(fem.nodes.from_id(p1))

            new_nodes = node_reordering(elem_type, nodes)
            if new_nodes is not None:
                nodes = new_nodes

            el = Elem(eltag, nodes, elem_type, parent=fem)
            elements.append(el)
    return elements


def get_elements_from_entities(model: gmsh.model, entities, fem: FEM) -> List[Elem]:
    elements = []
    for dim, ent in entities:
        elements += get_elements_from_entity(model, ent, fem, dim)
    return elements


def is_reorder_necessary(elem_type):
    meshio_type = ada_to_meshio_type[elem_type]
    if meshio_type in gmsh_to_meshio_ordering.keys():
        return True
    else:
        return False


def node_reordering(elem_type, nodes):
    """Based on work in meshio"""
    meshio_type = ada_to_meshio_type[elem_type]
    order = gmsh_to_meshio_ordering.get(meshio_type, None)
    if order is None:
        return None

    return [nodes[i] for i in order]


def multisession_gmsh_tasker(gmsh_tasks: List[GmshTask]):
    fem = FEM("AdaFEM")
    for gtask in gmsh_tasks:
        with GmshSession(silent=True) as gs:
            gs.options = gtask.options
            for obj in gtask.ada_obj:
                gs.add_obj(obj, gtask.geom_repr)
            gs.mesh(gtask.mesh_size)
            # TODO: Add operand type += for FEM
            tmp_fem = gs.get_fem()
            fem += tmp_fem
    return fem
