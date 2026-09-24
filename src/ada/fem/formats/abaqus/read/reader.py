from __future__ import annotations

import functools
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

from ..mapping import bc_types
from .helper_utils import get_set_from_assembly, list_cleanup
from .keywords import validate
from .lexer import (
    KeywordBlock,
    comment_property,
    iter_enclosed,
    iter_keywords,
    mark_read,
    tokenize,
    track_reads,
)
from .read_elements import get_elem_from_bulk_str, update_connector_data
from .read_masses import get_mass_from_bulk
from .read_materials import get_materials_from_bulk
from .read_orientations import get_lcsys_from_bulk
from .read_ref_points import add_ref_points_from_bulk, is_ref_point_block, node_by_id
from .read_sections import get_connector_sections_from_bulk, get_sections_from_inp
from .read_springs import get_springs_from_bulk, link_spring_sets
from .read_steps import (
    HISTORY_KEYWORDS,
    first_step_offset,
    read_amplitudes,
    read_steps,
    read_transforms,
)

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
    """This will create and add an AbaqusPart object based on a path reference to a Abaqus input file.

    Every keyword in the deck that is not read is recorded in the active conversion report (see
    :func:`report_unread_keywords`), so a caller can say exactly what did not come across.
    """
    with track_reads() as read:
        assembly, bulk_str, history_start = _read_fem(fem_file, fem_name)
    report_unread_keywords(bulk_str, read, history_start)
    return assembly


def _read_fem(fem_file, fem_name=None) -> tuple[Assembly, str, int]:
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
    # History data starts at the FIRST *Step (read_steps.first_step_offset); the end of the deck
    # when there is none. Cutting at the last one read an earlier step's data as model data,
    # and with no step at all a -1 here sliced off the deck's last character.
    step_start = first_step_offset(bulk_str)

    ass_start = ass_start + 1 if ass_start != -1 else ass_start
    ass_end = ass_end + 1 if ass_end != -1 else ass_end

    if ass_start == -1 and ass_end == -1:
        uses_assembly_parts = False
        assembly_str = bulk_str[:step_start]
        props_str = bulk_str[:step_start]
    else:
        uses_assembly_parts = True
        assembly_str = bulk_str[ass_start : ass_end + 2]
        props_str = bulk_str[ass_end + 2 : step_start]

    inst_end = assembly_str.lower().rfind("\n*end instance")
    inst_end = inst_end + 15 if inst_end != -1 else inst_end

    get_materials_from_bulk(assembly, props_str)
    get_intprop_from_lines(assembly, props_str)
    # Before anything that names one: a BC's AMPLITUDE= is resolved as the BC is read.
    read_amplitudes(props_str, assembly.fem)

    ass_data = extract_instance_data(assembly_str[:inst_end])

    part_list = import_parts(bulk_str[:ass_start], ass_data, assembly)
    if len(part_list) == 0:
        add_fem_without_assembly(bulk_str[:step_start], assembly)

    if uses_assembly_parts is True:
        ass_sets = assembly_str[inst_end:]
        assembly.fem.nodes += get_nodes_from_inp(ass_sets, assembly.fem)
        add_ref_points_from_bulk(ass_sets, assembly.fem)
        assembly.fem.lcsys.update(get_lcsys_from_bulk(ass_sets, assembly.fem))
        assembly.fem.connector_sections.update(get_connector_sections_from_bulk(props_str, assembly.fem))
        _add_keeping_ids(assembly.fem, get_elem_from_bulk_str(ass_sets, assembly.fem))
        assembly.fem.elements.build_sets()
        _add_keeping_ids(assembly.fem, get_springs_from_bulk(ass_sets, assembly.fem))
        assembly.fem.sets += get_sets_from_bulk(ass_sets, assembly.fem)
        link_spring_sets(assembly.fem)
        assembly.fem.sets.link_data()

        update_connector_data(ass_sets, assembly.fem)
        assembly.fem.surfaces.update(get_surfaces_from_bulk(ass_sets, assembly.fem))

        try:
            assembly.fem.constraints.update(get_constraints_from_inp(ass_sets, assembly.fem))
        except KeyError as e:
            logger.error(e)

        assembly.fem.bcs += get_bcs_from_bulk(props_str, assembly.fem)
        _add_keeping_ids(assembly.fem, get_mass_from_bulk(ass_sets, assembly.fem))

    add_interactions_from_bulk_str(props_str, assembly)
    get_initial_conditions_from_str(assembly, props_str)
    read_transforms(assembly_str, assembly.fem)
    read_steps(bulk_str[step_start:], assembly)
    assembly.fem.metadata.pop("_abaqus_transforms", None)
    # The assembly and its end are found by string search above, not by asking for the keyword.
    mark_read("ASSEMBLY", "END ASSEMBLY")
    return assembly, bulk_str, step_start


def _surface_or_set(name: str, fem: FEM):
    """A *Shell to Solid Coupling operand: a surface, per the Keywords Guide -- or, where the
    deck has no surface of that name, the node/element set adapy's own writer has always put
    there. (That writer output is not valid Abaqus; reading it back still must not fail.)"""
    try:
        return get_set_from_assembly(name, fem, "surface")
    except KeyError:
        for kind in ("nset", "elset"):
            try:
                return get_set_from_assembly(name, fem, kind)
            except KeyError:
                continue
        raise


def by_name(mapping, name: str):
    """``mapping[name]`` the way Abaqus looks names up: case-insensitively. A deck may define
    ``cpl_csys`` and refer to ``CPL_CSYS``; both are one name to Abaqus."""
    if name in mapping:
        return mapping[name]
    key = name.lower()
    return next((v for k, v in mapping.items() if k.lower() == key), None)


def _add_keeping_ids(fem: FEM, elements) -> None:
    """Add a deck's elements to ``fem`` under the ids the deck gave them.

    ``FemElements.__add__`` renumbers what it adds from ``max_id + 1`` -- right for merging two
    unrelated meshes, wrong for reading: a deck's ids are part of its data (its sets, sections
    and connector sections name elements by id), so a renumbered element is no longer the one
    they refer to. A clash is an error, as a duplicate element id in a deck is.
    """
    from ada.api.mesh.containers import ArrayElements

    array_backed = isinstance(fem.elements, ArrayElements)  # groups itself as it adds
    for el in elements:
        fem.elements.add(el, skip_grouping=not array_backed)
    if not array_backed:
        fem.elements._group_by_types()


#: The ``stage`` of every finding the reader records.
READER_STAGE = "abaqus reader"


def report_unread_keywords(bulk_str: str, read: set[str], history_start: int) -> None:
    """Record every keyword in the deck that the reader did not read, one finding per keyword.

    ``read`` is what the reader actually asked for while reading (see ``lexer.track_reads``), so
    this cannot drift from the code the way a hand-kept list would. A keyword is reported:

    * as a ``note`` when it changes nothing in the model -- a title, an output/print request, a
      solver control (``keywords.NO_MODEL_EFFECT``, ``keywords.SOLVER_CONTROLS``);
    * as ``omitted`` when it sits in history data (from the first ``*Step`` on) and is not one
      of the keywords the step reader reads (``read_steps.HISTORY_KEYWORDS``);
    * as ``omitted`` when no reader asked for it at all.

    Counted per keyword, never per block: a deck with a thousand ``*Cload`` blocks is one line
    saying a thousand. Line numbers are in the deck with its ``*Include`` files expanded.
    """
    from ada.fem.formats import conversion_report

    from .keywords import NO_MODEL_EFFECT, SOLVER_CONTROLS

    unread: dict[tuple[str, bool], list[int]] = {}
    for block in tokenize(bulk_str):
        in_history = block.start >= history_start
        if block.keyword in (HISTORY_KEYWORDS if in_history else read):
            continue
        entry = unread.setdefault((block.keyword, in_history), [0, block.lineno])
        entry[0] += 1

    report = conversion_report.current()
    for (keyword, in_history), (count, first_line) in sorted(unread.items()):
        subject = f"{count} block{'s' if count != 1 else ''}, first at line {first_line}"
        details = dict(blocks=count, first_line=first_line)
        if keyword in NO_MODEL_EFFECT:
            report.note(
                READER_STAGE, f"*{keyword}", subject, "not read: no effect on the model", count=count, **details
            )
        elif keyword in SOLVER_CONTROLS:
            report.note(
                READER_STAGE,
                f"*{keyword}",
                subject,
                "not read: a solver control, no model data",
                count=count,
                **details,
            )
        elif in_history:
            report.omitted(
                READER_STAGE,
                f"*{keyword}",
                subject,
                "no reader for this keyword in a step",
                count=count,
                **details,
            )
        else:
            report.omitted(READER_STAGE, f"*{keyword}", subject, "no reader for this keyword", count=count, **details)


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
    #
    # The FIRST such comment names the part and starts its body, as it always has: a flat deck
    # CAE writes for several instances carries one comment per instance, all read as one part
    # here, and the regex reader this replaced took the first. Requiring exactly one would
    # rename that part to a generated name -- same model, different name in every consumer.
    tagged = next(
        (
            (props["PART INSTANCE"], block)
            for block in tokenize(bulk_str)
            if (props := comment_property(block, "PART INSTANCE"))
        ),
        None,
    )

    if tagged is None:
        p_bulk = bulk_str
        p_name = None
    else:
        p_name, tag_block = tagged
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
        add_ref_points_from_bulk(bulk_str, fem)
        fem.elements = get_elem_from_bulk_str(bulk_str, fem)
    fem.elements.build_sets()
    # Before the sets: a deck's *Elset may list spring elements.
    _add_keeping_ids(fem, get_springs_from_bulk(bulk_str, fem))

    # Abaqus applies the instance translation first, then rotates about the axis (whose two
    # points are given in the already-translated/global frame — here point1 equals the
    # translation offset). nodes.move() rotates-then-translates, so do the two steps in order.
    if instance_data.transform.translation is not None:
        fem.nodes.move(move=instance_data.transform.translation)
    if instance_data.transform.rotation is not None:
        fem.nodes.move(rotate=instance_data.transform.rotation)
    fem.sets += get_sets_from_bulk(bulk_str, fem)
    link_spring_sets(fem)
    fem.sections = get_sections_from_inp(bulk_str, fem)
    fem.bcs += get_bcs_from_bulk(bulk_str, fem)
    _add_keeping_ids(fem, get_mass_from_bulk(bulk_str, fem))
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
    for ctype, (el_ids, conns, elsets, formulations) in by_type.items():
        blk = store.add_elem_block_from_id_conn(
            ctype, np.array(el_ids, dtype=np.int64), np.array(conns, dtype=np.int64)
        )
        if any(e is not None for e in elsets):
            blk.elsets = elsets
        blk.formulations = formulations

    fem.nodes = ArrayNodes(store, parent=fem)
    fem.elements = ArrayElements(store, fem_obj=fem)
    add_ref_points_from_bulk(bulk_str, fem)  # before the overflow elements, which may name them

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
        if is_ref_point_block(block):
            continue
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

    mark_read("SURFACE INTERACTION", "FRICTION", "SURFACE BEHAVIOR")
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
                    props["friction"] = float(sub_block.data_lines[0].split(",")[0])
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
    # Only the assignment's property name is read; the other *Contact ... blocks this walks past
    # (inclusions, formulation, initialisation) are not, and are reported as such.
    mark_read("CONTACT PROPERTY ASSIGNMENT")
    seen = False
    for block in tokenize(bulk_str):
        # By position, not identity: iter_keywords' blocks come from the tokenizer cache, and a
        # fresh tokenize() builds new objects, so ``is`` never matched and no general contact
        # was ever read.
        if block.start == contact_block.start:
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

    blocks = (b for b in iter_keywords(bulk_str, "NODE") if not is_ref_point_block(b))
    nodes = list(chain.from_iterable(map(getnodes, blocks)))

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

        from_id_fn = (
            parent_instance.elements.from_id
            if set_type_l == "elset"
            else functools.partial(node_by_id, parent_instance)
        )

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
                    # A set defined another way -- ``*Element, elset=right`` names a set
                    # without an *Elset block -- is already on the FEM, not in ``parsed``. An
                    # ``instance.set`` reference names a set of that instance's part.
                    owner, set_ref = parent_instance, ref
                    if "." in ref:
                        inst, set_ref = ref.split(".", 1)
                        owner = next((p.fem for p in all_parts if p.fem.instance_name == inst), None)
                    if owner is not None:
                        pool = owner.elsets if set_type_l == "elset" else owner.nsets
                        composed = by_name(pool, set_ref)
                if composed is None:
                    from ada.fem.formats import conversion_report

                    conversion_report.current().omitted(
                        READER_STAGE,
                        f"*{set_type_l.upper()}",
                        name,
                        "references a set that is not defined",
                        missing=ref,
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

        # Object members, not ids: these sets are read once and walked many times (set
        # composition, instance re-parenting, export), and re-resolving every id on each
        # walk doubled the read time of a CAE deck while saving no memory.
        fem_set = FemSet(
            name,
            resolved,
            set_type=set_type,
            metadata=metadata,
            parent=parent_instance,
            id_backed=False,
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
                    # Bc.magnitudes pairs with Bc.dofs position by position: the line's one
                    # magnitude applies to every DOF in its range, and to no other.
                    value = float(temp[3])
                    magn = [value if d is not None else None for d in dofs]

        else:
            # One line per DOF (or DOF range), each ``node, first_dof, last_dof, magnitude``.
            # Collected into the same six-slot form as the one-line case, so a BC reads as one
            # shape whichever way the deck wrote it -- and writes back as that shape.
            by_dof: dict[int, float | None] = {}
            named: list[str] = []
            for line in data_lines:
                ev = [x.strip() for x in line.split(",")]
                set_name = ev[0]
                try:
                    first = int(ev[1])
                except ValueError:
                    named.append(ev[1])  # a named restraint (ENCASTRE, XSYMM, ...) on this line
                    continue
                last = int(ev[2]) if len(ev) > 2 and ev[2] else first
                value = float(ev[3]) if len(ev) > 3 and ev[3] else None
                for d in range(first, last + 1):
                    by_dof[d] = value
            if named and not by_dof:
                dofs = named if len(named) > 1 else named[0]
            else:
                dofs = [x if x in by_dof else None for x in range(1, 7)]
                magn = [by_dof.get(x) if x in by_dof else None for x in range(1, 7)]
        magn = None if all(m is None for m in magn) else magn
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
                    # Registered with the FEM, not just held by the BC: a writer names the set
                    # the BC points at, and a set nobody defines reads back as a missing one.
                    fem_set = fem.sets.add(FemSet(bc_name + "_set", [node], "nset", parent=fem))
                else:
                    raise ValueError(f'Unable to find set "{set_name}" in part {fem}')

        if fem_set is None:
            raise Exception("Unable to Find node set")

        props = dict()
        if bc_type is not None:
            # CAE's name for the type, through the same table the writer writes it from. A type
            # adapy has no row for keeps the deck's own text rather than failing the read.
            table = bc_types()
            props["bc_type"] = table.from_abaqus(bc_type) if table.knows(bc_type) else bc_type
        if magn is not None:
            props["magnitudes"] = magn
        amplitude = _bc_amplitude(block, bc_name)
        if amplitude is not None:
            props["amplitude"] = amplitude

        return Bc(bc_name, fem_set, dofs, parent=fem, **props)

    def _bc_amplitude(block: KeywordBlock, bc_name: str):
        name = block.params.get("AMPLITUDE")
        if name is None:
            return None
        owner = fem.parent.get_assembly() if fem.parent is not None else None
        amplitude = by_name(owner.fem.amplitudes, name) if owner is not None else None
        if amplitude is None:
            from ada.fem.formats import conversion_report

            conversion_report.current().omitted(
                READER_STAGE, f"*{block.keyword}", bc_name, "names an amplitude that is not defined", missing=name
            )
        return amplitude

    def get_connector_motion(block: KeywordBlock) -> Bc:
        """``*Connector Motion``: ``connector set, component, magnitude`` lines, the TYPE parameter
        saying whether the magnitude is a displacement or a velocity."""
        props = comment_property(block, "Name", "Type")
        bc_name = props.get("Name") or next(bc_counter)
        kind = (block.params.get("TYPE") or "DISPLACEMENT").upper()
        bc_type = Bc.TYPES.CONN_VEL if kind == "VELOCITY" else Bc.TYPES.CONN_DISPL
        by_dof: dict[int, float | None] = {}
        set_name = None
        for line in block.data_lines:
            ev = [x.strip() for x in line.split(",")]
            set_name = ev[0]
            by_dof[int(ev[1])] = float(ev[2]) if len(ev) > 2 and ev[2] else None
        if "." in set_name:
            inst, local = set_name.split(".", 1)
            owner = next(p.fem for p in fem.parent.get_all_parts_in_assembly() if p.fem.instance_name == inst)
        else:
            owner, local = fem, set_name
        fem_set = by_name(owner.elsets, local)
        if fem_set is None:
            raise ValueError(f'abaqus read: *Connector Motion names element set "{set_name}", which is not defined')
        dofs = [x if x in by_dof else None for x in range(1, 7)]
        magn = [by_dof.get(x) for x in range(1, 7)]
        props = dict(bc_type=bc_type)
        if any(m is not None for m in magn):
            props["magnitudes"] = magn
        amplitude = _bc_amplitude(block, bc_name)
        if amplitude is not None:
            props["amplitude"] = amplitude
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
    for block in iter_keywords(bulk_str, "CONNECTOR MOTION"):
        validate(block)
        bcs.append(get_connector_motion(block))
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
        pos_tol = block.params.get("POSITION TOLERANCE")
        constraints.append(
            Constraint(
                block.params.get("NAME"),
                Constraint.TYPES.TIE,
                msurf,
                ssurf,
                pos_tol=float(pos_tol) if pos_tol else None,
                metadata=dict(adjust=block.params.get("ADJUST")),
                parent=fem,
            )
        )

    for block in iter_keywords(bulk_str, "RIGID BODY"):
        validate(block)
        name = comment_property(block, "Constraint").get("Constraint") or next(rbnames)
        ref_node = get_set_from_assembly(block.params.get("REF NODE"), fem, FemSet.TYPES.NSET)
        elset = get_set_from_assembly(block.params.get("ELSET"), fem, FemSet.TYPES.ELSET)
        constraints.append(Constraint(name, Constraint.TYPES.RIGID_BODY, ref_node, elset, parent=fem))

    couplings = []
    mark_read("COUPLING", "KINEMATIC")
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
        csys = None
        if csys_name is not None:
            csys = by_name(fem.lcsys, csys_name)
            if csys is None:
                raise ValueError(f'Csys "{csys_name}" was not found on part {fem}')

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
        surf1 = _surface_or_set(surfaces[0], fem)
        surf2 = _surface_or_set(surfaces[1], fem)
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

    # MPC's -- one constraint per *MPC block (and per MPC type within it), named from the block's
    # ``** Constraint:`` comment. Grouping every line of the deck by type merged separate MPCs
    # into one and lost their names.
    mpc_dict = dict()
    mpc_names = Counter(1, "mpc")
    for block in iter_keywords(bulk_str, "MPC"):
        validate(block)
        block_name = comment_property(block, "Constraint").get("Constraint") or next(mpc_names)
        block_types: list[str] = []
        for line in block.data_lines:
            fields = [x.strip() for x in line.split(",")]
            if len(fields) < 3:
                logger.warning("abaqus read: *MPC (line %d) data line %r needs three fields", block.lineno, line)
                continue
            mpc_type, m, s = fields[0], fields[1], fields[2]
            if mpc_type not in block_types:
                block_types.append(mpc_type)
            key = (block_name, mpc_type)
            mpc_dict.setdefault(key, [])
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

            mpc_dict[key].append((n1_, n2_))
        if len(block_types) > 1:  # a block mixing types: one constraint per type, told apart by suffix
            for t in block_types:
                mpc_dict[(f"{block_name}_{t.lower()}", t)] = mpc_dict.pop((block_name, t))

    def mpc_nodes(refs) -> list:
        """The nodes an MPC names: a node id, or every node of a named set."""
        nodes = []
        for ref in refs:
            if isinstance(ref, (int, np.integer)):
                nodes.append(fem.nodes.from_id(int(ref)))
            else:
                nodes.extend(ref.members)
        return nodes

    def get_mpc(mpc_name, mpc_type, mpc_values):
        m_refs, s_refs = zip(*mpc_values)
        # Node objects on a set that knows its FEM: built from bare ids with no parent, the sets
        # could not resolve their members, and writing the MPC back failed.
        mset = FemSet(mpc_name + "_m", mpc_nodes(m_refs), FemSet.TYPES.NSET, parent=fem)
        sset = FemSet(mpc_name + "_s", mpc_nodes(s_refs), FemSet.TYPES.NSET, parent=fem)
        return Constraint(mpc_name, Constraint.TYPES.MPC, mset, sset, mpc_type=mpc_type, parent=fem)

    mpcs = [get_mpc(name, mpc_type, values) for (name, mpc_type), values in mpc_dict.items()]

    return {
        c.name: c for c in chain.from_iterable([constraints, couplings, sh2solids, mpcs, _equations(bulk_str, fem)])
    }


def _equations(bulk_str: str, fem: FEM) -> list[Constraint]:
    """``*Equation`` blocks, one constraint per equation. A block may hold several; the first
    takes the block's ``** Constraint:`` name, the rest that name with ``_<n>``."""
    eq_names = Counter(1, "eq")
    parts = fem.parent.get_all_parts_in_assembly() if fem.parent is not None else []

    def operand(ref: str):
        inst, _, local = ref.rpartition(".")
        owner = fem if not inst else next((p.fem for p in parts if p.fem.instance_name == inst), None)
        if owner is None:
            raise ValueError(f'abaqus read: *Equation names instance "{inst}", which is not in the assembly')
        if local.isdigit():
            return node_by_id(owner, int(local))
        found = by_name(owner.nsets, local) or by_name(owner.ref_sets.nodes, local)
        if found is None:
            raise ValueError(f'abaqus read: *Equation names node set "{ref}", which is not defined')
        return found

    def as_set(ref, name: str) -> FemSet:
        if isinstance(ref, FemSet):
            return ref
        return FemSet(name, [ref], FemSet.TYPES.NSET, parent=ref.parent)

    out = []
    for block in iter_keywords(bulk_str, "EQUATION"):
        validate(block)
        tokens = [t.strip() for line in block.data_lines for t in line.split(",") if t.strip()]
        block_name = comment_property(block, "Constraint").get("Constraint") or next(eq_names)
        i, n_eq = 0, 0
        while i < len(tokens):
            n = int(tokens[i])
            raw = tokens[i + 1 : i + 1 + 3 * n]
            i += 1 + 3 * n
            terms = [(operand(raw[k]), int(raw[k + 1]), float(raw[k + 2])) for k in range(0, len(raw), 3)]
            name = block_name if n_eq == 0 else f"{block_name}_{n_eq}"
            n_eq += 1
            s_set = as_set(terms[0][0], f"{name}_s")
            m_set = as_set(terms[1][0], f"{name}_m") if len(terms) > 1 else s_set
            out.append(Constraint(name, Constraint.TYPES.EQUATION, m_set, s_set, equation_terms=terms, parent=fem))
    return out


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
        name = comment_property(block, "Interaction").get("Interaction") or next(gen_name)
        intprop = by_name(assembly.fem.intprops, block.params.get("INTERACTION"))
        surf1 = resolve_surface_ref(surfaces[0])
        surf2 = resolve_surface_ref(surfaces[1])
        # The fields the writer writes, under the names it reads them from -- not the raw
        # parameter dict, which the writer never looks at, so a round trip lost them.
        metadata = {}
        if "SMALL SLIDING" in block.params:
            metadata["small_sliding"] = "small sliding"
        if block.params.get("ADJUST") is not None:
            adjust = block.params.get("ADJUST")
            try:
                metadata["adjust"] = float(adjust)
            except ValueError:
                metadata["adjust"] = adjust  # a node set name
        if block.params.get("GEOMETRIC CORRECTION") is not None:
            metadata["geometric_correction"] = block.params.get("GEOMETRIC CORRECTION")
        kwargs = {}
        if block.params.get("TYPE") is not None:
            kwargs["surface_type"] = block.params.get("TYPE")
        assembly.fem.add_interaction(
            Interaction(
                name,
                ContactTypes.SURFACE,
                surf1,
                surf2,
                intprop,
                constraint=block.params.get("MECHANICAL CONSTRAINT"),
                metadata=metadata,
                **kwargs,
            )
        )

    mark_read("CONTACT INCLUSIONS")
    blocks = tokenize(bulk_str)
    for block in iter_keywords(bulk_str, "CONTACT"):
        validate(block)
        intprop_name = _general_contact_interaction(bulk_str, block)
        if intprop_name is None:
            logger.warning("abaqus read: *Contact (line %d) has no property assignment", block.lineno)
            continue
        intprop = by_name(assembly.fem.intprops, intprop_name)
        name = comment_property(block, "Interaction").get("Interaction") or next(gen_name)
        # Typed, from the blocks that make it up. This used to keep bulk_str[block.start:] --
        # the WHOLE rest of the deck -- as verbatim text, which the writer then wrote back.
        metadata = {"contact_mod": block.params.get("OP") or "NEW"}
        at = next((k for k, b in enumerate(blocks) if b.start == block.start), None)
        after = blocks[at + 1 :] if at is not None else []
        inclusions = next((b for b in after[:3] if b.keyword == "CONTACT INCLUSIONS"), None)
        if inclusions is not None:
            flags = [k for k, v in inclusions.params.items() if v is None]
            if flags:
                metadata["contact_inclusions"] = ", ".join(flags)
        assembly.fem.add_interaction(Interaction(name, ContactTypes.GENERAL, None, None, intprop, metadata=metadata))
