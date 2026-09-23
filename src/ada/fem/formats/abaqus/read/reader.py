from __future__ import annotations

import mmap
import os
import pathlib
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
from ada.fem.constraints import PreDefTypes
from ada.fem.containers import FemSets
from ada.fem.formats.utils import str_to_int
from ada.fem.interactions import ContactTypes, IntPropTypes
from ada.fem.shapes import ElemType

from .helper_utils import get_set_from_assembly, list_cleanup
from .keywords import validate
from .lexer import (
    KeywordBlock,
    comment_property,
    iter_enclosed,
    iter_keywords,
    tokenize,
)
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

    logger.info("Starting import of Abaqus input file")

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

    bulk_repl = dict()
    with open(inp_path, "r") as inpDeck:
        bulk_str = inpDeck.read()
        for block in iter_keywords(bulk_str, "INCLUDE"):
            validate(block)
            included = block.params.get("INPUT")
            if included is None:
                continue
            # The keyword line as written is the text to splice the file in for -- a quoted
            # path with a comma in it parses correctly here and would not have before.
            search_key = block.keyword_line
            filepath = (inp_path.parent / included.replace("\\", "/")).resolve()
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
    for block, body in iter_enclosed(assembly_bulk, "INSTANCE", "END INSTANCE"):
        validate(block)
        inst_name, part_name = block.params.get("NAME"), block.params.get("PART")
        inst_data = get_instance_data(inst_name, part_name, body, block)
        if inst_data.part_ref not in ass_data.keys():
            ass_data[inst_data.part_ref] = []
        ass_data[inst_data.part_ref].append(inst_data)

    return ass_data


def import_parts(bulk_str, instance_data: dict[str, List[InstanceData]], assembly: Assembly) -> List[Part]:
    part_list = []

    for block, part_bulk_str in iter_enclosed(bulk_str, "PART", "END PART"):
        validate(block)
        name = block.params.get("NAME")

        for i in instance_data[name]:
            p_bulk_str = i.instance_bulk if part_bulk_str == "" and i.instance_bulk != "" else part_bulk_str
            part = get_fem_from_bulk_str(name, p_bulk_str, assembly, i)
            part_list.append(part)
    return part_list


def add_fem_without_assembly(bulk_str, assembly: Assembly) -> Part:
    # ``** PART INSTANCE: <name>`` is a comment, and the deck below it is that part. Reading
    # it off the block it annotates means the name is whatever is on that one line.
    tagged = [
        (props["PART INSTANCE"], block)
        for block in tokenize(bulk_str)
        if (props := comment_property(block, "PART INSTANCE"))
    ]

    if len(tagged) != 1:
        p_bulk = bulk_str
        p_name = None
    else:
        p_name, tag_block = tagged[0]
        p_bulk = bulk_str[tag_block.start :]

    p_name = next(part_name_counter) if p_name is None else p_name
    inst = InstanceData("", p_name, "")

    return get_fem_from_bulk_str(p_name, p_bulk, assembly, inst)


def get_fem_from_bulk_str(name, bulk_str, assembly: Assembly, instance_data: InstanceData) -> "Part":
    from ada import FEM, Part
    from ada.config import Config

    instance_name = name if instance_data.instance_name is None else instance_data.instance_name
    if name in assembly.parts.keys():
        name = instance_name
    part = assembly.add_part(Part(name, fem=FEM(name=instance_name)))
    fem = part.fem

    if Config().meshing_array_backed:
        _build_array_nodes_elements(bulk_str, fem)
    else:
        fem.nodes = get_nodes_from_inp(bulk_str, fem)
        fem.elements = get_elem_from_bulk_str(bulk_str, fem)
    fem.elements.build_sets()

    # Abaqus applies the instance translation first, then rotates about the axis (whose two
    # points are given in the already-translated/global frame — here point1 equals the
    # translation offset). nodes.move() rotates-then-translates, so do the two steps in order.
    if instance_data.transform.translation is not None:
        fem.nodes.move(move=instance_data.transform.translation)
    if instance_data.transform.rotation is not None:
        fem.nodes.move(rotate=instance_data.transform.rotation)
    fem.sets += get_sets_from_bulk(bulk_str, fem)
    fem.sections = get_sections_from_inp(bulk_str, fem)
    fem.bcs += get_bcs_from_bulk(bulk_str, fem)
    fem.elements += get_mass_from_bulk(bulk_str, fem)
    fem.surfaces.update(get_surfaces_from_bulk(bulk_str, fem))
    fem.lcsys = get_lcsys_from_bulk(bulk_str, fem)
    fem.constraints = get_constraints_from_inp(bulk_str, fem)

    logger.info(8 * "-" + f'Imported "{part.fem.instance_name}"')

    return part


def _build_array_nodes_elements(bulk_str, fem) -> None:
    """Substrate-direct Abaqus node/element build: no object Node/Elem for the
    structural mesh. Special blocks (mass/connector/cross-instance) fall back to the
    object element parser as ArrayElements overflow."""
    import numpy as np

    from ada.api.mesh.containers import ArrayElements, ArrayNodes
    from ada.api.mesh.store import MeshArrays

    from .read_elements import get_elem_arrays, grab_elements

    coords, node_ids, nsets = get_nodes_from_inp_arrays(bulk_str)
    store = MeshArrays(coords, node_ids)
    by_type, overflow = get_elem_arrays(bulk_str)
    for ctype, (el_ids, conns, elsets) in by_type.items():
        blk = store.add_elem_block_from_id_conn(
            ctype, np.array(el_ids, dtype=np.int64), np.array(conns, dtype=np.int64)
        )
        if any(e is not None for e in elsets):
            blk.elsets = elsets

    fem.nodes = ArrayNodes(store, parent=fem)
    fem.elements = ArrayElements(store, fem_obj=fem)

    # node sets declared inline on *Node blocks (id-backed)
    for set_name, ids in nsets:
        fem.sets.add(FemSet(set_name, ids, "nset", parent=fem))

    # special/cross-instance element blocks via the object parser -> overflow
    for match in overflow:
        elems = grab_elements(match, fem)
        if elems:
            for e in elems:
                fem.elements.add(e)


def get_nodes_from_inp_arrays(bulk_str):
    """Parse *Node blocks into packed (coords (n,3), node_ids (n,)) arrays plus inline
    nset declarations as ``(name, [ids])`` — no Node objects."""
    import numpy as np

    ids: list[int] = []
    xyz: list = []
    nsets: list = []
    for block in iter_keywords(bulk_str, "NODE"):
        validate(block)
        res = np.fromstring(list_cleanup("\n".join(block.data_lines)), sep=",", dtype=np.float64)
        if res.size == 0:
            continue
        if res.size % 4 == 0:
            res_ = res.reshape(int(res.size / 4), 4)
            block_xyz = res_[:, 1:4]
        elif res.size % 3 == 0:
            res_ = res.reshape(int(res.size / 3), 3)
            block_xyz = np.column_stack([res_[:, 1:3], np.zeros(res_.shape[0])])
        else:
            raise ValueError(f"Abaqus *Node block has {res.size} values; not divisible by 4 (3D) or 3 (2D)")
        block_ids = [int(x) for x in res_[:, 0]]
        ids.extend(block_ids)
        xyz.extend(block_xyz.tolist())
        if block.params.get("NSET") is not None:
            nsets.append((block.params.get("NSET"), block_ids))

    coords = np.array(xyz, dtype=np.float64) if xyz else np.zeros((0, 3))
    node_ids = np.array(ids, dtype=np.int64) if ids else np.zeros((0,), dtype=np.int64)
    return coords, node_ids, nsets


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

    def grab_init_props(block: KeywordBlock):
        comment = comment_property(block, "Name", "Type")
        bc_name = comment.get("Name")
        # TYPE is the block's own required parameter. The ``** Name: ... Type: ...`` comment
        # above it is CAE's label for the same thing, spelled for a reader ("Geostatic
        # stress") rather than for the solver, so the parameter is the one to believe.
        bc_type = block.params.get("TYPE")
        props = [sort_props(line) for line in block.data_lines]
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

    for block in iter_keywords(bulk_str, "INITIAL CONDITIONS"):
        validate(block)
        field_type = block.params.get("TYPE")
        if field_type is None or field_type.upper() not in PreDefTypes.all:
            # adapy models VELOCITY and INITIAL STATE; a deck's geostatic stress, void ratio
            # or pore pressure is read past rather than aborting the import — the same policy
            # the element reader applies to element types it has no mapping for.
            logger.warning(
                "abaqus read: *Initial Conditions (line %d) type %r is not supported — skipping",
                block.lineno,
                field_type,
            )
            continue
        assembly.fem.add_predefined_field(grab_init_props(block))


def get_intprop_from_lines(assembly: Assembly, bulk_str):
    """
    *Surface Interaction, name=contactProp
    *Friction
    0.,
    *Surface Behavior, pressure-overclosure=HARD
    """
    assembly.fem.metadata["surf_smoothing"] = []
    for block in iter_keywords(bulk_str, "SURFACE SMOOTHING"):
        validate(block)
        assembly.fem.metadata["surf_smoothing"].append(dict(name=block.params.get("NAME"), bulk=block.data_text))

    all_blocks = tokenize(bulk_str)
    for i, block in enumerate(all_blocks):
        if block.keyword != "SURFACE INTERACTION":
            continue
        validate(block)
        props = dict(name=block.params.get("NAME"), friction=None)
        # *Friction and *Surface Behavior belong to the interaction above them; the run ends
        # at the first block that is neither.
        for sub_block in all_blocks[i + 1 :]:
            if sub_block.keyword == "FRICTION":
                validate(sub_block)
                if sub_block.data_lines:
                    props["friction"] = sub_block.data_lines[0].split(",")[0]
            elif sub_block.keyword == "SURFACE BEHAVIOR":
                validate(sub_block)
                behave = _surface_behaviour(sub_block)
                if sub_block.data_lines:
                    tabular = [tuple(np.fromstring(line, dtype=float, sep=",")) for line in sub_block.data_lines]
                    props["tabular"] = tabular
                if behave is not None and behave.upper() in IntPropTypes.all:
                    props["pressure_overclosure"] = behave
                elif behave is not None:
                    # Abaqus has more pressure-overclosure relationships than adapy models
                    # (LINEAR, EXPONENTIAL, SCALE FACTOR). Keeping the property with the
                    # default relationship preserves the name a *Contact Pair refers to;
                    # dropping it would break that lookup for an attribute we don't use.
                    logger.warning(
                        "abaqus read: *Surface Behavior (line %d) relationship %r is not supported — "
                        "keeping interaction property %r with the default",
                        sub_block.lineno,
                        behave,
                        props["name"],
                    )
            else:
                break
        assembly.fem.add_interaction_property(InteractionProperty(**props))


def _surface_behaviour(block: KeywordBlock) -> str | None:
    """The pressure-overclosure relationship on a ``*Surface Behavior`` block.

    Written either as a value (``pressure-overclosure=HARD``) or as a bare flag
    (``, penalty``), so the flag's own name is the answer when it carries no value.
    """
    for name, value in block.params.items():
        return value if value is not None else name
    return None


def _two_surfaces(block: KeywordBlock) -> tuple[str, str] | None:
    """The master/slave surface pair on a keyword block's single data line.

    ``*Tie``, ``*Contact Pair`` and ``*Shell to Solid Coupling`` all name their two surfaces
    this way. A block without them is reported and skipped rather than aborting the import.
    """
    if not block.data_lines:
        logger.warning("abaqus read: *%s (line %d) has no surface data line", block.keyword, block.lineno)
        return None
    fields = [x.strip() for x in block.data_lines[0].split(",") if x.strip() != ""]
    if len(fields) < 2:
        logger.warning(
            "abaqus read: *%s (line %d) names %d surface(s), expected 2", block.keyword, block.lineno, len(fields)
        )
        return None
    return fields[0], fields[1]


def _general_contact_interaction(bulk_str: str, contact_block: KeywordBlock) -> str | None:
    """The interaction property assigned by a general ``*Contact`` block.

    ``*Contact Property Assignment`` names it on a data line whose last field is the
    property; the blocks in between belong to the same block.
    """
    seen = False
    for block in tokenize(bulk_str):
        if block is contact_block:
            seen = True
            continue
        if not seen:
            continue
        if block.keyword == "CONTACT PROPERTY ASSIGNMENT":
            for line in block.data_lines:
                fields = [x.strip() for x in line.split(",") if x.strip() != ""]
                if fields:
                    return fields[-1]
        elif not block.keyword.startswith("CONTACT") and block.keyword != "SURFACE PROPERTY ASSIGNMENT":
            return None
    return None


def get_instance_data(inst_name, p_ref, inst_bulk, block: KeywordBlock | None = None) -> InstanceData:
    """Move/rotate data lines are specified here:

    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-instance.htm

    They are the ``*Instance`` block's own data lines: the first is a translation, the second
    a rotation. Taking them from the block rather than scanning the instance body means a
    node or element line inside the body can never be mistaken for a placement.
    """
    transform: Union[Transform, None] = Transform()
    data_lines = block.data_lines[:2] if block is not None else ()

    for j, line in enumerate(data_lines):
        values = [x.strip() for x in line.split(",") if x.strip() != ""]
        if j == 0:
            if len(values) < 3:
                logger.warning("abaqus read: *Instance %r translation line needs 3 values", inst_name)
                continue
            # Transform's fields are translation/rotation — setting .move/.rotate created
            # phantom attributes, so the instance placement was silently dropped (every
            # instance landed at the part origin). This matters once instances are merged.
            transform.translation = tuple(float(v) for v in values[:3])
        elif j == 1:
            if len(values) < 7:
                logger.warning("abaqus read: *Instance %r rotation line needs 7 values", inst_name)
                continue
            # Abaqus *Instance rotation line: a, b, c, d, e, f, angle — two points
            # (a,b,c) and (d,e,f) defining the axis, plus the angle. The axis DIRECTION is
            # point2 - point1; using point2 directly (a position ~1000 units off) rotated
            # the instance about a bogus far axis, severely distorting + flinging it.
            x1, y1, z1, x2, y2, z2, angle = (float(v) for v in values[:7])
            transform.rotation = Rotation((x1, y1, z1), (x2 - x1, y2 - y1, z2 - z1), angle)

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

    def getnodes(block: KeywordBlock):
        validate(block)
        # ``block.data_lines`` has already dropped the ``**`` comment lines that can sit
        # between the last numeric row and the next keyword (ada's own writer emits a
        # ``** No Nodes`` placeholder in the assembly-level node section when every node
        # lives at part level), so ``np.fromstring`` never sees a non-numeric token.
        res = np.fromstring(list_cleanup("\n".join(block.data_lines)), sep=",", dtype=np.float64)
        # 3D nodes are ``id, x, y, z``; 2D models (plane-stress /
        # plane-strain / axisymmetric / membrane decks) drop the z
        # column so each row is ``id, x, y``. Pick the layout that
        # divides evenly; pad z=0 for the 2D case so downstream
        # code keeps its (x, y, z) assumption.
        if res.size % 4 == 0:
            res_ = res.reshape(int(res.size / 4), 4)
            members = [Node(n[1:4], int(n[0]), parent=parent) for n in res_]
        elif res.size % 3 == 0:
            res_ = res.reshape(int(res.size / 3), 3)
            members = [Node((n[1], n[2], 0.0), int(n[0]), parent=parent) for n in res_]
        else:
            raise ValueError(
                f"Abaqus *Node block has {res.size} values; " f"not divisible by 4 (3D) or 3 (2D) — malformed?"
            )
        nset = block.params.get("NSET")
        if nset is not None:
            parent.sets.add(FemSet(nset, members, "nset", parent=parent))
        return members

    nodes = list(chain.from_iterable(map(getnodes, iter_keywords(bulk_str, "NODE"))))

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

    # Sets parsed so far, keyed by ``(set_type_lower, name)``. Abaqus
    # supports set-of-sets composition:
    #
    #     *ELSET, ELSET=all
    #     right, left, top
    #
    # where ``right``/``left``/``top`` are previously-defined ELSETs
    # rather than raw element ids. ``str_to_ints`` already handles
    # the heterogeneous-members case by falling back to strings, but
    # the original ``get_set`` then called ``elements.from_id("right")``
    # which fails. We now build an incremental name index and
    # resolve string references through it.
    parsed: dict[tuple[str, str], "FemSet"] = {}

    def get_set(block: KeywordBlock):
        validate(block)
        set_type = block.keyword.lower()
        set_type_l = set_type
        # The set's name is the value of the parameter that shares the keyword's name:
        # ``*Elset, elset=...`` / ``*Nset, nset=...``.
        name = block.params.get(block.keyword)
        internal = "INTERNAL" in block.params
        instance = block.params.get("INSTANCE")
        generate = "GENERATE" in block.params
        members_str = block.data_text
        gen_mem = str_to_ints(members_str) if generate is True else []
        raw_members = [] if generate is True else str_to_ints(members_str)
        metadata = dict(instance=instance, internal=internal, generate=generate, gen_mem=gen_mem)
        parent_instance = get_parent_instance(instance)

        from_id_fn = parent_instance.elements.from_id if set_type_l == "elset" else parent_instance.nodes.from_id

        resolved: list = []
        for ref in raw_members:
            if isinstance(ref, str):
                # Named-set reference: inline the previously-parsed
                # set's members. Look up by the SAME set_type as the
                # current set (Abaqus disallows cross-type set
                # composition); fall back to a case-insensitive name
                # match against any set_type if the strict match
                # misses.
                composed = parsed.get((set_type_l, ref))
                if composed is None:
                    # Case-insensitive scan as a fallback —
                    # Abaqus set names are case-insensitive but
                    # casing in the deck varies.
                    for (st, nm), fs in parsed.items():
                        if st == set_type_l and nm.lower() == ref.lower():
                            composed = fs
                            break
                if composed is None:
                    logger.warning(
                        "abaqus read: set %r references unknown sub-set %r — skipping that member",
                        name,
                        ref,
                    )
                    continue
                resolved.extend(composed.members)
                continue
            try:
                resolved.append(from_id_fn(ref))
            except ValueError as exc:
                logger.warning(
                    "abaqus read: set %r references unknown id %r — skipping (%s)",
                    name,
                    ref,
                    exc,
                )

        fem_set = FemSet(
            name,
            resolved,
            set_type=set_type,
            metadata=metadata,
            parent=parent_instance,
        )
        parsed[(set_type_l, name)] = fem_set

        return fem_set

    return FemSets([get_set(c) for c in iter_keywords(bulk_str, "ELSET", "NSET")], parent=fem)

    # import concurrent.futures
    # with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    #     futures = []
    #     for number in re_sets.finditer(bulk_str):
    #         future = executor.submit(get_set, number)
    #         futures.append(future)
    #     sets = [future.result() for future in concurrent.futures.as_completed(futures)]
    #
    # return FemSetsCollection(sets, parent)


# Abaqus standard named boundary conditions -> the DOFs they fix (1..3 translations,
# 4..6 rotations). ENCASTRE is handled separately (passed through as a string).
_NAMED_BC_DOFS = {
    "pinned": (1, 2, 3),
    "xsymm": (1, 5, 6),
    "ysymm": (2, 4, 6),
    "zsymm": (3, 4, 5),
    "xasymm": (2, 3, 4),
    "yasymm": (1, 3, 5),
    "zasymm": (1, 2, 6),
}


def get_bcs_from_bulk(bulk_str, fem: FEM) -> List[Bc]:
    bc_counter = Counter(1, "bc")

    def get_dofs(data_lines: tuple[str, ...]):
        """One data line constrains a DOF range; several constrain one DOF each.

        Which of the two it is used to be decided by counting newlines in the matched block,
        which made it depend on whether the block happened to end in one. The line count says
        it directly.
        """
        set_name = None
        dofs = []
        magn = []
        if len(data_lines) == 1:
            temp = data_lines[0].split(",")
            set_name = temp[0].strip()
            dof_in = temp[1].strip().replace("\n", "")
            low = dof_in.lower()
            if low == "encastre":
                dofs = dof_in
            elif low in _NAMED_BC_DOFS:
                # Abaqus standard named restraints (symmetry / pinned): map to the DOFs they
                # constrain instead of trying to parse the name as an integer DOF index.
                constrained = _NAMED_BC_DOFS[low]
                dofs = [x if x in constrained else None for x in range(1, 7)]
            else:
                dof = str_to_int(temp[1])
                # ``node, first_dof, last_dof, magnitude`` — last_dof may be written blank,
                # which per the guide means the single DOF given as first_dof. It reads as an
                # empty field rather than a missing one when a magnitude follows it.
                last = temp[2].strip() if len(temp) > 2 else ""
                dof_end = str_to_int(last) if last else dof
                dofs = [x if dof <= x <= dof_end else None for x in range(1, 7)]
                if len(temp) > 3 and temp[3].strip():
                    magn.append(temp[3].strip())

        else:
            for line in data_lines:
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

    def get_bc(block: KeywordBlock, data_lines: tuple[str, ...]):
        # Abaqus/CAE writes ``** Name: BC-1  Type: Displacement/Rotation`` directly above the
        # block. Reading it from this block's own comments is what stops a load's or an
        # interaction's identically-shaped comment from being taken as this BC's name.
        props = comment_property(block, "Name", "Type")
        bc_name = props.get("Name") or next(bc_counter)
        bc_type = props.get("Type")
        set_name, dofs, magn = get_dofs(data_lines)

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

    bcs: List[Bc] = []
    for block in iter_keywords(bulk_str, "BOUNDARY"):
        validate(block)
        # Each data line names its own set or node, and a keyword block may mix them — the deck
        # written by a solver rather than by CAE gives one line per constrained node. Group
        # by the name so each becomes its own Bc; the usual single-set block is one group and
        # so is unchanged.
        by_set: Dict[str, List[str]] = {}
        for line in block.data_lines:
            name = line.split(",")[0].strip()
            by_set.setdefault(name, []).append(line)
        for lines in by_set.values():
            bcs.append(get_bc(block, tuple(lines)))
    return bcs


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

    for block in iter_keywords(bulk_str, "SURFACE"):
        validate(block)
        name = (block.params.get("NAME") or "").strip()
        # TYPE is optional and defaults to ELEMENT -- the guide's default, and previously
        # unreachable because the pattern required TYPE to be present and to come first.
        surf_type = block.params.get("TYPE", "ELEMENT").upper()
        members_str: str = "\n".join(block.data_lines)
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

    for block in iter_keywords(bulk_str, "TIE"):
        validate(block)
        surfaces = _two_surfaces(block)
        if surfaces is None:
            continue
        # ADJUST is optional per the guide; a *Tie without it is legal and used to not match.
        msurf = get_set_from_assembly(surfaces[0], fem, "surface")
        ssurf = get_set_from_assembly(surfaces[1], fem, "surface")
        constraints.append(
            Constraint(
                block.params.get("NAME"),
                Constraint.TYPES.TIE,
                msurf,
                ssurf,
                metadata=dict(adjust=block.params.get("ADJUST")),
            )
        )

    for block in iter_keywords(bulk_str, "RIGID BODY"):
        validate(block)
        name = next(rbnames)
        ref_node = get_set_from_assembly(block.params.get("REF NODE"), fem, FemSet.TYPES.NSET)
        elset = get_set_from_assembly(block.params.get("ELSET"), fem, FemSet.TYPES.ELSET)
        constraints.append(Constraint(name, Constraint.TYPES.RIGID_BODY, ref_node, elset, parent=fem))

    couplings = []
    all_blocks = tokenize(bulk_str)
    for i, block in enumerate(all_blocks):
        if block.keyword != "COUPLING":
            continue
        validate(block)
        name = block.params.get("CONSTRAINT NAME")
        rn = (block.params.get("REF NODE") or "").strip()
        sf = (block.params.get("SURFACE") or "").strip()
        if rn.isnumeric():
            ref_set = FemSet(next(conames), [fem.nodes.from_id(int(rn))], FemSet.TYPES.NSET, parent=fem)
            fem.sets.add(ref_set)
        else:
            ref_set = fem.nsets[rn]

        surf = fem.surfaces[sf]

        # The DOF table belongs to the *Kinematic block that follows the *Coupling.
        kinematic = next((c for c in all_blocks[i + 1 :][:1] if c.keyword == "KINEMATIC"), None)
        if kinematic is None:
            logger.warning("abaqus read: *Coupling %r (line %d) has no *Kinematic block", name, block.lineno)
            continue
        res = np.fromstring(list_cleanup(kinematic.data_text), sep=",", dtype=int)
        size = res.size
        cols = 2
        rows = int(size / cols)
        dofs = res.reshape(rows, cols)

        csys_name = block.params.get("ORIENTATION")
        if csys_name is not None:
            if csys_name not in fem.lcsys.keys():
                raise ValueError(f'Csys "{csys_name}" was not found on part {fem}')
            csys = fem.lcsys[csys_name]
        else:
            csys = None

        couplings.append(Constraint(name, Constraint.TYPES.COUPLING, ref_set, surf, csys=csys, dofs=dofs, parent=fem))

    # Shell to Solid Couplings
    sh2solids = []
    for block in iter_keywords(bulk_str, "SHELL TO SOLID COUPLING"):
        validate(block)
        surfaces = _two_surfaces(block)
        if surfaces is None:
            continue
        name = (block.params.get("CONSTRAINT NAME") or "").strip()
        influence = block.params.get("INFLUENCE DISTANCE")
        pos_tol = block.params.get("POSITION TOLERANCE")
        surf1 = get_set_from_assembly(surfaces[0], fem, "surface")
        surf2 = get_set_from_assembly(surfaces[1], fem, "surface")
        sh2solids.append(
            Constraint(
                name,
                Constraint.TYPES.SHELL2SOLID,
                surf1,
                surf2,
                pos_tol=float(pos_tol) if pos_tol else None,
                influence_distance=float(influence) if influence else None,
                parent=fem,
            )
        )

    # MPC's
    mpc_dict = dict()
    for block in iter_keywords(bulk_str, "MPC"):
        validate(block)
        for line in block.data_lines:
            fields = [x.strip() for x in line.split(",")]
            if len(fields) < 3:
                logger.warning("abaqus read: *MPC (line %d) data line %r needs three fields", block.lineno, line)
                continue
            mpc_type, m, s = fields[0], fields[1], fields[2]
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

    for block in iter_keywords(bulk_str, "CONTACT PAIR"):
        validate(block)
        surfaces = _two_surfaces(block)
        if surfaces is None:
            continue
        d = dict(block.params)
        d["name"] = comment_property(block, "Interaction").get("Interaction")
        d["surf1"], d["surf2"] = surfaces
        intprop = assembly.fem.intprops[block.params.get("INTERACTION")]
        surf1 = resolve_surface_ref(surfaces[0])
        surf2 = resolve_surface_ref(surfaces[1])

        assembly.fem.add_interaction(Interaction(d["name"], ContactTypes.SURFACE, surf1, surf2, intprop, metadata=d))

    for block in iter_keywords(bulk_str, "CONTACT"):
        validate(block)
        interact_str = bulk_str[block.start :]
        intprop_name = _general_contact_interaction(bulk_str, block)
        if intprop_name is None:
            logger.warning("abaqus read: *Contact (line %d) has no property assignment", block.lineno)
            continue
        intprop = assembly.fem.intprops[intprop_name]
        # surf1 = resolve_surface_ref(d["surf1"])
        # surf2 = resolve_surface_ref(d["surf2"])

        assembly.fem.add_interaction(
            Interaction(next(gen_name), "general", None, None, intprop, metadata=dict(aba_bulk=interact_str))
        )
