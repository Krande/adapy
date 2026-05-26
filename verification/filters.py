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


# ---------------------------------------------------------------------
# Block-sugar handler for `<!-- paradoc:figure figure_source:
# eig_modes_section ... -->`. Registered at module load so paradoc
# picks it up alongside the @task discovery.
#
# Why this lives in filters.py and not tasks.py: paradoc has two
# user-facing markdown surfaces — ``${ name.attr }`` (Filter subclasses)
# and ``<!-- paradoc:figure ... -->`` (FigureSourceFilter subclasses).
# The names the markdown author references in either syntax conceptually
# belong to the "filters" file. tasks.py stays focused on workloads
# (@task functions that produce data).
# ---------------------------------------------------------------------

import hashlib
import logging
import pathlib  # noqa: F401  (kept for type/path manipulation in render helpers)
from typing import Literal

from pydantic import Field

from paradoc.figure_sources.filters.base import (
    FigureSourceFilter,
    MarkdownChunk,
    RenderResult,
    register_filter,
)
from paradoc.figure_sources.models import BaseFigureSource, register_spec

from ada.fem.results.docs import FeaDocAssets, assets_from_bundle_dir

_eig_logger = logging.getLogger(__name__)


class EigModesSection(BaseFigureSource):
    """Spec for ``figure_source: eig_modes_section``.

    Expands a comment block into a per-case markdown section for every
    case under ``assets_dir/`` whose case-name carries ``solver``'s short
    tag (``ca``, ``ccx``, ``aba``, ``sesam``). Each case yields either a
    full ``#### Mode N`` walk (``layout=mode_per_section``) or a flat
    sequence of mode figures (``layout=gallery`` — visual grouping
    today falls back to mode_per_section without subsection headings).

    The block carries no figure of its own; ``figure_title`` is
    inherited from :class:`BaseFigureSource` and ignored at render
    time. ``camera_pos`` / ``renderer`` are likewise unused (each mode's
    poster was baked upstream by ``fea_outputs``); they sit on the spec
    only because the base class declares them.
    """

    figure_source: Literal["eig_modes_section"] = "eig_modes_section"
    solver: Literal["abaqus", "calculix", "code_aster", "sesam"] = Field(
        ..., description="Which solver's cases the block expands."
    )
    layout: Literal["mode_per_section", "gallery"] = Field(
        "mode_per_section",
        description=(
            "How to lay out per-case modes. mode_per_section emits "
            "`#### Mode N` headings between figures; gallery emits a "
            "flat sequence of figures."
        ),
    )
    assets_dir: str = Field(
        "_assets",
        description=(
            "Path (relative to the doc / bundle root) where per-case "
            "FEA bundles live. The filter walks it for "
            "`<case>/fea.manifest.json`."
        ),
    )


register_spec("eig_modes_section", EigModesSection)


# Short-form tokens used in the case-name convention `eig_case_name`
# produces in tasks.py. Maps spec.solver → the token to substring-match
# against the case directory's basename. Keep in lockstep with
# `ada.fem.results.docs`'s naming convention.
_SOLVER_NAME_TAG = {
    "abaqus": "aba",
    "calculix": "ccx",
    "code_aster": "ca",
    "sesam": "sesam",
}


def _case_matches_solver(case_name: str, solver_tag: str) -> bool:
    """True if ``case_name`` belongs to ``solver_tag``'s solver.

    Case names look like ``cantilever_EIG_<tag>_<geom>_<order>_...``.
    Split on ``_`` and check the position-3 token (after the static
    ``cantilever`` / ``EIG`` prefix) — substring-matching would
    misfire on ``sesam`` matching ``ces`` etc.
    """
    parts = case_name.split("_")
    # cantilever_EIG_<tag>_..., so index 2 is the solver token.
    if len(parts) < 3:
        return False
    return parts[2] == solver_tag


@register_filter
class EigModesSectionFilter(FigureSourceFilter):
    """Block-sugar handler for ``eig_modes_section``.

    Walks ``bundle_root/<assets_dir>/`` for per-case FEA bundles
    (``fea.manifest.json`` + posters baked by the upstream
    ``fea_outputs`` task), filters by solver, and emits the per-case
    markdown sections. Returns a mixed list of
    :class:`MarkdownChunk` (section + mode headings, placeholder text)
    and :class:`RenderResult` (per-mode figure references); the
    preprocessor splices them into the document in order.

    The same case-name keys ``to_paradoc_rows`` already registered are
    reused for ``ThreeDData`` rows so the 3D viewer mounts against the
    same GLBs (``add_three_d`` uses ``INSERT OR REPLACE``).
    """

    figure_source = "eig_modes_section"

    _PLACEHOLDER = "_Mode-shape figures unavailable for this case._"

    def render(self, spec, *, key):  # type: ignore[override]
        if not isinstance(spec, EigModesSection):
            raise TypeError(
                f"EigModesSectionFilter received non-EigModesSection spec: "
                f"{type(spec).__name__}"
            )

        solver_tag = _SOLVER_NAME_TAG[spec.solver]
        # Source FEA bundles live under doc_root (the dir containing
        # paradoc.toml / tasks.py / filters.py / _assets/), NOT under
        # bundle_root (which is the markdown build-staging dir under
        # work_dir). The block-sugar reads pre-baked artefacts from the
        # source tree; paradoc's static-export step copies them into
        # the final bundle via the ThreeDData rows fea_outputs emits.
        assets_root = (self.doc_root / spec.assets_dir).resolve()

        if not assets_root.is_dir():
            _eig_logger.warning(
                "eig_modes_section: assets_dir %s does not exist under "
                "doc_root; emitting placeholder.",
                assets_root,
            )
            return [
                MarkdownChunk(
                    text=f"_No FEA bundles found under `{spec.assets_dir}`._"
                )
            ]

        case_dirs = sorted(
            d for d in assets_root.iterdir()
            if d.is_dir() and _case_matches_solver(d.name, solver_tag)
        )

        if not case_dirs:
            return [
                MarkdownChunk(
                    text=f"_No cases for solver `{spec.solver}`._"
                )
            ]

        entries: list = []
        for case_dir in case_dirs:
            case_name = case_dir.name
            manifest = case_dir / "fea.manifest.json"

            # Cache-only / pre-bake state: case dir exists (e.g. mode
            # GLBs committed) but the bake never ran, so manifest +
            # posters are absent. Emit the placeholder so the report
            # reader sees the case exists but has no figures yet.
            if not manifest.is_file():
                entries.append(
                    MarkdownChunk(text=f"\n### {case_name}\n\n{self._PLACEHOLDER}\n")
                )
                continue

            try:
                assets = assets_from_bundle_dir(case_dir, key=case_name)
            except Exception as exc:
                _eig_logger.warning(
                    "eig_modes_section: failed to load %s: %s", case_dir, exc
                )
                entries.append(
                    MarkdownChunk(text=f"\n### {case_name}\n\n{self._PLACEHOLDER}\n")
                )
                continue

            entries.extend(self._render_case(assets, layout=spec.layout))

        return entries or [MarkdownChunk(text="_No baked FEA cases found._")]

    def _render_case(
        self, assets: FeaDocAssets, *, layout: str
    ) -> list:
        """Build chunks + RenderResults for one case.

        ``layout='gallery'`` falls back to a flat sequence of figures
        without subsection headings — visual grouping (CSS grid) is a
        future iteration. mode_per_section is the default.
        """
        case_name = assets.key
        chunks: list = [MarkdownChunk(text=f"\n### {case_name}\n")]

        if not assets.poster_paths:
            chunks.append(MarkdownChunk(text=f"\n{self._PLACEHOLDER}\n"))
            return chunks

        # Source bundles live under doc_root, not bundle_root. Emit
        # absolute paths in the RenderResult — paradoc's preprocessor
        # handles absolute png_path via `os.path.relpath(absolute, md_dir)`
        # and the static-export glb resolver searches absolute paths
        # first. The user's repo convention has _assets/ next to
        # tasks.py / filters.py rather than under the markdown source_dir.
        glb_abs = str(assets.mesh_glb_path)
        mesh_sha = hashlib.sha256(assets.mesh_glb_path.read_bytes()).hexdigest()
        mesh_size = assets.mesh_glb_path.stat().st_size

        for mode_idx in sorted(assets.poster_paths.keys()):
            mode_n = mode_idx + 1
            poster = assets.poster_paths[mode_idx]
            png_abs = str(poster)

            if layout == "mode_per_section":
                chunks.append(MarkdownChunk(text=f"\n#### Mode {mode_n}\n"))

            chunks.append(
                RenderResult(
                    png_path=png_abs,
                    glb_path=glb_abs,
                    glb_sha256=mesh_sha,
                    glb_size=mesh_size,
                    caption=f"{case_name} — mode {mode_n}",
                    camera_pos="iso_3",
                    source_type="fea_artefact_bundle_mode_view",
                    metadata={
                        "fea_bundle_key": case_name,
                        "fea_mode_index": mode_idx,
                        "image_path": png_abs,
                    },
                )
            )

        return chunks
