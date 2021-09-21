from __future__ import annotations

from itertools import chain
from typing import List, Union

import gmsh
import numpy as np

from ada import FEM, Beam, Node, Pipe, Plate
from ada.core.utils import make_name_fem_ready
from ada.fem import Elem, FemSection, FemSet
from ada.fem.shapes import ElemType
from ada.fem.shapes.mesh_types import abaqus_to_meshio_type, gmsh_to_meshio_ordering

from .common import gmsh_map
from .concepts import GmshData


def add_fem_sections(model: gmsh.model, fem: FEM, model_obj: Union[Beam, Plate, Pipe], gmsh_data: GmshData) -> None:
    if type(model_obj) is Beam and gmsh_data.geom_repr == ElemType.SHELL:
        get_sh_sections_for_beam_obj(model, model_obj, gmsh_data, fem)
        return None

    if type(model_obj) is Pipe and gmsh_data.geom_repr == ElemType.SHELL:
        get_sh_sections_for_pipe_obj(model, model_obj, gmsh_data, fem)
        return None

    for _, ent in gmsh_data.entities:
        if gmsh_data.geom_repr == ElemType.SHELL:
            get_sh_sections_for_plate_obj(model, model_obj, ent, fem)
        elif gmsh_data.geom_repr == ElemType.SOLID:
            get_so_sections(model, model_obj, ent, fem)
        else:
            get_bm_sections(model, model_obj, ent, fem)


def get_sh_sections_for_beam_obj(model: gmsh.model, model_obj: Beam, gmsh_data: GmshData, fem: FEM):
    cogs_n_normals = []

    for _, ent in gmsh_data.entities:
        _, _, param = model.mesh.getNodes(2, ent, True)
        normal = model.getNormal(ent, param)[:3]
        cog = model.occ.getCenterOfMass(2, ent)
        cogs_n_normals.append((cog, normal))

    section_profile = model_obj.section.get_section_profile(False)
    from ada.concepts.transforms import Placement

    pl1 = Placement(model_obj.n1.p, model_obj.yvec, model_obj.up, model_obj.xvec)
    pl2 = Placement(model_obj.n2.p, model_obj.yvec, model_obj.up, model_obj.xvec)

    thick_assignment = section_profile.get_thickness_assignments_for_cogs(cogs_n_normals, pl1, pl2)

    for i, (_, ent) in enumerate(gmsh_data.entities):
        _, tags, _ = model.mesh.getElements(2, ent)
        (bm_part, thickness), normal = thick_assignment[i]

        set_name = make_name_fem_ready(f"el{model_obj.name}_e{ent}_{bm_part}_sh")
        fem_sec_name = make_name_fem_ready(f"d{model_obj.name}_e{ent}_{bm_part}_sh")
        add_shell_section(set_name, fem_sec_name, normal, thickness, tags, model_obj, fem)


def get_sh_sections_for_pipe_obj(model: gmsh.model, model_obj: Pipe, gmsh_data: GmshData, fem: FEM):
    thickness = model_obj.section.wt
    normal = model_obj.segments[0].zvec

    for i, (_, ent) in enumerate(gmsh_data.entities):
        _, tags, _ = model.mesh.getElements(2, ent)
        set_name = make_name_fem_ready(f"el{model_obj.name}_e{ent}_pipe_sh")
        fem_sec_name = make_name_fem_ready(f"d{model_obj.name}_e{ent}_pipe_sh")
        add_shell_section(set_name, fem_sec_name, normal, thickness, tags, model_obj, fem)


def get_sh_sections_for_plate_obj(model: gmsh.model, model_obj: Plate, ent, fem: FEM):
    _, tags, _ = model.mesh.getElements(2, ent)
    thickness = model_obj.t
    normal = model_obj.n

    set_name = make_name_fem_ready(f"el{model_obj.name}_e{ent}_pl_sh")
    fem_sec_name = make_name_fem_ready(f"d{model_obj.name}_e{ent}_pl_sh")

    add_shell_section(set_name, fem_sec_name, normal, thickness, tags, model_obj, fem)


def add_shell_section(set_name, fem_sec_name, normal, thickness, tags, model_obj: Union[Beam, Plate, Pipe], fem: FEM):
    fem_set = FemSet(set_name, [fem.elements.from_id(x) for x in chain.from_iterable(tags)], FemSet.TYPES.ELSET)
    props = dict(local_z=normal, thickness=thickness, int_points=5)
    fem_sec = FemSection(fem_sec_name, ElemType.SHELL, fem_set, model_obj.material, **props)
    add_sec_to_fem(fem, fem_sec, fem_set)


def get_bm_sections(model: gmsh.model, beam: Beam, ent, fem: FEM):

    elem_types, elem_tags, elem_node_tags = model.mesh.getElements(1, ent)
    elements = [fem.elements.from_id(tag) for tag in elem_tags[0]]

    set_name = make_name_fem_ready(f"el{beam.name}_set_bm")
    fem_sec_name = make_name_fem_ready(f"d{beam.name}_sec_bm")
    fem_set = FemSet(set_name, elements, FemSet.TYPES.ELSET, parent=fem)
    fem_sec = FemSection(fem_sec_name, ElemType.LINE, fem_set, beam.material, beam.section, beam.ori[2], refs=[beam])

    add_sec_to_fem(fem, fem_sec, fem_set)


def get_so_sections(model: gmsh.model, beam: Beam, ent, fem: FEM):
    _, tags, _ = model.mesh.getElements(3, ent)

    set_name = make_name_fem_ready(f"el{beam.name}_e{ent}_so")
    fem_sec_name = make_name_fem_ready(f"d{beam.name}_e{ent}_so")

    elements = [fem.elements.from_id(tag) for tag in tags[0]]

    fem_set = FemSet(set_name, elements, FemSet.TYPES.ELSET, parent=fem)
    fem_sec = FemSection(fem_sec_name, ElemType.SOLID, fem_set, beam.material)

    add_sec_to_fem(fem, fem_sec, fem_set)


def add_sec_to_fem(fem: FEM, fem_section: FemSection, fem_set: FemSet):
    fem_set_ = fem.sets.add(fem_set)
    fem_section.elset = fem_set_
    fem.add_section(fem_section)


def get_point(model: gmsh.model, p, tol):
    tol_vec = np.array([tol, tol, tol])
    lower = np.array(p) - tol_vec
    upper = np.array(p) + tol_vec
    return model.getEntitiesInBoundingBox(*lower.tolist(), *upper.tolist(), 0)


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
    meshio_type = abaqus_to_meshio_type[elem_type]
    if meshio_type in gmsh_to_meshio_ordering.keys():
        return True
    else:
        return False


def node_reordering(elem_type, nodes):
    """Based on work in meshio"""
    meshio_type = abaqus_to_meshio_type[elem_type]
    order = gmsh_to_meshio_ordering.get(meshio_type, None)
    if order is None:
        return None

    return [nodes[i] for i in order]


def build_bm_lines(model: gmsh.model, bm: Beam, point_tol):
    p1, p2 = bm.n1.p, bm.n2.p

    midpoints = bm.calc_con_points(point_tol=point_tol)

    if bm.connected_end1 is not None:
        p1 = bm.connected_end1.centre
    if bm.connected_end2 is not None:
        p2 = bm.connected_end2.centre

    s = get_point(model, p1, point_tol)
    e = get_point(model, p2, point_tol)

    if len(s) > 1:
        raise ValueError("Multiple nodes")

    if len(s) == 0:
        s = add_point(model, p1.tolist())
    if len(e) == 0:
        e = add_point(model, p2.tolist())

    if len(midpoints) == 0:
        return add_line(model, s, e)

    prev_p = None
    entities = []
    for i, con_centre in enumerate(midpoints):
        midp = get_point(model, con_centre, point_tol)
        if len(midp) == 0:
            midp = add_point(model, con_centre)

        if prev_p is None:
            entities += add_line(model, s, midp)
            prev_p = midp
            continue
        entities += add_line(model, prev_p, midp)
        prev_p = midp

    if prev_p is None:
        entities += add_line(model, s, e)
    else:
        entities += add_line(model, prev_p, e)

    return entities


def add_line(model: gmsh.model, s, e):
    line = gmsh.model.geo.addLine(s[0][1], e[0][1])
    model.geo.synchronize()
    return [(1, line)]


def add_point(model: gmsh.model, p):
    point = [(0, model.geo.addPoint(*p))]
    model.geo.synchronize()
    return point
