import os
from itertools import chain
from operator import attrgetter
from typing import TYPE_CHECKING, Union

from ada.fem import Amplitude, Interaction, InteractionProperty, PredefinedField, Spring
from ada.fem.conversion_utils import convert_ecc_to_mpc, convert_hinges_2_couplings
from ada.fem.interactions import ContactTypes
from ada.fem.steps import (
    Step,
    StepEigen,
    StepEigenComplex,
    StepExplicit,
    StepImplicit,
    StepSteadyState,
)

from .helper_utils import get_instance_name
from .write_connectors import connector_section_str, connector_str
from .write_constraints import constraint_str
from .write_elements import elements_str
from .write_masses import masses_str
from .write_materials import materials_str
from .write_orientations import orientations_str
from .write_output_requests import predefined_field_str
from .write_sections import section_str
from .write_sets import aba_set_str
from .write_steps import abaqus_step_str
from .write_surfaces import surface_str

if TYPE_CHECKING:
    from ada.concepts.levels import Assembly, Part

__all__ = ["to_fem"]

log_fin = "Please check your result and input. This is not a validated method of solving this issue"


_step_types = Union[StepEigen, StepImplicit, StepExplicit, StepSteadyState, StepEigenComplex]


def to_fem(assembly: "Assembly", name, analysis_dir=None, metadata=None):
    a = AbaqusWriter(assembly)
    a.write(name, analysis_dir)
    print(f'Created an Abaqus input deck at "{a.analysis_path}"')


class AbaqusWriter:
    _subr_path = None
    _subroutine = None
    _imperfections = str()
    _node_hist_out = ["UT", "VT", "AT"]
    _con_hist_out = ["CTF", "CVF", "CP", "CU"]
    _rf_node_out = ["RT"]
    analysis_path = None
    parts_and_assemblies = True

    def __init__(self, assembly: "Assembly"):
        self.assembly = assembly

    def write(self, name, analysis_dir):
        """Build the Abaqus Analysis folder"""
        print("creating: {0}".format(name))

        self.analysis_path = analysis_dir

        for part in self.assembly.get_all_subparts():
            if len(part.fem.elements) == 0:
                continue
            if self.assembly.convert_options.hinges_to_coupling is True:
                convert_hinges_2_couplings(part.fem)

            if self.assembly.convert_options.ecc_to_mpc is True:
                convert_ecc_to_mpc(part.fem)

            self.write_part_bulk(part)

        core_dir = self.analysis_path / r"core_input_files"
        os.makedirs(core_dir)

        # Main Input File
        with open(self.analysis_path / f"{name}.inp", "w") as d:
            d.write(self.main_inp_str)

        # Connector Sections
        with open(core_dir / "connector_sections.inp", "w") as d:
            d.write(self.connector_sections_str)

        # Connectors
        with open(core_dir / "connectors.inp", "w") as d:
            d.write(self.connectors_str if len(list(self.assembly.fem.elements.connectors)) > 0 else "**")

        # Constraints
        with open(core_dir / "constraints.inp", "w") as d:
            d.write(self.constraints_str if len(self.assembly.fem.constraints) > 0 else "**")

        # Assembly data
        with open(core_dir / "assembly_data.inp", "w") as d:
            if len(self.assembly.fem.nodes) > 0:
                assembly_nodes_str = (
                    "*Node\n"
                    + "".join(
                        [
                            f"{no.id:>7}, {no.x:>13.6f}, {no.y:>13.6f}, {no.z:>13.6f}\n"
                            for no in sorted(self.assembly.fem.nodes, key=attrgetter("id"))
                        ]
                    ).rstrip()
                )
            else:
                assembly_nodes_str = "** No Nodes"
            d.write(f"{assembly_nodes_str}\n{self.nsets_str}\n{self.elsets_str}\n{self.surfaces_str}\n")
            d.write(orientations_str(self.assembly, self) + "\n")
            d.write(elements_str(self.assembly.fem, True) + "\n")
            d.write(masses_str(self.assembly.fem))

        # Amplitude data
        with open(core_dir / "amplitude_data.inp", "w") as d:
            d.write(self.amplitude_str)

        # Interaction Properties
        with open(core_dir / "interaction_prop.inp", "w") as d:
            d.write(self.int_prop_str)

        # Interactions data
        self.eval_interactions()
        with open(core_dir / "interactions.inp", "a") as d:
            d.write(self.predefined_fields_str)

        # Materials data
        with open(core_dir / "materials.inp", "w") as d:
            d.write(materials_str(self.assembly))

        # Boundary Condition data
        with open(core_dir / "bc_data.inp", "w") as d:
            d.write(self.bc_str)

        # Analysis steps
        for step_in in self.assembly.fem.steps:
            self.write_step(step_in)

    def eval_interactions(self):
        if len(self.assembly.fem.steps) > 0:
            initial_step = self.assembly.fem.steps[0]
            if type(initial_step) is StepExplicit:
                for interact in self.assembly.fem.interactions.values():
                    if interact.name not in initial_step.interactions.keys():
                        initial_step.add_interaction(interact)
                        return
        with open(self.analysis_path / "core_input_files/interactions.inp", "w") as d:
            if self.interact_str != "":
                d.write(self.interact_str)
                d.write("\n")

    def write_step(self, step_in: _step_types):
        step_str = abaqus_step_str(step_in)
        with open(self.analysis_path / "core_input_files" / f"step_{step_in.name}.inp", "w") as d:
            d.write(step_str)
            if "*End Step" not in step_str:
                d.write("*End Step\n")

    def write_part_bulk(self, part_in: "Part"):
        bulk_path = self.analysis_path / f"bulk_{part_in.name}"
        bulk_file = bulk_path / "aba_bulk.inp"
        os.makedirs(bulk_path, exist_ok=True)

        if part_in.fem.initial_state is not None:
            with open(bulk_file, "w") as d:
                d.write("** This part is replaced by an initial state step")
        else:
            fempart = AbaqusPartWriter(part_in)
            with open(bulk_file, "w") as d:
                d.write(fempart.bulk_str)

    def inst_inp_str(self, part: "Part") -> str:
        if part.fem.initial_state is not None:
            import shutil

            istep = part.fem.initial_state
            analysis_name = os.path.basename(istep.initial_state_file.replace(".inp", ""))
            source_dir = os.path.dirname(istep.initial_state_file)
            for f in os.listdir(source_dir):
                if analysis_name in f:
                    dest_file = os.path.join(self.analysis_path, os.path.basename(f))
                    shutil.copy(os.path.join(source_dir, f), dest_file)
            return f"""*Instance, library={analysis_name}, instance={istep.initial_state_part.fem.instance_name}
**
** PREDEFINED FIELD
**
** Name: {part.fem.initial_state.name}   Type: Initial State
*Import, state=yes, update=no
*End Instance"""
        else:
            return f"""**\n*Instance, name={part.fem.instance_name}, part={part.name}\n*End Instance"""

    @property
    def constraint_control(self):
        constraint_ctrl_on = True
        for step in self.assembly.fem.steps:
            if type(step) == StepExplicit:
                constraint_ctrl_on = False
        return "**" if constraint_ctrl_on is False else "*constraint controls, print=yes"

    @property
    def main_inp_str(self):
        """Main input file for Abaqus analysis"""
        from .templates import main_inp_str

        def skip_if_this(p):
            if p.fem.initial_state is not None:
                return False
            return len(p.fem.elements)

        def inst_skip(p):
            if p.fem.initial_state is not None:
                return True
            return len(p.fem.elements)

        part_str = "\n".join(map(part_inp_str, filter(skip_if_this, self.assembly.get_all_subparts())))
        instance_str = "\n".join(map(self.inst_inp_str, filter(inst_skip, self.assembly.get_all_subparts())))
        step_str = (
            "\n".join(list(map(main_step_inp_str, self.assembly.fem.steps))).rstrip()
            if len(self.assembly.fem.steps) > 0
            else "** No Steps added"
        )
        incl = "*INCLUDE,INPUT=core_input_files"
        ampl_str = f"\n{incl}\\amplitude_data.inp" if self.amplitude_str != "" else "**"
        consec_str = f"\n{incl}\\connector_sections.inp" if self.connector_sections_str != "" else "**"
        int_prop_str = f"{incl}\\interaction_prop.inp" if self.int_prop_str != "" else "**"
        if self.interact_str != "" or self.predefined_fields_str != "":
            interact_str = f"{incl}\\interactions.inp"
        else:
            interact_str = "**"
        mat_str = f"{incl}\\materials.inp"
        fix_str = f"{incl}\\bc_data.inp" if self.bc_str != "" else "**"

        return main_inp_str.format(
            part_str=part_str,
            instance_str=instance_str.rstrip(),
            mat_str=mat_str,
            fix_str=fix_str,
            step_str=step_str,
            ampl_str=ampl_str,
            consec_str=consec_str,
            int_prop_str=int_prop_str,
            interact_str=interact_str,
            constr_ctrl=self.constraint_control,
        )

    @property
    def elsets_str(self):
        return (
            "\n".join([aba_set_str(el, True) for el in self.assembly.fem.elsets.values()]).rstrip()
            if len(self.assembly.fem.elsets) > 0
            else "** No element sets"
        )

    @property
    def nsets_str(self):
        return (
            "\n".join([aba_set_str(no, True) for no in self.assembly.fem.nsets.values()]).rstrip()
            if len(self.assembly.fem.nsets) > 0
            else "** No node sets"
        )

    @property
    def surfaces_str(self):
        return (
            "\n".join([surface_str(s, True) for s in self.assembly.fem.surfaces.values()])
            if len(self.assembly.fem.surfaces) > 0
            else "** No Surfaces"
        )

    @property
    def constraints_str(self):
        return (
            "\n".join([constraint_str(c, True) for c in self.assembly.fem.constraints])
            if len(self.assembly.fem.constraints) > 0
            else "** No Constraints"
        )

    @property
    def connector_sections_str(self):
        return "\n".join([connector_section_str(consec) for consec in self.assembly.fem.connector_sections.values()])

    @property
    def connectors_str(self):
        return "\n".join([connector_str(con, True) for con in self.assembly.fem.elements.connectors])

    @property
    def amplitude_str(self):
        return "\n".join([amplitude_str(ampl) for ampl in self.assembly.fem.amplitudes.values()])

    @property
    def interact_str(self):
        return "\n".join([interaction_str(interact, self) for interact in self.assembly.fem.interactions.values()])

    @property
    def int_prop_str(self):
        iprop_str = "\n".join([interaction_prop_str(iprop) for iprop in self.assembly.fem.intprops.values()])
        smoothings = self.assembly.fem.metadata.get("surf_smoothing", None)
        if smoothings is not None:
            iprop_str += "\n"
            for smooth in smoothings:
                name = smooth["name"]
                iprop_str += f"*Surface Smoothing, name={name}\n"
                iprop_str += smooth["bulk"] + "\n"
        return iprop_str

    @property
    def predefined_fields_str(self):
        def eval_fields(pre_field: PredefinedField):
            return True if pre_field.type != PredefinedField.TYPES.INITIAL_STATE else False

        return "\n".join(
            [
                predefined_field_str(prefield)
                for prefield in filter(eval_fields, self.assembly.fem.predefined_fields.values())
            ]
        )

    @property
    def bc_str(self):
        from .write_bc import bc_str

        return "\n".join(
            chain.from_iterable(
                (
                    [bc_str(bc, True) for bc in self.assembly.fem.bcs],
                    [bc_str(bc, True) for p in self.assembly.get_all_parts_in_assembly() for bc in p.fem.bcs],
                )
            )
        )

    def __repr__(self):
        return "AbaqusWriter()"


class AbaqusPartWriter:
    def __init__(self, part: "Part"):
        self.part = part

    @property
    def bulk_str(self):

        return f"""** Abaqus Part {self.part.name}
** Exported using ADA OpenSim
*NODE
{self.nodes_str}
{elements_str(self.part.fem, False)}
{self.elsets_str}
{self.nsets_str}
{self.sections_str}
{masses_str(self.part.fem)}
{self.surfaces_str}
{self.constraints_str}
{self.springs_str}""".rstrip()

    @property
    def sections_str(self):
        return section_str(self.part.fem)

    @property
    def elsets_str(self):
        if len(self.part.fem.elsets) > 0:
            return "\n".join([aba_set_str(el, False) for el in self.part.fem.elsets.values()]).rstrip()
        else:
            return "** No element sets"

    @property
    def nsets_str(self):
        if len(self.part.fem.nsets) > 0:
            return "\n".join([aba_set_str(no, False) for no in self.part.fem.nsets.values()]).rstrip()
        else:
            return "** No node sets"

    @property
    def nodes_str(self):
        f = "{nid:>7}, {x:>13.6f}, {y:>13.6f}, {z:>13.6f}"
        return (
            "\n".join(
                [
                    f.format(nid=no.id, x=no[0], y=no[1], z=no[2])
                    for no in sorted(self.part.fem.nodes, key=attrgetter("id"))
                ]
            ).rstrip()
            if len(self.part.fem.nodes) > 0
            else "** No Nodes"
        )

    @property
    def surfaces_str(self):
        if len(self.part.fem.surfaces) > 0:
            return "\n".join([surface_str(s, False) for s in self.part.fem.surfaces.values()])
        else:
            return "** No Surfaces"

    @property
    def constraints_str(self):
        return (
            "\n".join([constraint_str(c, False) for c in self.part.fem.constraints])
            if len(self.part.fem.constraints) > 0
            else "** No Constraints"
        )

    @property
    def springs_str(self):
        return (
            "\n".join([spring_str(c) for c in self.part.fem.springs.values()])
            if len(self.part.fem.springs) > 0
            else "** No Springs"
        )

    @property
    def instance_move_str(self):
        if self.part.fem.metadata["move"] is not None:
            move = self.part.fem.metadata["move"]
            mo_str = "\n " + ", ".join([str(x) for x in move])
        else:
            mo_str = "\n 0.,        0.,           0."

        if self.part.fem.metadata["rotate"] is not None:
            rotate = self.part.fem.metadata["rotate"]
            vecs = ", ".join([str(x) for x in rotate[0]])
            vece = ", ".join([str(x) for x in rotate[1]])
            angle = rotate[2]
            move_str = """{move_str}\n {vecs}, {vece}, {angle}""".format(
                move_str=mo_str, vecs=vecs, vece=vece, angle=angle
            )
        else:
            move_str = "" if mo_str == "0.,        0.,           0." else mo_str
        return move_str


def main_step_inp_str(step: _step_types) -> str:
    return f"""*INCLUDE,INPUT=core_input_files\\step_{step.name}.inp"""


def part_inp_str(part: "Part") -> str:
    return """**\n*Part, name={name}\n*INCLUDE,INPUT=bulk_{name}\\{inp_file}\n*End Part\n**""".format(
        name=part.name, inp_file="aba_bulk.inp"
    )


def interaction_str(interaction: Interaction, fem_writer) -> str:
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
{get_instance_name(interaction.surf1, fem_writer)}, {get_instance_name(interaction.surf2, fem_writer)}"""
    else:
        return f"""{top_str}\n*Contact, op={contact_mod}
*Contact Inclusions, {contact_incl}
*Contact Property Assignment
 ,  , {interaction.interaction_property.name}"""


def amplitude_str(amplitude: Amplitude) -> str:
    name, x, y, smooth = amplitude.name, amplitude.x, amplitude.y, amplitude.smooth
    a = 1
    data = ""
    for i, var in enumerate(zip(list(x), list(y))):
        if a == 4:
            if i == len(list(x)) - 1:
                data += "{:.4E}, {:.4E}, ".format(var[0], var[1])
            else:
                data += "{:.4E}, {:.4E},\n         ".format(var[0], var[1])
            a = 0
        else:
            data += "{:.4E}, {:.4E}, ".format(var[0], var[1])
        a += 1

    smooth = ", DEFINITION=TABULAR, SMOOTH={}".format(smooth) if smooth is not None else ""
    amplitude = """*Amplitude, name={0}{2}\n         {1}\n""".format(name, data, smooth)
    return amplitude.rstrip()


def interaction_prop_str(int_prop: InteractionProperty):
    """

    :param int_prop:
    :type int_prop: ada.fem.InteractionProperty
    :return:
    """
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


def spring_str(spring: Spring) -> str:
    from ada.fem.shapes import ElemShape

    if spring.type in ElemShape.TYPES.spring1n:
        _str = f'** Spring El "{spring.name}"\n\n'
        for dof, row in enumerate(spring.stiff):
            for j, stiffness in enumerate(row):
                if dof == j:
                    _str += f"""*Spring, elset={spring.fem_set.name}
 {dof + 1}
 {stiffness:.6E}
{spring.id}, {spring.nodes[0].id}\n"""
        return _str.rstrip()
    else:
        raise ValueError(f'Currently unsupported spring type "{spring.type}"')
