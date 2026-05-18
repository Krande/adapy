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

import json
import logging
import os
import pathlib
from typing import Iterable, Optional

import build_report_utils as ru
from dotenv import load_dotenv
from paradoc import OneDoc
from paradoc.db import dataframe_to_table_data, plotly_figure_to_plot_data
from paradoc.db.models import ThreeDData

import ada
from ada.config import logger
from ada.fem.cases import eigen_test
from ada.fem.formats.abaqus.config import AbaqusSetup
from ada.materials.metals import CarbonSteel, DnvGl16Mat

# Filter classes live in a sibling module; we instantiate at runtime and
# register manually so each instance can carry references to the live
# domain objects (no module-level state in filters.py).
from filters import Beam as BeamFilter
from filters import Eig as EigFilter
from filters import SolverCase as SolverCaseFilter
from filters import Versions as VersionsFilter


# Displacement field name per solver, used by FEAResult.to_gltf when
# warping the mesh into a mode shape. Keys match `FeaVerificationResult.fem_format`.
DISP_FIELD = {
    "calculix": "DISP",
    "code_aster": "result__DEPL",
    "abaqus": "U",
    "sesam": "DISP",
}

# Per-mode warp amplitude. A constant works fine here because the beam's
# bbox is fixed and the eigenvectors come in already mass-normalized; if
# the shapes look too small/large later, compute per-mode auto-scale
# (max(|disp|) → 5–10% of bbox diagonal).
WARP_SCALE = 10.0

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
                                continue
                            metadata = dict(
                                geo=geo, elo=elo, hexquad=hexquad, reduced_integration=uri
                            )
                            fvr = ru.postprocess_result(result, metadata)
                        except FileNotFoundError as e:
                            logger.warning(f"{case_label}: {e}")
                            continue
                        except Exception as e:
                            logger.warning(f"{case_label} failed: {e}")
                            continue
                        results.append(fvr)
    return results


def _bake_beam_glb(bm: ada.Beam, dest: pathlib.Path) -> bool:
    """Write the undeformed cantilever to `dest` as a GLB.

    Wraps the beam in a throwaway Assembly so the standard adapy
    Part.to_gltf path applies. Returns True on success.
    """
    try:
        asm = ada.Assembly("verification_beam")
        p = ada.Part("beam_only")
        p.add_beam(bm)
        asm.add_part(p)
        dest.parent.mkdir(parents=True, exist_ok=True)
        asm.to_gltf(dest)
        logger.info(f"wrote beam GLB → {dest}")
        return True
    except Exception as exc:
        logger.warning(f"beam GLB generation failed: {exc}")
        return False


def _bake_mode_glbs(results: Iterable[ru.FeaVerificationResult], num_modes: int) -> list[ThreeDData]:
    """Generate per-(case, mode) deformed-mesh GLBs from FEAResult objects.

    Skipped if the result was loaded from JSON cache (no FEAResult to
    warp). Returns the ThreeDData rows to register with the bundle so
    the frontend can serve them via `data-3d-key`.
    """
    rows: list[ThreeDData] = []
    for r in results:
        res = getattr(r, "results", None)
        if res is None:
            logger.info(f"{r.name}: no FEAResult attached (cached-only), skipping mode GLBs")
            continue
        field = DISP_FIELD.get(r.fem_format)
        if field is None:
            logger.warning(f"{r.name}: no displacement field for solver {r.fem_format!r}, skipping mode GLBs")
            continue
        case_dir = assets_dir / r.name
        case_dir.mkdir(parents=True, exist_ok=True)
        for mode_idx in range(1, num_modes + 1):
            glb_path = case_dir / f"mode_{mode_idx:02d}.glb"
            try:
                res.to_gltf(
                    str(glb_path), mode_idx, field,
                    warp_field=field, warp_step=mode_idx, warp_scale=WARP_SCALE,
                )
            except Exception as exc:
                logger.warning(f"{r.name}: mode {mode_idx} GLB failed: {exc}")
                continue
            rows.append(_three_d_row(
                key=f"{r.name}_mode_{mode_idx:02d}",
                glb_path=glb_path,
                caption=f"{r.fem_format} — {r.name} mode {mode_idx}",
            ))
    return rows


def _three_d_row(key: str, glb_path: pathlib.Path, caption: str) -> ThreeDData:
    import hashlib

    sha = hashlib.sha256(glb_path.read_bytes()).hexdigest()
    return ThreeDData(
        key=key,
        glb_path=str(glb_path.relative_to(THIS_DIR)),
        format="glb",
        camera_pos="iso_3",
        caption=caption,
        sha256=sha,
        size=glb_path.stat().st_size,
        source_type="fea_mode_shape",
    )


def _collect_assets() -> list[ThreeDData]:
    """Register every GLB already on disk under `_assets/`.

    Used in CI where we don't regenerate; whatever's committed is what
    gets served.
    """
    rows: list[ThreeDData] = []
    for glb in sorted(assets_dir.rglob("*.glb")):
        rel = glb.relative_to(assets_dir)
        # Beam geom uses fixed key `beam_geom`; case modes derive from path
        # `_assets/<case>/mode_NN.glb` → key `<case>_mode_NN`.
        if rel.parts == ("beam.glb",):
            key, caption = "beam_geom", "Cantilever beam geometry."
        else:
            case = rel.parts[0]
            stem = rel.stem  # mode_01
            key = f"{case}_{stem}"
            caption = f"{case} — {stem.replace('_', ' ')}"
        rows.append(_three_d_row(key=key, glb_path=glb, caption=caption))
    return rows


def _build_freq_vs_mode_plot(results: list[ru.FeaVerificationResult]) -> Optional[object]:
    """Return a plotly Figure of mode frequency vs mode-number per case."""
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


def _regenerate_results_detailed_md(results: list[ru.FeaVerificationResult]) -> None:
    """Overwrite ``01-app/00-results-detailed.md`` from the live results.

    Each ``${ <case>.modal_table }`` / ``${ <case>.mode_3d }`` reference uses
    the exact ``r.name`` produced by ``simulate()`` so paradoc's filter
    registry can always resolve it. Hand-edited filter names drift the
    moment a case parameter (hexquad / reduced-integration / element order)
    changes — generating the file removes that whole class of bug.

    Only emits a ``mode_3d`` line when the matching GLB has been baked under
    ``_assets/<case>/mode_01.glb`` — otherwise the bundle would carry a
    ``MISSING_3D_IMAGE.png`` placeholder for every case.
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
            lines.append(f"${{ {r.name}.modal_table }}{{tbl:sortby:Mode:asc;index:no}}")
            lines.append("")
            mode_glb = assets_dir / r.name / "mode_01.glb"
            if mode_glb.exists():
                lines.append(f"${{ {r.name}.mode_3d }}")
                lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"regenerated {target.name} with {len(results)} case sections")


def build_fea_report(bm: ada.Beam, results: list[ru.FeaVerificationResult], eig_modes: int, regen_assets: bool) -> OneDoc:
    """Hydrate `OneDoc` with filters + DB-backed tables/plots/3D assets."""
    _regenerate_results_detailed_md(results)
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
    # 3D assets: beam geometry + per-(case, mode) mode shapes.
    # ------------------------------------------------------------------
    beam_glb = assets_dir / "beam.glb"
    if regen_assets or not beam_glb.exists():
        _bake_beam_glb(bm, beam_glb)
    if regen_assets:
        _bake_mode_glbs(results, eig_modes)
    for row in _collect_assets():
        one.db_manager.add_three_d(row)

    # ------------------------------------------------------------------
    # Filters — instantiated with the live domain objects, registered
    # manually (bypassing discover_filters because state is runtime-only).
    # ------------------------------------------------------------------
    versions = _solver_versions(results)
    registry = one._filter_registry
    registry.register(BeamFilter(bm, name="beam"))
    registry.register(VersionsFilter(versions, name="versions"))
    registry.register(EigFilter(results, num_modes=eig_modes, name="eig"))
    for r in results:
        registry.register(SolverCaseFilter(r, name=r.name))

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
