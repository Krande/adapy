import logging
from itertools import groupby
from operator import attrgetter

import numpy as np

from ada.config import Settings as _Settings
from ada.fem import ElemShapes

from ..utils import _folder_prep, get_fem_model_from_assembly
from .common import abaqus_to_med_type


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
    Write Code_Aster .med, .export and .comm file from Assembly data


    MED writer modified and based on meshio implementation..

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
    """
    print(f"creating: {name}")
    analysis_dir = _folder_prep(scratch_dir, name, overwrite)

    if "info" not in assembly.metadata:
        assembly.metadata["info"] = dict(description="")

    assembly.metadata["info"]["description"] = description

    p = get_fem_model_from_assembly(assembly)
    # TODO: Implement support for multiple parts. Need to understand how submeshes in Salome and Code Aster works.
    # for p in filter(lambda x: len(x.fem.elements) != 0, assembly.get_all_parts_in_assembly(True)):
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
    """

    :param name:
    :param a:
    :param p:
    :param analysis_dir:
    :return:
    """
    comm_str = "DEBUT(LANG='EN')\n\n"
    comm_str += "mesh=LIRE_MAILLAGE(FORMAT=\"MED\", UNITE=20, VERI_MAIL=_F(VERIF='OUI'))\n\n"
    comm_str += "model=AFFE_MODELE(AFFE=_F(MODELISATION=('3D', ),PHENOMENE='MECANIQUE',TOUT='OUI'),MAILLAGE=mesh)\n\n"
    # TODO: Add missing parameters here
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


def write_to_med(name, part, analysis_dir):
    """
    Custom Method for writing a part directly based on meshio

    :param name:
    :param part:
    :param analysis_dir:
    :type part: ada.Part
    :return:
    """
    import h5py

    filename = (analysis_dir / name).with_suffix(".med")
    mesh_name = name if name is not None else part.fem.name

    f = h5py.File(filename, "w")

    # Strangely the version must be 3.0.x
    # Any version >= 3.1.0 will NOT work with SALOME 8.3
    info = f.create_group("INFOS_GENERALES")
    info.attrs.create("MAJ", 3)
    info.attrs.create("MIN", 0)
    info.attrs.create("REL", 0)

    time_step = _write_mesh_presets(f, mesh_name)

    profile = "MED_NO_PROFILE_INTERNAL"

    # Node and Element sets (familles in French)
    fas = f.create_group("FAS")
    families = fas.create_group(mesh_name)
    family_zero = families.create_group("FAMILLE_ZERO")  # must be defined in any case
    family_zero.attrs.create("NUM", 0)

    # Make sure that all member references are updated (TODO: Evaluate if this can be avoided using a smarter algorithm)
    part.fem.sets.add_references()

    # Nodes and node sets
    _write_nodes(part, time_step, profile, families)

    # Elements (mailles in French) and element sets
    _write_elements(part, time_step, profile, families)


def _write_nodes(part, time_step, profile, families):
    """

    TODO: Go through each data group and set in HDF5 file and make sure that it writes what was read 1:1.
        Use cylinder.med as a benchmark.

    Add the following datasets ['COO', 'FAM', 'NUM'] to the 'NOE' group

    :param part:
    :param time_step:
    :param profile:
    :return:
    """
    points = np.zeros((int(part.fem.nodes.max_nid), 3))

    def pmap(n):
        points[int(n.id - 1)] = n.p

    list(map(pmap, part.fem.nodes))

    # Try this
    if _Settings.ca_experimental_id_numbering is True:
        points = np.array([n.p for n in part.fem.nodes])

    nodes_group = time_step.create_group("NOE")
    nodes_group.attrs.create("CGT", 1)
    nodes_group.attrs.create("CGS", 1)

    nodes_group.attrs.create("PFL", np.string_(profile))
    coo = nodes_group.create_dataset("COO", data=points.flatten(order="F"))
    coo.attrs.create("CGT", 1)
    coo.attrs.create("NBR", len(points))

    if _Settings.ca_experimental_id_numbering is True:
        node_ids = [n.id for n in part.fem.nodes]
        num = nodes_group.create_dataset("NUM", data=node_ids)
        num.attrs.create("CGT", 1)
        num.attrs.create("NBR", len(points))

    if len(part.fem.nsets.keys()) > 0:
        _add_node_sets(nodes_group, part, points, families)


def _write_elements(part, time_step, profile, families):
    """

    Add the following ['FAM', 'NOD', 'NUM'] to the 'MAI' group

    **NOD** requires 'CGT' and 'NBR' attrs

    :param part:
    :param time_step:
    :param profile:
    :param families:
    :return:
    """

    def get_node_ids_from_element(el_):
        return [int(n.id) for n in el_.nodes]

    elements_group = time_step.create_group("MAI")
    elements_group.attrs.create("CGT", 1)
    for group, elements in groupby(part.fem.elements, key=attrgetter("type")):
        if group in ElemShapes.masses + ElemShapes.springs:
            logging.error("NotImplemented: Skipping Mass or Spring Elements")
            continue
        med_type = abaqus_to_med_type(group)
        elements = list(elements)
        cells = np.array(list(map(get_node_ids_from_element, elements)))

        med_cells = elements_group.create_group(med_type)
        med_cells.attrs.create("CGT", 1)
        med_cells.attrs.create("CGS", 1)
        med_cells.attrs.create("PFL", np.string_(profile))

        nod = med_cells.create_dataset("NOD", data=cells.flatten(order="F"))
        nod.attrs.create("CGT", 1)
        nod.attrs.create("NBR", len(cells))

        # Node Numbering is necessary for proper handling of
        num = med_cells.create_dataset("NUM", data=[int(el.id) for el in elements])
        num.attrs.create("CGT", 1)
        num.attrs.create("NBR", len(cells))

    # Add Element sets
    if len(part.fem.elsets.keys()) > 0:
        _add_cell_sets(elements_group, part, families)


def _write_mesh_presets(f, mesh_name):
    """

    :param f:
    :param mesh_name:
    :return: Time step 0
    """
    numpy_void_str = np.string_("")
    dim = 3

    # Meshes
    mesh_ensemble = f.create_group("ENS_MAA")

    med_mesh = mesh_ensemble.create_group(mesh_name)
    med_mesh.attrs.create("DIM", dim)  # mesh dimension
    med_mesh.attrs.create("ESP", dim)  # spatial dimension
    med_mesh.attrs.create("REP", 0)  # cartesian coordinate system (repère in French)
    med_mesh.attrs.create("UNT", numpy_void_str)  # time unit
    med_mesh.attrs.create("UNI", numpy_void_str)  # spatial unit
    med_mesh.attrs.create("SRT", 1)  # sorting type MED_SORT_ITDT

    # component names:
    names = ["X", "Y", "Z"][:dim]
    med_mesh.attrs.create("NOM", np.string_("".join(f"{name:<16}" for name in names)))
    med_mesh.attrs.create("DES", np.string_("Mesh created with meshio"))
    med_mesh.attrs.create("TYP", 0)  # mesh type (MED_NON_STRUCTURE)

    # Time-step
    step = "-0000000000000000001-0000000000000000001"  # NDT NOR
    time_step = med_mesh.create_group(step)
    time_step.attrs.create("CGT", 1)
    time_step.attrs.create("NDT", -1)  # no time step (-1)
    time_step.attrs.create("NOR", -1)  # no iteration step (-1)
    time_step.attrs.create("PDT", -1.0)  # current time
    return time_step


def resolve_ids_in_multiple(tags, tags_data, is_elem):
    """
    Find elements shared by multiple sets

    :param tags:
    :param tags_data:
    :return:
    """
    from ada.fem import FemSet

    fin_data = dict()
    for t, memb in tags_data.items():
        fin_data[t] = []
        for mem in memb:
            refs = list(filter(lambda x: type(x) == FemSet, mem.refs))
            if len(refs) > 1:
                names = [r.name for r in refs]
                if names not in tags.values():
                    new_int = min(tags.keys()) - 1 if is_elem else max(tags.keys()) + 1
                    tags[new_int] = names
                    fin_data[new_int] = []
                else:
                    rmap = {tuple(v): r for r, v in tags.items()}
                    new_int = rmap[tuple(names)]
                if mem not in fin_data[new_int]:
                    fin_data[new_int].append(mem)
            else:
                fin_data[t].append(mem)
    to_be_removed = []
    for i, f in fin_data.items():
        if len(f) == 0:
            to_be_removed.append(i)
    for t in to_be_removed:
        fin_data.pop(t)
        tags.pop(t)
    return fin_data


def _add_cell_sets(cells_group, part, families):
    """

    :param cells_group:
    :param part:
    :param families:
    :type part: ada.Part
    """
    cell_id_num = -4

    element = families.create_group("ELEME")
    tags = dict()
    tags_data = dict()

    cell_id_current = cell_id_num
    for cell_set in part.fem.elsets.values():
        tags[cell_id_current] = [cell_set.name]
        tags_data[cell_id_current] = cell_set.members
        cell_id_current -= 1

    res_data = resolve_ids_in_multiple(tags, tags_data, True)

    def get_node_ids_from_element(el_):
        return [int(n.id - 1) for n in el_.nodes]

    for group, elements in groupby(part.fem.elements, key=attrgetter("type")):
        if group in ElemShapes.masses + ElemShapes.springs:
            logging.error("NotImplemented: Skipping Mass or Spring Elements")
            continue
        elements = list(elements)
        cell_ids = {el.id: i for i, el in enumerate(elements)}

        cell_data = np.zeros(len(elements), dtype=np.int32)

        for t, mem in res_data.items():
            list_filtered = [cell_ids[el.id] for el in filter(lambda x: x.type == group, mem)]
            for index in list_filtered:
                cell_data[index] = t

        cells = np.array(list(map(get_node_ids_from_element, elements)))
        med_type = abaqus_to_med_type(group)
        med_cells = cells_group.get(med_type)
        family = med_cells.create_dataset("FAM", data=cell_data)
        family.attrs.create("CGT", 1)
        family.attrs.create("NBR", len(cells))

    _write_families(element, tags)


def _add_node_sets(nodes_group, part, points, families):
    """
    :param nodes_group:
    :param part:
    :param families:
    :type part: ada.Part
    """
    tags = dict()

    # TODO: Simplify this function
    # tags_data = dict()
    # cell_id_current = 4
    # for cell_set in part.fem.nsets.values():
    #     tags[cell_id_current] = [cell_set.name]
    #     tags_data[cell_id_current] = cell_set.members
    #     cell_id_current += 1
    #
    # nmap = {n.id: i for i, n in enumerate(part.fem.nodes)}
    #
    # res_data = resolve_ids_in_multiple(tags, tags_data, False)
    # points = np.zeros(len(points), dtype=np.int32)
    #
    # for t, rd in res_data.items():
    #     for r in rd:
    #         points[nmap[r.id]] = t
    # #
    nsets = dict()
    for key, val in part.fem.nsets.items():
        nsets[key] = [int(p.id) for p in val]

    points = _set_to_tags(nsets, points, 2, tags)

    family = nodes_group.create_dataset("FAM", data=points)
    family.attrs.create("CGT", 1)
    family.attrs.create("NBR", len(points))

    # For point tags
    node = families.create_group("NOEUD")
    _write_families(node, tags)


def _resolve_element_in_use_by_other_set(tagged_data, ind, tags, name, is_elem):
    """


    :param tagged_data:
    :param ind:
    :param tags:
    :param name:
    :param is_elem:
    """
    existing_id = int(tagged_data[ind])
    current_tags = tags[existing_id]
    all_tags = current_tags + [name]

    if name in current_tags:
        raise ValueError("Unexpected error. Name already exists in set during resolving set members.")

    new_int = None
    for i_, t_ in tags.items():
        if all_tags == t_:
            new_int = i_
            break

    if new_int is None:
        new_int = int(min(tags.keys()) - 1) if is_elem else int(max(tags.keys()) + 1)
        tags[new_int] = tags[existing_id] + [name]

    tagged_data[ind] = new_int


def _set_to_tags(sets, data, tag_start_int, tags, id_map=None):
    """

    :param sets:
    :param data:
    :param tag_start_int:
    :param
    :return: The tagged data.
    """
    tagged_data = np.zeros(len(data), dtype=np.int32)
    tag_int = 0 + tag_start_int

    is_elem = False if tag_int > 0 else True

    tag_int = tag_start_int
    tag_map = dict()
    # Generate basic tags upfront
    for name in sets.keys():
        tags[tag_int] = [name]
        tag_map[name] = tag_int
        if is_elem is True:
            tag_int -= 1
        else:
            tag_int += 1

    for name, set_data in sets.items():
        if len(set_data) == 0:
            continue

        for index_ in set_data:
            index = int(index_ - 1)

            if id_map is not None:
                index = id_map[index_]

            if index > len(tagged_data) - 1:
                raise IndexError()

            if tagged_data[index] != 0:  # id is already defined in another set
                _resolve_element_in_use_by_other_set(tagged_data, index, tags, name, is_elem)
            else:
                tagged_data[index] = tag_map[name]

    return tagged_data


def _family_name(set_id, name):
    """Return the FAM object name corresponding to the unique set id and a list of
    subset names
    """
    return "FAM" + "_" + str(set_id) + "_" + "_".join(name)


def _write_families(fm_group, tags):
    """Write point/cell tag information under FAS/[mesh_name]"""
    for set_id, name in tags.items():
        family = fm_group.create_group(_family_name(set_id, name))
        family.attrs.create("NUM", set_id)
        group = family.create_group("GRO")
        group.attrs.create("NBR", len(name))  # number of subsets
        dataset = group.create_dataset("NOM", (len(name),), dtype="80int8")
        for i in range(len(name)):
            # make name 80 characters
            name_80 = name[i] + "\x00" * (80 - len(name[i]))
            # Needs numpy array, see <https://github.com/h5py/h5py/issues/1735>
            dataset[i] = np.array([ord(x) for x in name_80])
