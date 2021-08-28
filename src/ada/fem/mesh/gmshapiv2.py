import os
from dataclasses import dataclass
from itertools import chain
from typing import Iterable, List, Union

import gmsh
import numpy as np

from ada.concepts.containers import Nodes
from ada.concepts.points import Node
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam
from ada.config import Settings
from ada.fem import FEM, Elem, FemSection, FemSet
from ada.fem.containers import FemElements
from ada.ifc.utils import create_guid

from .gmshapi import eval_thick_normal_from_cog_of_beam_plate, gmsh_map

GmshOptions = {
    "Mesh.Algorithm": 1,
    "Mesh.MeshSizeFromCurvature": True,
    "Mesh.MinimumElementsPerTwoPi": 12,
    "Mesh.MeshSizeMax": 0.1,
    "Mesh.ElementOrder": 1,
    "Mesh.SecondOrderIncomplete": 1,
    "Mesh.Smoothing": 3,
    "Geometry.Tolerance": 1e-3,
}


@dataclass
class GmshData:
    entities: Iterable
    geom_repr: str
    order: int


class GmshSession:
    def __init__(self, silent=False, persist=True, geom_repr="shall", settings: dict = None):
        print("init method called")
        self._gmsh = None
        self.settings = settings if settings is not None else GmshOptions
        if silent is True:
            self.settings["General.Terminal"] = 0
        self.geom_repr = geom_repr
        self.persist = persist
        self.model_map = dict()

    def add_obj(self, obj: Union[Shape, Beam], geom_repr, mesh_size=0.1, el_order=1, point_tol=1e-3):
        temp_dir = Settings.temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        name = f"{obj.name}_{create_guid()}"

        if geom_repr == "beam" and type(obj) is Beam:
            entities = create_bm_geom(self.gmsh, obj, mesh_size, point_tol)
            self.model.geo.synchronize()
            self.model_map[obj] = GmshData(entities, geom_repr, el_order)
        else:
            obj.to_stp(temp_dir / name, geom_repr=geom_repr, silent=True)
            entities = self.model.occ.importShapes(str(temp_dir / f"{name}.stp"))
            self.model.occ.synchronize()
            self.model_map[obj] = GmshData(entities, geom_repr, el_order)

    def mesh(self, size: float = None):
        if size is not None:
            self.settings["Mesh.MeshSizeMax"] = size

        model = self.model
        model.geo.synchronize()
        # model.mesh.setRecombine(3, 1)
        model.mesh.generate(3)
        model.mesh.removeDuplicateNodes()

    def get_fem(self) -> FEM:
        fem = get_nodes_and_elements(self.model)
        model = self.model
        for model_obj, gmsh_data in self.model_map.items():
            if type(model_obj) is Beam:
                add_bm_section_props(model, fem, model_obj, gmsh_data)
        return fem

    def _add_settings(self):
        if self.settings is not None:
            for setting, value in self.settings.items():
                self.gmsh.option.setNumber(setting, value)

    def open_gui(self):
        self.gmsh.fltk.run()

    def __enter__(self):
        print("Starting GMSH session")

        self._gmsh = gmsh
        self.gmsh.initialize()
        self._add_settings()
        self.model.add("ada")
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        print("Closing GMSH")
        self.gmsh.finalize()

    @property
    def gmsh(self) -> gmsh:
        return self._gmsh

    @property
    def model(self) -> "gmsh.model":
        return self.gmsh.model


def add_bm_section_props(model: gmsh.model, fem: FEM, beam: Beam, gmsh_data: GmshData):
    if gmsh_data.geom_repr == "shell":
        beam_to_shell_sections(model, fem, beam, gmsh_data)
    elif gmsh_data.geom_repr == "solid":
        fem_sec = FemSection(f"{beam.name}_sec", "solid", fem.elsets["all_elements"], beam.material)
        fem.add_section(fem_sec)
    else:  # beam
        beam_to_beam_sections(fem, beam, gmsh_data)


def beam_to_beam_sections(fem: FEM, beam: Beam, gmsh_data: GmshData):
    for ent in gmsh_data.entities:
        add_bm_fem_section(fem, beam, ent)


def beam_to_shell_sections(model: gmsh.model, fem: FEM, beam: Beam, gmsh_data: GmshData):
    for dim, ent in gmsh_data.entities:
        if dim != 2:
            continue
        r = model.occ.getCenterOfMass(2, ent)
        t, n, c = eval_thick_normal_from_cog_of_beam_plate(beam, r)
        _, tags, _ = model.mesh.getElements(2, ent)
        femset = FemSet(f"{beam.name}_ent{ent}", [fem.elements.from_id(x) for x in chain.from_iterable(tags)], "elset")
        fem.add_set(femset)
        props = dict(local_z=n, thickness=t, int_points=5)
        fem_sec = FemSection(f"{beam.name}_{c}_{ent}", "shell", femset, beam.material, **props)
        fem.add_section(fem_sec)


def extract_fem_data(model: gmsh.model, beam: Beam, ent):
    r = model.occ.getCenterOfMass(2, ent)
    t, n, c = eval_thick_normal_from_cog_of_beam_plate(beam, r)
    _, tags, _ = model.mesh.getElements(2, ent)

    return t, n, c, tags


def create_bm_geom(gmsh_session: gmsh, bm: Beam, size, point_tol):
    geo = gmsh_session.model.geo
    p1, p2 = bm.n1.p, bm.n2.p
    midpoints = bm.calc_con_points(point_tol=point_tol)

    if bm.connected_end1 is not None:
        p1 = bm.connected_end1.centre
    if bm.connected_end2 is not None:
        p2 = bm.connected_end2.centre

    s = get_point(gmsh_session, p1, point_tol)
    e = get_point(gmsh_session, p2, point_tol)

    if len(s) == 0:
        s = [(0, geo.addPoint(*p1.tolist(), size))]
    if len(e) == 0:
        e = [(0, geo.addPoint(*p2.tolist(), size))]
    line_entities = []
    if len(midpoints) > 0:
        prev_p = None
        for i, con_centre in enumerate(midpoints):
            midp = get_point(gmsh_session, con_centre, point_tol)
            if len(midp) == 0:
                midp = [(0, geo.addPoint(*con_centre, size))]
            if prev_p is None:
                line = geo.addLine(s[0][1], midp[0][1])
                line_entities.append(line)
                prev_p = midp
                continue

            line = geo.addLine(prev_p[0][1], midp[0][1])
            line_entities.append(line)
            prev_p = midp

        if prev_p is None:
            line = geo.addLine(s[0][1], e[0][1])
        else:
            line = geo.addLine(prev_p[0][1], e[0][1])
        line_entities.append(line)
    else:
        line = geo.addLine(s[0][1], e[0][1])
        line_entities.append(line)

    return line_entities


def get_point(gmsh_session: gmsh, p, tol):
    tol_vec = np.array([tol, tol, tol])
    lower = np.array(p) - tol_vec
    upper = np.array(p) + tol_vec
    return gmsh_session.model.getEntitiesInBoundingBox(*lower.tolist(), *upper.tolist(), 0)


def add_bm_fem_section(fem: FEM, bm: Beam, line_index):
    from ada.core.utils import make_name_fem_ready

    elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements(1, line_index)
    elements = [fem.elements.from_id(tag) for tag in elem_tags[0]]

    set_name = make_name_fem_ready(f"el{bm.name}_set")
    fem_sec_name = make_name_fem_ready(f"d{bm.name}_sec")

    if set_name in fem.elsets.keys():
        fem_set = fem.elsets[set_name]
        for el in elements:
            el.fem_sec = fem_set.members[0].fem_sec
        fem_set.add_members(elements)
    else:
        fem_set = FemSet(set_name, elements, "elset", parent=fem)
        fem.sets.add(fem_set)
        fem.add_section(
            FemSection(
                fem_sec_name,
                "beam",
                fem_set,
                bm.material,
                bm.section,
                bm.ori[2],
                metadata=dict(beam=bm, numel=len(elements)),
            )
        )


def get_nodes_from_gmsh(model: gmsh.model, fem: FEM) -> List[Node]:
    nodes = list(model.mesh.getNodes(-1, -1))
    node_ids = nodes[0]
    node_coords = nodes[1].reshape(len(node_ids), 3)
    return [Node(coord, nid, parent=fem) for nid, coord in zip(node_ids, node_coords)]


def get_nodes_and_elements(model: gmsh.model, fem: FEM = None, fem_set_name="all_elements") -> FEM:
    fem = FEM("AdaFEM") if fem is None else fem

    # Get nodes
    fem._nodes = Nodes(get_nodes_from_gmsh(model, fem), parent=fem)

    # Get elements
    elem_types, elem_tags, elem_node_tags = model.mesh.getElements(-1, -1)
    elements = []
    for k, element_list in enumerate(elem_tags):
        el_name, dim, _, numv, _, _ = model.mesh.getElementProperties(elem_types[k])
        if el_name == "Point":
            continue
        elem_type = gmsh_map[el_name]
        for j, eltag in enumerate(element_list):
            nodes = []
            for i in range(numv):
                idtag = numv * j + i
                p1 = elem_node_tags[k][idtag]
                nodes.append(fem.nodes.from_id(p1))

            el = Elem(eltag, nodes, elem_type, parent=fem)
            elements.append(el)

    fem._elements = FemElements(elements, fem_obj=fem)
    femset = FemSet(fem_set_name, elements, "elset")
    fem.sets.add(femset)
    return fem
