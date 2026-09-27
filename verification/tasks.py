"""paradoc.tasks declarations for `paradoc build verification`.

Complete declarative pipeline for the FEA verification report:

  design → mesh → run_eig                          (compute)
  postprocess (consumes=run_eig)                   (aggregator: one cell)
  beam_glb (parent=design)                         (CAD asset)
  fea_outputs (parent=postprocess)                 (bundles + per-case 3D)
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
      filters.py        # Filter subclasses + block-sugar handlers
      _assets/          # per-case FEA bundles (bake target)
      report/*.md       # the document body

The per-case ``#### Mode N`` markdown sections are NOT generated at
build time. Instead, ``report/01-app/00-results-detailed.md`` is
static, with one ``<!-- paradoc:figure figure_source: eig_modes_section
solver: X ... -->`` block per solver. The ``eig_modes_section``
figure-source handler in ``filters.py`` walks ``_assets/<case>/``
for baked bundles, filters by solver, and expands each block into
the per-case markdown at preprocessor time.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import pathlib
import sys

# `tasks.py` gets loaded via paradoc.tasks.discovery's
# spec_from_file_location, which does NOT add the parent dir to
# sys.path. Bootstrap THIS_DIR so sibling modules (utils, filters)
# import cleanly.
_THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

import utils as ru  # noqa: E402 — verification's private helpers
from paradoc.db.models import ThreeDData  # noqa: E402
from paradoc.tasks import (  # noqa: E402
    FilterOutcome,
    PlotOutcome,
    TableOutcome,
    ThreeDOutcome,
    task,
)

import ada  # noqa: E402
from ada.api.fem_tasks import (  # noqa: E402
    PLATE_PART_NAME,
    PLATE_STRIP_LENGTH,
    PLATE_STRIP_WIDTH,
    design_cantilever,
    design_plate_strip,
    eig_case_name,
    is_eig_skip,
    is_plate_skip,
    mesh_cantilever,
    mesh_plate_strip,
    plate_case_name,
    plate_closed_form_deflection,
    plate_closed_form_frequencies,
)
from ada.api.fem_tasks import run_eig as run_eig_helper  # noqa: E402
from ada.api.fem_tasks import run_plate_eig, run_plate_pressure  # noqa: E402
from ada.fem.exceptions.fea_software import FEASolverNotInstalled  # noqa: E402
from ada.fem.results import walk_cached_case_results  # noqa: E402
from ada.fem.results.docs import (  # noqa: E402
    FeaCaseFilter,
    bake_fea_bundles,
    collect_fea_bundles,
    to_paradoc_rows,
)

logger = logging.getLogger(__name__)


_SCRATCH_DIR = _THIS_DIR / "temp" / "eigen"
_CACHE_DIR = _THIS_DIR / ".cache"
_ASSETS_DIR = _THIS_DIR / "_assets"

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

_COMPARISON_SPECS = [
    ("eig_compare_solid_o1", "solid", 1, None, "Eigenfrequency comparison (Hz) — solid, 1st order."),
    ("eig_compare_solid_o2", "solid", 2, None, "Eigenfrequency comparison (Hz) — solid, 2nd order."),
    ("eig_compare_shell_o1", "shell", 1, True, "Eigenfrequency comparison (Hz) — shell, 1st order."),
    ("eig_compare_shell_o2", "shell", 2, True, "Eigenfrequency comparison (Hz) — shell, 2nd order."),
    ("eig_compare_line_o1", "line", 1, False, "Eigenfrequency comparison (Hz) — line, 1st order."),
    ("eig_compare_line_o2", "line", 2, False, "Eigenfrequency comparison (Hz) — line, 2nd order."),
]


# Wire the Abaqus odb post-processor at module-import time so any
# subsequent run_eig cell that hits the Abaqus solver picks it up.
# The legacy driver did this in create_fea_report(); putting it here
# keeps the side effect alongside the @task declarations.
try:
    from ada.fem.formats.abaqus.config import AbaqusSetup as _AbaqusSetup
    from ada.fem.formats.abaqus.post_processing import (
        get_odb_dump_exe as _get_odb_dump_exe,
    )
    from ada.fem.formats.abaqus.post_processing import (
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
        # Snapshot the FeaVerificationResult.safe_name cached_property
        # back into `.name` so every downstream consumer (bundle paths,
        # paradoc Filter keys, cache filenames) sees the same identifier
        # without each computing the cleaning independently.
        fvr.name = fvr.safe_name
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
    beam = next(b for b in assembly.get_all_physical_objects() if isinstance(b, ada.Beam))
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
def eff_mass_compare_tables(results: list) -> list:
    """Cross-solver effective modal mass comparison, mirroring the
    eigenfrequency comparison tables: one table per (geom, order, global
    direction). Directions with no excited mass (e.g. out-of-plane for a
    planar cantilever) are skipped, as are groups with no solver that
    reported effective mass."""
    out: list = []
    for key, geo, order, _hq, _caption in _COMPARISON_SPECS:
        for direction in ("X", "Y", "Z"):
            df = ru.create_eff_mass_comparison_df(results, geo, order, direction)
            if df is None or df.empty:
                continue
            out.append(
                TableOutcome(
                    key=f"{key}_meff_{direction.lower()}",
                    df=df,
                    caption=(f"Effective modal mass [kg], global {direction} — " f"{geo}, order {order}."),
                    show_index=False,
                    default_sort=("Mode", True),
                )
            )
    return out


@task(parent=postprocess)
def eff_mass_table(results: list) -> list:
    """Summary table of effective modal mass [kg] per case (summed over
    the captured modes, global X/Y/Z). Skipped when no solver in the run
    reported effective mass."""
    df = ru.create_eff_mass_summary_df(results)
    if df is None or df.empty:
        logger.info("no effective-mass data in any case, skipping summary table")
        return []
    return [
        TableOutcome(
            key="eff_mass_summary",
            df=df,
            caption=(
                "Effective modal mass [kg] per case, summed over the captured modes "
                "in the global X/Y/Z directions. Code_Aster reports translational "
                "effective mass only."
            ),
            show_index=False,
        )
    ]


@task(parent=postprocess)
def modal_tables(results: list) -> list:
    """Per-case modal TableOutcomes + JSON cache write side effect."""
    save_cache = os.environ.get("ADA_FEM_DO_NOT_SAVE_CACHE") is None
    out: list = []
    for r in results:
        if save_cache:
            r.save_to_json(_CACHE_DIR / r.name)
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
            rows.append(
                {
                    "case": r.name,
                    "solver": r.fem_format,
                    "geom": r.metadata.get("geo"),
                    "order": r.metadata.get("elo"),
                    "mode": int(m.no),
                    "f_hz": float(m.f_hz),
                }
            )
    if not rows:
        return None
    df = pd.DataFrame(rows)
    fig = px.line(
        df,
        x="mode",
        y="f_hz",
        color="case",
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


@task(parent=postprocess)
def fea_outputs(results: list) -> list:
    """Per-case FEA bundle bakes + ThreeD/Filter outcomes.

    One task fans out into all per-case artifacts:
    - Bakes fresh FEA bundles if `ADAPY_VERIFICATION_REGEN_ASSETS=1`
    - Picks up committed bundles for cases that weren't re-baked
    - Yields one ThreeDOutcome per paradoc row in the bundle + one
      FilterOutcome per `FeaCaseFilter`

    The bake / collect plumbing lives in `utils.py`; this task just
    sequences them and emits outcomes. The per-case markdown sections
    (`### case / #### Mode N` blocks) are NOT generated here. Instead,
    `report/01-app/00-results-detailed.md` is static and uses
    `<!-- paradoc:figure figure_source: eig_modes_section ... -->`
    block-sugar registered in `filters.py` to expand per-case sections
    at preprocessor time. See `verification/filters.py:EigModesSectionFilter`.
    """
    fresh: dict = {}
    if os.environ.get("ADAPY_VERIFICATION_REGEN_ASSETS", "0") == "1":
        fresh = bake_fea_bundles(results, out_dir=_ASSETS_DIR)
    cached = collect_fea_bundles(_ASSETS_DIR, skip_keys=set(fresh))

    assets_by_name: dict = {**fresh}
    for a in cached:
        assets_by_name.setdefault(a.key, a)

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


# ---------------------------------------------------------------------------------------------
# The plate strip: the shell half of the report, on plates rather than on a beam
# ---------------------------------------------------------------------------------------------
#
# Everything above is one *beam*, in three representations. This is a plate: an `ada.Plate` meshed
# into shells by the meshing module and written out as a deck per solver, checked against its own
# closed forms rather than only against another solver.
#
# Two analyses, because they exercise different halves of the writers. The eigen case reaches the
# mass and stiffness matrices and nothing else; the static case is the only one that reaches the
# distributed-load records -- `*DLOAD`, `FORCE_COQUE` and BEUSLO -- which is where the pressure
# support and its sign convention live.
#
# `stiffened` adds a T-profile bar on the centreline. It has no closed form (a parallel-spring
# estimate is not one), so it is a cross-solver agreement case, and it is what carries a T section
# through the deck writers.

#: Element seeds for the plate cases, metres: 32 / 64 / 128 quads per span, a factor of two apart so
#: the convergence order is readable off the table rather than asserted.
_PLATE_MESH_SIZES = [0.125, 0.0625, 0.03125]

#: Modes for the plate eigen case. The closed form covers the cylindrical modes, which are the low
#: ones, so a long list would mostly be modes with nothing to compare against.
_PLATE_EIG_MODES = 6

#: The one seed the eigen case runs at. Frequencies converge from above far faster than a deflection
#: does, and the static case already carries the mesh-refinement story.
_PLATE_EIG_MESH_SIZE = 0.0625

_PLATE_SCRATCH_DIR = _THIS_DIR / "temp" / "plate"

#: The plate cases cache beside `.cache`, not inside it. `walk_cached_case_results` uses `rglob`, so
#: a subdirectory would still be walked by the cantilever's own `retrieve_cached_results` -- and a
#: plate eigen snapshot IS a `FeaVerificationResult` carrying `geo="shell"`, `elo=1`, so it would
#: decode cleanly and land as an extra column in the cantilever's shell comparison table.
_PLATE_CACHE_DIR = _THIS_DIR / ".cache-plate"
_PLATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


@task(fanout={"stiffened": [False, True]})
def plate_design(*, stiffened: bool) -> ada.Assembly:
    """The strip as geometry: an `ada.Plate`, plus a T bar when stiffened. No FEM.

    `stiffened` is stashed on the assembly because a task body is handed only its *own* fanout
    kwargs -- the merged set reaches `skip_if` predicates, not the function. So a child that needs an
    ancestor's axis reads it off the object it was passed, which is the arrangement `mesh` uses too.
    """
    a = design_plate_strip(stiffened=stiffened)
    a.metadata["stiffened"] = stiffened
    return a


@task(parent=plate_design, fanout={"mesh_size": _PLATE_MESH_SIZES})
def plate_mesh(a: ada.Assembly, *, mesh_size: float) -> ada.Assembly:
    """Mesh the plate into quads through the meshing module and apply its supports.

    Deep-copied first, for the same reason `mesh` is: the runner reuses one `plate_design` result
    across every seed, and the supports are added to the FEM in place.

    The axes ride along on `metadata["plate_axes"]` so the solver tasks below can name their case
    without re-declaring the fanout (which would re-fan the matrix).
    """
    a = copy.deepcopy(a)
    stiffened = bool(a.metadata.get("stiffened", False))
    a = mesh_plate_strip(a, mesh_size=mesh_size, elem_order=1, use_quads=True)
    n_shells = len([el for el in a.get_part(PLATE_PART_NAME).fem.elements if el.type.value.startswith("QUAD")])
    a.metadata["plate_axes"] = {"mesh_size": mesh_size, "stiffened": stiffened, "n_shells": n_shells}
    return a


def _plate_static_skip(**kw: object) -> bool:
    return is_plate_skip(fem_format=kw["solver"], analysis="static", elem_order=1, stiffened=kw["stiffened"])


def _plate_eig_skip(**kw: object) -> bool:
    # One seed for the eigen case; the others are the static convergence study's.
    if kw["mesh_size"] != _PLATE_EIG_MESH_SIZE:
        return True
    return is_plate_skip(fem_format=kw["solver"], analysis="EIG", elem_order=1, stiffened=kw["stiffened"])


@task(
    parent=plate_mesh,
    fanout={"solver": ["abaqus", "calculix", "code_aster", "sesam"]},
    skip_if=_plate_static_skip,
)
def plate_run_static(a: ada.Assembly, *, solver: str):
    """Write the deck with a uniform pressure on the plate, solve it, keep mid-span ``u3``.

    A missing solver logs and returns None rather than failing the build -- the shape every cell in
    this report has, so a machine with only the free solvers still produces a report.
    """
    axes = a.metadata["plate_axes"]
    name = plate_case_name(solver, "static", 1, axes["stiffened"])
    name = f"{name}_h{str(axes['mesh_size']).replace('.', 'p')}"
    try:
        result = run_plate_pressure(
            copy.deepcopy(a),
            fem_format=solver,
            scratch_dir=_PLATE_SCRATCH_DIR,
            name=name,
            overwrite=True,
            execute=True,
        )
    except FEASolverNotInstalled as exc:
        logger.warning(f"{name}: solver {solver!r} not installed: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - one bad cell must not lose the report
        logger.warning(f"{name}: {type(exc).__name__}: {exc}", exc_info=True)
        return None

    if result is None:
        return None
    try:
        u3 = ru.mid_span_u3(result, PLATE_STRIP_LENGTH, PLATE_STRIP_WIDTH)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"{name}: could not sample mid-span u3: {exc}")
        return None

    result._plate_case = {"name": name, "solver": solver, "u3": u3, **axes}
    return result


@task(
    parent=plate_mesh,
    fanout={"solver": ["abaqus", "calculix", "code_aster", "sesam"]},
    skip_if=_plate_eig_skip,
)
def plate_run_eig(a: ada.Assembly, *, solver: str):
    """The same plate, as an eigenvalue analysis."""
    axes = a.metadata["plate_axes"]
    name = plate_case_name(solver, "EIG", 1, axes["stiffened"])
    try:
        result = run_plate_eig(
            copy.deepcopy(a),
            fem_format=solver,
            scratch_dir=_PLATE_SCRATCH_DIR,
            name=name,
            eigen_modes=_PLATE_EIG_MODES,
            overwrite=True,
            execute=True,
        )
    except FEASolverNotInstalled as exc:
        logger.warning(f"{name}: solver {solver!r} not installed: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"{name}: {type(exc).__name__}: {exc}", exc_info=True)
        return None

    if result is not None:
        result._plate_case = {"name": name, "solver": solver, **axes}
    return result


@task(consumes=plate_run_static)
def plate_static_postprocess(results: list) -> list:
    """Wrap each live static case, then layer on any cached ones.

    Same two-source shape as `postprocess`: what ran this build, plus the JSON snapshots in
    `.cache/` for the solvers this machine does not have. That is what lets a report built with only
    Calculix and Code_Aster still show the Abaqus and Sestra columns someone else measured.
    """
    out: list = []
    for r in results:
        case = getattr(r, "_plate_case", {})
        wrapper = ru.PlateStaticResult(
            name=case.get("name", "plate_static"),
            fem_format=r.softwares[0] if getattr(r, "softwares", None) else case.get("solver", "unknown"),
            results=r,
            metadata={"elo": 1},
            mid_span_u3=case.get("u3"),
            mesh_size=case.get("mesh_size"),
            stiffened=case.get("stiffened", False),
            n_shells=case.get("n_shells"),
        )
        wrapper.name = wrapper.safe_name
        cache_file = _PLATE_CACHE_DIR / f"{wrapper.name}.json"
        try:
            wrapper.save_to_json(cache_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"could not cache {wrapper.name}: {exc}")
        out.append(wrapper)

    live = {r.name for r in out}
    for cached in walk_cached_case_results(ru.PlateStaticResult, _PLATE_CACHE_DIR, skip_names=live):
        if cached.name.startswith("plate_static"):
            out.append(cached)

    logger.info(f"plate_static_postprocess: {len(out)} case(s) (live + cached)")
    return out


@task(parent=plate_static_postprocess)
def plate_static_tables(results: list) -> list:
    """One table of mid-span deflection against the closed form, per load case."""
    out: list = []
    for stiffened, caption in (
        (
            False,
            "Plate strip mid-span deflection [m] under 1 kPa, by solver and mesh seed. The closed "
            "form is 5 q L^4 / (384 D) for a strip in cylindrical bending.",
        ),
        (
            True,
            "The same strip with a T-profile stiffener on its centreline [m]. No closed form: this "
            "is a cross-solver agreement case.",
        ),
    ):
        subset = [r for r in results if getattr(r, "stiffened", False) is stiffened]
        df = ru.create_plate_static_df(subset, plate_closed_form_deflection())
        if df is None or df.empty:
            logger.info(f"no plate static rows for stiffened={stiffened}, skipping table")
            continue
        out.append(
            TableOutcome(
                key=f"plate_static_st{stiffened}",
                df=df,
                caption=caption,
                show_index=False,
            )
        )
    return out


@task(consumes=plate_run_eig)
def plate_eig_postprocess(results: list) -> list:
    """Wrap each live plate eigen case as a `FeaVerificationResult`, and cache it."""
    out: list = []
    for r in results:
        case = getattr(r, "_plate_case", {})
        fvr = ru.postprocess_result(
            r, {"elo": 1, "geo": "shell", "hexquad": True, "stiffened": case.get("stiffened", False)}
        )
        fvr.name = case.get("name", fvr.name)
        fvr.name = fvr.safe_name
        try:
            fvr.save_to_json(_PLATE_CACHE_DIR / f"{fvr.name}.json")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"could not cache {fvr.name}: {exc}")
        out.append(fvr)

    live = {r.name for r in out}
    for cached in walk_cached_case_results(ru.FeaVerificationResult, _PLATE_CACHE_DIR, skip_names=live):
        if cached.name.startswith("plate_EIG"):
            out.append(cached)

    logger.info(f"plate_eig_postprocess: {len(out)} case(s) (live + cached)")
    return out


@task(parent=plate_eig_postprocess)
def plate_eig_tables(results: list) -> list:
    """Plate eigenfrequencies by solver, against the cylindrical closed form."""
    # `FeaVerificationResult` has no `stiffened` field, so the axis rides in `metadata` -- reading it
    # off the attribute returned the default for both variants and put the stiffened plate's
    # frequencies in the unstiffened table.
    subset = [r for r in results if r.metadata.get("stiffened", False) is False]
    df = ru.create_plate_eig_df(subset, plate_closed_form_frequencies(_PLATE_EIG_MODES))
    if df is None or df.empty:
        logger.info("no plate eigen rows, skipping table")
        return []
    return [
        TableOutcome(
            key="plate_eig_compare",
            df=df,
            caption=(
                "Plate strip eigenfrequencies [Hz], by solver, at a 0.0625 m seed. The closed form "
                "is f_n = n^2 pi / (2 L^2) sqrt(D / (rho t)), valid for the cylindrical modes the "
                "long-edge constraint admits."
            ),
            show_index=False,
            default_sort=("Mode", True),
        )
    ]
