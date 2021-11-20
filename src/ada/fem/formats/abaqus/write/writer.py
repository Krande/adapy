import os
import shutil
from io import StringIO
from operator import attrgetter
from typing import TYPE_CHECKING

from .write_amplitudes import amplitudes_str
from .write_bc import boundary_conditions_str
from .write_connectors import connector_sections_str, connectors_str
from .write_constraints import constraints_str
from .write_elements import elements_str
from .write_interactions import eval_interactions, int_prop_str, interact_str
from .write_masses import masses_str
from .write_materials import materials_str
from .write_orientations import orientations_str
from .write_parts import write_all_parts
from .write_predefined_state import predefined_fields_str
from .write_sets import elsets_str, nsets_str
from .write_steps import constraint_control, main_step_inp_str, write_step
from .write_surfaces import surfaces_str

if TYPE_CHECKING:
    from ada.concepts.levels import Assembly, Part

__all__ = ["to_fem"]


def to_fem(assembly: "Assembly", name, analysis_dir=None, metadata=None, writable_obj: StringIO = None):
    """Build the Abaqus Analysis input deck"""

    # Write part bulk files
    write_all_parts(assembly, analysis_dir)

    # Write Assembly level files
    core_dir = analysis_dir / r"core_input_files"
    os.makedirs(core_dir)

    afem = assembly.fem

    # Main Input File
    with open(analysis_dir / f"{name}.inp", "w") as d:
        d.write(main_inp_str(assembly, analysis_dir))

    # Connector Sections
    with open(core_dir / "connector_sections.inp", "w") as d:
        d.write(connector_sections_str(afem))

    # Connectors
    with open(core_dir / "connectors.inp", "w") as d:
        d.write(connectors_str(afem) if len(list(afem.elements.connectors)) > 0 else "**")

    # Constraints
    with open(core_dir / "constraints.inp", "w") as d:
        d.write(constraints_str(afem) if len(afem.constraints) > 0 else "**")

    # Assembly data
    with open(core_dir / "assembly_data.inp", "w") as d:
        if len(afem.nodes) > 0:
            assembly_nodes_str = (
                "*Node\n"
                + "".join(
                    [
                        f"{no.id:>7}, {no.x:>13.6f}, {no.y:>13.6f}, {no.z:>13.6f}\n"
                        for no in sorted(afem.nodes, key=attrgetter("id"))
                    ]
                ).rstrip()
            )
        else:
            assembly_nodes_str = "** No Nodes"

        d.write(f"{assembly_nodes_str}\n")
        d.write(f"{nsets_str(afem)}\n")
        d.write(f"{elsets_str(afem)}\n")
        d.write(f"{surfaces_str(afem)}\n")
        d.write(orientations_str(afem, True) + "\n")
        d.write(elements_str(afem, True) + "\n")
        d.write(masses_str(afem))

    # Amplitude data
    with open(core_dir / "amplitude_data.inp", "w") as d:
        d.write(amplitudes_str(afem))

    # Interaction Properties
    with open(core_dir / "interaction_prop.inp", "w") as d:
        d.write(int_prop_str(afem))

    # Interactions data
    eval_interactions(assembly, analysis_dir)
    with open(core_dir / "interactions.inp", "a") as d:
        d.write(predefined_fields_str(afem))

    # Materials data
    with open(core_dir / "materials.inp", "w") as d:
        d.write(materials_str(assembly))

    # Boundary Condition data
    with open(core_dir / "bc_data.inp", "w") as d:
        d.write(boundary_conditions_str(assembly))

    # Analysis steps
    for step_in in afem.steps:
        write_step(step_in, analysis_dir)

    print(f'Created an Abaqus input deck at "{analysis_dir}"')


def main_inp_str(assembly: "Assembly", analysis_dir):
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

    part_str = "\n".join(map(part_inp_str, filter(skip_if_this, assembly.get_all_subparts())))
    i_str = "\n".join((instance_str(i, analysis_dir) for i in filter(inst_skip, assembly.get_all_subparts()))).rstrip()

    if len(assembly.fem.steps) > 0:
        step_str = "\n".join(list(map(main_step_inp_str, assembly.fem.steps))).rstrip()
    else:
        step_str = "** No Steps added"

    incl = "*INCLUDE,INPUT=core_input_files"
    ampl_str = f"\n{incl}\\amplitude_data.inp" if amplitudes_str(assembly.fem) != "" else "**"
    consec_str = f"\n{incl}\\connector_sections.inp" if connector_sections_str(assembly.fem) != "" else "**"
    iprop_str = f"{incl}\\interaction_prop.inp" if int_prop_str(assembly.fem) != "" else "**"
    if interact_str(assembly) != "" or predefined_fields_str != "":
        int_str = f"{incl}\\interactions.inp"
    else:
        int_str = "**"
    mat_str = f"{incl}\\materials.inp"
    fix_str = f"{incl}\\bc_data.inp" if boundary_conditions_str(assembly) != "" else "**"

    return main_inp_str.format(
        part_str=part_str,
        instance_str=i_str,
        mat_str=mat_str,
        fix_str=fix_str,
        step_str=step_str,
        ampl_str=ampl_str,
        consec_str=consec_str,
        int_prop_str=iprop_str,
        interact_str=int_str,
        constr_ctrl=constraint_control,
    )


def instance_str(part: "Part", analysis_dir) -> str:
    if part.fem.initial_state is None:
        return f"""**\n*Instance, name={part.fem.instance_name}, part={part.name}\n*End Instance"""

    istep = part.fem.initial_state
    analysis_name = os.path.basename(istep.initial_state_file.replace(".inp", ""))
    source_dir = os.path.dirname(istep.initial_state_file)
    for f in os.listdir(source_dir):
        if analysis_name in f:
            dest_file = os.path.join(analysis_dir, os.path.basename(f))
            shutil.copy(os.path.join(source_dir, f), dest_file)
    return f"""*Instance, library={analysis_name}, instance={istep.initial_state_part.fem.instance_name}
**
** PREDEFINED FIELD
**
** Name: {part.fem.initial_state.name}   Type: Initial State
*Import, state=yes, update=no
*End Instance"""


def part_inp_str(part: "Part") -> str:
    return """**\n*Part, name={name}\n*INCLUDE,INPUT=bulk_{name}\\{inp_file}\n*End Part\n**""".format(
        name=part.name, inp_file="aba_bulk.inp"
    )
