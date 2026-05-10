"""Streaming RMED reader for the FEA viewer artefact bake.

Mirrors the data layout of :mod:`med_reader` but exposes it through
the :class:`~ada.fem.results.artefacts.FEAStreamReader` protocol so
the bake can write per-field blobs one step at a time without ever
holding the full ``[n_steps × n_points × n_components]`` field stack
in memory.

The eager :func:`med_reader.med_to_mesh_data` stays — it's still the
right shape for FEA-modification consumers that need a populated
:class:`MeshData`. This module is the lazy/streaming peer used by the
artefact pipeline only.
"""

from __future__ import annotations

import os
import pathlib
from typing import Iterator

import numpy as np

from ada.fem.formats.code_aster.read.med_reader import _MED_SHORT_TO_STR
from ada.fem.results.artefacts import FieldSpec, MeshGeometry, StepValues
from ada.fem.results.common import CellBlockData


class RmedStreamReader:
    """Streaming RMED reader. Holds an open ``h5py.File``; the bake
    drives it via the FEAStreamReader protocol."""

    def __init__(self, rmed_path: os.PathLike):
        import h5py

        self._path = pathlib.Path(rmed_path)
        self._f = h5py.File(self._path, "r")
        self._mesh_group, self._dim = _locate_mesh(self._f)
        self._geom: MeshGeometry | None = None
        self._field_specs_cache: list[FieldSpec] | None = None

    # ----- protocol -------------------------------------------------------

    def read_mesh_geometry(self) -> MeshGeometry:
        if self._geom is not None:
            return self._geom
        pts_dataset = self._mesh_group["NOE"]["COO"]
        n_points = int(pts_dataset.attrs["NBR"])
        points = np.asarray(pts_dataset[()]).reshape((n_points, self._dim), order="F")
        if points.shape[1] == 2:
            # Pad 2D meshes with a zero z so the GLB stays valid.
            points = np.column_stack([points, np.zeros(points.shape[0])])

        cell_blocks: list[CellBlockData] = []
        if "MAI" in self._mesh_group:
            for med_short, med_group in self._mesh_group["MAI"].items():
                cell_type = _MED_SHORT_TO_STR.get(med_short)
                if cell_type is None:
                    raise ValueError(
                        f"Unknown MED cell-type code {med_short!r}; "
                        f"add it to _MED_SHORT_TO_STR."
                    )
                nod = med_group["NOD"]
                n_cells = int(nod.attrs["NBR"])
                conn = np.asarray(nod[()]).reshape((n_cells, -1), order="F") - 1
                cell_blocks.append(CellBlockData(cell_type=cell_type, data=conn))

        self._geom = MeshGeometry(points=points, cell_blocks=cell_blocks)
        return self._geom

    def field_specs(self) -> list[FieldSpec]:
        if self._field_specs_cache is not None:
            return self._field_specs_cache

        # Need n_points for back-fill; force geom read.
        n_points_mesh = int(self.read_mesh_geometry().points.shape[0])

        specs: list[FieldSpec] = []
        if "CHA" not in self._f:
            self._field_specs_cache = specs
            return specs

        for field_name, field_group in self._f["CHA"].items():
            nom = field_group.attrs.get("NOM")
            if nom is None:
                # Field with no component metadata — skip rather than
                # guess. RMED files in practice always set NOM.
                continue
            components = nom.decode().split()

            time_keys = sorted(field_group.keys())
            if not time_keys:
                continue

            # Inspect step 0 to decide the support kind.
            step0 = field_group[time_keys[0]]
            supports = list(step0.keys())
            if "NOE" in supports:
                support = "nodal"
            elif any(s.startswith("NOE.") for s in supports):
                support = "element_nodal"
            elif any(s.startswith("MAI.") for s in supports):
                support = "gauss"
            else:
                continue

            step_values = [float(field_group[k].attrs["PDT"]) for k in time_keys]

            specs.append(
                FieldSpec(
                    name=field_name,
                    components=components,
                    n_steps=len(time_keys),
                    n_points=n_points_mesh,
                    support=support,
                    step_values=step_values,
                )
            )

        self._field_specs_cache = specs
        return specs

    def iter_field_steps(self, field_name: str) -> Iterator[StepValues]:
        if "CHA" not in self._f or field_name not in self._f["CHA"]:
            raise KeyError(field_name)

        spec = next((s for s in self.field_specs() if s.name == field_name), None)
        if spec is None:
            raise KeyError(field_name)
        if spec.support != "nodal":
            # Phase 1 streams nodal fields only. Bake skips non-nodal
            # via FieldSpec.support; if a caller asks anyway, surface it.
            raise NotImplementedError(
                f"streaming non-nodal field {field_name!r} (support={spec.support}) "
                f"not implemented in Phase 1"
            )

        field_group = self._f["CHA"][field_name]
        time_keys = sorted(field_group.keys())
        profiles = self._f["PROFILS"] if "PROFILS" in self._f else None

        for i, key in enumerate(time_keys):
            step_group = field_group[key]
            values = _read_nodal_step(step_group, profiles, spec.n_points, spec.n_components)
            yield StepValues(
                step_index=i,
                step_value=spec.step_values[i],
                values=values,
            )

    def close(self) -> None:
        self._f.close()

    # context-manager sugar so callers can ``with RmedStreamReader(p) as r:``.
    def __enter__(self) -> "RmedStreamReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Shared h5py helpers
# ---------------------------------------------------------------------------


def _locate_mesh(f):
    """Find the single mesh group + spatial dim, descending one
    time-step level if the on-disk layout nests it."""

    mesh_ensemble = f["ENS_MAA"]
    mesh_keys = list(mesh_ensemble.keys())
    if len(mesh_keys) != 1:
        raise ValueError(f"Expected exactly 1 mesh in RMED, found {len(mesh_keys)}.")
    mesh = mesh_ensemble[mesh_keys[0]]
    dim = int(mesh.attrs["ESP"])
    if "NOE" not in mesh:
        ts_keys = list(mesh.keys())
        if len(ts_keys) != 1:
            raise ValueError(
                f"Expected exactly 1 time-step on mesh, found {len(ts_keys)}."
            )
        mesh = mesh[ts_keys[0]]
    return mesh, dim


def _read_nodal_step(
    step_group, profiles, n_points_mesh: int, n_components: int
) -> np.ndarray:
    """Read one step's nodal payload. Back-fills profile-restricted
    fields with NaN so the array is always shape ``(n_points_mesh,
    n_components)``."""

    profile = step_group["NOE"].attrs["PFL"]
    data_profile = step_group["NOE"][profile]
    n_data = int(data_profile.attrs["NBR"])

    raw = np.asarray(data_profile["CO"][()]).reshape((n_data, -1), order="F")

    if profile.decode() == "MED_NO_PROFILE_INTERNAL":
        if raw.shape[0] != n_points_mesh:
            # Should match the mesh; if it doesn't, something's odd.
            raise ValueError(
                f"Nodal field has {raw.shape[0]} values but mesh has "
                f"{n_points_mesh} points; profile={profile!r}."
            )
        values = raw
    else:
        if profiles is None:
            raise ValueError(f"Field uses profile {profile!r} but PROFILS group missing.")
        index_profile = np.asarray(profiles[profile]["PFL"][()]) - 1
        values = np.full((n_points_mesh, raw.shape[1]), np.nan)
        values[index_profile] = raw

    if values.shape[1] != n_components:
        raise ValueError(
            f"Field has {values.shape[1]} components but spec expected {n_components}."
        )
    return values
