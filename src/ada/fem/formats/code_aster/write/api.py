# Code related to the new 16.4 api of code_aster
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Iterable

import code_aster

from ada.config import logger

if code_aster.__version__.startswith("16"):
    CA = code_aster
else:
    from code_aster import CA

from code_aster.Cata.Language.SyntaxObjects import _F
from code_aster.Commands import AFFE_CARA_ELEM, AFFE_CHAR_MECA, AFFE_MODELE, DEFI_GROUP

import ada.fem
from ada.fem import Connector, ConnectorSection, Elem, Mass
from ada.fem.formats.code_aster.write.writer import write_to_med
from ada.fem.formats.utils import get_fem_model_from_assembly

if TYPE_CHECKING:
    from ada import FEM, Assembly

logger.info(f"Starting Code Aster {code_aster.__version__}")
DISPL_DOF_MAP = {1: "DX", 2: "DY", 3: "DZ", 4: "DRX", 5: "DRY", 6: "DRZ"}
FORCE_DOF_MAP = {1: "FX", 2: "FY", 3: "FZ", 4: "FRX", 5: "FRY", 6: "FRZ"}


def import_mesh(a: Assembly, scratch_dir):
    if isinstance(scratch_dir, str):
        scratch_dir = pathlib.Path(scratch_dir)

    p = get_fem_model_from_assembly(a)
    med_file = (scratch_dir / a.name).with_suffix(".med")
    write_to_med(a.name, p, med_file)

    mesh = CA.Mesh()
    mesh.readMedFile(med_file.as_posix(), a.name)

    DEFI_GROUP(MAILLAGE=mesh, reuse=mesh, CREA_GROUP_NO=_F(TOUT_GROUP_MA="OUI"))
    return mesh


def assembly_fem_iterator(a: Assembly) -> Iterable[FEM]:
    parts_w_fem = [p for p in a.get_all_parts_in_assembly() if not p.fem.is_empty()]
    if len(parts_w_fem) != 1:
        raise NotImplementedError("Assemblies with multiple parts containing FEM data is not yet supported")
    p = parts_w_fem[0]
    yield a.fem
    yield p.fem


def assembly_element_iterator(a: Assembly) -> Iterable[Elem]:
    for fem in assembly_fem_iterator(a):
        for elem in fem.elements:
            yield elem


def assign_element_definitions(a: Assembly, mesh: CA.Mesh) -> CA.Model | None:
    discrete_elements = []
    line_elements = []

    for elem in assembly_element_iterator(a):
        if isinstance(elem, (Connector, Mass)):
            discrete_elements.append(elem)
        elif isinstance(elem, Elem) and elem.fem_sec.type:
            line_elements.append(elem)

    # Discrete Elements
    discrete_modelings = []
    if len(discrete_elements) > 0:
        elset_names = [el.elset.name for el in discrete_elements]
        discrete_modelings.append(
            _F(
                GROUP_MA=elset_names,
                PHENOMENE="MECANIQUE",
                MODELISATION="DIS_T",
            )
        )

    model: CA.Model = AFFE_MODELE(AFFE=(*discrete_modelings,), MAILLAGE=mesh)
    return model


def assign_material_definitions(a: Assembly, mesh: CA.Mesh) -> CA.MaterialField:
    mat_map = {}
    for elem in assembly_element_iterator(a):
        if isinstance(elem, Connector):
            conn_prop = elem.con_sec
            if conn_prop not in mat_map.keys():
                mat_map[conn_prop] = []
            mat_map[conn_prop].append(elem.elset.name)
        elif isinstance(elem, Mass):
            continue
        else:
            mat = elem.fem_sec.material
            if mat not in mat_map.keys():
                mat_map[mat] = []
            mat_map[mat].append(elem.elset.name)

    material = CA.MaterialField(mesh)
    for mat, element_names in mat_map.items():
        if isinstance(mat, ConnectorSection):
            if isinstance(mat.elastic_comp, (float, int)):
                pass
                # Todo: figure out where this is supposed to be implemented
            else:
                raise NotImplementedError("Currently only supports linear elastic connectors")

            dummy = CA.Material()
            dummy.addProperties("ELAS", E=1, NU=0.3, RHO=1)
            material.addMaterialOnGroupOfCells(dummy, element_names)
        else:
            raise NotImplementedError("")

    material.build()
    return material


def assign_element_characteristics(a: Assembly, model: CA.Model, rigid_size=1e8) -> CA.ElementaryCharacteristics:
    discrete_elements = []

    for elem in assembly_element_iterator(a):
        if isinstance(elem, Mass):
            mass = elem.mass
            if isinstance(mass, (float, int)):
                value = mass
            else:
                raise NotImplementedError("A non-scalar mass is not yet supported")
            mass_def = _F(GROUP_MA=elem.elset.name, CARA="M_T_D_N", VALE=value)
            discrete_elements.append(mass_def)
        elif isinstance(elem, Connector):
            con_sec = elem.con_sec
            if isinstance(con_sec.elastic_comp, (float, int)):
                value = [con_sec.elastic_comp, con_sec.elastic_comp, con_sec.elastic_comp]
            else:
                raise NotImplementedError("Only scalar values are currently accepted for Connector elasticity")

            if isinstance(con_sec.rigid_dofs, list):
                for index in con_sec.rigid_dofs:
                    value[index] = rigid_size

            con_elem = _F(GROUP_MA=elem.elset.name, CARA="K_T_D_L", VALE=value, REPERE="GLOBAL")
            discrete_elements.append(con_elem)

        else:
            raise NotImplementedError(f"Currently unsupported non-discrete element type {elem}")

    elem_car: CA.ElementaryCharacteristics = AFFE_CARA_ELEM(MODELE=model, DISCRET=discrete_elements)
    return elem_car


def assign_boundary_conditions(a: Assembly, model: CA.Model) -> CA.MechanicalLoadReal:
    imposed_bcs = []

    for fem in assembly_fem_iterator(a):
        for bc in fem.bcs:
            # Todo: Need to figure out rules for when code_aster accepts imposing constraints on nodal rotations.
            skip_rotations = True

            if skip_rotations:
                dofs = [x for x in bc.dofs if x < 4]
            else:
                dofs = bc.dofs
            dofs_constrained = {DISPL_DOF_MAP[x]: 0 for x in dofs}
            ca_bc = _F(GROUP_NO=bc.fem_set.name, **dofs_constrained)
            imposed_bcs.append(ca_bc)

    fix: CA.MechanicalLoadReal = AFFE_CHAR_MECA(MODELE=model, DDL_IMPO=imposed_bcs)
    return fix


def assign_forces(a: Assembly, model: CA.Model) -> CA.MechanicalLoadReal:
    nodal_loads = []
    for fem in assembly_fem_iterator(a):
        for load in fem.get_all_loads():
            imposed_loads = {
                FORCE_DOF_MAP[x]: force for x, force in enumerate(load.forces, start=1) if float(force) != 0.0
            }
            ca_load = _F(GROUP_NO=load.fem_set.name, **imposed_loads)
            nodal_loads.append(ca_load)

    forces: CA.MechanicalLoadReal = AFFE_CHAR_MECA(MODELE=model, FORCE_NODALE=nodal_loads)
    return forces


def assign_steps(
    a: Assembly,
    model: CA.Model,
    fix: CA.MechanicalLoadReal,
    forces: CA.MechanicalLoadReal,
    material_field: CA.MaterialField,
    elem_car: CA.ElementaryCharacteristics,
) -> CA.ElasticResult:
    for step in a.fem.steps:
        if isinstance(step, ada.fem.StepImplicitDynamic):
            raise NotImplementedError("Not yet implemented 'StepImplicitDynamic'")
        elif isinstance(step, ada.fem.StepImplicitStatic):
            pass
