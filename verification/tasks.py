"""paradoc.tasks declarations for `paradoc build verification`.

Mirrors what `verification.build_verification_report.simulate()` does
today (a 5-D nested loop over geom × order × hq × ri × solver) as a
declarative @task tree. Phase 1 of the migration off the imperative
driver.

Layout matches paradoc's Q6 convention:
    verification/
      paradoc.toml      # build profiles, fanout overrides
      tasks.py          # this file
      filters.py        # Filter classes (TaskHandle binding follows
                        # in a later commit)
      report/*.md       # the document body
"""

from __future__ import annotations

import copy
import logging
import pathlib
import sys

# `tasks.py` gets loaded via paradoc.tasks.discovery's
# spec_from_file_location, which does NOT add the parent dir to
# sys.path. Bootstrap THIS_DIR so sibling modules (build_report_utils)
# import cleanly.
_THIS_DIR = pathlib.Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from paradoc.tasks import task  # noqa: E402

import ada  # noqa: E402
from ada.api.fem_tasks import (  # noqa: E402
    design_cantilever,
    eig_case_name,
    is_eig_skip,
    mesh_cantilever,
    run_eig as run_eig_helper,
)
from ada.fem.exceptions.fea_software import FEASolverNotInstalled  # noqa: E402

logger = logging.getLogger(__name__)


_SCRATCH_DIR = pathlib.Path(__file__).resolve().parent / "temp" / "eigen"


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
        return run_eig_helper(
            a,
            fem_format=solver,
            scratch_dir=_SCRATCH_DIR,
            name=name,
            eigen_modes=11,
            overwrite=True,
            execute=True,
        )
    except FEASolverNotInstalled as exc:
        logger.warning(f"{name}: solver {solver!r} not installed: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"{name}: {type(exc).__name__}: {exc}", exc_info=True)
        return None
