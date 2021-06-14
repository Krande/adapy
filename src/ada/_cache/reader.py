from ada import Assembly, Node, Part
from ada.core.containers import Nodes
from ada.fem import FEM, Elem
from ada.fem.containers import FemElements


def read_assembly_from_cache(h5_filename):
    import h5py

    f = h5py.File(h5_filename, "r")
    info = f["INFO"].attrs
    a = Assembly(info["NAME"])
    parts = f["PARTS"]
    for name, p in parts.items():
        p = get_part_from_cache(name, p)
        a.add_part(p)

    return a


def get_part_from_cache(name, cache_p):
    p = Part(name)
    if "FEM" in cache_p.keys():
        p._fem = get_fem_from_cache(cache_p["FEM"])
    return p


def get_fem_from_cache(cache_fem):
    node_groups = cache_fem["NODES"]
    points = node_groups[()]
    point_ids = node_groups.attrs["IDS"][()]
    fem = FEM(cache_fem.attrs["NAME"])
    nodes = Nodes([Node(points[int(pid - 1)], pid) for pid in point_ids], parent=fem)
    fem._nodes = nodes
    elements = []
    for eltype, mesh in cache_fem["MESH"].items():
        el_ids = mesh["ELEMENTS"][()]
        elements += [Elem(el_id[0], [nodes.from_id(eli) for eli in el_id[1:]], eltype) for el_id in el_ids]
    fem._elements = FemElements(elements, fem)

    return fem
