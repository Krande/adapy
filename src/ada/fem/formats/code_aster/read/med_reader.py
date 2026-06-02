"""Native RMED → MeshData reader using h5py.

Replaces the meshio-based ``meshio.read(file, "med")`` call in
``read_code_aster_results``. Walks the same HDF5 layout meshio
documents (ENS_MAA / FAS / CHA / PROFILS) but lands the result
in adapy's :class:`MeshData` shape directly. h5py is already a
required dep; this lets meshio drop out of adapy's required path.

Field-key formatting matches meshio exactly (`f"{name}[{i:d}] -
{t:g}"` for multi-step fields) so the parity test against
meshio's reader can do a direct dict-key compare.
"""

from __future__ import annotations

import os
import pathlib

import numpy as np

from ada.fem.results.common import CellBlockData, MeshData

# MED short cell-type codes (used in HDF5 group names) → canonical
# string-name. Mirrors meshio's `med_to_meshio_type` so the cell
# blocks we emit have the same `cell_type` values that meshio
# produces, which keeps the parity test direct.
_MED_SHORT_TO_STR = {
    "PO1": "vertex",
    "SE2": "line",
    "SE3": "line3",
    "TR3": "triangle",
    "TR6": "triangle6",
    "QU4": "quad",
    "QU8": "quad8",
    "TE4": "tetra",
    "T10": "tetra10",
    "HE8": "hexahedron",
    "H20": "hexahedron20",
    "PY5": "pyramid",
    "P13": "pyramid13",
    "PE6": "wedge",
    "P15": "wedge15",
}


def med_to_mesh_data(rmed_path: os.PathLike) -> MeshData:
    """Read an RMED file via h5py into a :class:`MeshData`.

    Single-step fields land in ``point_data[name]`` (or
    ``cell_data[name][block_index]`` for ELNO/ELGA fields).
    Multi-step fields are flattened into per-step keys following
    meshio's convention: ``name[i] - t`` where ``i`` is the
    0-based step index and ``t`` the time / eigenfrequency.
    """
    import h5py

    rmed_path = pathlib.Path(rmed_path)
    with h5py.File(rmed_path, "r") as f:
        return _read(f)


def _read(f) -> MeshData:
    mesh_ensemble = f["ENS_MAA"]
    mesh_keys = list(mesh_ensemble.keys())
    if len(mesh_keys) != 1:
        raise ValueError(f"Expected exactly 1 mesh in RMED, found {len(mesh_keys)}.")
    mesh_name = mesh_keys[0]
    mesh = mesh_ensemble[mesh_name]
    dim = int(mesh.attrs["ESP"])

    # Some MEDs nest the mesh inside a time-step group; if NOE
    # isn't a direct child, descend into the single time-step.
    if "NOE" not in mesh:
        ts_keys = list(mesh.keys())
        if len(ts_keys) != 1:
            raise ValueError(f"Expected exactly 1 time-step on mesh, found {len(ts_keys)}.")
        mesh = mesh[ts_keys[0]]

    point_data: dict[str, np.ndarray] = {}
    cell_data: dict[str, list] = {}

    # Points — Fortran-order on disk.
    pts_dataset = mesh["NOE"]["COO"]
    n_points = int(pts_dataset.attrs["NBR"])
    points = np.asarray(pts_dataset[()]).reshape((n_points, dim), order="F")

    # Per-node family tag — meshio surfaces this as `point_tags`.
    if "FAM" in mesh["NOE"]:
        point_data["point_tags"] = np.asarray(mesh["NOE"]["FAM"][()])

    # Cell blocks (one per geometry type present in MAI).
    cells: list[CellBlockData] = []
    cell_types_str: list[str] = []
    if "MAI" in mesh:
        med_cells = mesh["MAI"]
        for med_short, med_group in med_cells.items():
            cell_type = _MED_SHORT_TO_STR.get(med_short)
            if cell_type is None:
                raise ValueError(
                    f"Unknown MED cell-type code {med_short!r} in {mesh_name}; add it to _MED_SHORT_TO_STR."
                )
            cell_types_str.append(cell_type)

            nod = med_group["NOD"]
            n_cells = int(nod.attrs["NBR"])
            # MED stores 1-based connectivity in Fortran order.
            conn = np.asarray(nod[()]).reshape((n_cells, -1), order="F") - 1
            cells.append(CellBlockData(cell_type=cell_type, data=conn))

            if "FAM" in med_group:
                cell_data.setdefault("cell_tags", []).append(np.asarray(med_group["FAM"][()]))

    # Per-(field, step) result data, when present.
    if "CHA" in f:
        profiles = f["PROFILS"] if "PROFILS" in f else None
        _read_fields(f["CHA"], profiles, cell_types_str, point_data, cell_data)

    return MeshData(points=points, cells=cells, point_data=point_data, cell_data=cell_data)


def _read_fields(fields_group, profiles, cell_types_str, point_data, cell_data):
    """Walk every CHA/<field>/<step>/<support> entry and dispatch."""
    for field_name, data in fields_group.items():
        time_step_keys = sorted(data.keys())
        # Match meshio's naming: single-step fields keep their
        # bare name; multi-step fields get the "[i] - t" suffix.
        if len(time_step_keys) == 1:
            step_names = [field_name]
        else:
            step_names = []
            for i, key in enumerate(time_step_keys):
                t = data[key].attrs["PDT"]
                step_names.append(f"{field_name}[{i:d}] - {t:g}")

        for i, key in enumerate(time_step_keys):
            step_group = data[key]
            step_name = step_names[i]
            for support in step_group:
                if support == "NOE":
                    point_data[step_name] = _read_nodal_values(step_group, profiles)
                else:
                    # Element-nodal (NOE.<TYPE>) or Gauss (MAI.<TYPE>).
                    med_short = support.partition(".")[2]
                    cell_type = _MED_SHORT_TO_STR.get(med_short)
                    if cell_type is None or cell_type not in cell_types_str:
                        # Field data references a cell type we
                        # didn't pick up from MAI — surfaced
                        # silently rather than crash, so callers
                        # can still consume the rest.
                        continue
                    block_idx = cell_types_str.index(cell_type)
                    if step_name not in cell_data:
                        cell_data[step_name] = [None] * len(cell_types_str)
                    cell_data[step_name][block_idx] = _read_element_values(
                        step_group[support],
                        profiles,
                    )


def _read_nodal_values(step_group, profiles) -> np.ndarray:
    profile = step_group["NOE"].attrs["PFL"]
    data_profile = step_group["NOE"][profile]
    n_points = int(data_profile.attrs["NBR"])
    if profile.decode() == "MED_NO_PROFILE_INTERNAL":
        values = np.asarray(data_profile["CO"][()]).reshape((n_points, -1), order="F")
    else:
        n_data = int(profiles[profile].attrs["NBR"])
        # Profile lists the global node ids this restricted field
        # is defined on; back-fill the rest with NaN.
        index_profile = np.asarray(profiles[profile]["PFL"][()]) - 1
        values_profile = np.asarray(data_profile["CO"][()]).reshape((n_data, -1), order="F")
        values = np.full((n_points, values_profile.shape[1]), np.nan)
        values[index_profile] = values_profile
    if values.shape[-1] == 1:
        values = values[:, 0]
    return values


def _read_element_values(supp_group, profiles) -> np.ndarray:
    profile = supp_group.attrs["PFL"]
    data_profile = supp_group[profile]
    n_cells = int(data_profile.attrs["NBR"])
    n_gauss = int(data_profile.attrs["NGA"])
    if profile.decode() == "MED_NO_PROFILE_INTERNAL":
        values = np.asarray(data_profile["CO"][()]).reshape((n_cells, n_gauss, -1), order="F")
    else:
        n_data = int(profiles[profile].attrs["NBR"])
        index_profile = np.asarray(profiles[profile]["PFL"][()]) - 1
        values_profile = np.asarray(data_profile["CO"][()]).reshape(
            (n_data, n_gauss, -1),
            order="F",
        )
        values = np.full(
            (n_cells, values_profile.shape[1], values_profile.shape[2]),
            np.nan,
        )
        values[index_profile] = values_profile

    # Drop the Gauss-point axis when there's just one sample per
    # cell, matching meshio's `n_gauss_points == 1` shortcut.
    if n_gauss == 1:
        values = values[:, 0, :]
        if values.shape[-1] == 1:
            values = values[:, 0]
    return values
