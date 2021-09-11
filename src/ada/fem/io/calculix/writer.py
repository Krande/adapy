import traceback
from itertools import groupby
from operator import attrgetter
from typing import Iterable

from ada import Assembly
from ada.concepts.containers import Nodes
from ada.core.utils import NewLine, get_current_user
from ada.fem import Bc, Elem, FemSection, FemSet, Load, Step
from ada.fem.containers import FemElements
from ada.fem.io.utils import get_fem_model_from_assembly
from ada.fem.shapes import ElemShapes
from ada.sections import SectionCat as Sc

from ..abaqus.writer import AbaSection
from .compatibility import check_compatibility
from .templates import main_header_str
from .write_steps import step_str


def to_fem(assembly: Assembly, name, analysis_dir, metadata=None):
    """Write a Calculix input file stack"""

    check_compatibility(assembly)

    inp_file = (analysis_dir / name).with_suffix(".inp")

    p = get_fem_model_from_assembly(assembly)

    with open(inp_file, "w") as f:
        # Header
        f.write(main_header_str.format(username=get_current_user()))

        # Part level information
        f.write(nodes_str(p.fem.nodes) + "\n")
        f.write(elements_str(p.fem.elements).strip() + "\n")
        f.write("*USER ELEMENT,TYPE=U1,NODES=2,INTEGRATION POINTS=2,MAXDOF=6\n")
        f.write(elsets_str(p.fem.elsets) + "\n")
        f.write(elsets_str(assembly.fem.elsets) + "\n")
        f.write(nsets_str(p.fem.nsets) + "\n")
        f.write(nsets_str(assembly.fem.nsets) + "\n")
        f.write(solid_sec_str(p) + "\n")
        f.write(shell_sec_str(p) + "\n")
        f.write(beam_sec_str(p) + "\n")

        # Assembly Level information
        f.write("\n".join([material_str(mat) for mat in p.materials]) + "\n")
        f.write("\n".join([bc_str(x) for x in p.fem.bcs + assembly.fem.bcs]) + "\n")
        f.write(step_str(assembly.fem.steps[0]))

        # f.write(mass_str)
        # f.write(surfaces_str)
        # f.write(constraints_str)
        # f.write(springs_str)

    print(f'Created a Calculix input deck at "{analysis_dir}"')


class CcxSecTypes:
    GENERAL = "GENERAL"
    BOX = "BOX"
    PIPE = "PIPE"


def must_be_converted_to_general_section(sec_type):
    if sec_type in Sc.circular + Sc.igirders + Sc.iprofiles + Sc.general + Sc.angular:
        return True
    else:
        return False


def beam_str(fem_sec: FemSection):
    top_line = f"** Section: {fem_sec.elset.name}  Profile: {fem_sec.elset.name}"
    n1 = ", ".join(str(x) for x in fem_sec.local_y)
    ass = fem_sec.parent.parent.get_assembly()
    sec_str = get_section_str(fem_sec)
    rotary_str = ""
    if len(ass.fem.steps) > 0:
        initial_step = ass.fem.steps[0]
        if initial_step.type == Step.TYPES.EXPLICIT:
            rotary_str = ", ROTARY INERTIA=ISOTROPIC"

    if sec_str == CcxSecTypes.BOX:
        sec = fem_sec.section
        if sec.t_w * 2 > min(sec.w_top, sec.w_btn):
            raise ValueError("Web thickness cannot be larger than section width")
        return f"{top_line}\n{sec.w_top}, {sec.h}, {sec.t_w}, {sec.t_ftop}, {sec.t_w}, {sec.t_fbtn}\n {n1}"
    elif sec_str == CcxSecTypes.PIPE:
        return f"{top_line}\n{fem_sec.section.r}, {fem_sec.section.wt}\n {n1}"
    elif sec_str == CcxSecTypes.GENERAL:
        from ada.fem.io.abaqus.writer import eval_general_properties

        gp = eval_general_properties(fem_sec.section)
        fem_sec.material.model.plasticity_model = None
        props = f" {gp.Ax}, {gp.Iy}, {0.0}, {gp.Iz}, {gp.Ix}\n {n1}"
        return f"""{top_line}
*Beam Section, elset={fem_sec.elset.name}, material={fem_sec.material.name},  section=GENERAL{rotary_str}
{props}"""
    else:
        raise ValueError(f'Unsupported Section type "{sec_str}"')


def get_section_str(fem_sec: FemSection):
    sec_type = fem_sec.section.type
    if "section_type" in fem_sec.metadata.keys():
        return fem_sec.metadata["section_type"]
    if must_be_converted_to_general_section(sec_type):
        fem_sec.section.properties.calculate()
        return CcxSecTypes.GENERAL
    elif sec_type in Sc.box:
        return CcxSecTypes.BOX
    elif sec_type in Sc.tubular:
        fem_sec.section.properties.calculate()
        return CcxSecTypes.PIPE
    else:
        raise Exception(f'Section "{sec_type}" is not yet supported by Calculix exporter.\n{traceback.format_exc()}')


def nodes_str(fem_nodes: Nodes) -> str:
    if len(fem_nodes) == 0:
        return "** No Nodes"

    f = "{nid:>7}, {x:>13.6f}, {y:>13.6f}, {z:>13.6f}"
    n_ = (f.format(nid=no.id, x=no[0], y=no[1], z=no[2]) for no in sorted(fem_nodes, key=attrgetter("id")))

    return "*NODE\n" + "\n".join(n_).rstrip()


def elements_str(fem_elements: FemElements) -> str:
    if len(fem_elements) == 0:
        return "** No elements"

    el_str = ""
    for (el_type, fem_sec), elements in groupby(fem_elements, key=attrgetter("type", "fem_sec")):
        el_str += elwriter(el_type, fem_sec, elements)

    return el_str


def el_type_sub(el_type, fem_sec: FemSection) -> str:
    """Substitute Element types specifically Calculix"""
    el_map = dict(STRI65="S6")
    if el_type in ElemShapes.lines:
        if must_be_converted_to_general_section(fem_sec.section.type):
            return "U1"
    return el_map.get(el_type, el_type)


def elwriter(eltype, fem_sec: FemSection, elements: Iterable[Elem]):
    from ..abaqus.writer import aba_write

    if "connector" in eltype:
        return None

    sub_eltype = el_type_sub(eltype, fem_sec)
    el_set_str = f", ELSET={fem_sec.elset.name}" if fem_sec.elset is not None else ""
    el_str = "\n".join(map(aba_write, elements))

    return f"""*ELEMENT, type={sub_eltype}{el_set_str}\n{el_str}\n"""


def gen_set_str(fem_set: FemSet):
    if len(fem_set.members) == 0:
        if "generate" in fem_set.metadata.keys():
            if fem_set.metadata["generate"] is False:
                raise ValueError(f'set "{fem_set.name}" is empty. Please check your input')
        else:
            raise ValueError("No Members are found")

    generate = fem_set.metadata.get("generate", False)
    internal = fem_set.metadata.get("internal", False)
    newline = NewLine(15)

    el_str = "*Elset, elset" if fem_set.type == FemSet.TYPES.ELSET else "*Nset, nset"

    el_instances = dict()

    for p, mem in groupby(fem_set.members, key=attrgetter("parent")):
        el_instances[p.name] = list(mem)

    set_str = ""
    for elinst, members in el_instances.items():
        el_root = f"{el_str}={fem_set.name}"
        if internal is True:
            el_root += "" if "," in el_str[-2] else ", "
            el_root += "internal"

        if generate:
            assert len(fem_set.metadata["gen_mem"]) == 3
            el_root += "" if "," in el_root[-2] else ", "
            set_str += (
                el_root + "generate\n {},  {},   {}" "".format(*[no for no in fem_set.metadata["gen_mem"]]) + "\n"
            )
        else:
            set_str += el_root + "\n " + " ".join([f"{no.id}," + next(newline) for no in members]).rstrip()[:-1] + "\n"
    return set_str.rstrip()


def elsets_str(fem_elsets):
    if len(fem_elsets) > 0:
        return "\n".join([gen_set_str(el) for el in fem_elsets.values()]).rstrip()
    else:
        return "** No element sets"


def nsets_str(fem_nsets):
    return (
        "\n".join([gen_set_str(no) for no in fem_nsets.values()]).rstrip() if len(fem_nsets) > 0 else "** No node sets"
    )


def solid_sec_str(part):
    solid_secs = [AbaSection(sec, part).str for sec in part.fem.sections.solids]
    return "\n".join(solid_secs).rstrip() if len(solid_secs) > 0 else "** No solid sections"


def shell_sec_str(part):
    shell_secs = [AbaSection(sec, part).str for sec in part.fem.sections.shells]
    return "\n".join(shell_secs).rstrip() if len(shell_secs) > 0 else "** No shell sections"


def beam_sec_str(part):
    beam_secs = [beam_str(sec) for sec in part.fem.sections.lines]
    return "\n".join(beam_secs).rstrip() if len(beam_secs) > 0 else "** No beam sections"


def material_str(material):
    if "aba_inp" in material.metadata.keys():
        return material.metadata["aba_inp"]
    if "rayleigh_damping" in material.metadata.keys():
        alpha, beta = material.metadata["rayleigh_damping"]
    else:
        alpha, beta = None, None

    no_compression = material._metadata["no_compression"] if "no_compression" in material._metadata.keys() else False
    compr_str = "\n*No Compression" if no_compression is True else ""

    if material.model.eps_p is not None and len(material.model.eps_p) != 0:
        pl_str = "\n*Plastic\n"
        pl_str += "\n".join(
            ["{x:>12.5E}, {y:>10}".format(x=x, y=y) for x, y in zip(material.model.sig_p, material.model.eps_p)]
        )
    else:
        pl_str = ""

    if alpha is not None and beta is not None:
        d_str = "\n*Damping, alpha={alpha}, beta={beta}".format(alpha=material.model.alpha, beta=material.model.beta)
    else:
        d_str = ""

    if material.model.zeta is not None and material.model.zeta != 0.0:
        exp_str = "\n*Expansion\n {zeta}".format(zeta=material.model.zeta)
    else:
        exp_str = ""

    return f"""*Material, name={material.name}
*Elastic
 {material.model.E:.6E},  {material.model.v}{compr_str}
*Density
 {material.model.rho},{exp_str}{d_str}{pl_str}"""


def bc_str(bc: Bc) -> str:
    from ..abaqus.writer import _aba_bc_map, _valid_aba_bcs

    ampl_ref_str = "" if bc.amplitude_name is None else ", amplitude=" + bc.amplitude_name

    if bc.type in _valid_aba_bcs:
        aba_type = bc.type
    else:
        aba_type = _aba_bc_map[bc.type]

    dofs_str = ""
    for dof, magn in zip(bc.dofs, bc.magnitudes):
        if dof is None:
            continue
        # magn_str = f", {magn:.4f}" if magn is not None else ""

        if bc.type in ["connector displacement", "connector velocity"] or type(dof) is str:
            inst_name = bc.fem_set.name
            dofs_str += f" {inst_name}, {dof}\n"
        else:
            inst_name = bc.fem_set.name
            dofs_str += f" {inst_name}, {dof}\n"

    dofs_str = dofs_str.rstrip()

    if bc.type == "connector displacement":
        bcstr = "*Connector Motion"
        add_str = ", type=DISPLACEMENT"
    elif bc.type == "connector velocity":
        bcstr = "*Connector Motion"
        add_str = ", type=VELOCITY"
    else:
        bcstr = "*Boundary"
        add_str = ""

    return f"""** Name: {bc.name} Type: {aba_type}
{bcstr}{ampl_ref_str}{add_str}
{dofs_str}"""


def load_str(load: Load):
    dof = [0, 0, 1] if load.dof is None else load.dof
    if load.fem_set is None:
        raise ValueError("Calculix does not accept Loads without reference to a fem_set")

    fem_set = load.fem_set.name
    return f"""** Name: gravity   Type: Gravity
*Dload
{fem_set}, GRAV, {load.magnitude}, {', '.join([str(x) for x in dof[:3]])}"""


def surface_str(surface):
    """

    :param surface:
    :type surface: ada.fem.Surface
    :return:
    """
    top_line = f"*Surface, type={surface.type}, name={surface.name}"
    id_refs_str = "\n".join([f"{m[0]}, {m[1]}" for m in surface.id_refs]).strip()
    if surface.id_refs is None:
        if surface.type == "NODE":
            add_str = surface.weight_factor
        else:
            add_str = surface.face_id_label
        if surface.fem_set.name in surface.parent.elsets.keys():
            return f"{top_line}\n{surface.fem_set.name}, {add_str}"
        else:
            return f"""{top_line}
{surface.fem_set.name}, {add_str}"""
    else:
        return f"""{top_line}
{id_refs_str}"""


def interactions_str(interaction):
    """

    :param interaction:
    :type interaction: ada.fem.Interaction
    :return:
    """
    from ada.fem import Step

    if interaction.type == "SURFACE":
        adjust_par = interaction.metadata.get("adjust", None)
        geometric_correction = interaction.metadata.get("geometric_correction", None)
        small_sliding = interaction.metadata.get("small_sliding", None)

        stpstr = f"*Contact Pair, interaction={interaction.interaction_property.name}"

        if small_sliding is not None:
            stpstr += f", {small_sliding}"

        if type(interaction.parent) is Step:
            step = interaction.parent
            assert isinstance(step, Step)
            stpstr += "" if "explicit" in step.type else f", type={interaction.surface_type}"
        else:
            stpstr += f", type={interaction.surface_type}"

        if interaction.constraint is not None:
            stpstr += f", mechanical constraint={interaction.constraint}"

        if adjust_par is not None:
            stpstr += f", adjust={adjust_par}" if adjust_par is not None else ""

        if geometric_correction is not None:
            stpstr += f", geometric correction={geometric_correction}"

        stpstr += f"\n{interaction.surf1.name}, {interaction.surf2.name}"
    else:
        raise NotImplementedError(f'type "{interaction.type}"')

    return f"""**
** Interaction: {interaction.name}
{stpstr}"""
