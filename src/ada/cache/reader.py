import json
import logging

import h5py

from ada import Assembly, Beam, Material, Node, Part, Section
from ada.core.containers import Beams, Materials, Nodes, Sections
from ada.fem import FEM, Elem
from ada.fem.containers import FemElements
from ada.materials.metals import CarbonSteel

from .utils import from_safe_name, str_fix


def read_assembly_from_cache(h5_filename, assembly=None):
    import h5py

    f = h5py.File(h5_filename, "r")
    info = f["INFO"].attrs
    a = Assembly(info["NAME"]) if assembly is None else assembly
    walk_parts(f.get("PARTS"), a)

    return a


def walk_parts(cache_p, parent):
    for name, p in cache_p.items():
        if type(p) is h5py.Dataset:
            continue
        unsafe_name = from_safe_name(name)
        parent_name = from_safe_name(p.attrs.get("PARENT", ""))
        if parent.name != parent_name:
            logging.error("Unable to retrieve proper Hierarchy from HDF5 cache")
        curr_p = parent.add_part(get_part_from_cache(unsafe_name, p))
        walk_parts(p, curr_p)


def get_part_from_cache(name, part_cache):
    meta_str = part_cache.attrs.get("METADATA")
    metadata = None
    if meta_str is not None:
        metadata = json.loads(meta_str)

    p = Part(name, metadata=metadata)
    fem = part_cache.get("FEM")
    if fem is not None:
        p._fem = get_fem_from_cache(fem)

    node_group = part_cache.get("NODES")
    if node_group is not None:
        p._nodes = get_nodes_from_cache(node_group, p)

    sections = get_sections_from_cache(part_cache, p)
    if sections is not None:
        p._sections = sections

    materials = get_materials_from_cache(part_cache, p)
    if materials is not None:
        p._materials = materials

    beams = get_beams_from_cache(part_cache, p)
    if beams is not None:
        p._beams = beams

    return p


def get_beams_from_cache(part_cache, parent: Part):
    prefix = "BEAMS"
    beams_str = part_cache.get(f"{prefix}_STR")
    beams_int = part_cache.get(f"{prefix}_INT")
    beams_up = part_cache.get(f"{prefix}_UP")
    if beams_str is None:
        return None

    def bm_from_cache(bm_str, bm_int, bm_up):
        nid1, nid2 = [parent.nodes.from_id(nid) for nid in bm_int]
        guid, name, sec_name, mat_name, meta_str = str_fix(bm_str)
        sec = parent.sections.get_by_name(sec_name)
        mat = parent.materials.get_by_name(mat_name)
        metadata = None
        if meta_str is not None:
            metadata = json.loads(meta_str)
        return Beam(name, nid1, nid2, sec=sec, mat=mat, guid=guid, parent=parent, metadata=metadata, up=bm_up)

    bm_zip = zip(beams_str, beams_int, beams_up)

    return Beams([bm_from_cache(bm_str, bm_int, bm_up) for bm_str, bm_int, bm_up in bm_zip], parent=parent)


def get_sections_from_cache(part_cache, parent):
    sections_str = part_cache.get("SECTIONS_STR")
    sections_int = part_cache.get("SECTIONS_INT")
    if sections_str is None:
        return None

    def sec_from_list(sec_str, sec_int):
        guid, name, units, sec_type = str_fix(sec_str)
        r, wt, h, w_top, w_btn, t_w, t_ftop, t_fbtn, sec_id = [x if x != 0 else None for x in sec_int]
        return Section(
            name=name,
            guid=guid,
            sec_id=sec_id,
            units=units,
            sec_type=sec_type,
            r=r,
            wt=wt,
            h=h,
            w_top=w_top,
            w_btn=w_btn,
            t_w=t_w,
            t_ftop=t_ftop,
            t_fbtn=t_fbtn,
        )

    return Sections(
        [sec_from_list(sec_str, sec_int) for sec_str, sec_int in zip(sections_str, sections_int)], parent=parent
    )


def get_materials_from_cache(part_cache, parent):
    mat_str = part_cache.get("MATERIALS_STR")
    mat_int = part_cache.get("MATERIALS_INT")

    if mat_str is None:
        return None

    def mat_from_list(mat_int, mat_str):
        guid, name, units = str_fix(mat_str)
        E, rho, sigy, mat_id = mat_int
        return Material(
            name=name,
            guid=guid,
            mat_id=mat_id,
            units=units,
            mat_model=CarbonSteel(E=E, rho=rho, sig_y=sigy),
            parent=parent,
        )

    return Materials([mat_from_list(mat_int, mat_str) for mat_int, mat_str in zip(mat_int, mat_str)], parent=parent)


def get_fem_from_cache(cache_fem):
    node_groups = cache_fem["NODES"]
    fem = FEM(cache_fem.attrs["NAME"])
    fem._nodes = get_nodes_from_cache(node_groups, fem)
    elements = []
    for eltype, mesh in cache_fem["MESH"].items():
        el_ids = mesh["ELEMENTS"][()]
        elements += [Elem(el_id[0], [fem.nodes.from_id(eli) for eli in el_id[1:]], eltype) for el_id in el_ids]
    fem._elements = FemElements(elements, fem)

    return fem


def get_nodes_from_cache(node_group, parent):
    points_in = node_group[()]
    points = [Node(n[1:], n[0]) for n in points_in]
    return Nodes(points, parent=parent)
