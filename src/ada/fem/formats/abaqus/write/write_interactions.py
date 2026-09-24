from typing import TYPE_CHECKING

from ada.fem.interactions import ContactTypes
from ada.fem.steps import Step, StepExplicit

from ..grammar import Verbatim, format_number, render_keyword
from .helper_utils import get_instance_name, render_block

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

    top = ["", f"Interaction: {interaction.name}"]
    if interaction.type == ContactTypes.SURFACE:
        adjust_par = interaction.metadata.get("adjust", None)
        geometric_correction = interaction.metadata.get("geometric_correction", None)
        small_sliding = interaction.metadata.get("small_sliding", None)

        params = [("interaction", interaction.interaction_property.name)]
        if small_sliding is not None:
            params.append((small_sliding, None))

        if not (issubclass(type(interaction.parent), Step) and type(interaction.parent) is StepExplicit):
            params.append(("type", Verbatim(interaction.surface_type)))

        if interaction.constraint is not None:
            params.append(("mechanical constraint", interaction.constraint))

        if adjust_par is not None:
            params.append(("adjust", adjust_par))

        if geometric_correction is not None:
            params.append(("geometric correction", geometric_correction))

        surfs = f"{get_instance_name(interaction.surf1, True)}, {get_instance_name(interaction.surf2, True)}"
        return render_block("Contact Pair", params, [surfs], top)
    else:
        return (
            render_keyword("Contact", [("op", contact_mod)], (), top)
            + render_keyword("Contact Inclusions", [(contact_incl, None)])
            + render_block("Contact Property Assignment", (), [f" ,  , {interaction.interaction_property.name}"])
        )


def interaction_prop_str(int_prop: "InteractionProperty") -> str:
    iprop_str = render_keyword("Surface Interaction", [("name", int_prop.name)])

    # Friction
    iprop_str += render_keyword("Friction", (), [f"{int_prop.friction},"])

    # Behaviours
    # Exact: {:.3E} kept four significant digits of a pressure-overclosure table.
    tabular = int_prop.tabular if int_prop.tabular is not None else []
    tab = [f"{format_number(d[0])},{format_number(d[1])}" for d in tabular]
    iprop_str += render_keyword("Surface Behavior", [("pressure-overclosure", int_prop.pressure_overclosure)], tab)

    return iprop_str.rstrip()


def int_prop_str(fem: "FEM"):
    iprop_str = "\n".join([interaction_prop_str(iprop) for iprop in fem.intprops.values()])
    smoothings = fem.metadata.get("surf_smoothing", None)
    if smoothings is not None:
        iprop_str += "\n"
        for smooth in smoothings:
            iprop_str += render_keyword("Surface Smoothing", [("name", smooth["name"])])
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
