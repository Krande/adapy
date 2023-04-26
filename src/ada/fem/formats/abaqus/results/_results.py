from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING, List

from ada.config import logger
from ada.fem import StepEigen
from ada.fem.exceptions.fea_execution import (
    FEAnalysisUnableToStart,
    FEAnalysisUnsuccessfulError,
)
from ada.fem.formats.utils import DatFormatReader

from .read_odb import get_odb_data

if TYPE_CHECKING:
    from ada.fem.results.concepts import ElementDataOutput, FEMDataOutput, Results
    from ada.fem.results.eigenvalue import EigenDataSummary


def get_eigen_data(dat_file: str | os.PathLike) -> EigenDataSummary:
    from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

    dtr = DatFormatReader()

    re_compiled = dtr.compile_ff_re([int] + [float] * 5)
    re_compiled_2 = dtr.compile_ff_re([int] + [float] * 6)

    eig_str = "eigenvalueoutput"
    part_str = "participationfactors"
    eff_modal = "effectivemass"

    eig_res = dtr.read_data_lines(dat_file, re_compiled, eig_str, part_str, split_data=True)
    part_res = dtr.read_data_lines(dat_file, re_compiled_2, part_str, eff_modal, split_data=True)
    modalmass = dtr.read_data_lines(dat_file, re_compiled_2, eff_modal, split_data=True)

    eigen_modes: List[EigenMode] = []

    dof_base = ["x", "y", "z", "rx", "ry", "rz"]
    part_factor_names = ["p" + x for x in dof_base]
    eff_mass_names = ["ef" + x for x in dof_base]

    # Note! participation factors and effective modal mass are each deconstructed into 6 degrees of freedom
    for eig, part, modal in zip(eig_res, part_res, modalmass):
        mode, eig_value, freq_rad, freq_cycl, gen_mass, composite_modal_damping = eig
        eig_output = dict(eigenvalue=eig_value, f_rad=freq_rad, f_hz=freq_cycl)
        participation_data = {pn: p for pn, p in zip(part_factor_names, part[1:])}
        eff_mass_data = {pn: p for pn, p in zip(eff_mass_names, part[1:])}
        eigen_modes.append(EigenMode(no=mode, **eig_output, **participation_data, **eff_mass_data))

    return EigenDataSummary(eigen_modes)


def read_abaqus_results(results: "Results", file_ref: pathlib.Path, overwrite):
    dat_file = file_ref.with_suffix(".dat")
    if results.assembly is not None and results.assembly.fem.steps[0] == StepEigen:
        # TODO: Figure out if it is worthwhile adding support for reading step information or if it should be explicitly
        #   stated
        pass

    if dat_file.exists():
        results.eigen_mode_data = get_eigen_data(dat_file)

    check_execution(file_ref)

    logger.error("Result mesh data extraction is not supported for abaqus")

    return odb_data_to_results(file_ref, results)


def check_execution(file_ref: pathlib.Path):
    sta_file = file_ref.with_suffix(".sta")
    if sta_file.exists() is False:
        raise FEAnalysisUnableToStart()

    with open(sta_file, "r") as f:
        if "THE ANALYSIS HAS NOT BEEN COMPLETED" in f.read():
            raise FEAnalysisUnsuccessfulError()


def odb_data_to_results(odb_file: pathlib.Path, results: Results) -> None:
    from ada.fem.results.concepts import HistoryStepDataOutput, ResultsHistoryOutput

    odb_data = get_odb_data(odb_file)
    res = ResultsHistoryOutput()

    for step in odb_data["steps"].values():
        name = step["name"]
        step_type = step["procedure"]
        step_res = HistoryStepDataOutput(name=name, step_type=step_type)
        res.steps.append(step_res)

        for reg in step["historyRegions"].values():
            history_outputs = reg["historyOutputs"].values()
            name = reg["name"]
            if "element" in name.lower():
                step_res.element_data[name] = get_element_component_data(name, history_outputs)
            else:
                step_res.fem_data = get_fem_data_output(history_outputs)

    results.history_output = res


def get_element_component_data(name: str, history_outputs: dict) -> ElementDataOutput:
    from ada.fem.results.concepts import ElementDataOutput, ElemForceComp

    cu_map = {"CU1": 0, "CU2": 1, "CU3": 2, "CUR1": 3, "CUR2": 4, "CUR3": 5}
    cf_map = {"CTF1": 0, "CTF2": 1, "CTF3": 2, "CTM1": 3, "CTM2": 4, "CTM3": 5}
    displ_data = dict()
    force_data = dict()
    for data in history_outputs:
        comp = data["name"]
        cu = cu_map.get(comp, None)
        cf = cf_map.get(comp, None)
        if cu is not None:
            displ_data[cu] = [tuple(x) for x in data["data"]]
        elif cf is not None:
            force_data[cf] = ElemForceComp(comp, [tuple(x) for x in data["data"]])

    return ElementDataOutput(name=name, displacements=displ_data, forces=force_data)


def get_fem_data_output(history_outputs) -> dict[str, FEMDataOutput]:
    from ada.fem.results.concepts import FEMDataOutput

    return {x["name"]: FEMDataOutput(x["name"], [tuple(y) for y in x["data"]]) for x in history_outputs}
