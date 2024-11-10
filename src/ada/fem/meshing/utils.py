from __future__ import annotations

from itertools import chain

import gmsh
import numpy as np

from ada import FEM, Beam, Node, Pipe, Plate, Shape
from ada.api.transforms import Placement
from ada.base.types import GeomRepr
from ada.config import logger
from ada.core.utils import make_name_fem_ready
from ada.fem import Elem, FemSection, FemSet
from ada.fem.shapes import ElemType
from ada.fem.shapes.mesh_types import aba_to_meshio_types, gmsh_to_meshio_ordering

from .common import gmsh_map
from .concepts import GmshData
from .exceptions import MeshExtrationError


def add_fem_sections(model: gmsh.model, fem: FEM, model_obj: Beam | Plate | Pipe | Shape, gmsh_data: GmshData) -> None:
    if isinstance(model_obj, Beam) and gmsh_data.geom_repr == GeomRepr.SHELL:
        get_sh_sections_for_beam_obj(model, model_obj, gmsh_data, fem)
        return None

    if type(model_obj) is Pipe and gmsh_data.geom_repr == GeomRepr.SHELL:
        get_sh_sections_for_pipe_obj(model, model_obj, gmsh_data, fem)
        return None
    if gmsh_data.geom_repr == GeomRepr.SHELL:
        if isinstance(model_obj, Plate):
            get_sh_sections_for_plate_obj(model, model_obj, gmsh_data, fem)
        elif issubclass(type(model_obj), Shape):
            get_sh_sections_for_shape_obj(model, model_obj, gmsh_data, fem)
        else:
            raise NotImplementedError(
                f"Unsupported combination of geom_repr={gmsh_data.geom_repr}, and {type(model_obj)}"
            )
    elif gmsh_data.geom_repr == GeomRepr.SOLID:
        get_so_sections(model, model_obj, gmsh_data, fem)
    elif gmsh_data.geom_repr == GeomRepr.LINE:
        get_bm_sections(model, model_obj, gmsh_data, fem)
    else:
        raise ValueError(
            f'Unrecognized geometric representation "{gmsh_data.geom_repr}". ' f"Only {ElemType.all} are supported"
        )


def get_sh_sections_for_beam_obj(model: gmsh.model, beam: Beam, gmsh_data: GmshData, fem: FEM):
    from ada.sections.bm_sh_ident import eval_thick_normal_from_cog_of_beam_plate

    pl1 = Placement(beam.n1.p, beam.yvec, beam.up, beam.xvec)
    for _, ent in gmsh_data.entities:
        _, _, param = model.mesh.getNodes(2, ent, True)
        normal = np.array([0.0 if abs(x) == 0.0 else x for x in model.getNormal(ent, param)[:3]])
        cog = model.occ.getCenterOfMass(2, ent)
        pc = eval_thick_normal_from_cog_of_beam_plate(beam.section, cog, normal, pl1)

        _, tags, _ = model.mesh.getElements(2, ent)
        elements = [fem.elements.from_id(x) for x in chain.from_iterable(tags)]
        set_name = make_name_fem_ready(f"el{beam.name}_e{ent}_{pc.type}_sh")
        fem_sec_name = make_name_fem_ready(f"d{beam.name}_e{ent}_{pc.type}_sh")

        add_shell_section(set_name, fem_sec_name, normal, pc.thick, elements, beam, fem)


def get_sh_sections_for_pipe_obj(model: gmsh.model, model_obj: Pipe, gmsh_data: GmshData, fem: FEM):
    thickness = model_obj.section.wt
    normal = model_obj.segments[0].zvec1

    for i, (_, ent) in enumerate(gmsh_data.entities):
        _, tags, _ = model.mesh.getElements(2, ent)
        set_name = make_name_fem_ready(f"el{model_obj.name}_e{ent}_pipe_sh")
        fem_sec_name = make_name_fem_ready(f"d{model_obj.name}_e{ent}_pipe_sh")
        elements = [fem.elements.from_id(x) for x in chain.from_iterable(tags)]
        add_shell_section(set_name, fem_sec_name, normal, thickness, elements, model_obj, fem)


def get_sh_sections_for_shape_obj(model: gmsh.model, model_obj: Shape, gmsh_data: GmshData, fem: FEM):
    from ada.core.utils import Counter

    sides = Counter(1, "S")

    for dim, ent in gmsh_data.entities:
        _, tag, _ = model.mesh.getElements(2, ent)
        _, _, param = model.mesh.getNodes(2, ent, True)

        elements = [fem.elements.from_id(x) for x in chain.from_iterable(tag)]

        thickness = 0.0
        normal = np.array([0.0 if abs(x) == 0.0 else x for x in model.getNormal(ent, param)[:3]])
        s = next(sides)
        set_name = make_name_fem_ready(f"el{model_obj.name}{s}_sh")
        fem_sec_name = make_name_fem_ready(f"d{model_obj.name}{s}_sh")

        add_shell_section(set_name, fem_sec_name, normal, thickness, elements, model_obj, fem, is_rigid=True)

    # Add a reference Point
    # cog = model_obj.bbox.volume_cog
    # fem.add_rp(f"{model_obj.name}_rp", Node(cog))


def get_sh_sections_for_plate_obj(model: gmsh.model, model_obj: Plate, gmsh_data: GmshData, fem: FEM):
    tags = []
    for dim, ent in gmsh_data.entities:
        try:
            _, tag, _ = model.mesh.getElements(2, ent)
        except BaseException as e:
            logger.error(e)
            continue
        tags += tag

    elements = [fem.elements.from_id(x) for x in chain.from_iterable(tags)]

    thickness = model_obj.t
    normal = model_obj.n

    set_name = make_name_fem_ready(f"el{model_obj.name}_sh")
    fem_sec_name = make_name_fem_ready(f"d{model_obj.name}_sh")

    add_shell_section(set_name, fem_sec_name, normal, thickness, elements, model_obj, fem)


def add_shell_section(
    set_name,
    fem_sec_name,
    normal,
    thickness,
    elements,
    model_obj: Beam | Plate | Pipe | Shape,
    fem: FEM,
    is_rigid=False,
):
    fem_set = FemSet(set_name, elements, FemSet.TYPES.ELSET)
    props = dict(local_z=normal, thickness=thickness, int_points=5, is_rigid=is_rigid)
    fem_sec = FemSection(fem_sec_name, ElemType.SHELL, fem_set, model_obj.material, **props)
    add_sec_to_fem(fem, fem_sec, fem_set)


def get_bm_sections(model: gmsh.model, beam: Beam, gmsh_data, fem: FEM):
    from ada.core.vector_utils import vector_length

    tags = []
    for dim, ent in gmsh_data.entities:
        try:
            _, tag, _ = model.mesh.getElements(1, ent)
        except BaseException as e:
            raise MeshExtrationError(f"Failed to extract elements for {beam} due to {e}")

        tags += tag

    elements = [fem.elements.from_id(elid) for elid in chain.from_iterable(tags)]

    set_name = make_name_fem_ready(f"el{beam.name}_set_bm")
    fem_sec_name = make_name_fem_ready(f"d{beam.name}_sec_bm")
    fem_set = FemSet(set_name, elements, FemSet.TYPES.ELSET, parent=fem)

    fem_sec = fem.sections.name_map.get(fem_sec_name, None)
    if fem_sec is None:
        fem_sec = FemSection(
            fem_sec_name, ElemType.LINE, fem_set, beam.material, beam.section, beam.ori[2], refs=[beam]
        )
        add_sec_to_fem(fem, fem_sec, fem_set)

    hinge_prop = beam.connection_props.hinge_prop
    if hinge_prop is None:
        return

    end1_p = hinge_prop.end1.concept_node.p if hinge_prop.end1 is not None else None
    end2_p = hinge_prop.end2.concept_node.p if hinge_prop.end2 is not None else None

    for el in elements:
        n1 = el.nodes[0]
        n2 = el.nodes[-1]
        el.hinge_prop = hinge_prop
        if hinge_prop.end1 is not None and vector_length(end1_p - n1.p) == 0.0:
            el.hinge_prop.end1.fem_node = n1

        if hinge_prop.end2 is not None and vector_length(end2_p - n2.p) == 0.0:
            el.hinge_prop.end2.fem_node = n2


def get_so_sections(model: gmsh.model, solid_object: Beam, gmsh_data: GmshData, fem: FEM):
    tags = []
    for dim, ent in gmsh_data.entities:
        _, tag, _ = model.mesh.getElements(3, ent)
        tags += tag

    elements = [fem.elements.from_id(elid) for elid in chain.from_iterable(tags)]

    set_name = make_name_fem_ready(f"el{solid_object.name}_so")
    fem_sec_name = make_name_fem_ready(f"d{solid_object.name}_so")

    fem_set = FemSet(set_name, elements, FemSet.TYPES.ELSET, parent=fem)
    fem_sec = FemSection(fem_sec_name, ElemType.SOLID, fem_set, solid_object.material)

    add_sec_to_fem(fem, fem_sec, fem_set)


def add_sec_to_fem(fem: FEM, fem_section: FemSection, fem_set: FemSet):
    fem_set_ = fem.sets.add(fem_set, append_suffix_on_exist=True)
    fem_section.elset = fem_set_
    fem.add_section(fem_section)


def get_point(model: gmsh.model, p, tol):
    tol_vec = np.array([tol, tol, tol])
    lower = np.array(p) - tol_vec
    upper = np.array(p) + tol_vec
    return model.getEntitiesInBoundingBox(*lower.tolist(), *upper.tolist(), 0)


def get_nodes_from_gmsh(model: gmsh.model, fem: FEM) -> list[Node]:
    nodes = list(model.mesh.getNodes(-1, -1))
    node_ids = nodes[0]
    node_coords = nodes[1].reshape(len(node_ids), 3)
    return [Node(coord, nid, parent=fem) for nid, coord in zip(node_ids, node_coords)]


def get_elements_from_entity(model: gmsh.model, ent, fem: FEM, dim) -> list[Elem]:
    elem_types, elem_tags, elem_node_tags = model.mesh.getElements(dim, ent)
    elements = []
    el_tags = []
    for k, element_list in enumerate(elem_tags):
        el_name, _, _, numv, _, _ = model.mesh.getElementProperties(elem_types[k])
        if el_name == "Point":
            continue
        elem_type = gmsh_map[el_name]
        for j, eltag in enumerate(element_list):
            if eltag in el_tags:
                continue
            el_tags.append(eltag)
            nodes = []
            for i in range(numv):
                idtag = numv * j + i
                p1 = elem_node_tags[k][idtag]
                nodes.append(fem.nodes.from_id(p1))

            new_nodes = node_reordering(elem_type, nodes)
            if new_nodes is not None:
                nodes = new_nodes

            el = Elem(el_id=eltag, nodes=nodes, el_type=elem_type, parent=fem)
            elements.append(el)
    return elements


def get_elements_from_entities(model: gmsh.model, entities, fem: FEM) -> list[Elem]:
    elements = []
    for dim, ent in set(entities):
        try:
            elements += get_elements_from_entity(model, ent, fem, dim)
        except BaseException as e:
            logger.error(f"Error in get_elements_from_entities: {e}")

    return elements


def is_reorder_necessary(elem_type):
    meshio_type = aba_to_meshio_types[elem_type]
    if meshio_type in gmsh_to_meshio_ordering.keys():
        return True
    else:
        return False


def node_reordering(elem_type, nodes):
    """Based on work in meshio"""
    order = gmsh_to_meshio_ordering.get(elem_type, None)
    if order is None:
        return None

    return [nodes[i] for i in order]


def build_bm_lines(model: gmsh.model, bm: Beam, point_tol):
    p1, p2 = bm.n1.p, bm.n2.p
    con_props = bm.connection_props
    midpoints = con_props.calc_con_points(point_tol=point_tol)

    if con_props.connected_end1 is not None:
        p1 = con_props.connected_end1.centre
    if con_props.connected_end2 is not None:
        p2 = con_props.connected_end2.centre

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


def check_entities_exist(entity_list, gmsh_model: gmsh.model):
    existing_entities = []
    non_existing_entities = []

    # Get all existing entities grouped by dimension
    existing_entities_by_dim = {dim: gmsh_model.occ.getEntities(dim) for dim in range(4)}

    for dim, tag in entity_list:
        # Check if the entity exists in the list for its dimension
        if (dim, tag) in existing_entities_by_dim.get(dim, []):
            existing_entities.append((dim, tag))
        else:
            non_existing_entities.append((dim, tag))

    return existing_entities, non_existing_entities
