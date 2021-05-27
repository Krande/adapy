from ada import Assembly, Part
from ada.fem import FEM


def read_assembly_from_cache(h5_filename):
    import h5py

    f = h5py.File(h5_filename, "r")
    keys = f.keys()
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
    nodes = cache_fem["NODES"]
    points = nodes[()].reshape((nodes.attrs["NBR"], 3), order="F")
    for eltype, mesh in cache_fem["MESH"].items():
        elements = mesh["ELEMENTS"]
        print(eltype, mesh)
        # pts_dataset = fem["NOE"]["COO"]
        # n_points = pts_dataset.attrs["NBR"]

    fem = FEM(cache_fem.attrs["NAME"])
    return fem
