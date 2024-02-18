from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from .write_amplitudes import amplitudes_str
from .write_bc import boundary_conditions_str
from .write_connectors import connector_section_str, connector_str
from .write_constraints import constraints_str
from .write_elements import elements_str
from .write_interactions import eval_interactions, int_prop_str
from .write_main_inp import write_main_inp_str
from .write_masses import masses_str
from .write_materials import materials_str
from .write_nodes import nodes_str
from .write_orientations import orientations_str
from .write_parts import write_all_parts
from .write_predefined_state import predefined_fields_str
from .write_sets import elsets_str, nsets_str
from .write_steps import write_step
from .write_surfaces import surfaces_str

if TYPE_CHECKING:
    from ada.api.spatial import Assembly

__all__ = ["to_fem"]


def to_fem(
    assembly: Assembly, name, analysis_dir=None, metadata=None, writable_obj: StringIO = None, model_data_only=False
):
    """Build the Abaqus Analysis input deck"""

    # Write part bulk files
    write_all_parts(assembly, analysis_dir)

    # Write Assembly level files
    core_dir = analysis_dir / r"core_input_files"
    core_dir.mkdir(parents=True, exist_ok=True)

    afem = assembly.fem
    all_fem_parts = [p.fem for p in assembly.get_all_subparts(include_self=True)]

    # Main Input File
    with open(analysis_dir / f"{name}.inp", "w") as d:
        d.write(write_main_inp_str(assembly, analysis_dir))

    # Connector Sections
    all_con_sections = [con for fem_part in all_fem_parts for con in fem_part.connector_sections.values()]
    with open(core_dir / "connector_sections.inp", "w") as d:
        if len(all_con_sections) > 0:
            for con_section in all_con_sections:
                if con_section.str_override is not None:
                    d.write(con_section.str_override)
                    continue
                d.write(connector_section_str(con_section))
        else:
            d.write("** No Connector Sections")

    # Connectors
    all_connectors = [con for fem_part in all_fem_parts for con in fem_part.elements.connectors]
    with open(core_dir / "connectors.inp", "w") as d:
        if len(all_connectors) > 0:
            for con in all_connectors:
                d.write(connector_str(con, True))
            # d.write(connectors_str(afem))
        else:
            d.write("** No Connectors")

    # Constraints
    with open(core_dir / "constraints.inp", "w") as d:
        d.write(constraints_str(afem, True) if len(afem.constraints.keys()) > 0 else "**")

    # Assembly data
    with open(core_dir / "assembly_data.inp", "w") as d:
        assembly_nodes_str = "** No Nodes"
        if len(afem.nodes) > 0:
            assembly_nodes_str = nodes_str(afem)
        d.write(f"{assembly_nodes_str}\n")
        d.write(f"{nsets_str(afem, True)}\n")
        d.write(f"{elsets_str(afem, True)}\n")
        d.write(f"{surfaces_str(afem, True)}\n")
        d.write(orientations_str(afem, True) + "\n")
        d.write(elements_str(afem, True) + "\n")
        d.write(masses_str(afem, True))

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
