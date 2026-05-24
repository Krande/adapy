"""Build the FEA verification report as a paradoc static web bundle.

Drives the new paradoc Filter / DbManager paradigm: tables and the per-
mode plot live in `data.db`, GLBs land under `_assets/`, filter instances
holding live `ada.Beam` / `FeaVerificationResult` references resolve
markdown `${ filter.attr }` references at compile time.

CLI: `python build_verification_report.py <overwrite> <execute> [--regen-assets]`

- `overwrite`: re-run FEA cases even if cached
- `execute`: actually invoke solvers (False → use cached eigenvalue JSONs)
- `--regen-assets`: regenerate `_assets/*.glb`. Requires FEA result files
  on disk (the `.frd` / `.rmed` / `.odb` produced by an `execute=True` run).
  In CI/docs builds we leave this off and consume whatever GLBs are
  committed under `_assets/`.

DOCX export and the WebSocket live-update mode were removed; only the
static-web export survives.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib
import re
from typing import Optional

import build_report_utils as ru
from dotenv import load_dotenv
from paradoc import OneDoc
from paradoc.db import dataframe_to_table_data, plotly_figure_to_plot_data
from paradoc.db.models import ThreeDData

import ada
from ada.config import logger
from ada.fem.cases import eigen_test
from ada.fem.formats.abaqus.config import AbaqusSetup
from ada.fem.results.docs import (
    FeaCaseFilter,
    FeaDocAssets,
    assets_for_docs,
    assets_from_bundle_dir,
    to_paradoc_rows,
)
from ada.materials.metals import CarbonSteel, DnvGl16Mat

# Filter classes live in a sibling module; we instantiate at runtime and
# register manually so each instance can carry references to the live
# domain objects (no module-level state in filters.py). Per-case
# filters now come from adapy (`ada.fem.results.docs.FeaCaseFilter`);
# the SolverCase / DISP_FIELD / WARP_SCALE / per-mode poster helpers
# that used to live here moved into `ada.fem.results.{artefacts,docs}`
# in steps 1-2 of the FEA-docs generalisation.
from filters import Beam as BeamFilter
from filters import Eig as EigFilter
from filters import Versions as VersionsFilter

THIS_DIR = pathlib.Path(__file__).parent.resolve().absolute()
cache_dir = THIS_DIR / ".cache"
assets_dir = THIS_DIR / "_assets"
report_src_dir = THIS_DIR / "report"

os.makedirs(cache_dir, exist_ok=True)
os.makedirs(assets_dir, exist_ok=True)


def beam() -> ada.Beam:
    return ada.Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        ada.Material("S420", CarbonSteel("S420", plasticity_model=DnvGl16Mat(15e-3, "S355"))),
    )


load_dotenv()


_FILTER_NAME_RE = re.compile(r"[^0-9A-Za-z_]")


def _safe_filter_name(name: str) -> str:
    """Return a paradoc-friendly Filter name derived from *name*.

    Drops any file extension and replaces non-identifier chars with `_`.
    Falls back to a generic identifier if the result is empty or starts
    with a digit.
    """
    stem = pathlib.Path(name).stem if "." in name else name
    cleaned = _FILTER_NAME_RE.sub("_", stem)
    if not cleaned:
        return "case"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def simulate(
    bm, el_order, geom_repr, analysis_software, use_hex_quad, use_reduced_int, eig_modes, overwrite, execute
) -> list[ru.FeaVerificationResult]:
    """Run FEA cases. Returns whatever produced results (possibly empty).

    Every per-case failure is swallowed so the build keeps going. With
    ``--overwrite`` the live solver invocations under
    ``eigen_test → a.to_fem(execute=True)`` can fail in many ways
    (gmsh meshing, solver subprocess crash, post-processor unable to
    parse partial output). We log and continue rather than letting one
    bad case kill the docs build for everyone else.
    """
    results = []
    for elo in el_order:
        for geo in geom_repr:
            for soft in analysis_software:
                for hexquad in use_hex_quad:
                    for uri in use_reduced_int:
                        case_label = f"{soft}/{geo}/order={elo}/hq={hexquad}/ri={uri}"
                        logger.info(f"==> {case_label}: starting")
                        try:
                            result = eigen_test(
                                bm,
                                soft,
                                geo,
                                elo,
                                hexquad,
                                reduced_integration=uri,
                                short_name_map=ru.short_name_map,
                                overwrite=overwrite,
                                execute=execute,
                                eigen_modes=eig_modes,
                            )
                            if result is None:
                                logger.info(f"<== {case_label}: eigen_test returned None (likely a skip rule)")
                                continue
                            metadata = dict(
                                geo=geo, elo=elo, hexquad=hexquad, reduced_integration=uri
                            )
                            fvr = ru.postprocess_result(result, metadata)
                            # Defensive: paradoc Filter names must be valid
                            # Python identifiers, and adapy's per-solver
                            # FEAResult.name has historically leaked file
                            # extensions (`.rmed`, `.frd`, ...). Strip any
                            # extension + replace non-identifier chars so
                            # downstream filter / table keys can't trip the
                            # `Filter name '…' must be a valid Python
                            # identifier` validator.
                            fvr.name = _safe_filter_name(fvr.name)
                            logger.info(f"<== {case_label}: OK ({fvr.name})")
                        except FileNotFoundError as e:
                            logger.warning(f"{case_label}: {e}", exc_info=True)
                            continue
                        except Exception as e:
                            logger.warning(f"{case_label} failed: {type(e).__name__}: {e}", exc_info=True)
                            continue
                        results.append(fvr)
    logger.info(f"simulate(): produced {len(results)} live result(s)")
    return results


def _bake_beam_glb(bm: ada.Beam, dest: pathlib.Path) -> bool:
    """Write the undeformed cantilever to `dest` as a GLB + sibling PNG.

    The PNG poster is the same `pygfx_offscreen_utils.glb_to_image`
    pass the FEA bake uses for its per-mode posters — keeps the
    rendering convention single-sourced. Failures on the PNG side are
    non-fatal so the build still ships the GLB; the frontend falls
    back to a placeholder when no poster sits at ``<glb>.png``.
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


# `_bake_glb_poster_png`, `_resolve_disp_field_per_mode`,
# `_bake_per_mode_posters_from_bundle`, `_bake_fea_bundles`,
# `_count_displacement_steps`, `_three_d_row`, `_fea_bundle_row`,
# `_fea_bundle_mode_view_row`, the legacy `_bake_mode_glbs` alias —
# all moved into ``ada.fem.results.{artefacts,docs}`` in steps 1-2 of
# the FEA-docs generalisation. The thin per-case bake driver below
# uses :func:`assets_for_docs` (bake the bundle + per-mode posters
# in one call), :func:`to_paradoc_rows` (canonical + mode-view
# ThreeDData rows), and :func:`assets_from_bundle_dir` (cache-only
# CI builds against committed bundles).


def _bake_fea_assets(
    results,
) -> dict[str, FeaDocAssets]:
    """Bake one FEA artefact bundle per case via the shared
    :func:`ada.fem.results.docs.assets_for_docs` path.

    Wipes each case dir before baking so a stale per-mode PNG from
    an earlier run doesn't outlive the new bundle. Cached-only cases
    (no live ``FEAResult``) get skipped silently — the cache-only
    path in :func:`_collect_bundle_assets` picks the committed bundle
    up instead.

    Returns ``{case_name: FeaDocAssets}`` for the cases that baked.
    """
    import shutil

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
                f"{r.name}: FEA artefact bake failed: {exc}",
                exc_info=True,
            )
    return out


def _collect_bundle_assets(
    skip_keys: "set[str] | None" = None,
) -> list[FeaDocAssets]:
    """Pick up every committed FEA bundle under ``_assets/`` and build
    a :class:`FeaDocAssets` per bundle dir via
    :func:`assets_from_bundle_dir`.

    Used in CI / cache-only runs where ``_bake_fea_assets`` didn't
    fire (no source FEAResult on disk). ``skip_keys`` filters out
    bundles already baked in this run — the typical pattern is
    ``skip_keys = set(fresh.keys())`` to avoid double-registering a
    case that's both fresh-baked AND committed.
    """
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
            logger.warning(
                f"could not load bundle at {case_dir}: {exc}"
            )
    return out


def _build_freq_vs_mode_plot(
    results: list[ru.FeaVerificationResult],
    *,
    legend_position: str = "below",
) -> Optional[object]:
    """Return a plotly Figure of mode frequency vs mode-number per case.

    ``legend_position`` controls where the per-case legend lands:

    * ``"below"`` (default) — horizontal legend under the plot area.
      With ~6+ FEA cases the right-side legend ate ~25 % of the canvas
      width on the rendered doc page; the below layout reclaims that
      space for the actual curves at the cost of one extra row of
      vertical real estate.
    * ``"right"`` — plotly's stock right-side vertical legend. Useful
      when there are too many cases to fit horizontally without
      wrapping into multiple rows that shove the x-axis off-screen.
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
        # ``y=-0.2`` clears the x-axis title; ``yanchor=top`` pins the
        # legend's top edge to that y so it grows downward, not into
        # the plot. ``xanchor=center, x=0.5`` centres it under the plot.
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
    """Resolve solver versions from env probes with cache fallback."""
    version_cache = cache_dir / "software_versions.json"
    cached = {}
    if version_cache.exists():
        try:
            cached = json.loads(version_cache.read_text())
        except Exception:
            cached = {}

    versions = dict(
        calculix="2.21",            # Hardcoded today; replace with probe if needed
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


def _regenerate_results_detailed_md(
    results: list[ru.FeaVerificationResult],
    assets_by_name: dict[str, FeaDocAssets],
) -> None:
    """Overwrite ``01-app/00-results-detailed.md`` from the live results.

    Each case section emits one ``${ <case>.mode_<N> }`` per mode that
    has a baked poster. ``assets_by_name`` is the union of fresh-baked
    cases and committed cache-only bundles — every entry's
    ``poster_paths`` lists the baked modes, so the generated markdown
    never references a mode that doesn't have a paradoc row backing it.
    """
    target = report_src_dir / "01-app" / "00-results-detailed.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    by_solver: dict[str, list[ru.FeaVerificationResult]] = {}
    for r in results:
        by_solver.setdefault(r.fem_format, []).append(r)

    lines: list[str] = ["# Eigenvalue analysis detailed results", ""]
    for solver in sorted(by_solver):
        rs = sorted(by_solver[solver], key=lambda r: r.name)
        display = _SOLVER_DISPLAY_NAME.get(solver, solver)
        ver_attr = _SOLVER_VERSION_ATTR.get(solver)
        lines.append(f"## {display}")
        if ver_attr:
            lines.append(f"Using {display} v${{ versions.{ver_attr} }} the following results were obtained.")
        lines.append("")
        for r in rs:
            lines.append(f"### {r.name}")
            lines.append("")
            # Modal tables previously lived here, but the comparison
            # tables earlier in the report already enumerate every
            # mode's frequency — repeating the numbers per case in
            # Appendix A was redundant. Per-mode figures of the
            # deformed shape are more informative; emit one per mode
            # the bundle has a baked poster for, else a short
            # "not available" note (e.g. for cached-only Abaqus / Sesam
            # runs where the source result file wasn't bundled into CI).
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
    """ThreeDData for the standalone un-deformed cantilever — the only
    non-FEA-bundle 3D asset the report ships. Sibling ``beam.png``
    poster is registered via the ``image_path`` metadata hint so
    paradoc's static export copies it alongside.
    """
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


def build_fea_report(bm: ada.Beam, results: list[ru.FeaVerificationResult], eig_modes: int, regen_assets: bool) -> OneDoc:
    """Hydrate `OneDoc` with filters + DB-backed tables/plots/3D assets."""
    # Beam GLB + sibling PNG — the only non-FEA-bundle 3D asset in the
    # report. Standalone CAD geometry, separate from the bundle path.
    beam_glb = assets_dir / "beam.glb"
    if regen_assets or not beam_glb.exists():
        _bake_beam_glb(bm, beam_glb)

    # FEA bundles. ``_bake_fea_assets`` only fires on regen; cached-only
    # builds skip the bake and consume whatever's committed under
    # ``_assets/<case>/`` instead. The ``assets_by_name`` union below
    # is what drives both the per-case markdown generator and the
    # paradoc row registration — single source of truth, no risk of a
    # ``${ case.mode_N }`` ref pointing at an un-registered row.
    fresh_assets: dict[str, FeaDocAssets] = {}
    if regen_assets:
        fresh_assets = _bake_fea_assets(results)
    cached_assets = _collect_bundle_assets(skip_keys=set(fresh_assets))
    assets_by_name: dict[str, FeaDocAssets] = {**fresh_assets}
    for a in cached_assets:
        assets_by_name.setdefault(a.key, a)

    _regenerate_results_detailed_md(results, assets_by_name)
    one = OneDoc(source_dir=report_src_dir)

    # ------------------------------------------------------------------
    # Tables: per-(geom, order) eigenfrequency comparisons.
    # The data is built by the existing `create_df_of_data` helper.
    # ------------------------------------------------------------------
    comparison_specs = [
        ("eig_compare_solid_o1", "solid", 1, None, "Eigenfrequency comparison (Hz) — solid, 1st order."),
        ("eig_compare_solid_o2", "solid", 2, None, "Eigenfrequency comparison (Hz) — solid, 2nd order."),
        ("eig_compare_shell_o1", "shell", 1, True, "Eigenfrequency comparison (Hz) — shell, 1st order."),
        ("eig_compare_shell_o2", "shell", 2, True, "Eigenfrequency comparison (Hz) — shell, 2nd order."),
        ("eig_compare_line_o1",  "line",  1, False, "Eigenfrequency comparison (Hz) — line, 1st order."),
        ("eig_compare_line_o2",  "line",  2, False, "Eigenfrequency comparison (Hz) — line, 2nd order."),
    ]
    for key, geo, order, hq, caption in comparison_specs:
        df = ru.create_df_of_data(results, geo, order, hq)
        if df is None or df.empty:
            logger.info(f"no rows for {key}, skipping table")
            continue
        one.db_manager.add_table(dataframe_to_table_data(
            key=key, df=df, caption=caption, show_index=False, default_sort=("Mode", True),
        ))

    # ------------------------------------------------------------------
    # Per-case modal tables — one per FeaVerificationResult.
    # ------------------------------------------------------------------
    for r in results:
        if os.environ.get("ADA_FEM_DO_NOT_SAVE_CACHE") is None and cache_dir is not None:
            r.save_results_to_json(cache_dir / r.name)
        df = ru.eig_data_to_df(r.eig_data, ["Mode", "Eigenvalue (real)"])
        one.db_manager.add_table(dataframe_to_table_data(
            key=r.name, df=df, caption=r.name, show_index=False, default_sort=("Mode", True),
        ))

    # ------------------------------------------------------------------
    # Plot: frequency vs mode number, per case.
    # ------------------------------------------------------------------
    fig = _build_freq_vs_mode_plot(results)
    if fig is not None:
        one.db_manager.add_plot(plotly_figure_to_plot_data(
            key="eig_freq_vs_mode",
            fig=fig,
            caption="Eigenfrequency vs. mode number across FEA cases.",
            width=900, height=500,
        ))

    # ------------------------------------------------------------------
    # 3D assets: register the beam GLB + every FEA bundle's canonical
    # row + per-mode rows. `to_paradoc_rows` produces the right
    # ThreeDData shapes (`fea_artefact_bundle` for canonical,
    # `fea_artefact_bundle_mode_view` per mode) so paradoc's static
    # export copies the manifest + blobs alongside the figures.
    # ------------------------------------------------------------------
    if beam_glb.is_file():
        one.db_manager.add_three_d(_beam_geom_row(beam_glb))
    for assets in assets_by_name.values():
        for row in to_paradoc_rows(
            assets,
            base_dir=THIS_DIR,
            caption=f"{assets.solver or assets.key} — {assets.key} FEA results.",
        ):
            one.db_manager.add_three_d(row)

    # ------------------------------------------------------------------
    # Filters — instantiated with the live domain objects, registered
    # manually (bypassing discover_filters because state is runtime-only).
    # Per-case FeaCaseFilter comes from adapy now: built from the
    # already-baked FeaDocAssets so no re-bake fires on first attr
    # access in the markdown resolver.
    # ------------------------------------------------------------------
    versions = _solver_versions(results)
    registry = one._filter_registry
    registry.register(BeamFilter(bm, name="beam"))
    registry.register(VersionsFilter(versions, name="versions"))
    registry.register(EigFilter(results, num_modes=eig_modes, name="eig"))
    for assets in assets_by_name.values():
        registry.register(FeaCaseFilter.from_assets(assets))

    return one


def create_fea_report(overwrite: bool = False, execute: bool = False, regen_assets: bool = False) -> None:
    if ru.ODB_DUMP_EXE is not None:
        AbaqusSetup.set_default_post_processor(ru.post_processing_abaqus)

    software = ru.get_available_software()

    el_order = [1, 2]
    geom_repr = ["line", "shell", "solid"]
    eig_modes = 11
    uhq = [False, True]
    uri = [False, True]

    bm = beam()
    results = simulate(bm, el_order, geom_repr, software, uhq, uri, eig_modes, overwrite, execute)
    ru.retrieve_cached_results(results, cache_dir)

    one = build_fea_report(bm, results, eig_modes, regen_assets=regen_assets)

    adapy_root = pathlib.Path(__file__).parent.parent.parent.parent.resolve().absolute()
    static_output_dir = adapy_root / "docs" / "_static" / "fea-report"
    logger.info(f"exporting static web bundle to {static_output_dir}")
    # `../../` walks `_static/fea-report/index.html` → `<docs_root>/index.html`,
    # which Sphinx serves as the adapy docs landing page.
    one.export_static(
        static_output_dir,
        header_links=[{"label": "← adapy docs", "href": "../../index.html"}],
    )


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    import typer

    typer.run(create_fea_report)
