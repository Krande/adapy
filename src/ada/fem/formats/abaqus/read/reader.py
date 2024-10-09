from __future__ import annotations

import mmap
import os
import pathlib
import re
from dataclasses import dataclass, field
from itertools import chain
from typing import TYPE_CHECKING, Dict, List, Union

import numpy as np

from ada.api.containers import Nodes
from ada.api.nodes import Node
from ada.api.transforms import Rotation, Transform
from ada.config import logger
from ada.core.utils import Counter
from ada.fem import (
    Bc,
    Constraint,
    FemSet,
    Interaction,
    InteractionProperty,
    PredefinedField,
    Surface,
)
from ada.fem.containers import FemSets
from ada.fem.formats.utils import str_to_int
from ada.fem.interactions import ContactTypes
from ada.fem.shapes import ElemType

from . import cards
from .helper_utils import _re_in, get_set_from_assembly, list_cleanup
from .read_elements import get_elem_from_bulk_str, update_connector_data
from .read_masses import get_mass_from_bulk
from .read_materials import get_materials_from_bulk
from .read_orientations import get_lcsys_from_bulk
from .read_sections import get_connector_sections_from_bulk, get_sections_from_inp

part_name_counter = Counter(1, "Part")


if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part
    from ada.fem import FEM


@dataclass
class InstanceData:
    part_ref: str
    instance_name: str
    instance_bulk: str
    transform: Transform = field(default_factory=Transform)


def read_fem(fem_file, fem_name=None) -> Assembly:
    """This will create and add an AbaqusPart object based on a path reference to a Abaqus input file."""
    from ada import Assembly

    print("Starting import of Abaqus input file")

    if fem_name is not None:
        global part_name_counter
        part_name_counter = Counter(1, fem_name)

    assembly = Assembly("TempAssembly")

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

    ass_data = extract_instance_data(assembly_str[:inst_end])

    part_list = import_parts(bulk_str[:ass_start], ass_data, assembly)
    if len(part_list) == 0:
        add_fem_without_assembly(bulk_str, assembly)

    if uses_assembly_parts is True:
        ass_sets = assembly_str[inst_end:]
        assembly.fem.nodes += get_nodes_from_inp(ass_sets, assembly.fem)
        assembly.fem.lcsys.update(get_lcsys_from_bulk(ass_sets, assembly.fem))
        assembly.fem.connector_sections.update(get_connector_sections_from_bulk(props_str, assembly.fem))
        assembly.fem.elements += get_elem_from_bulk_str(ass_sets, assembly.fem)
        assembly.fem.elements.build_sets()
        assembly.fem.sets += get_sets_from_bulk(ass_sets, assembly.fem)
        assembly.fem.sets.link_data()

        update_connector_data(ass_sets, assembly.fem)
        assembly.fem.surfaces.update(get_surfaces_from_bulk(ass_sets, assembly.fem))

        try:
            assembly.fem.constraints.update(get_constraints_from_inp(ass_sets, assembly.fem))
        except KeyError as e:
            logger.error(e)

        assembly.fem.bcs += get_bcs_from_bulk(props_str, assembly.fem)
        assembly.fem.elements += get_mass_from_bulk(ass_sets, assembly.fem)

    add_interactions_from_bulk_str(props_str, assembly)
    get_initial_conditions_from_str(assembly, props_str)
    return assembly


def read_bulk_w_includes(inp_path) -> str:
    if isinstance(inp_path, str):
        inp_path = pathlib.Path(inp_path).resolve().absolute()

    re_bulk_include = re.compile(r"\*Include,\s*input=(.*?)$", _re_in)
    bulk_repl = dict()
    with open(inp_path, "r") as inpDeck:
        bulk_str = inpDeck.read()
        for m in re_bulk_include.finditer(bulk_str):
            search_key = m.group(0)
            filepath = (inp_path.parent / m.group(1).replace("\\", "/")).resolve()
            with open(filepath, "r") as d:
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


def extract_instance_data(assembly_bulk) -> dict[str, List[InstanceData]]:
    ass_data = {}
    for m in cards.inst_matches.finditer(assembly_bulk):
        d = m.groupdict()
        inst_name, part_name, bulk_str = d["inst_name"], d["part_name"], d["bulk_str"]
        inst_data = get_instance_data(inst_name, part_name, bulk_str)
        if inst_data.part_ref not in ass_data.keys():
            ass_data[inst_data.part_ref] = []
        ass_data[inst_data.part_ref].append(inst_data)

    return ass_data


def import_parts(bulk_str, instance_data: dict[str, List[InstanceData]], assembly: Assembly) -> List[Part]:
    part_list = []

    for m in cards.parts_matches.finditer(bulk_str):
        d = m.groupdict()
        name = d.get("name")
        part_bulk_str = d.get("bulk_str")

        for i in instance_data[name]:
            p_bulk_str = i.instance_bulk if part_bulk_str == "" and i.instance_bulk != "" else part_bulk_str
            part = get_fem_from_bulk_str(name, p_bulk_str, assembly, i)
            part_list.append(part)
    return part_list


def add_fem_without_assembly(bulk_str, assembly: Assembly) -> Part:
    part_name_matches = list(cards.part_names.finditer(bulk_str))
    p_nmatch = tuple(part_name_matches)

    if len(p_nmatch) != 1:
        p_bulk = bulk_str
        p_name = None
    else:
        p_name = p_nmatch[0].group(1)
        p_bulk = p_nmatch[0].group(2)

    p_name = next(part_name_counter) if p_name is None else p_name
    inst = InstanceData("", p_name, "")

    return get_fem_from_bulk_str(p_name, p_bulk, assembly, inst)


def get_fem_from_bulk_str(name, bulk_str, assembly: Assembly, instance_data: InstanceData) -> "Part":
    from ada import FEM, Part

    instance_name = name if instance_data.instance_name is None else instance_data.instance_name
    if name in assembly.parts.keys():
        name = instance_name
    part = assembly.add_part(Part(name, fem=FEM(name=instance_name)))
    fem = part.fem
    fem.nodes = get_nodes_from_inp(bulk_str, fem)
    fem.nodes.move(move=instance_data.transform.translation, rotate=instance_data.transform.rotation)
    fem.elements = get_elem_from_bulk_str(bulk_str, fem)
    fem.elements.build_sets()
    fem.sets += get_sets_from_bulk(bulk_str, fem)
    fem.sections = get_sections_from_inp(bulk_str, fem)
    fem.bcs += get_bcs_from_bulk(bulk_str, fem)
    fem.elements += get_mass_from_bulk(bulk_str, fem)
    fem.surfaces.update(get_surfaces_from_bulk(bulk_str, fem))
    fem.lcsys = get_lcsys_from_bulk(bulk_str, fem)
    fem.constraints = get_constraints_from_inp(bulk_str, fem)

    print(8 * "-" + f'Imported "{part.fem.instance_name}"')

    return part


def get_initial_conditions_from_str(assembly: Assembly, bulk_str: str):
    """

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

    re_str = r"(?:^\*\*\s*Name:\s*(?P<name>\S+)\s*Type:\s*(?P<type>\S+)\n)+\*Initial Conditions, type=.*?\n(?P<conditions>[\s\S]*?(?=\*|\Z))"

    def sort_props(line):
        ev = [x.strip() for x in line.split(",")]
        set_name = ev[0]
        try:
            dofs = int(ev[1])
        except BaseException as e:
            logger.debug(e)
            dofs = ev[1]
        if len(ev) > 2:
            magn = float(ev[2])
        else:
            magn = None
        return set_name, dofs, magn

    def grab_init_props(m):
        d = m.groupdict()
        bc_name = d.get("name")
        bc_type = d.get("type")
        bcs = d.get("conditions")
        props = [sort_props(line) for line in bcs.splitlines() if line != ""]
        set_name, dofs, magn = list(zip(*props))
        fem_set = None
        set_name_up = set_name[0]
        if "." in set_name_up:
            part_name, set_name_up = set_name_up.split(".")
            for p in assembly.get_all_parts_in_assembly():
                if p.fem.instance_name == part_name:
                    fem_set = p.fem.sets.get_nset_from_name(set_name_up)
                    break
        else:
            if set_name_up in assembly.fem.elsets.keys():
                fem_set = assembly.fem.elsets[set_name_up]
            elif set_name_up in assembly.fem.nsets.keys():
                fem_set = assembly.fem.nsets[set_name_up]
            else:
                for p in assembly.get_all_parts_in_assembly():
                    if set_name_up in p.fem.elsets.keys():
                        fem_set = p.fem.elsets[set_name_up]
                    elif set_name_up in p.fem.nsets.keys():
                        fem_set = p.fem.nsets[set_name_up]
        if fem_set is None:
            raise ValueError(f'Unable to find fem set "{set_name[0]}"')

        return PredefinedField(bc_name, bc_type, fem_set, dofs, magn, parent=assembly.fem)

    for match in re.finditer(re_str, bulk_str, _re_in):
        assembly.fem.add_predefined_field(grab_init_props(match))


def get_intprop_from_lines(assembly: Assembly, bulk_str):
    """
    *Surface Interaction, name=contactProp
    *Friction
    0.,
    *Surface Behavior, pressure-overclosure=HARD
    """
    re_str = r"(\*Surface Interaction, name=.*?)(?=\*)(?!\*Friction|\*Surface Behavior|\*Surface Smoothing)"
    # surf_interact = AbaFF("Surface Interaction", [("name=",), ("bulk>",)], [("Friction", [(), ("bulkFRIC>",)])])

    assembly.fem.metadata["surf_smoothing"] = []
    for m in cards.surface_smoothing.regex.finditer(bulk_str):
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


def get_instance_data(inst_name, p_ref, inst_bulk) -> InstanceData:
    """Move/rotate data lines are specified here:

    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-instance.htm
    """

    move_rot = re.compile(r"(?:^\s*(.*?),\s*(.*?),\s*(.*?)$)", _re_in)
    transform: Union[Transform, None] = Transform()
    mr = move_rot.finditer(inst_bulk)
    if mr is not None:
        for j, mo in enumerate(mr):
            content = mo.group(0)
            if "*" in content or j == 2 or content == "":
                break
            if j == 0:
                transform.move = (float(mo.group(1)), float(mo.group(2)), float(mo.group(3)))
            if j == 1:
                r = [float(x) for x in mo.group(3).split(",")]
                origin = (float(mo.group(1)), float(mo.group(2)), r[0])
                vector = (r[1], r[2], r[3])
                angle = r[4]
                transform.rotate = Rotation(origin, vector, angle)

    return InstanceData(p_ref, inst_name, inst_bulk, transform)


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


def get_nodes_from_inp(bulk_str, parent: FEM) -> Nodes:
    """Extract node information from abaqus input file string"""
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

    return Nodes(nodes, parent=parent)


def str_to_ints(instr):
    try:
        return [int(x.strip()) for l in instr.splitlines() for x in l.split(",") if x.strip() != ""]
    except ValueError:
        return [str(x.strip()) for l in instr.splitlines() for x in l.split(",") if x.strip() != ""]


def get_sets_from_bulk(bulk_str, fem: FEM) -> FemSets:
    from ada import Assembly

    if fem.parent is not None:
        all_parts = fem.parent.get_all_parts_in_assembly()
    else:
        all_parts = []

    def get_parent_instance(instance) -> FEM:
        if instance is None or fem.parent is None:
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

        if set_type.lower() == "elset":
            members = [parent_instance.elements.from_id(el_id) for el_id in members]
        else:
            members = [parent_instance.nodes.from_id(el_id) for el_id in members]

        fem_set = FemSet(
            name,
            members,
            set_type=set_type,
            metadata=metadata,
            parent=parent_instance,
        )

        return fem_set

    return FemSets([get_set(x) for x in cards.re_sets.finditer(bulk_str)], parent=fem)

    # import concurrent.futures
    # with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    #     futures = []
    #     for number in re_sets.finditer(bulk_str):
    #         future = executor.submit(get_set, number)
    #         futures.append(future)
    #     sets = [future.result() for future in concurrent.futures.as_completed(futures)]
    #
    # return FemSetsCollection(sets, parent)


def get_bcs_from_bulk(bulk_str, fem: FEM) -> List[Bc]:
    bc_counter = Counter(1, "bc")

    def get_dofs(content: str):
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
                    logger.debug(e)
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

    return [get_bc(match_in) for match_in in cards.re_bcs.finditer(bulk_str)]


def get_surfaces_from_bulk(bulk_str, parent):
    from ada.fem.elements import find_element_type_from_list
    from ada.fem.surfaces import SurfTypes

    def interpret_member(mem):
        msplit = mem.split(",")
        try:
            ref = str_to_int(msplit[0])
        except BaseException as e:
            logger.debug(e)
            ref = msplit[0].strip()

        return tuple([ref, msplit[1].strip()])

    surf_d = dict()

    for m in cards.surface.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["name"].strip()
        surf_type = d["type"].upper() if d["type"] is not None else "ELEMENT"
        members_str: str = d["bulk"]
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
            if surf_type == SurfTypes.NODE:
                if set_id_ref == "":
                    fem_set = FemSet(f"n{set_ref}_set", [parent.nodes.from_id(int(set_ref))], "nset")
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
                el_face_index = None
            else:
                weight_factor = None
                fem_set = parent.sets.get_elset_from_name(set_ref)
                el_type = find_element_type_from_list(fem_set.members)
                if el_type == ElemType.SOLID:
                    el_face_index = int(set_id_ref.replace("S", "")) - 1
                elif el_type == ElemType.SHELL:
                    el_face_index = -1 if set_id_ref == "SNEG" else 1
                else:
                    el_face_index = set_id_ref
        else:
            fem_set = None
            weight_factor = None
            el_face_index = None

        surf_d[name] = Surface(
            name,
            surf_type,
            fem_set,
            weight_factor,
            el_face_index,
            id_refs,
            parent=parent,
        )

    return surf_d


def get_constraints_from_inp(bulk_str: str, fem: FEM) -> Dict[str, Constraint]:
    """

    ** Constraint: Container_RigidBody
    *Rigid Body, ref node=container_rp, elset=container

    *MPC
     BEAM,    2007,     161
     BEAM,    2008,     162
    """

    # Rigid Bodies

    constraints = []
    rbnames = Counter(1, "rgb")
    conames = Counter(1, "co")

    for m in cards.tie.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["name"]
        msurf = get_set_from_assembly(d["surf1"], fem, "surface")
        ssurf = get_set_from_assembly(d["surf2"], fem, "surface")
        constraints.append(Constraint(name, Constraint.TYPES.TIE, msurf, ssurf, metadata=dict(adjust=d["adjust"])))

    for m in cards.rigid_bodies.regex.finditer(bulk_str):
        d = m.groupdict()
        name = next(rbnames)
        ref_node = get_set_from_assembly(d["ref_node"], fem, FemSet.TYPES.NSET)
        elset = get_set_from_assembly(d["elset"], fem, FemSet.TYPES.ELSET)
        constraints.append(Constraint(name, Constraint.TYPES.RIGID_BODY, ref_node, elset, parent=fem))

    couplings = []
    for m in cards.coupling.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["constraint_name"]
        rn = d["ref_node"].strip()
        sf = d["surface"].strip()
        if rn.isnumeric():
            ref_set = FemSet(next(conames), [fem.nodes.from_id(int(rn))], FemSet.TYPES.NSET, parent=fem)
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

        couplings.append(Constraint(name, Constraint.TYPES.COUPLING, ref_set, surf, csys=csys, dofs=dofs, parent=fem))

    # Shell to Solid Couplings
    sh2solids = []
    for m in cards.sh2so_re.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["constraint_name"]
        influence = d["influence_distance"]
        surf1 = get_set_from_assembly(d["surf1"], fem, "surface")
        surf2 = get_set_from_assembly(d["surf2"], fem, "surface")
        sh2solids.append(Constraint(name, Constraint.TYPES.SHELL2SOLID, surf1, surf2, influence_distance=influence))

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
                logger.debug(e)
                n1_ = get_set_from_assembly(m, fem, FemSet.TYPES.NSET)

            try:
                n2_ = str_to_int(s)
            except BaseException as e:
                logger.debug(e)
                n2_ = get_set_from_assembly(s, fem, FemSet.TYPES.NSET)

            mpc_dict[mpc_type].append((n1_, n2_))

    def get_mpc(mpc_values):
        m_set, s_set = zip(*mpc_values)
        mpc_name = mpc_type + "_mpc"
        mset = FemSet("mpc_" + mpc_type + "_m", m_set, FemSet.TYPES.NSET)
        sset = FemSet("mpc_" + mpc_type + "_s", s_set, FemSet.TYPES.NSET)
        return Constraint(mpc_name, Constraint.TYPES.MPC, mset, sset, mpc_type=mpc_type, parent=fem)

    mpcs = [get_mpc(mpc_values_in) for mpc_values_in in mpc_dict.values()]

    return {c.name: c for c in chain.from_iterable([constraints, couplings, sh2solids, mpcs])}


def add_interactions_from_bulk_str(bulk_str, assembly: Assembly) -> None:
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

    for m in cards.contact_pairs.regex.finditer(bulk_str):
        d = m.groupdict()
        intprop = assembly.fem.intprops[d["interaction"]]
        surf1 = resolve_surface_ref(d["surf1"])
        surf2 = resolve_surface_ref(d["surf2"])

        assembly.fem.add_interaction(Interaction(d["name"], ContactTypes.SURFACE, surf1, surf2, intprop, metadata=d))

    for m in cards.contact_general.regex.finditer(bulk_str):
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
