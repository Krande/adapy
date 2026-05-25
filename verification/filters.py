"""Paradoc filter classes for the FEA verification report.

Migration state (phase 2 of the paradoc.tasks rollout): the filter
classes now accept *either* the legacy data-passed shape or the new
TaskHandle-bound shape. Both work; the @attr methods pick the right
source automatically.

Legacy shape (still used by build_verification_report.py):

    one._filter_registry.register(Beam(bm, name="beam"))
    one._filter_registry.register(Eig(results, num_modes=11, name="eig"))

New TaskHandle shape (the migration target):

    from paradoc.tasks import TaskHandle

    one._filter_registry.register(
        Beam(name="beam", task=TaskHandle.unbound("design"))
    )
    one._filter_registry.register(
        Eig(name="eig", task=TaskHandle.unbound("run_eig"))
    )

With the task-bound shape, the filter @attr methods pull source data
via `self.task.results(...)`; the OneDoc instance must be constructed
with `runner=<paradoc.tasks.Runner>` so its discovery step binds the
handles. The next migration commit replaces the imperative driver
with `paradoc.tasks.build_document`, at which point this dual-mode
layer can collapse to task-only.

Markdown references resolve as `${ filter_name.attr_name(:fmtspec) }`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np

from paradoc.filters import Filter, FigureView, TableView, ThreeDView, attr

if TYPE_CHECKING:
    import ada
    from utils import FeaVerificationResult


class Beam(Filter):
    """Reads geometry/section/material straight off the analyzed ada.Beam.

    Legacy ctor: ``Beam(beam, name=...)`` — data passed in directly.
    Task ctor:   ``Beam(name=..., task=TaskHandle.unbound("design"))``
    — beam is walked off the runner's design output.
    """

    def __init__(
        self,
        beam: "ada.Beam | None" = None,
        *,
        name: str,
        task=None,
    ):
        super().__init__(name=name, task=task)
        self._beam_obj = beam

    def _beam(self) -> "ada.Beam":
        if self._beam_obj is not None:
            return self._beam_obj
        # Task-bound path: design task has one cell whose result is the
        # canonical Assembly. The beam is the sole physical object.
        import ada as _ada

        assembly = self.task.results()[0]
        return next(
            b
            for b in assembly.get_all_physical_objects()
            if isinstance(b, _ada.Beam)
        )

    @attr
    def length_m(self) -> float:
        bm = self._beam()
        n1 = np.asarray(bm.n1.p, dtype=float)
        n2 = np.asarray(bm.n2.p, dtype=float)
        return float(np.linalg.norm(n2 - n1))

    @attr
    def section_name(self) -> str:
        return self._beam().section.name

    @attr
    def section_type(self) -> str:
        return str(self._beam().section.type)

    @attr
    def material_name(self) -> str:
        return self._beam().material.name

    @attr
    def youngs_modulus_pa(self) -> float:
        return float(self._beam().material.model.E)

    @attr
    def yield_stress_pa(self) -> float:
        return float(self._beam().material.model.sig_y)

    @attr
    def density_kgm3(self) -> float:
        return float(self._beam().material.model.rho)

    @attr
    def description(self) -> str:
        return str(self._beam())

    @attr
    def geometry_3d(self) -> ThreeDView:
        return ThreeDView(
            glb_key="beam_geom",
            caption="Cantilever beam geometry.",
            camera_preset="iso_3",
        )


class Versions(Filter):
    """Solver version strings.

    Stays data-fed for now; solver versions are environmental (probed
    from PATH executables), not task-produced. A future `version_probe`
    integration could move this onto a runner-backed pattern.
    """

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

    Legacy ctor: ``Eig(results_list, num_modes=11, name=...)``.
    Task ctor:   ``Eig(name=..., task=TaskHandle.unbound("run_eig"))``
    — scalars are read live from `self.task.results()`.

    The `compare_*` table attrs and `freq_vs_mode_plot` return TableView
    / FigureView references; the actual data registration on
    `OneDoc.db_manager` still happens in the build driver before
    compile. Migrating that to filter @attr methods (or a dedicated
    bake task) is the next migration step.
    """

    _DEFAULT_NUM_MODES = 11

    def __init__(
        self,
        results: "list[FeaVerificationResult] | None" = None,
        num_modes: Optional[int] = None,
        *,
        name: str,
        task=None,
    ):
        super().__init__(name=name, task=task)
        self._results_legacy = results
        self._num_modes_override = num_modes

    def _live_results(self) -> list:
        """List of run_eig cell results, dropping the Nones.

        Legacy path: returns the stored results list directly (those
        were already filtered upstream by `simulate()`'s try/except).
        Task path: pulls from the runner and drops Nones (the run_eig
        task body returns None for missing-solver / failed cells).
        """
        if self._results_legacy is not None:
            return list(self._results_legacy)
        return [r for r in self.task.results() if r is not None]

    def _live_solvers(self) -> list[str]:
        """Solver names for cells that produced a result."""
        if self._results_legacy is not None:
            return sorted({r.fem_format for r in self._results_legacy})
        # Task path needs the cell to read solver from kwargs.
        live: set[str] = set()
        for c in self.task.cells():
            if self.task._runner.result_for(c) is None:
                continue
            live.add(c.kwargs["solver"])
        return sorted(live)

    @attr
    def num_modes(self) -> int:
        if self._num_modes_override is not None:
            return self._num_modes_override
        return self._DEFAULT_NUM_MODES

    @attr
    def num_cases(self) -> int:
        return len(self._live_results())

    @attr
    def solvers(self) -> str:
        return ", ".join(self._live_solvers())

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
        return FigureView(
            plot_key="eig_freq_vs_mode",
            caption="First-mode frequency by solver / geometry.",
        )


# ---------------------------------------------------------------------
# Module-level instances. paradoc.filters.discover_filters picks these
# up from `verification/filters.py`; OneDoc binds the TaskHandles when
# the runner-aware compile path runs (CLI: `paradoc build verification`,
# or `create_fea_report` after the driver flip).
#
# Versions is NOT instantiated here because it carries runtime version
# data the driver constructs separately via `_solver_versions()`. When
# version_probe becomes accessible on TaskHandle, Versions moves here
# too.
# ---------------------------------------------------------------------

from paradoc.tasks import TaskHandle  # noqa: E402 — module-level instances need this

beam = Beam(name="beam", task=TaskHandle.unbound("design"))
eig = Eig(name="eig", task=TaskHandle.unbound("run_eig"))


# `SolverCase` lived here until step 5 of the FEA-docs generalisation
# moved per-case filter logic into `ada.fem.results.docs.FeaCaseFilter`.
# That class covers `.solver` / `.solver_version` / `.n_modes` plus
# class-level `.mode_1` … `.mode_30` views — superset of what
# `SolverCase` exposed. The verification report registers
# `FeaCaseFilter.from_assets(assets)` instead of `SolverCase(result)`;
# the modal-table attr (`SolverCase.modal_table`) was unused by the
# generated markdown so it didn't need a forwarding shim. If a future
# consumer wants a per-case frequency table, register it directly with
# `one.db_manager.add_table(...)` and reference by key — paradoc
# resolves the bare `${ <table_key> }` substitution without going
# through a filter attr.
