from __future__ import annotations

from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import logger
from ada.fem.formats.prescribed import prescribed_dofs
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.utils import is_quad8_shell_elem, is_tri6_shell_elem

from ..compatibility import check_compatibility
from . import names as concept_names
from .templates import el_convert_str, main_comm_str
from .write_bc import check_overlaps, prescribed_at_zero_str, supports_str
from .write_constraints import (
    create_coupling_str,
    create_ref_points_mesh_str,
    create_ref_points_model_str,
    get_couplings,
    model_bcs,
    ref_points_group,
    step_bcs,
)
from .write_loads import add_line_load_groups
from .write_materials import materials_str
from .write_med import med_elements, med_nodes
from .write_sections import create_sections_str
from .write_steps import all_steps, steps_str

if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part


def to_fem(assembly: Assembly, name, analysis_dir, metadata=None, model_data_only=False):
    """Write Code_Aster .med and .comm file from Assembly data"""
    from ada.fem.cavities import refuse_fluid_cavities
    from ada.materials.utils import shorten_material_names

    from .beams_sidecar import dump_beams_sidecar
    from .name_map import apply_name_map, build_name_map, dump_name_map

    check_compatibility(assembly)
    refuse_fluid_cavities(assembly, "code_aster")

    if "info" not in metadata:
        metadata["info"] = dict(description="")

    p = get_fem_model_from_assembly(assembly)
    # Prepare model for
    shorten_material_names(assembly)

    # A line load is written over mesh groups of its own (Code_Aster names a group, never an element or node);
    # they exist for this deck only and are taken off the model again once it is written.
    line_load_groups = add_line_load_groups(all_steps(assembly), p.fem)
    try:
        # Code Aster's MED reader drops any GROUP_MA/GROUP_NO name >24 chars
        # at LIRE_MAILLAGE time (MED_7 alarm — bibcxx/IOManager/MedToAsterReader.cxx).
        # Mutate every FemSet name to a deterministic short id BEFORE we
        # write either the .med (NOM datasets) or the .comm (GROUP_MA refs),
        # so both sides stay in sync. The reverse map is dumped as a JSON
        # sidecar so callers can resolve short ids back to the original
        # FemSet names when interpreting .rmed results.
        name_map = build_name_map(assembly)
        apply_name_map(assembly, name_map)

        # TODO: Implement support for multiple parts. Need to understand how submeshes in Salome and Code Aster works.
        # for p in filter(lambda x: len(x.fem.elements) != 0, assembly.get_all_parts_in_assembly(True)):

        filename = (analysis_dir / name).with_suffix(".med")
        write_to_med(name, p, filename)

        dump_name_map(name_map, (analysis_dir / name).with_suffix(".name_map.json"))

        # FEM lineage + tessellation sidecar — viewer's RMED stream reader
        # picks this up to tessellate line elements as 3D extruded solids
        # AND to resolve CAD↔FEA linkage. The .med format has no section
        # / orientation / lineage on its own, so this is the only way to
        # feed the streaming bake without re-parsing the .comm.
        dump_beams_sidecar(assembly, (analysis_dir / name).with_suffix(".adapy_fem.json"))

        if model_data_only:
            return

        with open((analysis_dir / name).with_suffix(".comm"), "w") as f, concept_names.deck():
            f.write(create_comm_str(assembly, p))

    finally:
        for fs in line_load_groups:
            p.fem.sets.remove(fs)

    logger.info(f'Created a Code_Aster input deck at "{analysis_dir}"')


def create_comm_str(assembly: Assembly, part: Part) -> str:
    """Create COMM file input str"""
    couplings = get_couplings(part)
    mat_str = materials_str(assembly)
    ref_group = ref_points_group(part)
    sections_str = create_sections_str(
        part.fem.sections,
        has_ref_points=len(couplings) > 0,
        ref_points_group=ref_group,
        membrane_large_strain=membranes_large_strain(part, all_steps(assembly)),
    )
    # The part's and the assembly's, each once. ``bcs += assembly.fem.bcs`` on ``part.fem.bcs`` extended the part's own
    # list with the assembly's on every write.
    bcs = model_bcs(part)
    prescribed = prescribed_dofs(bcs)
    steps = all_steps(assembly)
    # A step's own Bc holds in it and every later step (write_constraints.step_bcs), so the last step has them all:
    # checked once over the model's and every step's.
    every = step_bcs(part, steps[-1]) if steps else bcs
    check_overlaps(every, prescribed_dofs(every))
    supports = [supports_str(bcs, prescribed), prescribed_at_zero_str(bcs)]
    bc_str = "\n".join([s for s in supports if s] + [create_coupling_str(con) for con in couplings])
    step_str = steps_str(steps, part)

    type_tmpl_str = "_F(GROUP_MA={elset_str}, PHENOMENE='MECANIQUE', MODELISATION='{el_formula}',),"

    model_type_str = ""
    section_sets = ""
    input_mesh = "mesh"
    lines = part.fem.sections.lines
    # Beams are Euler-Bernoulli POU_D_E; trusses BARRE; cables CABLE (U3.11.01, U3.11.04, U3.11.06).
    for group, el_formula, members in (
        ("bm_sets", "POU_D_E", [fs for fs in lines if not fs.is_axial_only]),
        ("tr_sets", "BARRE", [fs for fs in lines if fs.is_axial_only and not fs.is_cable]),
        ("cb_sets", "CABLE", [fs for fs in lines if fs.is_cable]),
    ):
        if members:
            elset_str = ",".join([f"'{fs.elset.name}'" for fs in members])
            section_sets += f"{group} = ({elset_str},)\n"
            model_type_str += type_tmpl_str.format(elset_str=group, el_formula=el_formula)

    membranes = [fs for fs in part.fem.sections.shells if fs.is_membrane]
    if membranes:
        # MEMBRANE takes the 3-, 4-, 6- and 8-node cells as they are (U3.12.03).
        elset_str = ",".join([f"'{fs.elset.name}'" for fs in membranes])
        section_sets += f"mb_sets = ({elset_str},)\n"
        model_type_str += type_tmpl_str.format(elset_str="mb_sets", el_formula="MEMBRANE")

    bending_shells = [fs for fs in part.fem.sections.shells if not fs.is_membrane]
    if len(bending_shells) > 0:
        sh_elset_str = ""
        second_order = ""
        is_tri6 = False
        is_quad8 = False
        for sh_fs in bending_shells:
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
        # Reduced integration is the modelisation, not the mesh cell: 3D_SI (check_reduced_integration
        # has already refused the cells it doesn't cover).
        so_formula = "3D_SI" if part.fem.options.CODE_ASTER.use_reduced_integration else "3D"
        model_type_str += type_tmpl_str.format(elset_str="so_sets", el_formula=so_formula)

    if len(couplings) > 0:
        output_mesh = "mesh_ref"
        section_sets += create_ref_points_mesh_str(part, input_mesh, output_mesh, ref_group)
        input_mesh = output_mesh
        model_type_str += create_ref_points_model_str(ref_group)

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


def membranes_large_strain(part: Part, steps) -> bool:
    """Whether the model's membranes are solved with large strains (``GROT_GDEP``): in a geometrically nonlinear
    step. Their material differs between the two (:func:`.write_sections.write_membrane_section`) and is written once,
    so a model of membranes with both linear and nonlinear static steps is refused."""
    from ada.fem.exceptions import IncompatibleElements
    from ada.fem.steps import Step

    if not any(fs.is_membrane for fs in part.fem.sections.shells):
        return False
    static = [st for st in steps if st.type == Step.TYPES.STATIC]
    nonlinear = [st for st in static if st.nl_geom is True and len(st.load_cases) == 0]
    if nonlinear and len(nonlinear) != len(static):
        raise IncompatibleElements(
            "code_aster writer: a membrane takes ELAS_MEMBRANE with small strains and ELAS with large ones, and the "
            "model has both linear and geometrically nonlinear static steps; make them all one or the other"
        )
    return len(nonlinear) > 0


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
