"""Paradoc filter classes for the FEA verification report.

Instances are constructed at runtime in `build_verification_report.py` so
each filter carries a reference to the live domain object
(`ada.Beam`, `FeaVerificationResult`, …) rather than hardcoded strings.
The build script then registers them with `one._filter_registry.register`
before compilation; we don't rely on paradoc's auto-discovery here
because filter state depends on runtime data.

Markdown references resolve as `${ filter_name.attr_name(:fmtspec) }`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from paradoc.filters import Filter, FigureView, TableView, ThreeDView, attr

if TYPE_CHECKING:
    import ada
    from build_report_utils import FeaVerificationResult


class Beam(Filter):
    """Reads geometry/section/material straight off the analyzed ada.Beam."""

    def __init__(self, beam: ada.Beam, **kw):
        super().__init__(**kw)
        self._beam = beam

    @attr
    def length_m(self) -> float:
        n1 = np.asarray(self._beam.n1.p, dtype=float)
        n2 = np.asarray(self._beam.n2.p, dtype=float)
        return float(np.linalg.norm(n2 - n1))

    @attr
    def section_name(self) -> str:
        return self._beam.section.name

    @attr
    def section_type(self) -> str:
        return str(self._beam.section.type)

    @attr
    def material_name(self) -> str:
        return self._beam.material.name

    @attr
    def youngs_modulus_pa(self) -> float:
        return float(self._beam.material.model.E)

    @attr
    def yield_stress_pa(self) -> float:
        return float(self._beam.material.model.sig_y)

    @attr
    def density_kgm3(self) -> float:
        return float(self._beam.material.model.rho)

    @attr
    def description(self) -> str:
        return str(self._beam)

    @attr
    def geometry_3d(self) -> ThreeDView:
        return ThreeDView(
            glb_key="beam_geom",
            caption="Cantilever beam geometry.",
            camera_preset="iso_3",
        )


class Versions(Filter):
    """Solver version strings."""

    def __init__(self, versions: dict, **kw):
        super().__init__(**kw)
        self._v = versions

    @attr
    def ccx(self) -> str:
        return self._v.get("calculix", "unknown")

    @attr
    def ca(self) -> str:
        return self._v.get("code_aster", "unknown")

    @attr
    def aba(self) -> str:
        return self._v.get("abaqus", "unknown")

    @attr
    def ses(self) -> str:
        return self._v.get("sesam", "unknown")


class Eig(Filter):
    """Across-solver eigenvalue comparison views.

    `results` is the live list of `FeaVerificationResult`; tables are
    keyed via stable strings (see `build_verification_report.py` for the
    matching `db_manager.add_table` calls).
    """

    def __init__(self, results: list[FeaVerificationResult], num_modes: int, **kw):
        super().__init__(**kw)
        self._results = results
        self._num_modes = num_modes

    @attr
    def num_modes(self) -> int:
        return self._num_modes

    @attr
    def num_cases(self) -> int:
        return len(self._results)

    @attr
    def solvers(self) -> str:
        return ", ".join(sorted({r.fem_format for r in self._results}))

    @attr
    def compare_solid_o1(self) -> TableView:
        return TableView(table_key="eig_compare_solid_o1")

    @attr
    def compare_solid_o2(self) -> TableView:
        return TableView(table_key="eig_compare_solid_o2")

    @attr
    def compare_shell_o1(self) -> TableView:
        return TableView(table_key="eig_compare_shell_o1")

    @attr
    def compare_shell_o2(self) -> TableView:
        return TableView(table_key="eig_compare_shell_o2")

    @attr
    def compare_line_o1(self) -> TableView:
        return TableView(table_key="eig_compare_line_o1")

    @attr
    def compare_line_o2(self) -> TableView:
        return TableView(table_key="eig_compare_line_o2")

    @attr
    def freq_vs_mode_plot(self) -> FigureView:
        return FigureView(plot_key="eig_freq_vs_mode", caption="First-mode frequency by solver / geometry.")


class SolverCase(Filter):
    """One filter instance per FeaVerificationResult.

    The filter `name` is set to the result name (a valid Python
    identifier, e.g. `cantilever_EIG_calculix_shell_o1`), which is also
    the table key in `db_manager`.
    """

    def __init__(self, result: FeaVerificationResult, **kw):
        super().__init__(**kw)
        self._r = result

    @attr
    def solver(self) -> str:
        return self._r.fem_format

    @attr
    def case_name(self) -> str:
        return self._r.name

    @attr
    def first_freq_hz(self) -> float:
        return float(self._r.eig_data.modes[0].f_hz)

    @attr
    def modal_table(self) -> TableView:
        return TableView(table_key=self._r.name)

    @attr
    def fea_3d(self) -> ThreeDView:
        """FEA artefact bundle figure — one figure per case, all modes inside.

        The bundle's manifest + per-mode field blobs let the live
        viewer's SimulationControls switch between modes without a
        separate figure per mode. Keyed by ``<case>`` (no _mode_NN
        suffix); paradoc-frontend dispatches on the
        ``fea_artefact_bundle`` source_type to the artefact-aware
        mount path.
        """
        return ThreeDView(
            glb_key=self._r.name,
            caption=f"{self._r.fem_format} — {self._r.name} mode shapes.",
            camera_preset="iso_3",
        )

    # Back-compat alias — old markdown still references `mode_3d`
    # during the rebake transition. Points at the same FEA bundle so
    # references keep resolving until the next markdown regen sweeps
    # the document tree.
    @attr
    def mode_3d(self) -> ThreeDView:
        return self.fea_3d

    # Per-mode figures (`fea_3d_mode_1`, `fea_3d_mode_2`, ...) —
    # generated dynamically via __getattr__ so we don't have to
    # hand-declare 20 @attr methods per case. Each returns a
    # ThreeDView whose glb_key matches the mode-view ThreeDData row
    # the bake registered alongside the canonical bundle row
    # (`<case>_mode_<N>`); paradoc's exporter dedupes the on-disk
    # files via `fea_bundle_key` so all per-mode rows share the
    # same `assets/3d/<case>/` artefact set with only differing
    # `fea_mode_index` metadata.
    def __getattr__(self, name: str):  # noqa: D401 — dunder
        import re as _re
        m = _re.fullmatch(r"fea_3d_mode_(\d+)", name)
        if m is None:
            raise AttributeError(name)
        mode_n = int(m.group(1))
        case_name = self._r.name
        fem_format = self._r.fem_format

        def _per_mode_attr() -> ThreeDView:
            return ThreeDView(
                glb_key=f"{case_name}_mode_{mode_n}",
                caption=f"{fem_format} — {case_name} mode {mode_n}.",
                camera_preset="iso_3",
            )

        # Mark it so paradoc's filter resolver accepts it.
        from paradoc.filters.base import _ATTR_MARKER  # type: ignore
        setattr(_per_mode_attr, _ATTR_MARKER, True)
        return _per_mode_attr
