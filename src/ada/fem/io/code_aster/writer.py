from itertools import groupby
from operator import attrgetter

import meshio
import numpy as np

from ..io_meshio import ada_to_meshio_type
from ..utils import _folder_prep, get_fem_model_from_assembly

meshio_to_med_type = {
    "vertex": "PO1",
    "line": "SE2",
    "line3": "SE3",
    "triangle": "TR3",
    "triangle6": "TR6",
    "quad": "QU4",
    "quad8": "QU8",
    "tetra": "TE4",
    "tetra10": "T10",
    "hexahedron": "HE8",
    "hexahedron20": "H20",
    "pyramid": "PY5",
    "pyramid13": "P13",
    "wedge": "PE6",
    "wedge15": "P15",
}

med_to_meshio_type = {v: k for k, v in meshio_to_med_type.items()}
numpy_void_str = np.string_("")


def to_fem(
    assembly,
    name,
    scratch_dir=None,
    description=None,
    execute=False,
    run_ext=False,
    cpus=2,
    gpus=None,
    overwrite=False,
    exit_on_complete=True,
):
    """
    Write a Code_Aster .med and .comm file


    Based on meshio implementation..

    Todo: Evaluate if meshio can replace parts of the mesh writing algorithm..

    :param assembly:
    :param name:
    :param scratch_dir:
    :param description:
    :param execute:
    :param run_ext:
    :param cpus:
    :param gpus:
    :param overwrite:
    :param exit_on_complete:
    :return:
    """
    print(f"creating: {name}")
    analysis_dir = _folder_prep(scratch_dir, name, overwrite)

    if "info" not in assembly.metadata:
        assembly.metadata["info"] = dict(description="")

    assembly.metadata["info"]["description"] = description

    p = get_fem_model_from_assembly(assembly)

    write_to_med(name, p, analysis_dir)

    with open((analysis_dir / name).with_suffix(".export"), "w") as f:
        f.write(write_export_file(analysis_dir, name, 2))

    # TODO: Finish .comm setup based on Salome meca setup
    with open((analysis_dir / name).with_suffix(".comm"), "w") as f:
        f.write(write_to_comm(name, assembly, p, analysis_dir))

    print(f'Created a Code_Aster input deck at "{analysis_dir}"')

    if execute:
        from .execute import run_code_aster

        run_code_aster(
            (analysis_dir / name).with_suffix(".export"),
            cpus=cpus,
            gpus=gpus,
            run_ext=run_ext,
            metadata=assembly.metadata,
            execute=execute,
            exit_on_complete=exit_on_complete,
        )


def write_to_comm(name, a, p, analysis_dir):
    comm_str = "DEBUT(LANG='EN')\n\n"
    comm_str += 'mesh=LIRE_MAILLAGE(FORMAT="MED", UNITE=20)\n\n'
    comm_str += "model=AFFE_MODELE(AFFE=_F(MODELISATION=('3D', ),PHENOMENE='MECANIQUE',TOUT='OUI'),MAILLAGE=mesh)\n\n"
    # Add missing parameters here
    comm_str += "FIN()"

    return comm_str


def write_export_file(analysis_dir, name, cpus):
    """

    :param analysis_dir:
    :param name:
    :param cpus:
    :return:
    """
    #     alt_str = r"""P actions make_etude
    # P memjob 507904
    # P memory_limit 496.0
    # P mode interactif
    # P mpi_nbcpu 1
    # P ncpus {cpus}
    # P rep_trav {analysis_dir}
    # P time_limit 60.0
    # P tpsjob 2
    # P version stable
    # A memjeveux 62.0
    # A tpmax 60.0
    # F comm {analysis_dir}\{name}.comm D  1
    # F mmed {analysis_dir}\{name}.med D  20"""

    export_str = rf"""P actions make_etude
P memory_limit 1274
P time_limit 900
P version stable
P mpi_nbcpu 1
P mode interactif
P ncpus {cpus}
P rep_trav {analysis_dir}
F comm {analysis_dir}\{name}.comm D  1
F mmed {analysis_dir}\{name}.med D  20
F rmed {analysis_dir}\{name}.rmed R 80"""

    return export_str


def write_to_med(name, p, analysis_dir):
    """
    Method for writing a part directly based on meshio example

    :param name: name
    :param p: Part
    :param analysis_dir:
    :type p: ada.Part
    """
    import h5py

    def get_nids(el):
        return [n.id for n in el.nodes]

    cells = []
    plist = list(sorted(p.fem.nodes, key=attrgetter("id")))
    if len(plist) == 0:
        return None

    pid = plist[-1].id
    points = np.zeros((int(pid + 1), 3))

    def pmap(n):
        points[n.id] = n.p

    list(map(pmap, p.fem.nodes))
    for group, elements in groupby(p.fem.elements, key=attrgetter("type")):
        med_el = ada_to_meshio_type[group]
        el_mapped = np.array(list(map(get_nids, elements)))
        cells.append((med_el, el_mapped))

    mesh = meshio.Mesh(points, cells)
    part_file = (analysis_dir / name).with_suffix(".med")
    f = h5py.File(part_file, "w")

    # Strangely the version must be 3.0.x
    # Any version >= 3.1.0 will NOT work with SALOME 8.3
    info = f.create_group("INFOS_GENERALES")
    info.attrs.create("MAJ", 3)
    info.attrs.create("MIN", 0)
    info.attrs.create("REL", 0)

    # Meshes
    mesh_ensemble = f.create_group("ENS_MAA")
    mesh_name = "mesh"
    med_mesh = mesh_ensemble.create_group(mesh_name)
    med_mesh.attrs.create("DIM", mesh.points.shape[1])  # mesh dimension
    med_mesh.attrs.create("ESP", mesh.points.shape[1])  # spatial dimension
    med_mesh.attrs.create("REP", 0)  # cartesian coordinate system (repère in French)
    med_mesh.attrs.create("UNT", numpy_void_str)  # time unit
    med_mesh.attrs.create("UNI", numpy_void_str)  # spatial unit
    med_mesh.attrs.create("SRT", 1)  # sorting type MED_SORT_ITDT
    med_mesh.attrs.create("NOM", np.string_(_component_names(mesh.points.shape[1])))  # component names
    med_mesh.attrs.create("DES", np.string_("Mesh created with meshio"))
    med_mesh.attrs.create("TYP", 0)  # mesh type (MED_NON_STRUCTURE)

    # Time-step
    step = "-0000000000000000001-0000000000000000001"  # NDT NOR
    time_step = med_mesh.create_group(step)
    time_step.attrs.create("CGT", 1)
    time_step.attrs.create("NDT", -1)  # no time step (-1)
    time_step.attrs.create("NOR", -1)  # no iteration step (-1)
    time_step.attrs.create("PDT", -1.0)  # current time

    # Points
    nodes_group = time_step.create_group("NOE")
    nodes_group.attrs.create("CGT", 1)
    nodes_group.attrs.create("CGS", 1)
    profile = "MED_NO_PROFILE_INTERNAL"
    nodes_group.attrs.create("PFL", np.string_(profile))
    coo = nodes_group.create_dataset("COO", data=mesh.points.flatten(order="F"))
    coo.attrs.create("CGT", 1)
    coo.attrs.create("NBR", len(mesh.points))

    # Point tags
    if "point_tags" in mesh.point_data:  # only works for med -> med
        family = nodes_group.create_dataset("FAM", data=mesh.point_data["point_tags"])
        family.attrs.create("CGT", 1)
        family.attrs.create("NBR", len(mesh.points))

    # Cells (mailles in French)
    if len(mesh.cells) != len(np.unique([c.type for c in mesh.cells])):
        raise ValueError("MED files cannot have two sections of the same cell type.")
    cells_group = time_step.create_group("MAI")
    cells_group.attrs.create("CGT", 1)
    for k, (cell_type, cells) in enumerate(mesh.cells):
        med_type = meshio_to_med_type[cell_type]
        med_cells = cells_group.create_group(med_type)
        med_cells.attrs.create("CGT", 1)
        med_cells.attrs.create("CGS", 1)
        med_cells.attrs.create("PFL", np.string_(profile))
        nod = med_cells.create_dataset("NOD", data=cells.flatten(order="F") + 1)
        nod.attrs.create("CGT", 1)
        nod.attrs.create("NBR", len(cells))

        # Cell tags
        if "cell_tags" in mesh.cell_data:  # works only for med -> med
            family = med_cells.create_dataset("FAM", data=mesh.cell_data["cell_tags"][k])
            family.attrs.create("CGT", 1)
            family.attrs.create("NBR", len(cells))

    # Information about point and cell sets (familles in French)
    fas = f.create_group("FAS")
    families = fas.create_group(mesh_name)
    family_zero = families.create_group("FAMILLE_ZERO")  # must be defined in any case
    family_zero.attrs.create("NUM", 0)

    # For point tags
    try:
        if len(mesh.point_tags) > 0:
            node = families.create_group("NOEUD")
            _write_families(node, mesh.point_tags)
    except AttributeError:
        pass

    # For cell tags
    try:
        if len(mesh.cell_tags) > 0:
            element = families.create_group("ELEME")
            _write_families(element, mesh.cell_tags)
    except AttributeError:
        pass

    # Write nodal/cell data
    fields = f.create_group("CHA")

    # Nodal data
    for name, data in mesh.point_data.items():
        if name == "point_tags":  # ignore point_tags already written under FAS
            continue
        supp = "NOEU"  # nodal data
        _write_data(fields, mesh_name, profile, name, supp, data)

    # Cell data
    # Only support writing ELEM fields with only 1 Gauss point per cell
    # Or ELNO (DG) fields defined at every node per cell
    for name, d in mesh.cell_data.items():
        if name == "cell_tags":  # ignore cell_tags already written under FAS
            continue
        for cell, data in zip(mesh.cells, d):
            # Determine the nature of the cell data
            # Either shape = (n_data, ) or (n_data, n_components) -> ELEM
            # or shape = (n_data, n_gauss_points, n_components) -> ELNO or ELGA
            med_type = meshio_to_med_type[cell.type]
            if data.ndim <= 2:
                supp = "ELEM"
            elif data.shape[1] == num_nodes_per_cell[cell_type]:
                supp = "ELNO"
            else:  # general ELGA data defined at unknown Gauss points
                supp = "ELGA"
            _write_data(
                fields,
                mesh_name,
                profile,
                name,
                supp,
                data,
                med_type,
            )


num_nodes_per_cell = {
    "vertex": 1,
    "line": 2,
    "triangle": 3,
    "quad": 4,
    "quad8": 8,
    "tetra": 4,
    "hexahedron": 8,
    "hexahedron20": 20,
    "hexahedron24": 24,
    "wedge": 6,
    "pyramid": 5,
    #
    "line3": 3,
    "triangle6": 6,
    "quad9": 9,
    "tetra10": 10,
    "hexahedron27": 27,
    "wedge15": 15,
    "wedge18": 18,
    "pyramid13": 13,
    "pyramid14": 14,
    #
    "line4": 4,
    "triangle10": 10,
    "quad16": 16,
    "tetra20": 20,
    "wedge40": 40,
    "hexahedron64": 64,
    #
    "line5": 5,
    "triangle15": 15,
    "quad25": 25,
    "tetra35": 35,
    "wedge75": 75,
    "hexahedron125": 125,
    #
    "line6": 6,
    "triangle21": 21,
    "quad36": 36,
    "tetra56": 56,
    "wedge126": 126,
    "hexahedron216": 216,
    #
    "line7": 7,
    "triangle28": 28,
    "quad49": 49,
    "tetra84": 84,
    "wedge196": 196,
    "hexahedron343": 343,
    #
    "line8": 8,
    "triangle36": 36,
    "quad64": 64,
    "tetra120": 120,
    "wedge288": 288,
    "hexahedron512": 512,
    #
    "line9": 9,
    "triangle45": 45,
    "quad81": 81,
    "tetra165": 165,
    "wedge405": 405,
    "hexahedron729": 729,
    #
    "line10": 10,
    "triangle55": 55,
    "quad100": 100,
    "tetra220": 220,
    "wedge550": 550,
    "hexahedron1000": 1000,
    "hexahedron1331": 1331,
    #
    "line11": 11,
    "triangle66": 66,
    "quad121": 121,
    "tetra286": 286,
}


def _write_data(
    fields,
    mesh_name,
    profile,
    name,
    supp,
    data,
    med_type=None,
):
    # Skip for general ELGA fields defined at unknown Gauss points
    if supp == "ELGA":
        return

    # Field
    try:  # a same MED field may contain fields of different natures
        field = fields.create_group(name)
        field.attrs.create("MAI", np.string_(mesh_name))
        field.attrs.create("TYP", 6)  # MED_FLOAT64
        field.attrs.create("UNI", numpy_void_str)  # physical unit
        field.attrs.create("UNT", numpy_void_str)  # time unit
        n_components = 1 if data.ndim == 1 else data.shape[-1]
        field.attrs.create("NCO", n_components)  # number of components
        field.attrs.create("NOM", np.string_(_component_names(n_components)))

        # Time-step
        step = "0000000000000000000100000000000000000001"
        time_step = field.create_group(step)
        time_step.attrs.create("NDT", 1)  # time step 1
        time_step.attrs.create("NOR", 1)  # iteration step 1
        time_step.attrs.create("PDT", 0.0)  # current time
        time_step.attrs.create("RDT", -1)  # NDT of the mesh
        time_step.attrs.create("ROR", -1)  # NOR of the mesh

    except ValueError:  # name already exists
        field = fields[name]
        ts_name = list(field.keys())[-1]
        time_step = field[ts_name]

    # Field information
    if supp == "NOEU":
        typ = time_step.create_group("NOE")
    elif supp == "ELNO":
        typ = time_step.create_group("NOE." + med_type)
    else:  # 'ELEM' with only 1 Gauss points!
        typ = time_step.create_group("MAI." + med_type)

    typ.attrs.create("GAU", numpy_void_str)  # no associated Gauss points
    typ.attrs.create("PFL", np.string_(profile))
    profile = typ.create_group(profile)
    profile.attrs.create("NBR", len(data))  # number of data
    if supp == "ELNO":
        profile.attrs.create("NGA", data.shape[1])
    else:
        profile.attrs.create("NGA", 1)
    profile.attrs.create("GAU", numpy_void_str)

    # Dataset
    profile.create_dataset("CO", data=data.flatten(order="F"))


def _component_names(n_components):
    """
    To be correctly read in a MED viewer, each component must be a
    string of width 16. Since we do not know the physical nature of
    the data, we just use V1, V2, ...
    """
    return "".join(["V%-15d" % (i + 1) for i in range(n_components)])


def _family_name(set_id, name):
    """
    Return the FAM object name corresponding to
    the unique set id and a list of subset names
    """
    return "FAM" + "_" + str(set_id) + "_" + "_".join(name)


def _write_families(fm_group, tags):
    """
    Write point/cell tag information under FAS/[mesh_name]
    """
    for set_id, name in tags.items():
        family = fm_group.create_group(_family_name(set_id, name))
        family.attrs.create("NUM", set_id)
        group = family.create_group("GRO")
        group.attrs.create("NBR", len(name))  # number of subsets
        dataset = group.create_dataset("NOM", (len(name),), dtype="80int8")
        for i in range(len(name)):
            name_80 = name[i] + "\x00" * (80 - len(name[i]))  # make name 80 characters
            dataset[i] = [ord(x) for x in name_80]
