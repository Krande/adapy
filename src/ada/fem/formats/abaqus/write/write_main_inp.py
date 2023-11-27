from __future__ import annotations

import os
import shutil
from typing import TYPE_CHECKING

from .templates import main_inp_str
from .write_interactions import interact_str
from .write_predefined_state import predefined_fields_str
from .write_steps import constraint_control, main_step_inp_str

if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part


def write_main_inp_str(assembly: Assembly, analysis_dir) -> str:
    part_str = "\n".join(map(part_inp_str, filter(skip_if_this, assembly.get_all_subparts())))
    i_str = "\n".join((instance_str(i, analysis_dir) for i in filter(inst_skip, assembly.get_all_subparts()))).rstrip()
    all_fem_parts = [p.fem for p in assembly.get_all_subparts(include_self=True)]

    step_str = "** No Steps added"
    incl = "*INCLUDE,INPUT=core_input_files"
    ampl_str = "**"
    consec_str = "**"
    iprop_str = "**"
    int_str = "**"

    if len(assembly.fem.steps) > 0:
        step_str = "\n".join(list(map(main_step_inp_str, assembly.fem.steps))).rstrip()
    if len(assembly.fem.amplitudes) > 0:
        ampl_str = f"{incl}\\amplitude_data.inp"
    if len([con for fem_part in all_fem_parts for con in fem_part.connector_sections.values()]) > 0:
        consec_str = f"{incl}\\connector_sections.inp"
    if len(assembly.fem.intprops) > 0:
        iprop_str = f"{incl}\\interaction_prop.inp"
    if interact_str(assembly.fem) != "" or predefined_fields_str(assembly.fem) != "":
        int_str = f"{incl}\\interactions.inp"

    mat_str = f"{incl}\\materials.inp"
    fix_str = f"{incl}\\bc_data.inp"

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
        constr_ctrl=constraint_control(assembly.fem),
    )


def part_inp_str(part: "Part") -> str:
    return """**\n*Part, name={name}\n*INCLUDE,INPUT=bulk_{name}\\{inp_file}\n*End Part\n**""".format(
        name=part.name, inp_file="aba_bulk.inp"
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


def skip_if_this(p):
    if p.fem.initial_state is not None:
        return False

    return len(p.fem.elements) + len(p.fem.nodes) > 0


def inst_skip(p):
    if p.fem.initial_state is not None:
        return True

    return len(p.fem.elements) + len(p.fem.nodes) > 0
