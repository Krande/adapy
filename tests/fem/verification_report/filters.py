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
    def mode_3d(self) -> ThreeDView:
        # Defaults to the first eigenmode; per-mode embeds can use a
        # second filter attr or extend this to accept a mode kwarg once
        # paradoc's substitution parser supports call args here.
        return ThreeDView(
            glb_key=f"{self._r.name}_mode_01",
            caption=f"{self._r.fem_format} — mode 1 deformed shape.",
            camera_preset="iso_3",
        )
