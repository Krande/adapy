from __future__ import annotations

from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.utils import is_quad8_shell_elem, is_tri6_shell_elem

from ..compatibility import check_compatibility
from .templates import el_convert_str, main_comm_str
from .write_bc import create_bc_str
from .write_materials import materials_str
from .write_med import med_elements, med_nodes
from .write_sections import create_sections_str
from .write_steps import create_step_str

if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part


def to_fem(assembly: Assembly, name, analysis_dir, metadata=None, model_data_only=False):
    """Write Code_Aster .med and .comm file from Assembly data"""
    from ada.materials.utils import shorten_material_names

    check_compatibility(assembly)

    if "info" not in metadata:
        metadata["info"] = dict(description="")

    p = get_fem_model_from_assembly(assembly)
    # Prepare model for
    shorten_material_names(assembly)
    # TODO: Implement support for multiple parts. Need to understand how submeshes in Salome and Code Aster works.
    # for p in filter(lambda x: len(x.fem.elements) != 0, assembly.get_all_parts_in_assembly(True)):

    filename = (analysis_dir / name).with_suffix(".med")
    write_to_med(name, p, filename)
    if model_data_only:
        return

    with open((analysis_dir / name).with_suffix(".comm"), "w") as f:
        f.write(create_comm_str(assembly, p))

    print(f'Created a Code_Aster input deck at "{analysis_dir}"')


def create_comm_str(assembly: Assembly, part: Part) -> str:
    """Create COMM file input str"""
    mat_str = materials_str(assembly)
    sections_str = create_sections_str(part.fem.sections)
    bcs = part.fem.bcs
    if assembly != part:
        bcs += assembly.fem.bcs
    bc_str = "\n".join([create_bc_str(bc) for bc in bcs])
    step_str = "\n".join([create_step_str(s, part) for s in assembly.fem.steps])

    type_tmpl_str = "_F(GROUP_MA={elset_str}, PHENOMENE='MECANIQUE', MODELISATION='{el_formula}',),"

    model_type_str = ""
    section_sets = ""
    input_mesh = "mesh"
    if len(part.fem.sections.lines) > 0:
        bm_elset_str = ",".join([f"'{bm_fs.elset.name}'" for bm_fs in part.fem.sections.lines])
        section_sets += f"bm_sets = ({bm_elset_str})\n"
        model_type_str += type_tmpl_str.format(elset_str="bm_sets", el_formula="POU_D_E")

    if len(part.fem.sections.shells) > 0:
        sh_elset_str = ""
        second_order = ""
        is_tri6 = False
        is_quad8 = False
        for sh_fs in part.fem.sections.shells:
            is_quad8 = is_quad8_shell_elem(sh_fs)
            is_tri6 = is_tri6_shell_elem(sh_fs)
            if is_tri6 or is_quad8:
                second_order += f"'{sh_fs.elset.name}',"
            else:
                sh_elset_str += f"'{sh_fs.elset.name}',"

        if sh_elset_str != "":
            elset = "sh_sets"
            section_sets += f"{elset} = ({sh_elset_str})\n"
            model_type_str += type_tmpl_str.format(elset_str=elset, el_formula="DKT")

        if second_order != "":
            elset = "sh_2nd_order_sets"
            section_sets += f"{elset} = ({second_order})\n"

            if is_tri6:
                output_mesh = "ma_tri6"
                section_sets += el_convert_str.format(
                    output_mesh=output_mesh, input_mesh=input_mesh, el_set=elset, convert_option="TRIA6_7"
                )
                input_mesh = output_mesh

            if is_quad8:
                output_mesh = "ma_quad8"
                section_sets += el_convert_str.format(
                    output_mesh=output_mesh, input_mesh=input_mesh, el_set=elset, convert_option="QUAD8_9"
                )
                input_mesh = output_mesh
            model_type_str += type_tmpl_str.format(elset_str=elset, el_formula="COQUE_3D")

    if len(part.fem.sections.solids) > 0:
        so_elset_str = ",".join([f"'{solid_fs.elset.name}'" for solid_fs in part.fem.sections.solids])
        section_sets += f"so_sets = ({so_elset_str})\n"
        model_type_str += type_tmpl_str.format(elset_str="so_sets", el_formula="3D")

    comm_str = main_comm_str.format(
        section_sets=section_sets,
        input_mesh=input_mesh,
        model_type_str=model_type_str,
        materials_str=mat_str,
        sections_str=sections_str,
        bc_str=bc_str,
        step_str=step_str,
    )

    return comm_str


def write_to_med(name, part: Part, filename):
    """Custom Method for writing a part directly based on meshio"""

    with h5py.File(filename, "w") as f:
        mesh_name = name if name is not None else part.fem.name
        # Strangely the version must be 3.0.x
        # Any version >= 3.1.0 will NOT work with SALOME 8.3
        info = f.create_group("INFOS_GENERALES")
        info.attrs.create("MAJ", 3)
        info.attrs.create("MIN", 0)
        info.attrs.create("REL", 0)

        time_step = _write_mesh_presets(f, mesh_name)

        profile = "MED_NO_PROFILE_INTERNAL"

        # Node and Element sets (familles in French)
        fas = f.create_group("FAS")
        families = fas.create_group(mesh_name)
        family_zero = families.create_group("FAMILLE_ZERO")  # must be defined in any case
        family_zero.attrs.create("NUM", 0)

        # Make sure that all member references are updated
        # TODO: Evaluate if this can be avoided using a smarter algorithm
        part.fem.sets.add_references()

        # Nodes and node sets
        med_nodes(part, time_step, profile, families)

        # Elements (mailles in French) and element sets
        med_elements(part, time_step, profile, families)


def _write_mesh_presets(f, mesh_name):
    numpy_void_str = np.bytes_("")
    dim = 3

    # Meshes
    mesh_ensemble = f.create_group("ENS_MAA")

    med_mesh = mesh_ensemble.create_group(mesh_name)
    med_mesh.attrs.create("DIM", dim)  # mesh dimension
    med_mesh.attrs.create("ESP", dim)  # spatial dimension
    med_mesh.attrs.create("REP", 0)  # cartesian coordinate system (repère in French)
    med_mesh.attrs.create("UNT", numpy_void_str)  # time unit
    med_mesh.attrs.create("UNI", numpy_void_str)  # spatial unit
    med_mesh.attrs.create("SRT", 1)  # sorting type MED_SORT_ITDT

    # component names:
    names = ["X", "Y", "Z"][:dim]
    med_mesh.attrs.create("NOM", np.bytes_("".join(f"{name:<16}" for name in names)))
    med_mesh.attrs.create("DES", np.bytes_("Mesh created with adapy"))
    med_mesh.attrs.create("TYP", 0)  # mesh type (MED_NON_STRUCTURE)

    # Time-step
    step = "-0000000000000000001-0000000000000000001"  # NDT NOR
    time_step = med_mesh.create_group(step)
    time_step.attrs.create("CGT", 1)
    time_step.attrs.create("NDT", -1)  # no time step (-1)
    time_step.attrs.create("NOR", -1)  # no iteration step (-1)
    time_step.attrs.create("PDT", -1.0)  # current time
    return time_step
