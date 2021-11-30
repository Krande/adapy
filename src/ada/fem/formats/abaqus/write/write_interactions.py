from typing import TYPE_CHECKING

from ada.fem.interactions import ContactTypes
from ada.fem.steps import Step, StepExplicit

from .helper_utils import get_instance_name

if TYPE_CHECKING:
    from ada import FEM, Assembly
    from ada.fem import Interaction, InteractionProperty


def interact_str(fem: "FEM"):
    return "\n".join([interaction_str(interact) for interact in fem.interactions.values()])


def interaction_str(interaction: "Interaction") -> str:
    # Allowing Free text to be parsed directly through interaction class.
    if "aba_bulk" in interaction.metadata.keys():
        return interaction.metadata["aba_bulk"]

    contact_mod = interaction.metadata["contact_mod"] if "contact_mod" in interaction.metadata.keys() else "NEW"
    contact_incl = (
        interaction.metadata["contact_inclusions"]
        if "contact_inclusions" in interaction.metadata.keys()
        else "ALL EXTERIOR"
    )

    top_str = f"**\n** Interaction: {interaction.name}"
    if interaction.type == ContactTypes.SURFACE:
        adjust_par = interaction.metadata.get("adjust", None)
        geometric_correction = interaction.metadata.get("geometric_correction", None)
        small_sliding = interaction.metadata.get("small_sliding", None)

        first_line = "" if small_sliding is None else f", {small_sliding}"

        if issubclass(type(interaction.parent), Step):
            step = interaction.parent
            first_line += "" if type(step) is StepExplicit else f", type={interaction.surface_type}"
        else:
            first_line += f", type={interaction.surface_type}"

        if interaction.constraint is not None:
            first_line += f", mechanical constraint={interaction.constraint}"

        if adjust_par is not None:
            first_line += f", adjust={adjust_par}" if adjust_par is not None else ""

        if geometric_correction is not None:
            first_line += f", geometric correction={geometric_correction}"

        return f"""{top_str}
*Contact Pair, interaction={interaction.interaction_property.name}{first_line}
{get_instance_name(interaction.surf1, True)}, {get_instance_name(interaction.surf2, True)}"""
    else:
        return f"""{top_str}\n*Contact, op={contact_mod}
*Contact Inclusions, {contact_incl}
*Contact Property Assignment
 ,  , {interaction.interaction_property.name}"""


def interaction_prop_str(int_prop: "InteractionProperty") -> str:
    iprop_str = f"*Surface Interaction, name={int_prop.name}\n"

    # Friction
    iprop_str += f"*Friction\n{int_prop.friction},\n"

    # Behaviours
    tab_str = (
        "\n" + "\n".join(["{:>12.3E},{:>12.3E}".format(d[0], d[1]) for d in int_prop.tabular])
        if int_prop.tabular is not None
        else ""
    )
    iprop_str += f"*Surface Behavior, pressure-overclosure={int_prop.pressure_overclosure}{tab_str}"

    return iprop_str.rstrip()


def int_prop_str(fem: "FEM"):
    iprop_str = "\n".join([interaction_prop_str(iprop) for iprop in fem.intprops.values()])
    smoothings = fem.metadata.get("surf_smoothing", None)
    if smoothings is not None:
        iprop_str += "\n"
        for smooth in smoothings:
            name = smooth["name"]
            iprop_str += f"*Surface Smoothing, name={name}\n"
            iprop_str += smooth["bulk"] + "\n"
    return iprop_str


def eval_interactions(assembly: "Assembly", analysis_dir):
    if len(assembly.fem.steps) > 0:
        initial_step = assembly.fem.steps[0]
        if type(initial_step) is StepExplicit:
            for interact in assembly.fem.interactions.values():
                if interact.name not in initial_step.interactions.keys():
                    initial_step.add_interaction(interact)
                    return

    with open(analysis_dir / "core_input_files/interactions.inp", "w") as d:
        istr = interact_str(assembly.fem)
        if istr != "":
            d.write(istr)
            d.write("\n")
