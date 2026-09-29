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

from .read_odb import get_odb_data

if TYPE_CHECKING:
    from ada.fem.results.concepts import ElementDataOutput, FEMDataOutput, Results
    from ada.fem.results.eigenvalue import EigenDataSummary


#: The ``.dat`` tables of a *FREQUENCY step, by their letter-spaced banner with the spaces removed.
_DAT_TABLES = {
    "EIGENVALUEOUTPUT": "eig",
    "PARTICIPATIONFACTORS": "part",
    "EFFECTIVEMASS": "eff",
}


def _dat_tables(dat_file: str | os.PathLike) -> tuple[dict[str, dict[int, list[float]]], list[float] | None]:
    """The eigen tables of an Abaqus ``.dat``: ``{table: {mode: values}}`` and the effective-mass TOTAL.

    A row is a mode number followed by numbers. Above the rows a table has headings and blank
    lines, which are skipped; the rows are contiguous, so the first other line after them ends the
    table (the effective-mass TOTAL row is read on the way out). A later step's table replaces an
    earlier one, so a file with several frequency steps reports its last.
    """
    tables: dict[str, dict[int, list[float]]] = {}
    total: list[float] | None = None
    current: str | None = None
    with open(dat_file, "r", errors="replace") as f:
        for line in f:
            banner = line.replace(" ", "").strip().upper()
            if banner in _DAT_TABLES:
                current = _DAT_TABLES[banner]
                tables[current] = {}
                continue
            if current is None:
                continue
            tokens = line.split()
            row = None
            if tokens and tokens[0].isdigit():
                try:
                    row = [float(x) for x in tokens[1:]]
                except ValueError:
                    row = None
            if row is not None:
                tables[current][int(tokens[0])] = row
                continue
            if not tables[current]:
                continue  # still in the headings
            if tokens and tokens[0] == "TOTAL" and current == "eff":
                total = [float(x) for x in tokens[1:7]]
            if tokens or current != "eff":
                current = None
    return tables, total


def get_eigen_data(dat_file: str | os.PathLike) -> EigenDataSummary:
    """Frequencies, participation factors and effective masses from an Abaqus ``.dat``.

    Abaqus prints all three for a *FREQUENCY step by default: EIGENVALUE OUTPUT (eigenvalue,
    rad/time, cycles/time, generalized mass, ...), then PARTICIPATION FACTORS and EFFECTIVE MASS,
    each in the six global DOF. The participation factors refer to the step's eigenvector
    normalization (effective mass = factor² × generalized mass); the effective masses do not.
    """
    from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

    tables, total = _dat_tables(dat_file)
    eig = tables.get("eig", {})
    part = tables.get("part", {})
    eff = tables.get("eff", {})

    dof_base = ["x", "y", "z", "rx", "ry", "rz"]
    eigen_modes: List[EigenMode] = []
    for no in sorted(eig):
        values = eig[no]
        mode = EigenMode(no=no, eigenvalue=values[0], f_rad=values[1], f_hz=values[2])
        for dof, value in zip(dof_base, part.get(no, [])):
            setattr(mode, f"p{dof}", value)
        for dof, value in zip(dof_base, eff.get(no, [])):
            setattr(mode, f"ef{dof}", value)
        eigen_modes.append(mode)

    return EigenDataSummary(eigen_modes, total)


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
