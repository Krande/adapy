# Code related to the new 16.4 api of code_aster
from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Iterable

import code_aster
from code_aster.Cata.Language.SyntaxObjects import _F
from code_aster.Commands import DEFI_GROUP, AFFE_MODELE

from ada.fem import Elem, Connector, Mass, ConnectorSection
from ada.fem.formats.code_aster.write.writer import write_to_med
from ada.fem.formats.utils import get_fem_model_from_assembly

if TYPE_CHECKING:
    from ada import Assembly


def import_mesh(a: Assembly, scratch_dir):
    if isinstance(scratch_dir, str):
        scratch_dir = pathlib.Path(scratch_dir)

    p = get_fem_model_from_assembly(a)
    med_file = (scratch_dir / a.name).with_suffix('.med')
    write_to_med(a.name, p, med_file)

    mesh = code_aster.Mesh()
    mesh.readMedFile(med_file.as_posix(), a.name)

    DEFI_GROUP(
        MAILLAGE=mesh,
        reuse=mesh,
        CREA_GROUP_NO=_F(TOUT_GROUP_MA="OUI")
    )
    return mesh


def assembly_element_iterator(a: Assembly) -> Iterable[Elem]:
    parts_w_fem = [p for p in a.get_all_parts_in_assembly() if not p.fem.is_empty()]
    if len(parts_w_fem) != 1:
        raise NotImplementedError("Assemblies with multiple parts containing FEM data is not yet supported")
    p = parts_w_fem[0]

    for elem in a.fem.elements:
        yield elem
    for elem in p.fem.elements:
        yield elem


def assign_element_definitions(a: Assembly, mesh: code_aster.Mesh) -> code_aster.Model | None:
    discrete_elements = []
    line_elements = []

    for elem in assembly_element_iterator(a):
        if isinstance(elem, (Connector, Mass)):
            discrete_elements.append(elem.elset.name)
        elif isinstance(elem, Elem) and elem.fem_sec.type:
            line_elements.append(elem.elset.name)

    # Discrete Elements
    discrete_modelings = []
    if len(discrete_elements) > 0:
        discrete_modelings.append(_F(
            GROUP_MA=discrete_elements,
            PHENOMENE='MECANIQUE',
            MODELISATION='DIS_T',
        ))

    model: code_aster.Model = AFFE_MODELE(
        AFFE=(*discrete_modelings,),
        MAILLAGE=mesh
    )
    return model


def assign_material_definitions(a: Assembly, mesh: code_aster.Mesh) -> code_aster.MaterialField:
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

    material = code_aster.MaterialField(mesh)
    for mat, element_names in mat_map.items():
        if isinstance(mat, ConnectorSection):
            if isinstance(mat.elastic_comp, float):
                e_mod = mat.elastic_comp
                # Todo: figure out where this is supposed to be implemented
            else:
                raise NotImplementedError("Currently only supports linear elastic connectors")

            dummy = code_aster.Material()
            dummy.addProperties("ELAS", E=1, NU=0.3, RHO=1)
            material.addMaterialOnGroupOfCells(dummy, element_names)
        else:
            raise NotImplementedError("")

    material.build()
    return material
