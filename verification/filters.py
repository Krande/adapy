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

import hashlib
import json
import logging
import pathlib
from typing import TYPE_CHECKING, Literal, Optional

import numpy as np
from paradoc.figure_sources.filters.base import (
    FigureSourceFilter,
    MarkdownChunk,
    RenderResult,
    register_filter,
)
from paradoc.figure_sources.models import BaseFigureSource, register_spec
from paradoc.filters import FigureView, Filter, TableView, ThreeDView, attr
from pydantic import Field

from ada.fem.results.docs import FeaDocAssets, assets_from_bundle_dir

if TYPE_CHECKING:
    from utils import FeaVerificationResult

    import ada

_eig_logger = logging.getLogger(__name__)

_ASSETS_DIR = pathlib.Path(__file__).parent / "_assets"
#: The cantilever eigen cases' JSON cache (tasks.py writes it); the plate cases keep theirs apart.
_CACHE_DIR = pathlib.Path(__file__).parent / ".cache"
_PLATE_CACHE_DIR = pathlib.Path(__file__).parent / ".cache-plate"


def _poster(rel_path: str) -> str | None:
    """The static poster a geometry GLB task wrote beside its GLB, for the DOCX / PDF exports.

    Without it paradoc stands a ``MISSING_3D_IMAGE.png`` in for the interactive view there.
    Forward slashes: the path lands in markdown ``![cap](path)``, where a Windows path's
    backslashes are escapes and pandoc would find no file.
    """
    path = _ASSETS_DIR / rel_path
    return path.as_posix() if path.is_file() else None


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
        return next(b for b in assembly.get_all_physical_objects() if isinstance(b, _ada.Beam))

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
            image_path=_poster("beam.png"),
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

    @attr
    def num_modes(self) -> int:
        if self._num_modes_override is not None:
            return self._num_modes_override
        return self._DEFAULT_NUM_MODES

    def _compared_cases(self) -> list[tuple[str, str]]:
        """``(name, solver)`` for every case the comparison tables hold: this build's live
        results plus the cached ones. Counting only live results made a cache-only build (CI,
        or a machine without the solvers) report "0 cases are compared" above full tables."""
        from ada.fem.results import FeaCaseResult, walk_cached_case_results

        live = self._live_results()
        cases = {getattr(r, "name", None): getattr(r, "fem_format", None) for r in live}
        if self._results_legacy is None:
            # Task path: run_eig's results are raw FEAResults; name them by the cell's solver.
            cases = {}
            for c in self.task.cells():
                result = self.task._runner.result_for(c)
                if result is not None:
                    cases[result.name] = c.kwargs["solver"]
        for cached in walk_cached_case_results(FeaCaseResult, _CACHE_DIR, skip_names=set(cases)):
            cases.setdefault(cached.name, cached.fem_format)
        return sorted(cases.items())

    @attr
    def num_cases(self) -> int:
        return len(self._compared_cases())

    @attr
    def solvers(self) -> str:
        return ", ".join(sorted({solver for _, solver in self._compared_cases() if solver}))

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
    def eff_mass_summary(self) -> TableView:
        # Per-case effective modal mass [kg] summed over the captured
        # modes (global X/Y/Z); registered by the eff_mass_table task.
        return TableView(table_key="eff_mass_summary")

    # Per-mode cross-solver modal-mass comparison for the appendix: one effective
    # mass (meff) and one participation factor (pf) table per (geom, order), rows
    # by (mode, direction), registered by eff_mass_compare_tables. Every key is
    # always registered (placeholder row when no solver reported it), because the
    # report references them statically and paradoc errors on an unresolved one.
    @attr
    def meff_solid_o1(self) -> TableView:
        return TableView(table_key="eig_compare_solid_o1_meff")

    @attr
    def meff_solid_o2(self) -> TableView:
        return TableView(table_key="eig_compare_solid_o2_meff")

    @attr
    def meff_shell_o1(self) -> TableView:
        return TableView(table_key="eig_compare_shell_o1_meff")

    @attr
    def meff_shell_o2(self) -> TableView:
        return TableView(table_key="eig_compare_shell_o2_meff")

    @attr
    def meff_line_o1(self) -> TableView:
        return TableView(table_key="eig_compare_line_o1_meff")

    @attr
    def meff_line_o2(self) -> TableView:
        return TableView(table_key="eig_compare_line_o2_meff")

    @attr
    def pf_solid_o1(self) -> TableView:
        return TableView(table_key="eig_compare_solid_o1_pf")

    @attr
    def pf_solid_o2(self) -> TableView:
        return TableView(table_key="eig_compare_solid_o2_pf")

    @attr
    def pf_shell_o1(self) -> TableView:
        return TableView(table_key="eig_compare_shell_o1_pf")

    @attr
    def pf_shell_o2(self) -> TableView:
        return TableView(table_key="eig_compare_shell_o2_pf")

    @attr
    def pf_line_o1(self) -> TableView:
        return TableView(table_key="eig_compare_line_o1_pf")

    @attr
    def pf_line_o2(self) -> TableView:
        return TableView(table_key="eig_compare_line_o2_pf")

    @attr
    def freq_vs_mode_plot(self) -> FigureView:
        return FigureView(
            plot_key="eig_freq_vs_mode",
            caption="First-mode frequency by solver / geometry.",
        )


class Plate(Filter):
    """The plate strip's views: deflection under pressure, and its eigenfrequencies.

    Task-bound to ``plate_run_static``, so the scalars below report what actually ran on this
    machine rather than what the matrix declares. The tables themselves are registered by
    ``plate_static_tables`` / ``plate_eig_tables``; these are references to their keys.
    """

    def __init__(self, *, name: str, task=None):
        super().__init__(name=name, task=task)

    def _live_cells(self) -> list:
        if self.task is None:
            return []
        return [c for c in self.task.cells() if self.task._runner.result_for(c) is not None]

    @attr
    def num_cases(self) -> int:
        return len(self._live_cells())

    @attr
    def solvers(self) -> str:
        return ", ".join(sorted({c.kwargs["solver"] for c in self._live_cells()})) or "none on this machine"

    @attr
    def length_m(self) -> float:
        from ada.api.fem_tasks import PLATE_STRIP_LENGTH

        return PLATE_STRIP_LENGTH

    @attr
    def width_m(self) -> float:
        from ada.api.fem_tasks import PLATE_STRIP_WIDTH

        return PLATE_STRIP_WIDTH

    @attr
    def thickness_m(self) -> float:
        from ada.api.fem_tasks import PLATE_STRIP_THICKNESS

        return PLATE_STRIP_THICKNESS

    @attr
    def pressure_pa(self) -> float:
        from ada.api.fem_tasks import PLATE_STRIP_PRESSURE

        return PLATE_STRIP_PRESSURE

    @attr
    def closed_form_deflection_m(self) -> float:
        from ada.api.fem_tasks import plate_closed_form_deflection

        return plate_closed_form_deflection()

    @attr
    def static_plain(self) -> TableView:
        return TableView(table_key="plate_static_stFalse")

    @attr
    def static_stiffened(self) -> TableView:
        return TableView(table_key="plate_static_stTrue")

    @attr
    def eig_compare(self) -> TableView:
        return TableView(table_key="plate_eig_compare")

    @attr
    def geometry_3d(self) -> ThreeDView:
        return ThreeDView(
            glb_key="plate_geom",
            caption="Plate strip geometry.",
            camera_preset="iso_3",
            image_path=_poster("plate/plate.png"),
        )

    @attr
    def geometry_stiffened_3d(self) -> ThreeDView:
        return ThreeDView(
            glb_key="plate_stiffened_geom",
            caption="Plate strip with a T-profile stiffener on its centreline.",
            camera_preset="iso_3",
            image_path=_poster("plate/plate_stiffened.png"),
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
plate = Plate(name="plate", task=TaskHandle.unbound("plate_run_static"))


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
# fea_modes_compare ... -->`. Registered at module load so paradoc
# picks it up alongside the @task discovery.
#
# Why this lives in filters.py and not tasks.py: paradoc has two
# user-facing markdown surfaces — ``${ name.attr }`` (Filter subclasses)
# and ``<!-- paradoc:figure ... -->`` (FigureSourceFilter subclasses).
# The names the markdown author references in either syntax conceptually
# belong to the "filters" file. tasks.py stays focused on workloads
# (@task functions that produce data).
# ---------------------------------------------------------------------


class FeaModesCompare(BaseFigureSource):
    """Spec for ``figure_source: fea_modes_compare``.

    Expands a comment block into the results appendix for one family of cases -- every bundle under
    ``assets_dir/`` whose name starts with ``case_prefix`` -- organised so the formats can be read
    against each other: one ``###`` section per mesh configuration, one ``#### Mode N`` per mode, and
    under it one figure per solver, side by side.

    Grouping by configuration first and by solver last is the point. The previous layout ran one
    solver's cases end to end, so the Calculix and Code_Aster pictures of the same mode sat pages
    apart; side by side, a swapped mode pair or a different deflected shape is visible at a glance.

    ``analysis="static"`` drops the per-mode heading: a static case has one displacement step.

    The block carries no figure of its own; ``figure_title`` / ``camera_pos`` / ``renderer`` are
    inherited from :class:`BaseFigureSource` and unused (the posters were baked upstream).
    """

    figure_source: Literal["fea_modes_compare"] = "fea_modes_compare"
    case_prefix: str = Field(
        ...,
        description=(
            "Case-name prefix before the solver token, e.g. `cantilever_EIG`, `plate_EIG`, "
            "`plate_static`. Case names are `<prefix>_<solver>_<configuration>`."
        ),
    )
    analysis: Literal["eigen", "static"] = Field("eigen", description="`static` drops the per-mode heading.")
    assets_dir: str = Field(
        "_assets",
        description="Path (relative to the doc root) searched recursively for `<case>/fea.manifest.json`.",
    )
    beam_solids: bool = Field(
        False,
        description=(
            "Open the interactive viewers with beam elements drawn as their solid cross-section "
            "instead of as lines -- which also shows twist, so a torsion mode is visible. Applies "
            "to bundles that carry beam solids; the viewer's toggle still switches back."
        ),
    )


register_spec("fea_modes_compare", FeaModesCompare)


#: Solver tokens as the case names spell them, in the column order the grid uses. Keep in lockstep
#: with ``ada.api.fem_tasks.SHORT_NAME_MAP``; the cantilever's Sesam cache spells it both ways.
_SOLVER_ORDER = ("aba", "ccx", "ca", "ses", "sesam")
_SOLVER_LABEL = {"aba": "Abaqus", "ccx": "Calculix", "ca": "Code_Aster", "ses": "Sesam", "sesam": "Sesam"}

#: Tailwind utilities the paradoc frontend already ships: one column on a phone, two from `sm` up.
_GRID_DIV_OPEN = '::: {class="grid grid-cols-1 sm:grid-cols-2 gap-4"}'
_GRID_DIV_CLOSE = ":::"


def _snapshot(case_key: str) -> dict:
    """A case's JSON snapshot, which every build writes for every solver (the live ones are
    gitignored, the licensed ones committed). Empty when there is none or it cannot be read."""
    for cache_dir in (_CACHE_DIR, _PLATE_CACHE_DIR):
        path = cache_dir / f"{case_key}.json"
        if not path.is_file():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _eig_logger.warning("fea_modes_compare: unreadable snapshot %s: %s", path, exc)
            return {}
    return {}


def _snapshot_modes(case_key: str) -> dict[int, dict]:
    """A case's per-mode data from its snapshot: mode number -> the mode's fields. Empty when none."""
    modes = _snapshot(case_key).get("eigen_mode_data") or {}
    return {int(m["no"]): m for m in modes.values() if isinstance(m, dict) and m.get("no") is not None}


def _peak_displacements(case_dir: pathlib.Path) -> dict:
    """The largest translation per global axis, ``{"ux": m, "uy": m, "uz": m}``, from the case's
    bundle manifest -- the displacement field's per-component ranges, so nothing is re-read from the
    result. The axes are found by component name (Sesam's displacement leads with a reduction,
    ``ALL``, not an axis). An axis the field does not carry is left out."""
    from ada.fem.results.artefacts.mode_normalization import AXIS_ALIASES

    try:
        manifest = json.loads((case_dir / "fea.manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    field = next((f for f in manifest.get("fields", []) if f.get("category") == "displacement"), None)
    if field is None:
        return {}
    ranges = field.get("scalar_range") or {}
    # A bundle baked with mode normalization holds ``raw * factor``. Undone for a single step (the
    # ranges span all steps, so with several there is no one factor to undo); a peak that cannot be
    # brought back to the solver's value is not shown.
    factors = (field.get("mode_normalization") or {}).get("factors") or []
    if len(factors) > 1:
        return {}
    scale = 1.0 / float(factors[0]) if factors and float(factors[0]) else 1.0
    by_lower = {c.lower(): c for c in field.get("components", [])}
    out = {}
    for axis, aliases in zip(("ux", "uy", "uz"), AXIS_ALIASES):
        comp = next((by_lower[a] for a in aliases if a in by_lower), None)
        r = ranges.get(comp) if comp else None
        if r:
            out[axis] = max(abs(float(r[0])), abs(float(r[1]))) * scale
    return out


def _closed_form_deflection(case_prefix: str, config: str) -> float | None:
    """The plain plate strip's closed-form mid-span deflection [m]; None for every other case (the
    stiffened strip has none -- its stiffener is what the solvers are there to answer)."""
    if not case_prefix.startswith("plate_static") or "stFalse" not in config:
        return None
    from ada.api.fem_tasks import plate_closed_form_deflection

    return plate_closed_form_deflection()


def _closed_form_frequency(case_prefix: str, config: str, mode_idx: int) -> float | None:
    """The plain plate strip's closed-form frequency of mode ``mode_idx`` (0-based) [Hz]; None for
    every other case. Mode-for-mode, as the plate eigen summary table compares them."""
    if not case_prefix.startswith("plate_EIG") or "stFalse" not in config:
        return None
    from ada.api.fem_tasks import plate_closed_form_frequencies

    return plate_closed_form_frequencies(mode_idx + 1)[mode_idx]


def _deviation_pct(value, reference) -> float | None:
    if value is None or not reference:
        return None
    return (value - reference) / reference * 100.0


def _reference(values: list, given: float | None) -> tuple[float | None, str]:
    """What a deviation column compares against: ``given`` (a closed form) when there is one, else
    the mean of the solvers' values -- which only says something with at least two of them."""
    if given is not None:
        return given, "closed form"
    present = [v for v in values if v is not None]
    if len(present) >= 2:
        return sum(present) / len(present), "mean"
    return None, ""


def _fmt(value, spec: str) -> str:
    if value is None:
        return "–"
    text = format(value, spec)
    # A nonzero value too small for the fixed-point spec (an off-axis factor of 1e-14, say) would
    # print as a bare 0.00: show it in scientific notation instead.
    if float(text) == 0.0 and value != 0:
        return format(value, ".2e")
    return text


def mode_data_table(rows: list[tuple[str, dict | None]], closed_form_hz: float | None = None) -> str:
    """The table under one mode's figures: a row per solver shown, with what its reader reported.

    ``rows`` is ``(solver label, mode fields | None)``. The participation factor is scaled to unit
    generalised mass, ``sign(Γ)·√Meff`` -- each solver normalises its eigenvectors its own way, so the raw
    factors do not compare (see the modal participation section). A quantity no solver reported is left
    out; a value one solver did not report shows as "–". Empty when no row has data.

    ``Δf`` is each solver's frequency against ``closed_form_hz`` when the case has one, else against
    the mean of the solvers shown.
    """
    from types import SimpleNamespace

    from utils import MODAL_MASS_QUANTITIES

    pf_of = MODAL_MASS_QUANTITIES["pf"][0]  # the same scaling as the modal participation tables

    def field(key):
        return lambda m: m.get(key)

    def participation(direction):
        keys = ("px", "py", "pz", "efx", "efy", "efz")
        return lambda m: pf_of(SimpleNamespace(**{k: m.get(k) for k in keys}), direction)

    data = [(label, m) for label, m in rows if m]
    if not data:
        return ""
    ref, ref_label = _reference([m.get("f_hz") for _, m in data], closed_form_hz)
    columns = [
        ("f [Hz]", field("f_hz"), ".3f"),
        *([(f"Δf vs {ref_label} [%]", lambda m: _deviation_pct(m.get("f_hz"), ref), "+.2f")] if ref else []),
        ("λ [rad²/s²]", field("eigenvalue"), ".6g"),
        *[(f"Meff {d} [kg]", field(f"ef{d.lower()}"), ".2f") for d in "XYZ"],
        *[(f"Γ {d}", participation(d), ".3f") for d in "XYZ"],
    ]
    kept = [c for c in columns if any(c[1](m) is not None for _, m in data)]
    if not kept:
        return ""
    header = "| Solver | " + " | ".join(name for name, *_ in kept) + " |"
    rule = "|:--|" + "--:|" * len(kept)
    body = ["| " + label + " | " + " | ".join(_fmt(get(m), spec) for _, get, spec in kept) + " |" for label, m in data]
    if ref_label == "closed form":
        body.append(f"| Closed form | {_fmt(ref, '.3f')} |" + " |" * (len(kept) - 1))
    return "\n".join([header, rule, *body])


def static_data_table(rows: list[tuple[str, dict | None]], closed_form_m: float | None = None) -> str:
    """The table under a static case's figures: a row per solver with its deflection.

    ``rows`` is ``(solver label, {"mid": .., "ux": .., "uy": .., "uz": ..} | None)``, metres: the
    mid-span deflection the summary tables compare, and the largest translation per axis over the
    whole model. Shown in millimetres. ``Δ`` is the mid-span deflection against ``closed_form_m``
    when the case has one, else against the mean of the solvers shown. Empty when no row has data.
    """
    data = [(label, d) for label, d in rows if d]
    if not data:
        return ""
    ref, ref_label = _reference([d.get("mid") for _, d in data], closed_form_m)

    def mm(key):
        return lambda d: None if d.get(key) is None else d[key] * 1e3

    columns = [
        ("Mid-span u_z [mm]", mm("mid"), ".4f"),
        *([(f"Δ vs {ref_label} [%]", lambda d: _deviation_pct(d.get("mid"), ref), "+.2f")] if ref else []),
        ("Peak u_z [mm]", mm("uz"), ".4f"),
        ("Peak u_x [mm]", mm("ux"), ".4f"),
        ("Peak u_y [mm]", mm("uy"), ".4f"),
    ]
    kept = [c for c in columns if any(c[1](d) is not None for _, d in data)]
    if not kept:
        return ""
    header = "| Solver | " + " | ".join(name for name, *_ in kept) + " |"
    rule = "|:--|" + "--:|" * len(kept)
    body = ["| " + label + " | " + " | ".join(_fmt(get(d), spec) for _, get, spec in kept) + " |" for label, d in data]
    if ref_label == "closed form":
        body.append(f"| Closed form | {_fmt(ref * 1e3, '.4f')} |" + " |" * (len(kept) - 1))
    return "\n".join([header, rule, *body])


def _split_case_name(case_name: str, prefix: str) -> tuple[str, str] | None:
    """``<prefix>_<solver>_<configuration>`` -> ``(solver, configuration)``, or None if not ours."""
    head = f"{prefix}_"
    if not case_name.startswith(head):
        return None
    solver, _, config = case_name[len(head) :].partition("_")
    if solver not in _SOLVER_LABEL or not config:
        return None
    return solver, config


def _config_label(config: str) -> str:
    """``shell_o1_hqTrue_riFalse`` -> ``Shell, 1st order, QUAD``; unknown tokens pass through."""
    tokens = config.split("_")
    geom = tokens[0].lower()
    parts = [geom.capitalize()]
    for tok in tokens[1:]:
        if tok in ("o1", "o2"):
            parts.append("1st order" if tok == "o1" else "2nd order")
        elif tok.startswith("hq"):
            hq = tok == "hqTrue"
            if geom == "solid":
                parts.append("HEX" if hq else "TET")
            elif geom == "shell":
                parts.append("QUAD" if hq else "TRI")
        elif tok.startswith("ri"):
            if tok == "riTrue":
                parts.append("reduced integration")
        elif tok.startswith("st"):
            parts.append("stiffened" if tok == "stTrue" else "plain")
        elif tok.startswith("h") and "p" in tok:
            parts.append(f"seed {tok[1:].replace('p', '.')} m")
        else:
            parts.append(tok)
    return ", ".join(parts)


@register_filter
class FeaModesCompareFilter(FigureSourceFilter):
    """Block-sugar handler for ``fea_modes_compare``.

    Walks ``doc_root/<assets_dir>/`` for baked bundles (``fea.manifest.json`` + posters, written by
    the ``fea_outputs`` / ``plate_*_fea_outputs`` tasks), groups them by configuration and returns a
    mixed list of :class:`MarkdownChunk` (headings, grid fences, placeholders) and
    :class:`RenderResult` (one per solver per mode) that the preprocessor splices in order.

    The grid is a pandoc fenced div; the frontend renders a Div's classes as-is, and each image in it
    is its own paragraph, so each stays a Figure the 3D viewer mounts on.
    """

    figure_source = "fea_modes_compare"

    def render(self, spec, *, key):  # type: ignore[override]
        if not isinstance(spec, FeaModesCompare):
            raise TypeError(f"FeaModesCompareFilter received non-FeaModesCompare spec: {type(spec).__name__}")

        # Bundles are read from the source tree (doc_root), not the build staging dir; the static
        # export copies them into the bundle via the ThreeDData rows each RenderResult registers.
        assets_root = (self.doc_root / spec.assets_dir).resolve()
        if not assets_root.is_dir():
            _eig_logger.warning("fea_modes_compare: %s does not exist; emitting placeholder.", assets_root)
            return [MarkdownChunk(text=f"_No FEA bundles found under `{spec.assets_dir}`._")]

        # configuration -> solver -> case dir
        groups: dict[str, dict[str, pathlib.Path]] = {}
        for case_dir in sorted(p for p in assets_root.rglob("*") if p.is_dir()):
            parsed = _split_case_name(case_dir.name, spec.case_prefix)
            if parsed is None:
                continue
            solver, config = parsed
            groups.setdefault(config, {})[solver] = case_dir

        if not groups:
            return [MarkdownChunk(text=f"_No baked cases for `{spec.case_prefix}`._")]

        entries: list = []
        for config in sorted(groups):
            entries.extend(
                self._render_config(
                    config,
                    groups[config],
                    analysis=spec.analysis,
                    beam_solids=spec.beam_solids,
                    case_prefix=spec.case_prefix,
                )
            )
        return entries

    def _render_config(
        self,
        config: str,
        by_solver: dict[str, pathlib.Path],
        *,
        analysis: str,
        beam_solids: bool = False,
        case_prefix: str = "",
    ) -> list:
        entries: list = [MarkdownChunk(text=f"\n### {_config_label(config)}\n")]

        loaded: dict[str, FeaDocAssets] = {}
        unavailable: list[str] = []
        for solver in sorted(by_solver, key=_SOLVER_ORDER.index):
            case_dir = by_solver[solver]
            try:
                assets = assets_from_bundle_dir(case_dir, key=case_dir.name)
            except Exception as exc:  # noqa: BLE001 - a cache-only / half-baked case degrades, not fails
                _eig_logger.warning("fea_modes_compare: failed to load %s: %s", case_dir, exc)
                unavailable.append(solver)
                continue
            if not assets.poster_paths:
                unavailable.append(solver)
                continue
            loaded[solver] = assets

        if unavailable:
            names = ", ".join(_SOLVER_LABEL[s] for s in unavailable)
            entries.append(MarkdownChunk(text=f"\n_Figures unavailable for: {names}._\n"))
        if not loaded:
            return entries

        # One hash per mesh, not one per figure: every mode of a case shares its GLB.
        glb_sha = {s: hashlib.sha256(a.mesh_glb_path.read_bytes()).hexdigest() for s, a in loaded.items()}
        modes_by_solver = {s: _snapshot_modes(a.key) for s, a in loaded.items()} if analysis == "eigen" else {}
        mode_indices = sorted({i for a in loaded.values() for i in a.poster_paths})
        for mode_idx in mode_indices:
            if analysis == "eigen":
                entries.append(MarkdownChunk(text=f"\n#### Mode {mode_idx + 1}\n"))
            entries.append(MarkdownChunk(text=_GRID_DIV_OPEN))
            for solver, assets in loaded.items():
                poster = assets.poster_paths.get(mode_idx)
                if poster is None:
                    continue
                entries.append(
                    self._render_one(
                        solver, assets, glb_sha[solver], mode_idx, poster, analysis=analysis, beam_solids=beam_solids
                    )
                )
            entries.append(MarkdownChunk(text=_GRID_DIV_CLOSE))
            shown = [s for s, a in loaded.items() if a.poster_paths.get(mode_idx) is not None]
            if analysis == "eigen":
                rows = [(_SOLVER_LABEL[s], modes_by_solver[s].get(mode_idx + 1)) for s in shown]
                table = mode_data_table(rows, closed_form_hz=_closed_form_frequency(case_prefix, config, mode_idx))
            else:
                rows = [
                    (
                        _SOLVER_LABEL[s],
                        {"mid": _snapshot(loaded[s].key).get("mid_span_u3"), **_peak_displacements(by_solver[s])},
                    )
                    for s in shown
                ]
                table = static_data_table(rows, closed_form_m=_closed_form_deflection(case_prefix, config))
            if table:
                entries.append(MarkdownChunk(text=f"\n{table}\n"))
        return entries

    @staticmethod
    def _render_one(
        solver: str,
        assets: FeaDocAssets,
        glb_sha: str,
        mode_idx: int,
        poster: pathlib.Path,
        *,
        analysis: str,
        beam_solids: bool = False,
    ):
        caption = _SOLVER_LABEL[solver]
        freqs = assets.frequencies or []
        if analysis == "eigen" and mode_idx < len(freqs) and freqs[mode_idx] is not None:
            caption += f" — mode {mode_idx + 1}, {freqs[mode_idx]:.3f} Hz"
        elif analysis == "static":
            caption += " — displacement"

        # Absolute paths: the preprocessor relpaths png_path against the markdown dir, and the static
        # export's glb resolver tries absolute paths first.
        return RenderResult(
            png_path=str(poster),
            glb_path=str(assets.mesh_glb_path),
            glb_sha256=glb_sha,
            glb_size=assets.mesh_glb_path.stat().st_size,
            caption=caption,
            camera_pos="iso_3",
            source_type="fea_artefact_bundle_mode_view",
            metadata={
                "fea_bundle_key": assets.key,
                "fea_mode_index": mode_idx,
                "image_path": str(poster),
                # The viewer's starting state for beam elements, from the document block.
                "fea_beam_solids": beam_solids,
            },
        )
