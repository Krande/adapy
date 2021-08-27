import os
from dataclasses import dataclass
from itertools import chain
from typing import Union

import gmsh

from ada.concepts.points import Node
from ada.concepts.primitives import Shape
from ada.concepts.structural import Beam
from ada.config import Settings
from ada.fem import FEM, FemSection, FemSet
from ada.fem.mesh.gmshapi import get_nodes_and_elements
from ada.ifc.utils import create_guid

from .gmshapi import eval_thick_normal_from_cog_of_beam_plate

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
    entities: object
    geom_repr: str
    p1: Node = None
    p2: Node = None


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

    def add_obj(self, obj: Union[Shape, Beam], geom_repr, mesh_size=0.1):
        temp_dir = Settings.temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        name = f"{obj.name}_{create_guid()}"

        if geom_repr == "beam" and type(obj) is Beam:
            s, e = create_gmsh_bm_geom(self.gmsh, mesh_size, obj.n1, obj.n2)
            self.model_map[obj] = GmshData(None, geom_repr, s, e)
        else:
            obj.to_stp(temp_dir / name, geom_repr=geom_repr, silent=True)
            entities = self.gmsh.model.occ.importShapes(str(temp_dir / f"{name}.stp"))
            self.gmsh.model.occ.synchronize()
            self.model_map[obj] = GmshData(entities, geom_repr)

    def mesh(self, size: float = None):
        if size is not None:
            self.settings["Mesh.MeshSizeMax"] = size

        model = self.gmsh.model
        model.geo.synchronize()
        model.mesh.setRecombine(3, 1)
        model.mesh.generate(3)
        model.mesh.removeDuplicateNodes()

    def get_fem(self) -> FEM:
        fem = get_nodes_and_elements(self.gmsh)
        model = self.gmsh.model
        for model_obj, gmsh_data in self.model_map.items():
            if type(model_obj) is Beam:
                add_gmsh_bm_elem_to_fem(model, fem, model_obj, gmsh_data)
        return fem

    def _add_settings(self):
        if self.settings is not None:
            for setting, value in self.settings.items():
                self.gmsh.option.setNumber(setting, value)

    def __enter__(self):
        print("Starting GMSH session")

        self._gmsh = gmsh
        self.gmsh.initialize()
        self._add_settings()
        self.gmsh.model.add("ada")
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        print("Closing GMSH")
        self.gmsh.finalize()

    @property
    def gmsh(self) -> gmsh:
        return self._gmsh


def add_gmsh_bm_elem_to_fem(model: gmsh.model, fem: FEM, beam: Beam, gmsh_data: GmshData):
    if gmsh_data.geom_repr == "shell":
        make_beam_shell_sections(model, fem, beam, gmsh_data)
    elif gmsh_data.geom_repr == "solid":
        fem_sec = FemSection(f"{beam.name}_sec", "solid", fem.elsets["all_elements"], beam.material)
        fem.add_section(fem_sec)
    else:  # beam
        fem_sec = FemSection(
            f"d{beam.name}_sec",
            "beam",
            fem.elsets["all_elements"],
            beam.material,
            beam.section,
            beam.ori[2],
        )
        fem.add_section(fem_sec)


def make_beam_shell_sections(model: gmsh.model, fem: FEM, beam: Beam, gmsh_data: GmshData):
    for _, ent in gmsh_data.entities:
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


def create_gmsh_bm_geom(gmsh_session: gmsh, max_size, n1: Node, n2: Node):
    from .gmshapi import get_point

    model = gmsh_session.model
    p1, p2 = n1.p, n2.p
    s = get_point(gmsh_session, p1)
    e = get_point(gmsh_session, p2)
    if len(s) == 0:
        s = [(0, model.geo.addPoint(*p1.tolist(), max_size))]
    if len(e) == 0:
        e = [(0, model.geo.addPoint(*p2.tolist(), max_size))]
    return s, e
