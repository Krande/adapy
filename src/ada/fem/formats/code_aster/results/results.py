from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

import h5py
import numpy as np

from ada.config import logger
from ada.fem import StepEigen
from ada.fem.elements import ElemShape
from ada.fem.formats.code_aster.read.med_reader import med_to_mesh_data
from ada.fem.formats.code_aster.read.reader import med_to_fem
from ada.fem.results.common import MeshData

if TYPE_CHECKING:
    from ada.fem.results import Results
    from ada.fem.results.eigenvalue import EigenDataSummary


def get_eigen_data(rmed_file) -> EigenDataSummary:
    from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

    with h5py.File(rmed_file) as f:
        modes = f.get("CHA/modes___DEPL")
        eigen_modes = []

        for mname, m in modes.items():
            mode = m.attrs["NDT"]
            freq = m.attrs["PDT"]
            eigen_modes.append(EigenMode(int(mode), f_hz=float(freq)))

    # Effective modal mass + participation factors aren't in the MED field
    # output — the writer dumps them (global translational axes) to a CSV
    # next to the .rmed via NORM_MODE/IMPR_TABLE. Merge them in when present.
    modal_mass = _read_modal_mass_csv(pathlib.Path(rmed_file).with_suffix(".modalmass.csv"))
    if modal_mass:
        for em in eigen_modes:
            row = modal_mass.get(em.no)
            if row is None:
                continue
            em.efx, em.efy, em.efz = row.get("MASS_EFFE_DX"), row.get("MASS_EFFE_DY"), row.get("MASS_EFFE_DZ")
            em.px, em.py, em.pz = row.get("FACT_PARTICI_DX"), row.get("FACT_PARTICI_DY"), row.get("FACT_PARTICI_DZ")

    return EigenDataSummary(eigen_modes)


def _read_modal_mass_csv(csv_path: pathlib.Path) -> dict[int, dict[str, float]]:
    """Parse the Code_Aster IMPR_TABLE (FORMAT='TABLEAU', SEPARATEUR=',')
    modal-parameter dump into ``{nume_mode: {param: value}}``.

    Returns an empty dict if the file is absent (non-eigen run, or an older
    result produced before this output existed)."""
    if not csv_path.is_file():
        return {}

    out: dict[int, dict[str, float]] = {}
    header: list[str] | None = None
    for raw in csv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        cells = [c.strip() for c in raw.split(",")]
        if header is None:
            if "NUME_MODE" in cells and "FREQ" in cells:
                header = cells
            continue
        if len(cells) < len(header):
            continue
        row = dict(zip(header, cells))
        try:
            nume = int(float(row["NUME_MODE"]))
        except (KeyError, ValueError):
            continue
        parsed: dict[str, float] = {}
        for key, val in row.items():
            if key in ("NUME_MODE", ""):
                continue
            try:
                parsed[key] = float(val)
            except ValueError:
                continue
        out[nume] = parsed
    return out


def get_eigen_frequency_deformed_meshes(rmed_file):
    fem = med_to_fem(rmed_file, "temp")

    with h5py.File(rmed_file) as f:
        modes = f.get("CHA/modes___DEPL")
        nodes = fem.nodes.to_np_array()
        eig_deformed_meshes = []

        for mname, m in modes.items():
            res = m["NOE"]["MED_NO_PROFILE_INTERNAL"]["CO"][()]
            dofs = res.reshape(len(fem.nodes), 6)
            eig_deformed_meshes.append(nodes + np.delete(dofs, np.s_[2:5], 1))
            mode = m.attrs["NDT"]
            freq = m.attrs["PDT"]
            logger.debug("mode=%s freq=%s", mode, freq)

    # TODO: Figure out what kind of information is needed for animating frames in threejs/blender
    return fem, eig_deformed_meshes


def read_code_aster_results(results: "Results", file_ref: pathlib.Path, overwrite) -> MeshData | None:
    if results.assembly is not None and isinstance(results.assembly.fem.steps[0], StepEigen):
        results.eigen_mode_data = get_eigen_data(file_ref)

    fem = med_to_fem(file_ref, "temp")
    if any([x.type == ElemShape.TYPES.shell.TRI7 for x in fem.elements.shell]):
        logger.error("7 node Triangle elements are not yet supported")
        return None

    if any([x.type == ElemShape.TYPES.shell.QUAD9 for x in fem.elements.shell]):
        logger.error("9 node QUAD elements are not yet supported")
        return None

    return med_to_mesh_data(file_ref)
