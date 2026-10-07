from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby
from operator import attrgetter
from typing import TYPE_CHECKING

from ada.api.containers import Nodes
from ada.config import logger
from ada.core.utils import NewLine, get_current_user
from ada.fem import Bc, FemSection, FemSet, Load
from ada.fem.exceptions import IncompatibleElements
from ada.fem.formats.abaqus.write.write_bc import abaqus_bc_type
from ada.fem.formats.abaqus.write.write_sections import (
    eval_general_properties,
    shell_section_str,
    solid_section_str,
)
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.steps import StepExplicit

from ..compatibility import check_compatibility
from .templates import main_header_str
from .write_constraints import constraints_str
from .write_elements import elements_str
from .write_loads import STAGE
from .write_steps import steps_str

if TYPE_CHECKING:
    from ada import Assembly, Part
    from ada.fem import Interaction, Surface


def to_fem(assembly: Assembly, name, analysis_dir, metadata=None, model_data_only=False):
    """Write a Calculix input file stack"""

    check_compatibility(assembly)

    inp_file = (analysis_dir / name).with_suffix(".inp")

    p = get_fem_model_from_assembly(assembly)
    steps = all_steps(assembly)
    deck = DeckContext.of(p, steps)

    with open(inp_file, "w") as f:
        # Header
        f.write(main_header_str.format(username=get_current_user()))

        # Part level information
        f.write(nodes_str(p.fem.nodes) + "\n")
        f.write(elements_str(p.fem.elements, report_locking=len(steps) > 0).strip() + "\n")
        if deck.u1_elements:
            f.write("*USER ELEMENT,TYPE=U1,NODES=2,INTEGRATION POINTS=2,MAXDOF=6\n")
        f.write(elsets_str(p.fem.elsets) + "\n")
        f.write(elsets_str(assembly.fem.elsets) + "\n")
        if deck.grav_elset_str:
            f.write(deck.grav_elset_str + "\n")
        f.write(nsets_str(p.fem.nsets) + "\n")
        f.write(nsets_str(assembly.fem.nsets) + "\n")
        f.write(solid_sec_str(p) + "\n")
        f.write(shell_sec_str(p) + "\n")
        f.write(beam_sec_str(p, report=len(steps) > 0) + "\n")

        # Assembly Level information
        u1_materials = {el.fem_sec.material.name for el in p.fem.elements.lines if el.id in deck.u1_elements}
        f.write("\n".join([material_str(mat, mat.name in u1_materials) for mat in p.materials]) + "\n")
        f.write(constraints_str(p, assembly) + "\n")
        f.write("\n".join([bc_str(x) for x in p.fem.bcs + assembly.fem.bcs]) + "\n")
        # A model with no analysis step is still a deck worth writing -- it simply has no
        # *STEP block. ``ada convert --to calculix`` produces exactly that (a conversion
        # carries geometry and mesh, not an analysis), and the abaqus writer already guards
        # the same way.
        if len(steps) > 0:
            f.write(steps_str(steps, deck))
        else:
            f.write("** No steps\n")

    logger.info(f'Created a Calculix input deck at "{analysis_dir}"')


def all_steps(assembly: Assembly) -> list:
    """Every step the deck carries: the assembly's, then each part FEM's -- where ``Part.to_fem_obj`` puts the step
    its concept load cases become. Only the assembly's first used to be written, so a second step and a part's step
    were left out of the deck."""
    return list(assembly.fem.steps) + [s for p in assembly.get_all_subparts() for s in p.fem.steps]


#: The element set the deck's ``*DLOAD GRAV`` names: every structural element CalculiX takes a body force on.
GRAV_ELSET = "ADA_GRAV"


@dataclass
class DeckContext:
    """What the step writers need to know about the model the deck is written from."""

    part: Part
    u1_elements: frozenset = frozenset()
    grav_elset: str | None = None
    grav_elset_str: str = ""

    @staticmethod
    def of(part: Part, steps) -> DeckContext:
        from ada.fem.shapes import definitions as shape_def

        from .write_elements import is_u1

        u1 = frozenset(el.id for el in part.fem.elements.lines if is_u1(el))
        has_gravity = any(ld.type in (Load.TYPES.GRAVITY, Load.TYPES.ACC) for st in steps for ld in st.loads)
        grav, grav_str = None, ""
        if has_gravity:
            # ccx stops at a body force on a U1 element ("*ERROR in e_c3d_u1: no body forces"), so the weight of
            # U1 beams is written as nodal loads (write_loads.gravity_load_str) and the GRAV set holds the rest.
            members = sorted(
                (
                    el
                    for el in part.fem.elements
                    if shape_def.is_structural(el.type) and el.fem_sec is not None and el.id not in u1
                ),
                key=attrgetter("id"),
            )
            if members:
                if GRAV_ELSET in part.fem.elsets:
                    raise ValueError(f"calculix writer: the model already has an element set named {GRAV_ELSET!r}")
                grav = GRAV_ELSET
                newline = NewLine(15)
                ids = " ".join(f"{el.id}," + next(newline) for el in members).rstrip()[:-1]
                grav_str = f"*Elset, elset={GRAV_ELSET}\n {ids}"
        return DeckContext(part, u1, grav, grav_str)


class CcxSecTypes:
    GENERAL = "GENERAL"
    BOX = "BOX"
    PIPE = "PIPE"


#: The Timoshenko shear coefficient written on every ``U1`` section: large enough that the element is
#: Euler-Bernoulli.
#:
#: The fifth value of a ``U1`` general section is the shear coefficient (CalculiX 2.23 manual, 6.2.46 and 6.3.3), not
#: the torsion constant adapy wrote there (Abaqus' ``*BEAM GENERAL SECTION`` layout). On an IPE300 that put kappa =
#: 2.0e-7 into the shear stiffness and a simply supported 4 m beam under 1 kN at mid-span deflected 11.79 m. A
#: realistic kappa does not give Timoshenko's answer either: measured on the same beam, U1's shear term is
#: mesh-dependent and below Euler-Bernoulli on a coarse mesh (kappa = 1, 2 elements: 7.32e-5 m against 7.94e-5 m
#: Euler-Bernoulli and 8.18e-5 m Timoshenko; 32 elements: 8.15e-5 m). With kappa = 1e8 the deflection is
#: Euler-Bernoulli's to the 7 digits ccx prints, simply supported and cantilevered, at 8, 64 and 256 elements (1e10
#: starts to lose digits to conditioning at 256). Code_Aster's POU_D_E, which adapy writes, is Euler-Bernoulli too.
U1_SHEAR_COEFFICIENT = 1.0e8


def beam_str(fem_sec: FemSection, report: bool = True):
    """The ``*BEAM SECTION`` of a beam element set: ``SECTION=GENERAL`` on ``U1``, ``BOX`` or ``PIPE`` on ``B32R``.

    A ``U1`` section is ``A, I11, I12, I22, kappa`` and a direction, where -- measured, ccx 2.23, an IPE300 4 m simply
    supported under 1 kN at mid-span -- the first inertia carries bending *along* the direction given: with
    ``(0, 1, 0)`` a load along z deflected the beam by ``P L^3 / (48 E I22)`` and a load along y by
    ``P L^3 / (48 E I11)``, each to 7 digits. adapy wrote ``Iy`` first with the section's local y, so a vertical load
    met the weak axis. ``Iy`` (bending along local z) is now written first, with local z.
    """
    from ada.fem.formats import conversion_report

    from .write_elements import B32R, beam_element_type

    top_line = f"** Section: {fem_sec.elset.name}  Profile: {fem_sec.elset.name}"
    lines = fem_sec.elset.members
    el_type = beam_element_type(lines[0].type, fem_sec) if lines else None
    if el_type == B32R:
        sec = fem_sec.section
        n1 = ", ".join(str(float(x)) for x in fem_sec.local_y)
        head = f"*Beam Section, elset={fem_sec.elset.name}, material={fem_sec.material.name}, section="
        if get_section_str(fem_sec) == CcxSecTypes.BOX:
            # a along local 1 (y), b along local 2 (z); t1..t4 the walls on +1, +2, -1, -2.
            if sec.t_w * 2 > min(sec.w_top, sec.w_btn):
                raise ValueError("Web thickness cannot be larger than section width")
            data = f"{sec.w_top}, {sec.h}, {sec.t_w}, {sec.t_ftop}, {sec.t_w}, {sec.t_fbtn}"
            return f"{top_line}\n{head}BOX\n{data}\n {n1}"
        return f"{top_line}\n{head}PIPE\n{sec.r}, {sec.wt}\n {n1}"

    gp = eval_general_properties(fem_sec.section)
    if abs(gp.Iyz) > 1e-9 * (gp.Iy + gp.Iz):
        raise IncompatibleElements(
            f"calculix writer: section {fem_sec.section.name!r} ({fem_sec.elset.name}) has a product of inertia "
            f"Iyz = {gp.Iyz}; a U1 general section takes principal axes only (I12 must be zero, CalculiX 2.23 "
            f"manual 6.3.3)"
        )
    rep = conversion_report.current()
    i_p = gp.Iy + gp.Iz
    if report and abs(i_p - gp.Ix) > 1e-6 * i_p:
        # Measured, ccx 2.23: a 4 m U1 cantilever of an IPE300 under a tip torque of 1 kN m twisted by
        # T L / (G (Iy + Iz)) = 5.757449e-4 rad, the torsion constant being 2.0e-7 m^4 against Iy + Iz = 8.6e-5 m^4.
        # Nothing in the section changes it: ccx refuses a Poisson ratio above 0.5, the only other way to G I_p.
        rep.approximated(
            STAGE,
            "*BEAM SECTION",
            fem_sec.elset.name,
            "CalculiX's U1 beam takes its torsional stiffness from the polar moment Iy + Iz, not the section's "
            "torsion constant; the beam is stiffer (or softer) in torsion by the ratio given",
            section=fem_sec.section.name,
            torsion_constant=gp.Ix,
            polar_moment=i_p,
            ratio=i_p / gp.Ix if gp.Ix else float("inf"),
        )
    if report:
        rep.note(
            STAGE,
            "*BEAM SECTION",
            fem_sec.elset.name,
            "written as CalculiX U1 beams (Euler-Bernoulli, shear coefficient 1e8); ccx's nodal forces (RF) at U1 nodes "
            "are the elements' end forces and not reactions -- measured on a simply supported beam, +500 and -500 N for "
            "two 500 N reactions",
            section=fem_sec.section.name,
        )
    n3 = ", ".join(str(float(x)) for x in fem_sec.local_z)
    return f"""{top_line}
*Beam Section, elset={fem_sec.elset.name}, material={fem_sec.material.name}, section=GENERAL
 {gp.Ax}, {gp.Iy}, 0.0, {gp.Iz}, {U1_SHEAR_COEFFICIENT}
 {n3}"""


def get_section_str(fem_sec: FemSection):
    from ada.sections.categories import BaseTypes

    sec_type = fem_sec.section.type
    if "section_type" in fem_sec.metadata.keys():
        return fem_sec.metadata["section_type"]

    if sec_type == BaseTypes.BOX:
        return CcxSecTypes.BOX
    elif sec_type == BaseTypes.TUBULAR:
        return CcxSecTypes.PIPE
    return CcxSecTypes.GENERAL


def nodes_str(fem_nodes: Nodes) -> str:
    if len(fem_nodes) == 0:
        return "** No Nodes"

    f = "{nid:>7}, {x:>13.6f}, {y:>13.6f}, {z:>13.6f}"
    n_ = (f.format(nid=no.id, x=no[0], y=no[1], z=no[2]) for no in sorted(fem_nodes, key=attrgetter("id")))

    return "*NODE\n" + "\n".join(n_).rstrip()


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
    solids = part.fem.sections.solids
    return "\n".join([solid_section_str(so) for so in solids]) if len(solids) > 0 else "** No solid sections"


def shell_sec_str(part):
    shells = part.fem.sections.shells
    return "\n".join([shell_section_str(so) for so in shells]) if len(shells) > 0 else "** No shell sections"


def beam_sec_str(part, report: bool = True):
    beam_secs = [beam_str(sec, report) for sec in part.fem.sections.lines]
    return "\n".join(beam_secs).rstrip() if len(beam_secs) > 0 else "** No beam sections"


def material_str(material, elastic_only: bool = False):
    """A ``*MATERIAL`` card. ``elastic_only`` for a material of U1 beams: U1 is linear elastic (CalculiX 2.23 manual
    6.2.46) and a ``*PLASTIC`` card on it stops ccx -- measured, "*ERROR in resultsmech_u1: no anisotropic material".
    The plasticity is then left out of the card, which every element of that material shares, and reported; the writer
    used to set the model's own plasticity model to None for that, without a word."""
    if "aba_inp" in material.metadata.keys():
        return material.metadata["aba_inp"]
    if "rayleigh_damping" in material.metadata.keys():
        alpha, beta = material.metadata["rayleigh_damping"]
    else:
        alpha, beta = None, None

    no_compression = material._metadata["no_compression"] if "no_compression" in material._metadata.keys() else False
    compr_str = "\n*No Compression" if no_compression is True else ""

    pl_str = ""
    if elastic_only and material.model.plasticity_model is not None:
        from ada.fem.formats import conversion_report

        conversion_report.current().approximated(
            STAGE,
            "*PLASTIC",
            material.name,
            "the material of U1 beams, which are linear elastic in CalculiX; written elastic, for every element of it",
        )
    elif material.model.plasticity_model is not None:
        pl_model = material.model.plasticity_model
        if pl_model.eps_p is not None and len(pl_model.eps_p) != 0:
            pl_str = "\n*Plastic\n"
            pl_str += "\n".join(
                ["{x:>12.5E}, {y:>10}".format(x=x, y=y) for x, y in zip(pl_model.sig_p, pl_model.eps_p)]
            )

    d_str = ""
    if alpha is not None and beta is not None:
        d_str = "\n*Damping, alpha={alpha}, beta={beta}".format(alpha=material.model.alpha, beta=material.model.beta)

    exp_str = ""
    if material.model.zeta is not None and material.model.zeta != 0.0:
        exp_str = "\n*Expansion\n {zeta}".format(zeta=material.model.zeta)

    return f"""*Material, name={material.name}
*Elastic
 {material.model.E:.6E},  {material.model.v}{compr_str}
*Density
 {material.model.rho},{exp_str}{d_str}{pl_str}"""


def bc_str(bc: Bc) -> str:
    from ada.fem.formats import conversion_report
    from ada.fem.formats.abaqus.write.write_bc import is_settlement

    if is_settlement(bc):
        # The *BOUNDARY lines below carry no value, so the dofs are held at zero. A prescribed displacement belongs to
        # a load case (or every one) and its own *STEPs, which this writer does not lay out.
        conversion_report.current().omitted(
            STAGE,
            "*BOUNDARY",
            bc.name,
            "a prescribed displacement; its dofs are written held at zero and the values are not written",
            values=", ".join(f"{d}={m}" for d, m in zip(bc.dofs, bc.magnitudes or ()) if m not in (None, 0, 0.0)),
        )
    ampl_ref_str = "" if bc.amplitude is None else ", amplitude=" + bc.amplitude.name

    aba_type = abaqus_bc_type(bc.type)

    dofs_str = ""
    for dof, magn in zip(bc.dofs, bc.magnitudes):
        if dof is None:
            continue
        # magn_str = f", {magn:.4f}" if magn is not None else ""

        if bc.type in ["connector displacement", "connector velocity"] or isinstance(dof, str):
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


def surface_str(surface: Surface) -> str:
    top_line = f"*Surface, type={surface.type}, name={surface.name}"
    id_refs_str = "\n".join([f"{m[0]}, {m[1]}" for m in surface.id_refs]).strip()
    if surface.id_refs is None:
        if surface.type == "NODE":
            add_str = surface.weight_factor
        else:
            add_str = surface.el_face_index
        if surface.fem_set.name in surface.parent.elsets.keys():
            return f"{top_line}\n{surface.fem_set.name}, {add_str}"
        else:
            return f"""{top_line}
{surface.fem_set.name}, {add_str}"""
    else:
        return f"""{top_line}
{id_refs_str}"""


def interactions_str(interaction: Interaction) -> str:
    from ada.fem.steps import Step

    if interaction.type == "SURFACE":
        adjust_par = interaction.metadata.get("adjust", None)
        geometric_correction = interaction.metadata.get("geometric_correction", None)
        small_sliding = interaction.metadata.get("small_sliding", None)

        stpstr = f"*Contact Pair, interaction={interaction.interaction_property.name}"

        if small_sliding is not None:
            stpstr += f", {small_sliding}"

        if issubclass(type(interaction.parent), Step):
            step = interaction.parent
            assert isinstance(step, Step)
            stpstr += "" if type(step) is StepExplicit else f", type={interaction.surface_type}"
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
