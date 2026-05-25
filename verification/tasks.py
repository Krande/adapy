"""paradoc.tasks declarations for `paradoc build verification`.

Complete declarative pipeline for the FEA verification report:

  design → mesh → run_eig                          (compute)
  postprocess (consumes=run_eig)                   (aggregator: one cell)
  beam_glb (parent=design)                         (CAD asset)
  fea_outputs (parent=postprocess)                 (bundles + per-case 3D + md regen)
  eig_tables / modal_tables / freq_plot
      (each parent=postprocess)                    (data outcomes)
  versions_filter (parent=postprocess)             (env-probed filter)

Each task that produces a table / plot / 3D row / filter returns a
typed Outcome (or a list of them); paradoc.tasks auto-registers those
on OneDoc before compile. The static bundle export is declared in
paradoc.toml as `outputs = ["static"]` — no imperative postcompile
hook needed.

Layout matches paradoc's Q6 convention:
    verification/
      paradoc.toml      # build profiles, fanout overrides, static target
      tasks.py          # this file
      filters.py        # Filter classes
      report/*.md       # the document body
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import pathlib
import re
import shutil
import sys

# `tasks.py` gets loaded via paradoc.tasks.discovery's
# spec_from_file_location, which does NOT add the parent dir to
# sys.path. Bootstrap THIS_DIR so sibling modules (utils, filters)
# import cleanly.
_THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from paradoc.tasks import (  # noqa: E402
    BuildContext,
    FilterOutcome,
    PlotOutcome,
    TableOutcome,
    ThreeDOutcome,
    task,
)

import ada  # noqa: E402
from ada.api.fem_tasks import (  # noqa: E402
    design_cantilever,
    eig_case_name,
    is_eig_skip,
    mesh_cantilever,
    run_eig as run_eig_helper,
)
from ada.fem.exceptions.fea_software import FEASolverNotInstalled  # noqa: E402
from ada.fem.results.docs import (  # noqa: E402
    FeaCaseFilter,
    FeaDocAssets,
    assets_for_docs,
    assets_from_bundle_dir,
    to_paradoc_rows,
)

from paradoc.db.models import ThreeDData  # noqa: E402

import utils as ru  # noqa: E402 — verification's private helpers

logger = logging.getLogger(__name__)


_SCRATCH_DIR = _THIS_DIR / "temp" / "eigen"
_CACHE_DIR = _THIS_DIR / ".cache"
_ASSETS_DIR = _THIS_DIR / "_assets"
_REPORT_SRC_DIR = _THIS_DIR / "report"

_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# Optional dotenv hook — keeps parity with the legacy build_hooks setup
# without making python-dotenv a hard import requirement.
try:
    from dotenv import load_dotenv as _load_dotenv  # noqa: E402
    _load_dotenv()
except ImportError:
    pass

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

_COMPARISON_SPECS = [
    ("eig_compare_solid_o1", "solid", 1, None, "Eigenfrequency comparison (Hz) — solid, 1st order."),
    ("eig_compare_solid_o2", "solid", 2, None, "Eigenfrequency comparison (Hz) — solid, 2nd order."),
    ("eig_compare_shell_o1", "shell", 1, True, "Eigenfrequency comparison (Hz) — shell, 1st order."),
    ("eig_compare_shell_o2", "shell", 2, True, "Eigenfrequency comparison (Hz) — shell, 2nd order."),
    ("eig_compare_line_o1",  "line",  1, False, "Eigenfrequency comparison (Hz) — line, 1st order."),
    ("eig_compare_line_o2",  "line",  2, False, "Eigenfrequency comparison (Hz) — line, 2nd order."),
]


# Wire the Abaqus odb post-processor at module-import time so any
# subsequent run_eig cell that hits the Abaqus solver picks it up.
# The legacy driver did this in create_fea_report(); putting it here
# keeps the side effect alongside the @task declarations.
try:
    from ada.fem.formats.abaqus.config import AbaqusSetup as _AbaqusSetup
    from ada.fem.formats.abaqus.post_processing import (
        get_odb_dump_exe as _get_odb_dump_exe,
        post_processing_abaqus as _post_processing_abaqus,
    )

    if _get_odb_dump_exe() is not None:
        _AbaqusSetup.set_default_post_processor(_post_processing_abaqus)
except Exception as _exc:  # noqa: BLE001
    logger.warning(f"abaqus post-processor wiring skipped: {_exc}")


@task
def design() -> ada.Assembly:
    """Pure geometry: canonical IPE400 cantilever, S420 steel."""
    return design_cantilever()


@task(
    parent=design,
    fanout={
        "geom_repr": ["line", "shell", "solid"],
        "elem_order": [1, 2],
        "use_hex_quad": [False, True],
        "reduced_integration": [False, True],
    },
)
def mesh(
    a: ada.Assembly,
    *,
    geom_repr: str,
    elem_order: int,
    use_hex_quad: bool,
    reduced_integration: bool,
) -> ada.Assembly:
    """Mesh + BC + reduced-integration option toggle.

    Each mesh cell deep-copies the assembly first. The runner reuses
    `design()`'s single result across every mesh cell; without the
    copy, the second cell's `add_bc("Fixed", ...)` would collide with
    the first's. Q8's pickle work guarantees deepcopy survives the
    OCCT/IFC caches the audit identified.

    Axes (geom_repr, elem_order, use_hex_quad, reduced_integration) are
    stashed onto `a.metadata["case_axes"]` so the downstream `run_eig`
    task can reconstruct the case name without re-declaring them on its
    own fanout (which would re-fan-out the matrix, not what we want).
    """
    a = copy.deepcopy(a)
    a = mesh_cantilever(
        a,
        geom_repr=geom_repr,
        elem_order=elem_order,
        use_hex_quad=use_hex_quad,
        reduced_integration=reduced_integration,
    )
    a.metadata["case_axes"] = {
        "geom_repr": geom_repr,
        "elem_order": elem_order,
        "use_hex_quad": use_hex_quad,
        "reduced_integration": reduced_integration,
    }
    return a


def _eig_skip(**kw: object) -> bool:
    """Translate Cell.full_kwargs into adapy's is_eig_skip predicate.

    Cell.full_kwargs delivers every ancestor's kwargs merged in, so this
    sees mesh's axes + run_eig's solver in one dict — exactly what
    is_eig_skip needs.
    """
    return is_eig_skip(
        fem_format=kw["solver"],
        geom_repr=kw["geom_repr"],
        elem_order=kw["elem_order"],
        use_hex_quad=kw["use_hex_quad"],
        reduced_integration=kw["reduced_integration"],
    )


@task(
    parent=mesh,
    fanout={"solver": ["abaqus", "calculix", "code_aster", "sesam"]},
    skip_if=_eig_skip,
)
def run_eig(a: ada.Assembly, *, solver: str):
    """Add eigen step + invoke solver. Returns FEAResult or None.

    Mirrors `simulate()`'s per-case try/except: a missing solver
    executable (`FEASolverNotInstalled`) logs and returns None rather
    than killing the build, matching the current behavior.

    Before returning, the case's mesh axes (geom_repr, elem_order,
    use_hex_quad, reduced_integration) get stashed onto the result as
    `_case_axes` so the downstream `postprocess` aggregator can read
    them without re-walking cells.
    """
    axes = a.metadata["case_axes"]
    name = eig_case_name(
        solver,
        axes["geom_repr"],
        axes["elem_order"],
        axes["use_hex_quad"],
        axes["reduced_integration"],
    )
    try:
        result = run_eig_helper(
            a,
            fem_format=solver,
            scratch_dir=_SCRATCH_DIR,
            name=name,
            eigen_modes=_EIG_MODES,
            overwrite=True,
            execute=True,
        )
    except FEASolverNotInstalled as exc:
        logger.warning(f"{name}: solver {solver!r} not installed: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"{name}: {type(exc).__name__}: {exc}", exc_info=True)
        return None

    if result is not None:
        # FEAResult is a plain @dataclass — attribute injection is fine
        # and survives pickle round-trip through the cache layer.
        result._case_axes = {
            "geo": axes["geom_repr"],
            "elo": axes["elem_order"],
            "hexquad": axes["use_hex_quad"],
            "reduced_integration": axes["reduced_integration"],
        }
    return result


# ---------------- post-processing / outcomes ----------------


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


@task(consumes=run_eig)
def postprocess(results: list) -> list:
    """Aggregator: turn each FEAResult into a FeaVerificationResult and
    augment with any cached JSON in `.cache/`.

    `results` arrives with Nones filtered out (the runner drops them
    upstream). Each live result was tagged with `_case_axes` by
    `run_eig`. The cache walk then layers on any cases that weren't
    re-executed this build — the path CI takes when no solver is
    installed.
    """
    out: list = []
    for r in results:
        case_meta = getattr(r, "_case_axes", {})
        fvr = ru.postprocess_result(r, dict(case_meta))
        fvr.name = _safe_filter_name(fvr.name)
        out.append(fvr)

    ru.retrieve_cached_results(out, _CACHE_DIR)
    logger.info(f"postprocess: {len(out)} result(s) (live + cached)")
    return out


@task(parent=design, outputs=[_ASSETS_DIR / "beam.glb"])
def beam_glb(assembly: ada.Assembly):
    """Bake the standalone beam GLB + ThreeDData row.

    The outputs= declaration drives cache pre-flight: if the user
    deletes the .glb, this task re-runs to regenerate it. The PNG
    poster is best-effort (frontend has its own fallback) so it's not
    declared in outputs=.
    """
    beam = next(
        b for b in assembly.get_all_physical_objects() if isinstance(b, ada.Beam)
    )
    dest = _ASSETS_DIR / "beam.glb"
    regen = os.environ.get("ADAPY_VERIFICATION_REGEN_ASSETS", "0") == "1"
    if regen or not dest.exists():
        try:
            asm = ada.Assembly("verification_beam")
            p = ada.Part("beam_only")
            p.add_beam(beam)
            asm.add_part(p)
            dest.parent.mkdir(parents=True, exist_ok=True)
            asm.to_gltf(dest)
            logger.info(f"wrote beam GLB → {dest}")
            try:
                from ada.visit.rendering.pygfx_offscreen_utils import glb_to_image
                glb_to_image(dest).save(str(dest.with_suffix(".png")))
            except Exception as exc:
                logger.warning(f"beam poster PNG failed: {exc}")
        except Exception as exc:
            logger.warning(f"beam GLB generation failed: {exc}")
            return None

    if not dest.is_file():
        return None

    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    metadata: dict = {}
    png_path = dest.with_suffix(".png")
    if png_path.is_file():
        metadata["image_path"] = str(png_path.relative_to(_THIS_DIR))
    return ThreeDOutcome(
        row=ThreeDData(
            key="beam_geom",
            glb_path=str(dest.relative_to(_THIS_DIR)),
            format="glb",
            camera_pos="iso_3",
            caption="Cantilever beam geometry.",
            sha256=sha,
            size=dest.stat().st_size,
            source_type="cad_model_file",
            metadata=metadata,
        )
    )


@task(parent=postprocess)
def eig_tables(results: list) -> list:
    """Six comparison TableOutcomes — one per (geom, order) combo."""
    out: list = []
    for key, geo, order, hq, caption in _COMPARISON_SPECS:
        df = ru.create_df_of_data(results, geo, order, hq)
        if df is None or df.empty:
            logger.info(f"no rows for {key}, skipping table")
            continue
        out.append(
            TableOutcome(
                key=key,
                df=df,
                caption=caption,
                show_index=False,
                default_sort=("Mode", True),
            )
        )
    return out


@task(parent=postprocess)
def modal_tables(results: list) -> list:
    """Per-case modal TableOutcomes + JSON cache write side effect."""
    save_cache = os.environ.get("ADA_FEM_DO_NOT_SAVE_CACHE") is None
    out: list = []
    for r in results:
        if save_cache:
            r.save_results_to_json(_CACHE_DIR / r.name)
        df = ru.eig_data_to_df(r.eig_data, ["Mode", "Eigenvalue (real)"])
        out.append(
            TableOutcome(
                key=r.name,
                df=df,
                caption=r.name,
                show_index=False,
                default_sort=("Mode", True),
            )
        )
    return out


@task(parent=postprocess)
def freq_plot(results: list):
    """Plotly Figure: mode frequency vs mode-number per case."""
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
    return PlotOutcome(
        key="eig_freq_vs_mode",
        fig=fig,
        caption="Eigenfrequency vs. mode number across FEA cases.",
        width=900,
        height=500,
    )


@task(parent=postprocess)
def versions_filter(results: list):
    """FilterOutcome wrapping the Versions filter, env-probed.

    Mirrors the legacy build_hooks `_solver_versions` logic: hardcoded
    defaults overridden by env-installed solver versions, with a
    `software_versions.json` cache for offline replay.
    """
    from filters import Versions as VersionsFilter

    version_cache = _CACHE_DIR / "software_versions.json"
    cached: dict = {}
    if version_cache.exists():
        try:
            cached = json.loads(version_cache.read_text())
        except Exception:
            cached = {}

    versions = dict(
        calculix="2.21",
        code_aster="17.1.0",
        abaqus=cached.get("abaqus", "2021"),
        sesam=cached.get("sesam", "10"),
    )
    from ada.fem.formats.abaqus.versions import get_abaqus_exe, get_abaqus_version
    from ada.fem.formats.sesam.sesam_exe_locator import (
        get_sestra_default_exe_path,
        get_sestra_version,
    )
    if get_abaqus_exe() is not None:
        try:
            versions["abaqus"] = get_abaqus_version()
        except Exception as exc:
            logger.warning(f"abaqus version probe failed: {exc}")
    if get_sestra_default_exe_path() is not None:
        try:
            versions["sesam"] = get_sestra_version()
        except Exception as exc:
            logger.warning(f"sesam version probe failed: {exc}")

    version_cache.write_text(json.dumps(versions, indent=4))
    return FilterOutcome(filter=VersionsFilter(versions, name="versions"))


def _bake_fea_assets(results: list) -> dict:
    """One FEA bundle per case via `ada.fem.results.docs.assets_for_docs`.

    Only fires when `ADAPY_VERIFICATION_REGEN_ASSETS=1`. Wipes each
    case dir first so a stale per-mode PNG from an earlier run doesn't
    outlive the new bundle. Cases without a live FEAResult are skipped
    silently (cache-only path picks the committed bundle up below).
    """
    out: dict = {}
    for r in results:
        res = getattr(r, "results", None)
        if res is None:
            logger.info(
                f"{r.name}: no FEAResult attached (cached-only) — "
                "skipping FEA artefact bake"
            )
            continue
        case_dir = _ASSETS_DIR / r.name
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


def _collect_bundle_assets(skip_keys: set) -> list:
    """Pick up committed FEA bundles under `_assets/`. CI / cache-only
    path: when `_bake_fea_assets` didn't fire, these are the bundles
    paradoc renders against."""
    out: list = []
    for manifest_path in sorted(_ASSETS_DIR.rglob("fea.manifest.json")):
        case_dir = manifest_path.parent
        case = case_dir.name
        if case in skip_keys:
            continue
        try:
            out.append(assets_from_bundle_dir(case_dir, key=case))
        except Exception as exc:
            logger.warning(f"could not load bundle at {case_dir}: {exc}")
    return out


def _regenerate_results_detailed_md(results: list, assets_by_name: dict) -> None:
    """Overwrite `report/01-app/00-results-detailed.md` from live results.

    Each case section emits one `${ <case>.mode_<N> }` per mode that
    has a baked poster — `assets_by_name` is the union of fresh-baked
    and committed bundles, so the generated markdown never references
    a mode that doesn't have a paradoc row backing it.
    """
    target = _REPORT_SRC_DIR / "01-app" / "00-results-detailed.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    by_solver: dict = {}
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


@task(
    parent=postprocess,
    outputs=[_REPORT_SRC_DIR / "01-app" / "00-results-detailed.md"],
)
def fea_outputs(results: list) -> list:
    """Per-case FEA bundle bakes + ThreeD/Filter outcomes + detailed-md regen.

    One task fans out into all per-case artifacts:
    - Bakes fresh FEA bundles if `ADAPY_VERIFICATION_REGEN_ASSETS=1`
    - Picks up committed bundles for cases that weren't re-baked
    - Writes `report/01-app/00-results-detailed.md` so OneDoc has the
      per-mode references resolvable at compile time
    - Yields one ThreeDOutcome per paradoc row in the bundle + one
      FilterOutcome per `FeaCaseFilter`

    outputs= covers the regenerated markdown so cache pre-flight
    catches deletion of that file. The bundle dirs themselves aren't
    declared (they're solver-output dependents controlled by the
    REGEN env flag, not by paradoc's cache layer).
    """
    fresh: dict = {}
    if os.environ.get("ADAPY_VERIFICATION_REGEN_ASSETS", "0") == "1":
        fresh = _bake_fea_assets(results)
    cached = _collect_bundle_assets(skip_keys=set(fresh))

    assets_by_name: dict = {**fresh}
    for a in cached:
        assets_by_name.setdefault(a.key, a)

    _regenerate_results_detailed_md(results, assets_by_name)

    outcomes: list = []
    for assets in assets_by_name.values():
        for row in to_paradoc_rows(
            assets,
            base_dir=_THIS_DIR,
            caption=f"{assets.solver or assets.key} — {assets.key} FEA results.",
        ):
            outcomes.append(ThreeDOutcome(row=row))
        outcomes.append(FilterOutcome(filter=FeaCaseFilter.from_assets(assets)))

    return outcomes
