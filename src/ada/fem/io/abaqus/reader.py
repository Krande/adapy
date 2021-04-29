import logging
import mmap
import os
import pathlib
import re
from itertools import chain

import numpy as np

from ada.core.containers import Nodes
from ada.core.utils import Counter, roundoff
from ada.fem import (
    FEM,
    Bc,
    Connector,
    ConnectorSection,
    Constraint,
    Csys,
    Elem,
    ElemShapes,
    FemSection,
    FemSet,
    Interaction,
    InteractionProperty,
    Mass,
    PredefinedField,
    Surface,
)
from ada.fem.containers import FemElements, FemSections, FemSets
from ada.fem.io.abaqus.common import AbaCards
from ada.materials.metals import CarbonSteel

from ..utils import str_to_int

part_name_counter = Counter(1, "Part")
_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL


def read_fem(assembly, fem_file, fem_name=None):
    """
    This will create and add an AbaqusPart object based on a path reference to a Abaqus input file.

    :param assembly: Assembly object
    :param fem_file: Absolute or relative path to Abaqus *.inp file. See below for further explanation
    :param fem_name: Currently not in use.
    :type assembly: ada.Assembly
    """
    print("Starting import of Abaqus input file")

    if fem_name is not None:
        global part_name_counter
        part_name_counter = Counter(1, fem_name)

    bulk_str = read_bulk_w_includes(fem_file)
    lbulk = bulk_str.lower()
    ass_start = lbulk.find("\n*assembly")
    ass_end = lbulk.rfind("\n*end assembly")
    step_start = lbulk.rfind("\n*step")

    ass_start = ass_start + 1 if ass_start != -1 else ass_start
    ass_end = ass_end + 1 if ass_end != -1 else ass_end
    step_start = step_start + 1 if step_start != -1 else step_start

    if ass_start == -1 and ass_end == -1:
        uses_assembly_parts = False
        assembly_str = bulk_str
        props_str = bulk_str
    else:
        uses_assembly_parts = True
        assembly_str = bulk_str[ass_start : ass_end + 2]
        props_str = bulk_str[ass_end + 2 : step_start]

    inst_end = assembly_str.lower().rfind("\n*end instance")
    inst_end = inst_end + 15 if inst_end != -1 else inst_end
    get_materials_from_bulk(assembly, props_str)
    get_intprop_from_lines(assembly, props_str)

    inst_matches = re.compile(r"\*Instance, name=(.*?), part=(.*?)\n(.*?)\*End Instance", _re_in)
    ass_data = {}
    for ad in map(get_instance_data, inst_matches.finditer(assembly_str[:inst_end])):
        if ad[0] not in ass_data.keys():
            ass_data[ad[0]] = []
        ass_data[ad[0]].append(ad[1:])

    import_parts(assembly, bulk_str[:ass_start], ass_data)

    ass_sets = assembly_str[inst_end:]

    ass_fem = assembly.fem

    if uses_assembly_parts is True:
        assembly.fem._nodes += get_nodes_from_inp(ass_sets, ass_fem)
        assembly.fem.lcsys.update(get_lcsys_from_bulk(ass_sets, ass_fem))
        assembly.fem._elements += get_elem_from_inp(ass_sets, ass_fem)
        assembly.fem._sets += get_sets_from_bulk(ass_sets, ass_fem)
        assembly.fem.sets.link_data()
        assembly.fem.surfaces.update(get_surfaces_from_bulk(ass_sets, ass_fem))
        assembly.fem._constraints += get_constraints_from_inp(ass_sets, ass_fem)
        assembly.fem.connector_sections.update(get_connector_sections_from_bulk(props_str, ass_fem))
        assembly.fem.connectors.update(get_connectors_from_inp(ass_sets, ass_fem))
        assembly.fem._bcs += get_bcs_from_bulk(props_str, ass_fem)

    get_interactions_from_bulk_str(props_str, assembly)
    get_initial_conditions_from_lines(assembly, props_str)


def read_bulk_w_includes(inp_path):
    """

    :param inp_path: Absolute path to input file
    :return: bulk str
    """

    re_bulk_include = re.compile(r"\*Include,\s*input=(.*?)$", _re_in)
    bulk_repl = dict()
    with open(inp_path, "r") as inpDeck:
        bulk_str = inpDeck.read()
        for m in re_bulk_include.finditer(bulk_str):
            search_key = m.group(0)
            with open(os.path.join(os.path.dirname(inp_path), m.group(1)), "r") as d:
                bulk_repl[search_key] = d.read()

    for key, val in bulk_repl.items():
        bulk_str = bulk_str.replace(key, val)
    return bulk_str


def import_bulk(file_path, buffer_function):
    with open(file_path, "r") as f:
        return buffer_function(f.read())


def import_bulk2(file_path, buffer_function):
    with open(file_path, "r") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
            return buffer_function(m)


def import_parts(assembly, bulk_str, ass_data):
    """
    Import parts from an assembly

    :param assembly:
    :param bulk_str:
    :param ass_data:
    :type assembly: ada.Assembly
    """

    parts_matches = re.compile(r"\*Part, name=(.*?)\n(.*?)\*End Part", _re_in)
    part_names = re.compile(r"\*\*\s*PART INSTANCE:\s*(.*?)\n(.*)", _re_in)

    def grab_instance_data(name):
        if name in ass_data:
            return ass_data[name]
        else:
            return None, None, None

    def get_part(m):
        name = m.group(1)
        part_bulk_str = m.group(2)
        instances = grab_instance_data(name)
        parts = []
        for instance in instances:
            inst_name, inst_bulk, inst_metadata = instance
            part_bulk_str = inst_bulk if part_bulk_str == "" and inst_bulk != "" else part_bulk_str
            part = get_part_from_bulk_str(name, part_bulk_str, assembly, inst_name, inst_metadata)
            parts.append(part)
        return parts

    def get_part_without_assembly():
        part_name_matches = list(part_names.finditer(bulk_str))
        p_nmatch = tuple(part_name_matches)

        if len(p_nmatch) != 1:
            p_bulk = bulk_str
            p_name = None
        else:
            p_name = p_nmatch[0].group(1)
            p_bulk = p_nmatch[0].group(2)

        p_name = next(part_name_counter) if p_name is None else p_name
        return get_part_from_bulk_str(p_name, p_bulk, assembly)

    # import concurrent.futures
    # with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    #     futures = []
    #     for number in parts_matches.finditer(bulk_str):
    #         future = executor.submit(get_part, number)
    #         futures.append(future)
    #
    #     if len(futures) == 0:
    #         future = executor.submit(get_part_without_assembly)
    #         futures.append(future)
    #
    #     for future in concurrent.futures.as_completed(futures):
    #         p = future.result()
    #         self.assembly.add_part(p)
    #         print(8 * '-' + f'Imported "{p.fem.instance_name}"')
    #
    new_parts = 0
    from itertools import count

    ncounter = count(1)
    part_list = list(chain.from_iterable(map(get_part, parts_matches.finditer(bulk_str))))
    for p in part_list:
        if p.name is None:
            p.name = f"Part{next(ncounter)}"
        if p.name in assembly.parts.keys():
            p.name = p.fem.name
        assembly.add_part(p)
        new_parts += 1
        print(8 * "-" + f'Imported "{p.fem.instance_name}"')
    if new_parts == 0:
        p = get_part_without_assembly()
        if p.name is None:
            p.name = f"Part{next(ncounter)}"
        assembly.add_part(p)
        print(8 * "-" + f'Imported "{p.fem.instance_name}"')


def get_part_from_bulk_str(name, bulk_str, parent, instance_name=None, metadata=None):
    from ada import Part

    metadata = dict(move=None, rotate=None) if metadata is None else metadata
    instance_name = name if instance_name is None else instance_name
    if instance_name is None:
        instance_name = "Temp"
    fem = FEM(name=instance_name, metadata=metadata)
    part = Part(name, fem=fem, parent=parent)
    fem._nodes = get_nodes_from_inp(bulk_str, fem)
    fem.nodes.move(move=fem.metadata["move"], rotate=fem.metadata["rotate"])
    fem._elements = get_elem_from_inp(bulk_str, fem)
    fem.elements.build_sets()
    fem._sets = fem.sets + get_sets_from_bulk(bulk_str, fem)
    fem._sections = get_sections_from_inp(bulk_str, fem)
    fem._bcs += get_bcs_from_bulk(bulk_str, fem)
    fem._masses = get_mass_from_bulk(bulk_str, fem)
    fem.surfaces.update(get_surfaces_from_bulk(bulk_str, fem))
    fem._lcsys = get_lcsys_from_bulk(bulk_str, fem)
    fem._constraints = get_constraints_from_inp(bulk_str, fem)

    return part


def get_initial_conditions_from_lines(assembly, bulk_str):
    """
    TODO: Optimize this function


    ** PREDEFINED FIELDS
    **
    ** Name: IC-1   Type: Velocity
    *Initial Conditions, type=VELOCITY
    CONTAINER20FT-10000KG, 1, 0.
    CONTAINER20FT-10000KG, 2, 0.
    CONTAINER20FT-10000KG, 3, 4.1
    """
    if bulk_str.find("*Initial Conditions") == -1:
        return
    re_str = r"(?:\*\*\s*Name:\s*(.*?)\s*Type:\s*(.*?)\n)+\*Initial Conditions, type=.*?\n(?<=)((?:.*?)(?=\*|\Z))"

    def sort_props(line):
        ev = [x.strip() for x in line.split(",")]
        set_name = ev[0]
        try:
            dofs = int(ev[1])
        except BaseException as e:
            logging.debug(e)
            dofs = ev[1]
        if len(ev) > 2:
            magn = ev[2]
        else:
            magn = None
        return set_name, dofs, magn

    def grab_init_props(m):
        bc_name = m.group(1)
        bc_type = m.group(2)
        bcs = m.group(3)
        props = [sort_props(line) for line in bcs.splitlines()]
        set_name, dofs, magn = list(zip(*props))
        fem_set = None
        set_name_up = set_name[0]
        if set_name_up in assembly.fem.elsets.keys():
            fem_set = assembly.fem.elsets[set_name_up]
        elif set_name_up in assembly.fem.nsets.keys():
            fem_set = assembly.fem.nsets[set_name_up]
        else:
            for p in assembly.get_all_parts_in_assembly():
                if set_name_up in p.fem.elsets.keys():
                    fem_set = p.fem.elsets[set_name_up]
        if fem_set is None:
            raise ValueError(f'Unable to find fem set "{set_name[0]}"')

        return PredefinedField(bc_name, bc_type, fem_set, dofs, magn, parent=assembly.fem)

    for match in re.finditer(re_str, bulk_str, _re_in):
        assembly.fem.add_predefined_field(grab_init_props(match))


def get_materials_from_bulk(assembly, bulk_str):
    re_str = (
        r"(\*Material,\s*name=.*?)(?=\*|\Z)(?!\*Elastic|\*Density|\*Plastic|"
        r"\*Damage Initiation|\*Damage Evolution|\*Expansion)"
    )
    re_materials = re.compile(re_str, _re_in)
    for m in re_materials.finditer(bulk_str):
        mat = mat_str_to_mat_obj(m.group())
        assembly.add_material(mat)


def get_intprop_from_lines(assembly, bulk_str):
    """
    *Surface Interaction, name=contactProp
    *Friction
    0.,
    *Surface Behavior, pressure-overclosure=HARD
    """
    re_str = r"(\*Surface Interaction, name=.*?)(?=\*)(?!\*Friction|\*Surface Behavior|\*Surface Smoothing)"
    # surf_interact = AbaFF("Surface Interaction", [("name=",), ("bulk>",)], [("Friction", [(), ("bulkFRIC>",)])])

    assembly.fem.metadata["surf_smoothing"] = []
    for m in AbaCards.surface_smoothing.regex.finditer(bulk_str):
        d = m.groupdict()
        assembly.fem.metadata["surf_smoothing"].append(d)

    for m in re.findall(re_str, bulk_str, _re_in):
        name = re.search(r"name=(.*?)\n", m, _re_in).group(1)
        re_fric = re.search(r"\*Friction\n(.*?),", m, _re_in)
        fric = None
        if re_fric is not None:
            fric = re_fric.group(1)
        props = dict(name=name, friction=fric)
        res = re.search(
            r"\*Surface Behavior,\s*(?P<btype>.*?)(?:=(?P<behaviour>.*?)(?:$\n(?P<tabular>.*?)(?:\*|\Z)|$)|$)",
            m,
            _re_in,
        )
        if res is not None:
            res = res.groupdict()
            btype = res["btype"]
            behave = res["behaviour"] if btype.upper() != "PENALTY" else btype
            tabular = res["tabular"]
            if tabular is not None:
                tab_list = []
                for line in tabular.splitlines():
                    tab_list.append(tuple(np.fromstring(line, dtype=float, sep=",")))
                tabular = tab_list
                props.update(dict(pressure_overclosure=behave, tabular=tabular))
        assembly.fem.add_interaction_property(InteractionProperty(**props))


def get_instance_data(m):
    """

    Move/rotate data lines are specified here:

    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-instance.htm

    """

    move_rot = re.compile(r"(?:^\s*(.*?),\s*(.*?),\s*(.*?)$)", _re_in)
    metadata = dict(move=None, rotate=None)
    inst_name = m.group(1)
    p_ref = m.group(2)
    inst_bulk = m.group(3)
    move = None
    rotate = None
    mr = move_rot.finditer(inst_bulk)
    if mr is not None:
        for j, mo in enumerate(mr):
            content = mo.group(0)
            if "*" in content or j == 2 or content == "":
                break
            if j == 0:
                move = [float(mo.group(1)), float(mo.group(2)), float(mo.group(3))]
            if j == 1:
                r = [float(x) for x in mo.group(3).split(",")]
                rotate = [
                    float(mo.group(1)),
                    float(mo.group(2)),
                    r[0],
                    r[1],
                    r[2],
                    r[3],
                    r[4],
                ]
            if move is not None:
                metadata["move"] = tuple(move)
            if rotate is not None:
                metadata["rotate"] = (
                    tuple(rotate[:3]),
                    tuple(rotate[3:-1]),
                    rotate[-1],
                )
    return p_ref, inst_name, inst_bulk, metadata


def mat_str_to_mat_obj(mat_str):
    """
    Converts a Abaqus materials str into a ADA Materials object

    :param mat_str:
    :return:
    """
    from ada import Material

    rd = roundoff

    # Name
    name = re.search(r"name=(.*?)\n", mat_str, _re_in).group(1).split("=")[-1].strip()

    # Density
    density_ = re.search(r"\*Density\n(.*?)(?:,|$)", mat_str, _re_in)
    if density_ is not None:
        density = rd(density_.group(1).strip().split(",")[0].strip(), 10)
    else:
        print('No density flag found for material "{}"'.format(name))
        density = None

    # Elastic
    re_elastic_ = re.search(r"\*Elastic(?:,\s*type=(.*?)|)\n(.*?)(?:\*|$)", mat_str, _re_in)
    if re_elastic_ is not None:
        re_elastic = re_elastic_.group(2).strip().split(",")
        young, poisson = rd(re_elastic[0]), rd(re_elastic[1])
    else:
        print('No Elastic properties found for material "{name}"'.format(name=name))
        young, poisson = None, None

    # Plastic
    re_plastic_ = re.search(r"\*Plastic\n(.*?)(?:\*|\Z)", mat_str, _re_in)
    if re_plastic_ is not None:
        re_plastic = [tuple(x.split(",")) for x in re_plastic_.group(1).strip().splitlines()]
        sig_p = [rd(x[0]) for x in re_plastic]
        eps_p = [rd(x[1]) for x in re_plastic]
    else:
        eps_p, sig_p = None, None

    # Expansion
    re_zeta = re.search(r"\*Expansion(?:,\s*type=(.*?)|)\n(.*?)(?:\*|$)", mat_str, _re_in)
    if re_zeta is not None:
        zeta = float(re_zeta.group(2).split(",")[0].strip())
    else:
        zeta = 0.0

    # Return material object
    model = CarbonSteel(
        rho=density,
        E=young,
        v=poisson,
        eps_p=eps_p,
        zeta=zeta,
        sig_p=sig_p,
        plasticity_model=None,
    )
    return Material(name=name, mat_model=model)


def import_multiple_inps(input_files_dir):
    """
    Import a set of inp files from a folder

    :param input_files_dir:
    """

    def read_inp(fname):
        if ".inp" not in fname:
            return None
        else:
            with open(pathlib.Path(input_files_dir) / fname, "r") as d:
                return d.read()

    return "".join([x for x in map(read_inp, os.listdir(input_files_dir)) if x is not None])


def get_nodes_from_inp(bulk_str, parent):
    """
    Extract node information from abaqus input file string

    """
    from ada import Node

    re_no = re.compile(
        r"^\*Node\s*(?:,\s*nset=(?P<nset>.*?)\n|\n)(?P<members>(?:.*?)(?=\*|\Z))",
        _re_in,
    )

    def getnodes(m):
        d = m.groupdict()
        res = np.fromstring(list_cleanup(d["members"]), sep=",", dtype=np.float64)
        res_ = res.reshape(int(res.size / 4), 4)
        members = [Node(n[1:4], int(n[0]), parent=parent) for n in res_]
        if d["nset"] is not None:
            parent.sets.add(FemSet(d["nset"], members, "nset", parent=parent))
        return members

    nodes = list(chain.from_iterable(map(getnodes, re_no.finditer(bulk_str))))

    return Nodes(
        nodes,
        parent=parent,
    )


def get_elem_from_inp(bulk_str, fem):
    """
    Extract elements from abaqus input file

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM

    :return:
    :rtype: ada.fem.containers.FemElements
    """

    from ada import Node, Part

    re_el = re.compile(
        r"^\*Element,\s*type=(?P<eltype>.*?)(?:\n|,\s*elset=(?P<elset>.*?)\s*\n)(?<=)(?P<members>(?:.*?)(?=\*|\Z))",
        _re_in,
    )

    def grab_elements(match):
        d = match.groupdict()
        eltype = d["eltype"]
        elset = d["elset"]
        members = d["members"]
        res = re.search("[a-zA-Z]", members)
        if eltype.upper() in ElemShapes.cube20 + ElemShapes.cube27 or res is None:
            if eltype.upper() in ElemShapes.cube20 + ElemShapes.cube27:
                temp = members.splitlines()
                ntext = "".join([l1.strip() + "    " + l2.strip() + "\n" for l1, l2 in zip(temp[:-1:2], temp[1::2])])
            else:
                ntext = d["members"]
            res = np.fromstring(ntext.replace("\n", ","), sep=",", dtype=int)
            n = ElemShapes.num_nodes(eltype) + 1
            res_ = res.reshape(int(res.size / n), n)
            return [Elem(e[0], [fem.nodes.from_id(n) for n in e[1:]], eltype, elset, parent=fem) for e in res_]
        else:

            # TODO: This code needs to be re-worked!
            elems = []
            for li in members.splitlines():
                new_mem = []
                temp = li.split(",")
                elid = str_to_int(temp[0])
                for d in temp[1:]:
                    temp2 = [x.strip() for x in d.split(".")]
                    par_ = None
                    if len(temp2) == 2:
                        par, setr = temp2
                        pfems = []
                        parents = fem.parent.get_all_parts_in_assembly()
                        for p in parents:
                            assert isinstance(p, Part)
                            pfems.append(p.fem.name)
                            if p.fem.name == par:
                                par_ = p
                                break
                        if par_ is None:
                            raise ValueError(f'Unable to find parent for "{par}"')
                        r = par_.fem.nodes.from_id(str_to_int(setr))
                        if type(r) != Node:
                            raise ValueError("Node ID not found")
                        new_mem.append(r)
                    else:
                        r = fem.nodes.from_id(str_to_int(d))
                        if type(r) != Node:
                            raise ValueError("Node ID not found")
                        new_mem.append(r)
                elems.append(Elem(elid, new_mem, eltype, elset, parent=fem))
            return elems

    return FemElements(
        chain.from_iterable(map(grab_elements, re_el.finditer(bulk_str))),
        fem_obj=fem,
    )


def get_sections_from_inp(bulk_str, fem):
    """

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    :return:
    """
    iter_beams = get_beam_sections_from_inp(bulk_str, fem)
    iter_shell = get_shell_sections_from_inp(bulk_str, fem)
    iter_solid = get_solid_sections_from_inp(bulk_str, fem)

    return FemSections(chain.from_iterable([iter_beams, iter_shell, iter_solid]), fem)


def get_sets_from_bulk(bulk_str, fem):
    """

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    :return: Collection of sets
    :rtype: FemSets
    """
    from ada import Assembly

    re_sets = re.compile(
        r"(?:\*(nset|elset),\s*(?:nset|elset))=(.*?)(?:,\s*(internal\s*)|(?:))"
        r"(?:,\s*instance=(.*?)|(?:))(?:,\s*(generate)|(?:))\s*\n(?<=)((?:.*?)(?=\*|\Z))",
        _re_in,
    )

    all_parts = fem.parent.get_all_parts_in_assembly()

    def str_to_ints(instr):
        try:
            return [int(x.strip()) for l in instr.splitlines() for x in l.split(",") if x.strip() != ""]
        except ValueError:
            return [str(x.strip()) for l in instr.splitlines() for x in l.split(",") if x.strip() != ""]

    def get_parent_instance(instance):
        """

        :rtype: ada.fem.FEM
        """
        if instance is None:
            return fem
        elif instance is not None and type(fem.parent) is Assembly:
            for p in filter(lambda x: x.fem.instance_name == instance, all_parts):
                return p.fem
        else:
            raise ValueError(f'Unable to find instance "{instance}" amongst assembly parts')

    def get_set(match):
        name = match.group(2)
        set_type = match.group(1)
        internal = True if match.group(3) is not None else False
        instance = match.group(4)
        generate = True if match.group(5) is not None else False
        members_str = match.group(6)
        gen_mem = str_to_ints(members_str) if generate is True else []
        members = [] if generate is True else str_to_ints(members_str)
        metadata = dict(instance=instance, internal=internal, generate=generate, gen_mem=gen_mem)

        parent_instance = get_parent_instance(instance)

        fem_set = FemSet(
            name,
            members,
            set_type=set_type,
            metadata=metadata,
            parent=parent_instance,
        )
        return fem_set

    return FemSets(list(map(get_set, re_sets.finditer(bulk_str))), fem)

    # import concurrent.futures
    # with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    #     futures = []
    #     for number in re_sets.finditer(bulk_str):
    #         future = executor.submit(get_set, number)
    #         futures.append(future)
    #     sets = [future.result() for future in concurrent.futures.as_completed(futures)]
    #
    # return FemSetsCollection(sets, parent)


def get_bcs_from_bulk(bulk_str, fem):
    """

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    :return:
    """

    bc_counter = Counter(1, "bc")

    re_bcs = re.compile(
        r"(?:\*\*\s*Name:\s*(?P<name>.*?)\s*Type:\s*(?P<type>.*?)\n|)"
        r"\*Boundary\n(?<=)(?P<content>(?:.*?)(?=\*|\Z))",
        _re_in,
    )

    def get_dofs(content):
        set_name = None
        dofs = []
        magn = []
        if content.count("\n") == 1:
            temp = content.split(",")
            set_name = temp[0].strip()
            dof_in = temp[1].strip().replace("\n", "")
            if dof_in.lower() == "encastre":
                dofs = dof_in
            else:
                dof = str_to_int(temp[1])
                if len(temp) == 2:
                    dofs = [x if dof == x else None for x in range(1, 7)]
                else:
                    dof_end = str_to_int(temp[2])
                    dofs = [x if dof <= x <= dof_end else None for x in range(1, 7)]

        else:
            for line in content.splitlines():
                ev = [x.strip() for x in line.split(",")]
                set_name = ev[0]
                try:
                    dofs.append(int(ev[1]))
                except BaseException as e:
                    logging.debug(e)
                    dofs.append(ev[1])
                if len(ev) > 3:
                    magn.append(ev[2])
        magn = None if len(magn) == 0 else magn
        return set_name, dofs, magn

    def get_nset(part_instance_name, set_name):
        for p in fem.parent.parts.values():
            if p.fem.instance_name == part_instance_name:
                return p.fem.sets.get_nset_from_name(set_name)

    def get_bc(match):
        d = match.groupdict()
        bc_name = d["name"] if d["name"] is not None else next(bc_counter)
        bc_type = d["type"]
        set_name, dofs, magn = get_dofs(d["content"])

        if "." in set_name:
            part_instance_name, set_name = set_name.split(".")
            fem_set = get_nset(part_instance_name, set_name)
        else:
            if set_name in fem.nsets.keys():
                fem_set = fem.sets.get_nset_from_name(set_name)
            else:
                val = str_to_int(set_name)
                if val in fem.nodes.dmap.keys():
                    node = fem.nodes.from_id(val)
                    fem_set = FemSet(bc_name + "_set", [node], "nset", parent=fem)
                else:
                    raise ValueError(f'Unable to find set "{set_name}" in part {fem}')

        if fem_set is None:
            raise Exception("Unable to Find node set")

        props = dict()
        if bc_type is not None:
            props["bc_type"] = bc_type
        if magn is not None:
            props["magnitudes"] = magn

        return Bc(bc_name, fem_set, dofs, parent=fem, **props)

    return [get_bc(match_in) for match_in in re_bcs.finditer(bulk_str)]


def get_mass_from_bulk(bulk_str, parent):
    """

    *MASS,ELSET=MASS3001
    2.00000000E+03,

    :return:
    """

    re_masses = re.compile(
        r"\*(?P<mass_type>Nonstructural Mass|Mass|Rotary Inertia),\s*elset=(?P<elset>.*?)"
        r"(?:,\s*type=(?P<ptype>.*?)\s*|\s*)(?:, units=(?P<units>.*?)|\s*)\n\s*(?P<mass>.*?)$",
        _re_in,
    )

    def map_element(el_set, mass_prop):
        """

        :param el_set:
        :param mass_prop:
        :type el_set: ada.fem.FemSet
        """
        elem = el_set.members[0]
        elem.mass_prop = mass_prop

    def get_mass(match):
        d = match.groupdict()
        elset = parent.sets.get_elset_from_name(d["elset"])
        mass_type = d["mass_type"]
        p_type = d["ptype"]
        mass = [str_to_int(x.strip()) for x in d["mass"].split(",") if x.strip() != ""]
        units = d["units"]
        mass = Mass(d["elset"], elset, mass, mass_type, p_type, units, parent=parent)
        map_element(elset, mass)
        return mass

    return {m.name: m for m in map(get_mass, re_masses.finditer(bulk_str))}


def get_surfaces_from_bulk(bulk_str, parent):
    """

    *Surface, type=ELEMENT, name=btn_surf
    _btn_surf_SPOS, SPOS

    :return:
    """

    def interpret_member(mem):
        msplit = mem.split(",")
        try:
            ref = str_to_int(msplit[0])
        except BaseException as e:
            logging.debug(e)
            ref = msplit[0].strip()

        return tuple([ref, msplit[1].strip()])

    surf_d = dict()
    for m in AbaCards.surface.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["name"].strip()
        surf_type = d["type"] if d["type"] is not None else "ELEMENT"
        members_str = d["bulk"]
        if members_str.count("\n") >= 1:
            id_refs = [interpret_member(m) for m in members_str.splitlines()]
            set_ref, set_id_ref = None, None
        else:
            id_refs = None
            res = [x.strip() for x in members_str.split(",")]
            if len(res) == 2:
                set_ref, set_id_ref = res
            else:
                set_ref = res[0]
                set_id_ref = 1.0

        if id_refs is None:
            if surf_type == "NODE":
                if set_id_ref == "":
                    fem_set = FemSet(f"n{set_ref}_set", [int(set_ref)], "nset")
                    parent.add_set(fem_set)
                    weight_factor = 1.0
                else:
                    if "." in set_ref:
                        ssplit = set_ref.split(".")
                        parent_ = None
                        fem_set = None
                        for prt in parent.parent.get_all_parts_in_assembly():
                            if prt.fem.name == ssplit[0]:
                                parent_ = prt.fem
                                fem_set = prt.fem.nsets[ssplit[1]]
                                break
                        if parent_ is None:
                            raise ValueError(f'Unable to find parent FEM "{ssplit[0]}"')
                    else:
                        fem_set = parent.nsets[set_ref]
                    weight_factor = float(set_id_ref)
                face_id_label = None
            else:
                weight_factor = None
                face_id_label = set_id_ref
                fem_set = parent.sets.get_elset_from_name(set_ref)
        else:
            fem_set = None
            weight_factor = None
            face_id_label = None
        surf_d[name] = Surface(
            name,
            surf_type,
            fem_set,
            weight_factor,
            face_id_label,
            id_refs,
            parent=parent,
        )

    return surf_d


def get_lcsys_from_bulk(bulk_str, parent):
    """
    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-orientation.htm#simakey-r-orientation


    :param bulk_str:
    :param parent:
    :return:
    """
    # re_lcsys = re.compile(
    #     r"^\*Orientation(:?,\s*definition=(?P<definition>.*?)|)(?:,\s*system=(?P<system>.*?)|)\s*name=(?P<name>.*?)\n(?P<content>.*?)$",
    #     _re_in,
    # )

    lcsysd = dict()
    for m in AbaCards.orientation.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["name"].replace('"', "")
        defi = d["definition"] if d["definition"] is not None else "COORDINATES"
        system = d["system"] if d["system"] is not None else "RECTANGULAR"
        if defi.upper() == "COORDINATES":
            coords = [
                (float(d["ax"]), float(d["ay"]), float(d["az"])),
                (float(d["bx"]), float(d["by"]), float(d["bz"])),
            ]
            if d["cx"] is not None:
                coords += [(float(d["cx"]), float(d["cy"]), float(d["cz"]))]
            lcsysd[name] = Csys(name, system=system, coords=coords, parent=parent)
        else:
            raise NotImplementedError(f'Orientation definition "{defi}" is not yet supported')

    return lcsysd


def get_constraints_from_inp(bulk_str, fem):
    """

    ** Constraint: Container_RigidBody
    *Rigid Body, ref node=container_rp, elset=container

    *MPC
     BEAM,    2007,     161
     BEAM,    2008,     162

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    """

    # Rigid Bodies

    constraints = []
    rbnames = Counter(1, "rgb")
    conames = Counter(1, "co")

    for m in AbaCards.tie.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["name"]
        msurf = grab_set_from_assembly(d["surf1"], fem, "surface")
        ssurf = grab_set_from_assembly(d["surf2"], fem, "surface")
        constraints.append(Constraint(name, "tie", msurf, ssurf, metadata=dict(adjust=d["adjust"])))

    for m in AbaCards.rigid_bodies.regex.finditer(bulk_str):
        d = m.groupdict()
        name = next(rbnames)
        ref_node = grab_set_from_assembly(d["ref_node"], fem, "nset")
        elset = grab_set_from_assembly(d["elset"], fem, "elset")
        constraints.append(Constraint(name, "rigid body", ref_node, elset, parent=fem))

    couplings = []
    for m in AbaCards.coupling.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["constraint_name"]
        rn = d["ref_node"].strip()
        sf = d["surface"].strip()
        if rn.isnumeric():
            ref_set = FemSet(next(conames), [int(rn)], "nset", parent=fem)
            fem.sets.add(ref_set)
        else:
            ref_set = fem.nsets[rn]

        surf = fem.surfaces[sf]

        res = np.fromstring(list_cleanup(d["bulk"]), sep=",", dtype=int)
        size = res.size
        cols = 2
        rows = int(size / cols)
        dofs = res.reshape(rows, cols)

        csys_name = d.get("orientation", None)
        if csys_name is not None:
            if csys_name not in fem.lcsys.keys():
                raise ValueError(f'Csys "{csys_name}" was not found on part {fem}')
            csys = fem.lcsys[csys_name]
        else:
            csys = None

        couplings.append(Constraint(name, "coupling", ref_set, surf, csys=csys, dofs=dofs, parent=fem))

    # Shell to Solid Couplings
    sh2solids = []
    for m in AbaCards.sh2so_re.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["constraint_name"]
        surf1 = grab_set_from_assembly(d["surf1"], fem, "surface")
        surf2 = grab_set_from_assembly(d["surf2"], fem, "surface")
        sh2solids.append(Constraint(name, "shell2solid", surf1, surf2))

    # MPC's
    mpc_dict = dict()
    mpc_re = re.compile(r"\*mpc\n(?P<bulk>.*?)^\*", _re_in)
    mpc_content = re.compile(r"\s*(?P<mpc_type>.*?),\s*(?P<master>.*?),\s*(?P<slave>.*?)$", _re_in)
    for match in mpc_re.finditer(bulk_str):
        d1 = match.groupdict()
        for subm in mpc_content.finditer(d1["bulk"]):
            d = subm.groupdict()
            mpc_type = d["mpc_type"]
            m = d["master"]
            s = d["slave"]
            if mpc_type not in mpc_dict.keys():
                mpc_dict[mpc_type] = []
            try:
                n1_ = str_to_int(m)
            except BaseException as e:
                logging.debug(e)
                n1_ = grab_set_from_assembly(m, fem, "nset")

            try:
                n2_ = str_to_int(s)
            except BaseException as e:
                logging.debug(e)
                n2_ = grab_set_from_assembly(s, fem, "nset")

            mpc_dict[mpc_type].append((n1_, n2_))

    def get_mpc(mpc_values):
        m_set, s_set = zip(*mpc_values)
        mpc_name = mpc_type + "_mpc"
        mset = FemSet("mpc_" + mpc_type + "_m", m_set, "nset")
        sset = FemSet("mpc_" + mpc_type + "_s", s_set, "nset")
        return Constraint(mpc_name, "mpc", mset, sset, mpc_type=mpc_type, parent=fem)

    mpcs = [get_mpc(mpc_values_in) for mpc_values_in in mpc_dict.values()]

    return list(chain.from_iterable([constraints, couplings, sh2solids, mpcs]))


def get_connector_sections_from_bulk(bulk_str, parent):
    """

    :param bulk_str:
    :param parent:
    :return:
    """

    consecsd = dict()

    for m in AbaCards.connector_behaviour.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["name"]
        comp = int(d["component"])

        res = np.fromstring(list_cleanup(d["bulk"]), sep=",", dtype=np.float64)
        size = res.size
        cols = comp + 1
        rows = int(size / cols)
        res_ = res.reshape(rows, cols)
        consecsd[name] = ConnectorSection(name, [res_], [], metadata=d)
    return consecsd


def get_connectors_from_inp(bulk_str, fem):
    """

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    :return:
    """

    nsuffix = Counter(1, "_")
    cons = dict()
    for m in AbaCards.connector_section.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["behavior"] + next(nsuffix)
        elset = fem.elsets[d["elset"]]
        elem = elset.members[0]

        csys_ref = d["csys"].replace('"', "")
        csys_ref = csys_ref[:-1] if csys_ref[-1] == "," else csys_ref

        con_type = d["contype"]
        if con_type[-1] == ",":
            con_type = con_type[:-1]

        n1 = elem.nodes[0]
        n2 = elem.nodes[1]

        cons[name] = Connector(
            name,
            elem.id,
            n1,
            n2,
            con_type,
            fem.connector_sections[d["behavior"]],
            csys=fem.lcsys[csys_ref],
        )
    return cons


def get_beam_sections_from_inp(bulk_str, fem):
    """

    https://abaqus-docs.mit.edu/2017/English/SIMACAEELMRefMap/simaelm-c-beamcrosssectlib.htm

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    """
    from ada import Section
    from ada.sections import GeneralProperties

    if bulk_str.lower().find("*beam section") == -1:
        return []
    re_beam = re.compile(
        r"(?:\*\*\s*Section:\s*(?P<sec_name>.*?)\s*Profile:\s*(?P<profile_name>.*?)\n|)"
        r"\*Beam Section,\s*elset=(?P<elset>.*?)\s*,\s*material=(?P<material>.*?)\s*,\s*"
        r"(?:temperature=(?P<temperature>.*?),|)\s*(?:section=|sect=)(?P<sec_type>.*?)\n"
        r"(?P<line1>.*?)\n(?P<line2>.*?)$",
        _re_in,
    )

    def interpret_section(profile_name, sec_type, props):
        props_clean = [roundoff(x) for x in filter(lambda x: x.strip() != "", props.split(","))]
        if sec_type.upper() == "BOX":
            b, h, t1, t2, t3, t4 = props_clean
            return Section(
                profile_name,
                "BG",
                h=h,
                w_btn=b,
                w_top=b,
                t_w=t1,
                t_fbtn=t4,
                t_ftop=t2,
                parent=fem,
            )
        elif sec_type.upper() == "CIRC":
            return Section(profile_name, "CIRC", r=props_clean[0], parent=fem)
        elif sec_type.upper() == "I":
            (
                l,
                h,
                b1,
                b2,
                t1,
                t2,
                t3,
            ) = props_clean
            return Section(
                profile_name,
                "IG",
                h=h,
                w_btn=b1,
                w_top=b2,
                t_w=t3,
                t_fbtn=t1,
                t_ftop=t2,
                parent=fem,
            )
        elif sec_type.upper() == "L":
            b, h, t1, t2 = props_clean
            return Section(profile_name, "HP", h=h, w_btn=b, t_w=t2, t_fbtn=t1, parent=fem)
        elif sec_type.upper() == "PIPE":
            r, t = props_clean
            return Section(profile_name, "TUB", r=r, wt=t, parent=fem)
        elif sec_type.upper() == "TRAPEZOID":
            # Currently converts Trapezoid to general beam
            b, h, a, d = props_clean
            # Assuming the Abaqus trapezoid element is symmetrical
            c = (b - a) / 2

            # The properties were quickly copied from a resource online. Most likely it contains error
            # https: // www.efunda.com / math / areas / trapezoidJz.cfm
            genprops = GeneralProperties(
                ax=h * (a + b) / 2,
                ix=h
                * (
                    b * h ** 2
                    + 3 * a * h ** 2
                    + a ** 3
                    + 3 * a * c ** 2
                    + 3 * c * a ** 2
                    + b ** 3
                    + c * b ** 2
                    + a * b ** 2
                    + b * c ** 2
                    + 2 * a * b * c
                    + b * a ** 2
                ),
                iy=(h ** 3) * (3 * a + b) / 12,
                iz=h
                * (
                    a ** 3
                    + 3 * a * c ** 2
                    + 3 * c * a ** 2
                    + b ** 3
                    + c * b ** 2
                    + a * b ** 2
                    + 2 * a * b * c
                    + b * a ** 2
                )
                / 12,
            )
            return Section(profile_name, "GENBEAM", genprops=genprops, parent=fem)
        else:
            raise ValueError(f'Currently unsupported section type "{sec_type}"')

    def grab_beam(match):
        d = match.groupdict()
        elset = fem.elsets[d["elset"]]
        name = d["sec_name"] if d["sec_name"] is not None else elset.name
        profile = d["profile_name"] if d["profile_name"] is not None else elset.name
        ass = fem.parent.get_assembly()
        material = ass.materials.get_by_name(d["material"])
        # material = parent.parent.materials.get_by_name(d['material'])
        temperature = d["temperature"]
        section_type = d["sec_type"]
        geo_props = d["line1"]
        sec = interpret_section(profile, section_type, geo_props)
        beam_y = [float(x.strip()) for x in d["line2"].split(",") if x.strip() != ""]
        metadata = dict(
            temperature=temperature,
            profile=profile.strip(),
            section_type=section_type,
            line1=geo_props,
        )
        res = fem.parent.sections.add(sec)
        if res is not None:
            sec = res
        return FemSection(
            name.strip(),
            sec_type="beam",
            elset=elset,
            section=sec,
            local_y=beam_y,
            material=material,
            metadata=metadata,
            parent=fem,
        )

    return map(grab_beam, re_beam.finditer(bulk_str))


def get_solid_sections_from_inp(bulk_str, fem):
    """

    ** Section: Section-80-MAT2TH1
    *Shell Section, elset=MAT2TH1, material=S3_BS__S355_16_T__40_M2
    0.02, 5

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM

    """
    secnames = Counter(1, "solidsec")

    if bulk_str.lower().find("*solid section") == -1:
        return []
    re_solid = re.compile(
        r"(?:\*\s*Section:\s*(.*?)\n|)\*\Solid Section,\s*elset=(.*?)\s*,\s*material=(.*?)\s*$",
        _re_in,
    )
    solid_iter = re_solid.finditer(bulk_str)

    def grab_solid(m_in):
        name = m_in.group(1) if m_in.group(1) is not None else next(secnames)
        elset = m_in.group(2)
        material = m_in.group(3)
        return FemSection(
            name=name,
            sec_type="solid",
            elset=elset,
            material=material,
            parent=fem,
        )

    return map(grab_solid, solid_iter)


def get_shell_sections_from_inp(bulk_str, fem):
    """

    ** Section: Section-80-MAT2TH1
    *Shell Section, elset=MAT2TH1, material=S3_BS__S355_16_T__40_M2
    0.02, 5
    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    :return: map object containing list of FemSection objects

    """

    shname = Counter(1, "sh")

    if bulk_str.lower().find("*shell section") == -1:
        return []
    re_offset = r"(?:, offset=(?P<offset>.*?)|)"
    re_controls = r"(?:, controls=(?P<controls>.*?)|)"

    re_shell = re.compile(
        rf"(?:\*\s*Section:\s*(?P<name>.*?)\n|)\*\Shell Section, elset"
        rf"=(?P<elset>.*?)\s*, material=(?P<material>.*?){re_offset}{re_controls}\s*\n(?P<t>.*?),"
        rf"(?P<int_points>.*?)$",
        _re_in,
    )

    def grab_shell(m):
        d = m.groupdict()
        name = d["name"] if d["name"] is not None else next(shname)
        elset = fem.sets.get_elset_from_name(d["elset"])
        material = d["material"]
        thickness = float(d["t"])
        offset = d["offset"]
        int_points = d["int_points"]
        metadata = dict(controls=d["controls"])
        return FemSection(
            name=name,
            sec_type="shell",
            thickness=thickness,
            elset=elset,
            material=material,
            int_points=int_points,
            offset=offset,
            parent=fem,
            metadata=metadata,
        )

    return map(grab_shell, re_shell.finditer(bulk_str))


def get_interactions_from_bulk_str(bulk_str, assembly):
    """

    :param bulk_str:
    :param assembly:
    :type assembly: ada.Assembly
    :return:
    """
    gen_name = Counter(1, "general")

    if bulk_str.find("** Interaction") == -1 and bulk_str.find("*Contact") == -1:
        return

    def resolve_surface_ref(surf_ref):
        surf_name = surf_ref.split(".")[-1] if "." in surf_ref else surf_ref
        surf = None
        if surf_name in assembly.fem.surfaces.keys():
            surf = assembly.fem.surfaces[surf_name]

        for p in assembly.get_all_parts_in_assembly():
            if surf_name in p.fem.surfaces.keys():
                surf = p.fem.surfaces[surf_name]
        if surf is None:
            raise ValueError("Unable to find surfaces in assembly parts")

        return surf

    for m in AbaCards.contact_pairs.regex.finditer(bulk_str):
        d = m.groupdict()
        intprop = assembly.fem.intprops[d["interaction"]]
        surf1 = resolve_surface_ref(d["surf1"])
        surf2 = resolve_surface_ref(d["surf2"])

        assembly.fem.add_interaction(Interaction(d["name"], "surface", surf1, surf2, intprop, metadata=d))

    for m in AbaCards.contact_general.regex.finditer(bulk_str):
        s = m.start()
        e = m.endpos
        interact_str = bulk_str[s:e]
        d = m.groupdict()
        intprop = assembly.fem.intprops[d["interaction"]]
        # surf1 = resolve_surface_ref(d["surf1"])
        # surf2 = resolve_surface_ref(d["surf2"])

        assembly.fem.add_interaction(
            Interaction(next(gen_name), "general", None, None, intprop, metadata=dict(aba_bulk=interact_str))
        )


def list_cleanup(membulkstr):
    return membulkstr.replace(",\n", ",").replace("\n", ",")


def is_set_in_part(part, set_name, set_type):
    """

    :param part:
    :param set_name:
    :param set_type:
    :type part: ada.Part
    :return: Set (node, element or surface)
    """

    set_map = {"nset": part.fem.nsets, "elset": part.fem.elsets, "surface": part.fem.surfaces}
    el_map = {"nset": part.fem.nodes, "elset": part.fem.elements}

    if set_name in set_map[set_type].keys():
        return set_map[set_type][set_name]
    else:
        _id = int(set_name)
        return el_map[set_type].from_id(_id)


def grab_set_from_assembly(set_str, fem, set_type):
    """

    :param set_str:
    :param fem:
    :param set_type:
    :type fem: ada.fem.FEM
    :rtype: Union[ada.fem.FemSet, ada.fem.Surface]
    """
    res = set_str.split(".")
    if len(res) == 1:
        set_map = {"nset": fem.nsets, "elset": fem.elsets, "surface": fem.surfaces}
        set_name = res[0]
        return set_map[set_type][set_name]
    else:
        set_name = res[1]
        p_name = res[0]
        for part in fem.parent.get_all_parts_in_assembly():
            if p_name == part.fem.instance_name:
                r = is_set_in_part(part, set_name, set_type)
                if r is not None:
                    return r
    raise ValueError(f'No {set_type} "{set_str}" was found')
