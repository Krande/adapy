"""paradoc.tasks build hooks for the FEA verification report.

The orchestrator (`paradoc build verification`) calls `setup(one,
runner)` between OneDoc construction and `one.compile()`, then
`postcompile(one)` afterwards. These functions absorb everything the
legacy `build_verification_report.py` driver used to do imperatively:

- collect FeaVerificationResults from the runner's `run_eig` cells
- bake the beam GLB (controlled by env var `ADAPY_VERIFICATION_REGEN_ASSETS`)
- bake per-case FEA artefact bundles (same flag)
- pick up committed bundles for cache-only builds
- regenerate the per-case markdown
- register comparison tables, modal tables, the freq-vs-mode plot,
  and ThreeDData rows on `one.db_manager`
- register the Versions filter (env-probed) + FeaCaseFilter per case
- export the static bundle to `adapy/docs/_static/fea-report`

Environment toggles:
- `ADAPY_VERIFICATION_REGEN_ASSETS=1` — re-bake GLBs from FEA result files
- `ADA_FEM_DO_NOT_SAVE_CACHE=1` — skip persisting modal JSON caches

Invocation:
- `paradoc build verification`            — cache replay, no regen
- `paradoc build verification --no-cache` — force the task DAG to re-execute
  (regen of GLBs is still env-controlled to keep solver-output dependencies
  explicit)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import sys
from typing import TYPE_CHECKING, Optional

# Mirror tasks.py's sys.path bootstrap — the orchestrator loads this
# module via spec_from_file_location and that doesn't add the parent
# dir to sys.path, so sibling imports (build_report_utils, filters)
# need help finding each other.
_THIS_DIR_STR = str(pathlib.Path(__file__).resolve().parent)
if _THIS_DIR_STR not in sys.path:
    sys.path.insert(0, _THIS_DIR_STR)

import build_report_utils as ru  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from paradoc.db import dataframe_to_table_data, plotly_figure_to_plot_data  # noqa: E402
from paradoc.db.models import ThreeDData  # noqa: E402

import ada  # noqa: E402
from ada.fem.results.docs import (  # noqa: E402
    FeaCaseFilter,
    FeaDocAssets,
    assets_for_docs,
    assets_from_bundle_dir,
    to_paradoc_rows,
)

from filters import Versions as VersionsFilter  # noqa: E402

if TYPE_CHECKING:
    from paradoc import OneDoc
    from paradoc.tasks import Runner


THIS_DIR = pathlib.Path(__file__).parent.resolve().absolute()
cache_dir = THIS_DIR / ".cache"
assets_dir = THIS_DIR / "_assets"
report_src_dir = THIS_DIR / "report"

os.makedirs(cache_dir, exist_ok=True)
os.makedirs(assets_dir, exist_ok=True)

load_dotenv()

logger = logging.getLogger(__name__)

_EIG_MODES = 11
_FILTER_NAME_RE = re.compile(r"[^0-9A-Za-z_]")

_SOLVER_DISPLAY_NAME = {
    "calculix": "Calculix",
    "code_aster": "Code Aster",
    "abaqus": "Abaqus",
    "sesam": "Sesam",
}
_SOLVER_VERSION_ATTR = {
    "calculix": "ccx",
    "code_aster": "ca",
    "abaqus": "aba",
    "sesam": "ses",
}


# ---------------- public hook entry points ----------------


def setup(one: "OneDoc", runner: "Runner") -> None:
    """Bake derived assets + register data on `one.db_manager`.

    Called by paradoc.tasks.build_document between OneDoc construction
    and `one.compile()`. Without this, the markdown resolver hits
    TableView/FigureView/ThreeDView refs with no backing rows in the
    database.
    """
    regen_assets = os.environ.get("ADAPY_VERIFICATION_REGEN_ASSETS", "0") == "1"

    bm = _beam_from_runner(runner)
    results = _collect_results(runner)
    ru.retrieve_cached_results(results, cache_dir)

    # Beam GLB + sibling PNG — the only non-FEA-bundle 3D asset in the
    # report. Standalone CAD geometry, separate from the bundle path.
    beam_glb = assets_dir / "beam.glb"
    if regen_assets or not beam_glb.exists():
        _bake_beam_glb(bm, beam_glb)

    # FEA bundles. `_bake_fea_assets` only fires on regen; cache-only
    # builds skip the bake and consume whatever's committed under
    # `_assets/<case>/` instead. The `assets_by_name` union below is
    # what drives both the per-case markdown generator and the
    # paradoc-row registration — single source of truth so a
    # `${ case.mode_N }` ref can't point at an un-registered row.
    fresh_assets: dict[str, FeaDocAssets] = {}
    if regen_assets:
        fresh_assets = _bake_fea_assets(results)
    cached_assets = _collect_bundle_assets(skip_keys=set(fresh_assets))
    assets_by_name: dict[str, FeaDocAssets] = {**fresh_assets}
    for a in cached_assets:
        assets_by_name.setdefault(a.key, a)

    _regenerate_results_detailed_md(results, assets_by_name)

    _register_eig_tables(one, results)
    _register_modal_tables(one, results)
    _register_eig_plot(one, results)
    _register_three_d_rows(one, beam_glb, assets_by_name)

    # Filters that aren't task-backed yet: Versions (env-probed) +
    # FeaCaseFilter (per-case asset wrapper, baked just above).
    # beam + eig come from filters.py module-level instances + the
    # orchestrator's TaskHandle binding.
    versions = _solver_versions(results)
    one._filter_registry.register(VersionsFilter(versions, name="versions"))
    for assets in assets_by_name.values():
        one._filter_registry.register(FeaCaseFilter.from_assets(assets))


def postcompile(one: "OneDoc") -> None:
    """Export the static bundle to adapy's docs landing page."""
    adapy_root = THIS_DIR.parent
    static_output_dir = adapy_root / "docs" / "_static" / "fea-report"
    logger.info(f"exporting static web bundle to {static_output_dir}")
    # `../../` walks `_static/fea-report/index.html` → `<docs_root>/index.html`,
    # which Sphinx serves as the adapy docs landing page.
    one.export_static(
        static_output_dir,
        header_links=[{"label": "← adapy docs", "href": "../../index.html"}],
    )


# ---------------- private helpers ----------------


def _safe_filter_name(name: str) -> str:
    """Drop file extension + replace non-identifier chars with `_`.

    paradoc Filter names must be valid Python identifiers, and adapy's
    per-solver FEAResult.name has historically leaked extensions
    (`.rmed`, `.frd`, ...). Falls back to a generic identifier if the
    result is empty or starts with a digit.
    """
    stem = pathlib.Path(name).stem if "." in name else name
    cleaned = _FILTER_NAME_RE.sub("_", stem)
    if not cleaned:
        return "case"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def _beam_from_runner(runner: "Runner") -> ada.Beam:
    """Pull the canonical beam off the runner's `design` task output."""
    design_assembly = runner.result_for(runner.cells_for("design")[0])
    return next(
        b for b in design_assembly.get_all_physical_objects() if isinstance(b, ada.Beam)
    )


def _collect_results(runner: "Runner") -> list[ru.FeaVerificationResult]:
    """Walk `run_eig` cells; postprocess each FEAResult into a
    FeaVerificationResult that the tables / plots / asset baking
    downstream consume. Cells that returned None (missing solver /
    failed run) are dropped silently — `tasks.py::run_eig` already
    logged those."""
    out: list[ru.FeaVerificationResult] = []
    for cell in runner.cells_for("run_eig"):
        result = runner.result_for(cell)
        if result is None:
            continue
        axes = cell.full_kwargs
        metadata = dict(
            geo=axes["geom_repr"],
            elo=axes["elem_order"],
            hexquad=axes["use_hex_quad"],
            reduced_integration=axes["reduced_integration"],
        )
        fvr = ru.postprocess_result(result, metadata)
        fvr.name = _safe_filter_name(fvr.name)
        out.append(fvr)
    logger.info(f"_collect_results: {len(out)} live result(s)")
    return out


def _bake_beam_glb(bm: ada.Beam, dest: pathlib.Path) -> bool:
    """Write the undeformed cantilever to `dest` as a GLB + sibling PNG.

    The PNG poster is rendered via `pygfx_offscreen_utils.glb_to_image`
    so it matches the convention the per-mode posters use. PNG failure
    is non-fatal; the frontend falls back to a placeholder when
    `<glb>.png` is missing.
    """
    try:
        asm = ada.Assembly("verification_beam")
        p = ada.Part("beam_only")
        p.add_beam(bm)
        asm.add_part(p)
        dest.parent.mkdir(parents=True, exist_ok=True)
        asm.to_gltf(dest)
        logger.info(f"wrote beam GLB → {dest}")
        try:
            from ada.visit.rendering.pygfx_offscreen_utils import glb_to_image

            glb_to_image(dest).save(str(dest.with_suffix(".png")))
        except Exception as exc:
            logger.warning(f"beam poster PNG failed: {exc}")
        return True
    except Exception as exc:
        logger.warning(f"beam GLB generation failed: {exc}")
        return False


def _bake_fea_assets(results: list) -> dict[str, FeaDocAssets]:
    """One FEA bundle per case via `ada.fem.results.docs.assets_for_docs`.

    Wipes each case dir first so a stale per-mode PNG from an earlier
    run doesn't outlive the new bundle. Cases without a live FEAResult
    are skipped silently (cache-only path picks the committed bundle
    up below).
    """
    out: dict[str, FeaDocAssets] = {}
    for r in results:
        res = getattr(r, "results", None)
        if res is None:
            logger.info(
                f"{r.name}: no FEAResult attached (cached-only) — "
                "skipping FEA artefact bake"
            )
            continue
        case_dir = assets_dir / r.name
        if case_dir.exists():
            shutil.rmtree(case_dir)
        try:
            out[r.name] = assets_for_docs(
                res, key=r.name, out_dir=case_dir, modes="all",
            )
            logger.info(
                f"{r.name}: baked FEA artefacts → {case_dir.name}/ "
                f"(n_modes={out[r.name].n_modes})"
            )
        except Exception as exc:
            logger.warning(
                f"{r.name}: FEA artefact bake failed: {exc}", exc_info=True
            )
    return out


def _collect_bundle_assets(
    skip_keys: "set[str] | None" = None,
) -> list[FeaDocAssets]:
    """Pick up every committed FEA bundle under `_assets/` via
    `assets_from_bundle_dir`. CI / cache-only path: when
    `_bake_fea_assets` didn't fire (no source FEAResult on disk),
    these are the bundles paradoc renders against."""
    skip_keys = skip_keys or set()
    out: list[FeaDocAssets] = []
    for manifest_path in sorted(assets_dir.rglob("fea.manifest.json")):
        case_dir = manifest_path.parent
        case = case_dir.name
        if case in skip_keys:
            continue
        try:
            out.append(assets_from_bundle_dir(case_dir, key=case))
        except Exception as exc:
            logger.warning(f"could not load bundle at {case_dir}: {exc}")
    return out


def _build_freq_vs_mode_plot(
    results: list,
    *,
    legend_position: str = "below",
) -> Optional[object]:
    """Plotly Figure: mode frequency vs mode-number per case.

    `legend_position="below"` (default) puts a horizontal legend under
    the plot — the right-side default ate ~25% of the canvas with the
    ~6+ cases the verification report ships.
    """
    try:
        import pandas as pd
        import plotly.express as px
    except ImportError as exc:
        logger.warning(f"plotly not available, skipping freq-vs-mode plot: {exc}")
        return None

    rows = []
    for r in results:
        for m in r.eig_data.modes:
            rows.append({
                "case": r.name,
                "solver": r.fem_format,
                "geom": r.metadata.get("geo"),
                "order": r.metadata.get("elo"),
                "mode": int(m.no),
                "f_hz": float(m.f_hz),
            })
    if not rows:
        return None
    df = pd.DataFrame(rows)
    fig = px.line(
        df, x="mode", y="f_hz", color="case",
        markers=True,
        labels={"mode": "Mode number", "f_hz": "Frequency [Hz]"},
        title="Eigenfrequency vs. mode number, per FEA case",
    )
    if legend_position == "below":
        fig.update_layout(
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.2,
                xanchor="center",
                x=0.5,
                title_text="",
            ),
        )
    elif legend_position != "right":
        logger.warning(
            f"unknown legend_position={legend_position!r}; using plotly default (right)"
        )
    return fig


def _solver_versions(results) -> dict[str, str]:
    """Solver versions: env probes with cache fallback. Cached at
    `cache_dir/software_versions.json` for offline replay."""
    version_cache = cache_dir / "software_versions.json"
    cached: dict = {}
    if version_cache.exists():
        try:
            cached = json.loads(version_cache.read_text())
        except Exception:
            cached = {}

    versions = dict(
        calculix="2.21",            # hardcoded today; replace with probe if needed
        code_aster="17.1.0",
        abaqus=cached.get("abaqus", "2021"),
        sesam=cached.get("sesam", "10"),
    )
    if ru.ABAQUS_EXE is not None:
        try:
            versions["abaqus"] = ru.get_abaqus_version()
        except Exception as exc:
            logger.warning(f"abaqus version probe failed: {exc}")
    if ru.SESTRA_EXE is not None:
        try:
            versions["sesam"] = ru.get_sesam_version()
        except Exception as exc:
            logger.warning(f"sesam version probe failed: {exc}")

    version_cache.write_text(json.dumps(versions, indent=4))
    return versions


def _regenerate_results_detailed_md(
    results: list, assets_by_name: dict[str, FeaDocAssets]
) -> None:
    """Overwrite `01-app/00-results-detailed.md` from the live results.

    Each case section emits one `${ <case>.mode_<N> }` per mode that
    has a baked poster — `assets_by_name` is the union of fresh-baked
    and committed bundles, so the generated markdown never references
    a mode that doesn't have a paradoc row backing it.
    """
    target = report_src_dir / "01-app" / "00-results-detailed.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    by_solver: dict[str, list] = {}
    for r in results:
        by_solver.setdefault(r.fem_format, []).append(r)

    lines: list[str] = ["# Eigenvalue analysis detailed results", ""]
    for solver in sorted(by_solver):
        rs = sorted(by_solver[solver], key=lambda r: r.name)
        display = _SOLVER_DISPLAY_NAME.get(solver, solver)
        ver_attr = _SOLVER_VERSION_ATTR.get(solver)
        lines.append(f"## {display}")
        if ver_attr:
            lines.append(
                f"Using {display} v${{ versions.{ver_attr} }} the following results were obtained."
            )
        lines.append("")
        for r in rs:
            lines.append(f"### {r.name}")
            lines.append("")
            assets = assets_by_name.get(r.name)
            if assets is None or not assets.poster_paths:
                lines.append(
                    "_Mode-shape figures unavailable for this case "
                    "(solver result file not bundled into CI)._"
                )
                lines.append("")
                continue
            for mode_idx in sorted(assets.poster_paths.keys()):
                mode_n = mode_idx + 1
                lines.append(f"#### Mode {mode_n}")
                lines.append("")
                lines.append(f"${{ {r.name}.mode_{mode_n} }}")
                lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"regenerated {target.name} with {len(results)} case sections")


def _beam_geom_row(beam_glb: pathlib.Path) -> ThreeDData:
    """ThreeDData row for the standalone undeformed beam GLB."""
    sha = hashlib.sha256(beam_glb.read_bytes()).hexdigest()
    metadata: dict = {}
    png_path = beam_glb.with_suffix(".png")
    if png_path.is_file():
        metadata["image_path"] = str(png_path.relative_to(THIS_DIR))
    return ThreeDData(
        key="beam_geom",
        glb_path=str(beam_glb.relative_to(THIS_DIR)),
        format="glb",
        camera_pos="iso_3",
        caption="Cantilever beam geometry.",
        sha256=sha,
        size=beam_glb.stat().st_size,
        source_type="cad_model_file",
        metadata=metadata,
    )


_COMPARISON_SPECS = [
    ("eig_compare_solid_o1", "solid", 1, None, "Eigenfrequency comparison (Hz) — solid, 1st order."),
    ("eig_compare_solid_o2", "solid", 2, None, "Eigenfrequency comparison (Hz) — solid, 2nd order."),
    ("eig_compare_shell_o1", "shell", 1, True, "Eigenfrequency comparison (Hz) — shell, 1st order."),
    ("eig_compare_shell_o2", "shell", 2, True, "Eigenfrequency comparison (Hz) — shell, 2nd order."),
    ("eig_compare_line_o1",  "line",  1, False, "Eigenfrequency comparison (Hz) — line, 1st order."),
    ("eig_compare_line_o2",  "line",  2, False, "Eigenfrequency comparison (Hz) — line, 2nd order."),
]


def _register_eig_tables(one, results) -> None:
    for key, geo, order, hq, caption in _COMPARISON_SPECS:
        df = ru.create_df_of_data(results, geo, order, hq)
        if df is None or df.empty:
            logger.info(f"no rows for {key}, skipping table")
            continue
        one.db_manager.add_table(
            dataframe_to_table_data(
                key=key, df=df, caption=caption, show_index=False, default_sort=("Mode", True),
            )
        )


def _register_modal_tables(one, results) -> None:
    save_cache = os.environ.get("ADA_FEM_DO_NOT_SAVE_CACHE") is None
    for r in results:
        if save_cache:
            r.save_results_to_json(cache_dir / r.name)
        df = ru.eig_data_to_df(r.eig_data, ["Mode", "Eigenvalue (real)"])
        one.db_manager.add_table(
            dataframe_to_table_data(
                key=r.name, df=df, caption=r.name, show_index=False, default_sort=("Mode", True),
            )
        )


def _register_eig_plot(one, results) -> None:
    fig = _build_freq_vs_mode_plot(results)
    if fig is None:
        return
    one.db_manager.add_plot(
        plotly_figure_to_plot_data(
            key="eig_freq_vs_mode",
            fig=fig,
            caption="Eigenfrequency vs. mode number across FEA cases.",
            width=900, height=500,
        )
    )


def _register_three_d_rows(one, beam_glb: pathlib.Path, assets_by_name) -> None:
    if beam_glb.is_file():
        one.db_manager.add_three_d(_beam_geom_row(beam_glb))
    for assets in assets_by_name.values():
        for row in to_paradoc_rows(
            assets,
            base_dir=THIS_DIR,
            caption=f"{assets.solver or assets.key} — {assets.key} FEA results.",
        ):
            one.db_manager.add_three_d(row)
