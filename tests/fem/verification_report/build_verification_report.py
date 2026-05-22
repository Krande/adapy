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
import re
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
WARP_SCALE = 1.0  # render the solver's reported displacement
                  # magnitudes faithfully; amplification belongs in a
                  # user-facing control, not baked into the figures.

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
        _bake_glb_poster_png(dest)
        return True
    except Exception as exc:
        logger.warning(f"beam GLB generation failed: {exc}")
        return False


def _bake_glb_poster_png(glb_path: pathlib.Path) -> bool:
    """Render a static PNG poster for ``glb_path``.

    Lets paradoc's frontend show a real raster preview while the
    interactive 3D viewer is lazy-loaded. The poster sits alongside
    the GLB at ``<glb>.png``; ``_collect_assets`` registers both and
    the paradoc static-export step copies them into the bundle.

    Backend selection (``ADA_FEA_POSTER_BACKEND`` env var):

    * ``"pygfx"`` (default) — fast, no browser. Camera framing is a
      hand-port of the embed's ``applyCameraPreset`` math; semantics
      can drift if the embed changes.
    * ``"chromium"`` — drives the production embed in headless
      Chromium via Playwright (see
      ``ada.visit.rendering.chromium_offscreen_utils``). Output is
      bit-identical to what the live viewer renders, at the cost of
      ~5s/GLB and a chromium dependency on the build host.

    Failures are logged and don't abort the bake — the frontend
    shows a generic placeholder card when no poster is found.
    """
    backend = os.environ.get("ADA_FEA_POSTER_BACKEND", "pygfx").lower()

    if backend == "chromium":
        try:
            from ada.visit.rendering.chromium_offscreen_utils import (
                glb_to_image_via_browser,
            )
        except Exception as exc:
            logger.warning(
                f"chromium poster backend unavailable, falling back to pygfx for "
                f"{glb_path.name}: {exc}"
            )
            backend = "pygfx"

    png_path = glb_path.with_suffix(".png")
    try:
        if backend == "chromium":
            image = glb_to_image_via_browser(glb_path)
        else:
            from ada.visit.rendering.pygfx_offscreen_utils import glb_to_image
            # `glb_to_image` reads the GLB the same way Three.js does (mode 1
            # LINES stay LINES) and frames the camera with the embed's
            # `iso_3` preset math — defaults match `iso_3`, so the poster
            # and the embedded 3D view line up without us specifying anything.
            image = glb_to_image(glb_path)
        image.save(str(png_path))
        return True
    except Exception as exc:
        logger.warning(f"poster PNG ({backend}) for {glb_path.name} failed: {exc}")
        return False


def _resolve_disp_field_per_mode(res, fem_format: str, mode_idx: int) -> str | None:
    """Pick the FEAResult field-name that holds mode-shape displacements.

    Calculix / Abaqus / Sesam keep one stable field name across all
    modes — we just look it up in DISP_FIELD. Code Aster's MED reader
    suffixes the field per time-step (`modes___DEPL[0] - 1`,
    `modes___DEPL[1] - 2`, ...) so a single static name doesn't match
    any key in `get_results_grouped_by_field_value()`. For that path
    we scan the available keys for one containing `DEPL` and `[mode-1]`.
    """
    if fem_format != "code_aster":
        return DISP_FIELD.get(fem_format)

    try:
        keys = list(res.get_results_grouped_by_field_value().keys())
    except Exception:
        return None
    needle = f"[{mode_idx - 1:d}]"
    for k in keys:
        if "DEPL" in k and needle in k:
            return k
    # Last resort: any DEPL key (the result will warp to whatever step
    # adapy considers the default for that field).
    for k in keys:
        if "DEPL" in k:
            return k
    return None


def _bake_per_mode_posters_from_bundle(
    case_dir: pathlib.Path,
    mesh_glb_path: pathlib.Path,
    manifest_path: pathlib.Path,
) -> tuple[int, dict[int, pathlib.Path]]:
    """Render one PNG per (displacement-field × step) mode from a
    bundle on disk.

    Thin wrapper around `ada.visit.rendering.fea_offscreen
    .render_fea_mode_from_bundle` — that helper handles the AFBL warp
    + abaqus colourmap + pygfx render. Here we just enumerate modes,
    write each PIL image into the bundle dir with the expected
    filename convention, and pack the results into the
    `{mode_idx → poster_path}` dict the caller needs.

    Returns ``(n_modes, posters_dict)``. The canonical (mode 1) PNG
    ends up at ``case_dir / fea.mesh.png`` so the exporter's
    `<glb>.png` sibling lookup finds it; modes >= 2 land at
    ``fea.mesh.mode_<N>.png`` next to the mesh.
    """
    import json as _json

    from ada.visit.rendering.fea_offscreen import (
        _list_displacement_entries,
        render_fea_mode_from_bundle,
    )

    try:
        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"{case_dir.name}: manifest parse failed: {exc}")
        return (0, {})

    entries = _list_displacement_entries(manifest)
    if not entries:
        return (0, {})

    posters: dict[int, pathlib.Path] = {}
    for mode_idx in range(len(entries)):
        mode_n = mode_idx + 1
        dest_png = (
            mesh_glb_path.with_suffix(".png")
            if mode_n == 1
            else mesh_glb_path.with_name(f"fea.mesh.mode_{mode_n}.png")
        )
        try:
            img = render_fea_mode_from_bundle(case_dir, mode_index=mode_idx)
            img.save(str(dest_png))
            posters[mode_idx] = dest_png
        except Exception as exc:
            logger.warning(
                f"{case_dir.name}: per-mode poster bake mode {mode_n} failed: {exc}",
                exc_info=True,
            )

    return (len(entries), posters)


def _bake_fea_bundles(results: Iterable[ru.FeaVerificationResult]) -> list[ThreeDData]:
    """Bake one FEA artefact bundle per case via the streaming pipeline.

    Replaces the legacy per-mode GLB writer. Each case produces:

      <case>/fea.manifest.json   — index of every blob
      <case>/fea.mesh.glb        — un-deformed geometry (one copy, shared
                                   across modes; no duplication)
      <case>/fea.mesh.edges.bin  — element-edge wireframe sidecar
      <case>/fea.mesh.elements.bin
      <case>/fea.<field>.bin     — displacement / stress field blobs

    The frontend mounts these via the same ``load_fea_streaming.ts``
    path the standalone viewer uses for live FEA sessions — so the
    Interactive 3D view gets:

      * SimulationControls (mode slider, deformation scale, play/pause)
        from ``hasAnimation = true``,
      * Abaqus colormap applied client-side from raw scalar data,
      * Mode switching via the slider (no separate per-mode GLB).

    Skipped if the result was loaded from JSON cache (no FEAResult
    object to bake). Returns one ThreeDData row per case, with
    metadata pointing at the bundle so paradoc knows to dispatch to
    the artefact-aware mount path instead of plain ``mountViewer``.
    """
    from ada.fem.results.artefacts import FEAResultStreamAdapter, bake_artefacts

    rows: list[ThreeDData] = []
    for r in results:
        res = getattr(r, "results", None)
        if res is None:
            logger.info(f"{r.name}: no FEAResult attached (cached-only), skipping FEA bundle bake")
            continue
        case_dir = assets_dir / r.name
        # Wipe any legacy per-mode GLBs so the bundle starts clean and
        # _collect_assets doesn't pick up stale `mode_NN.glb` files.
        if case_dir.exists():
            import shutil

            shutil.rmtree(case_dir)
        case_dir.mkdir(parents=True, exist_ok=True)

        try:
            reader = FEAResultStreamAdapter(res)
            bake = bake_artefacts(reader, case_dir, src=r.name)
        except Exception as exc:
            logger.warning(f"{r.name}: FEA artefact bake failed: {exc}", exc_info=True)
            continue

        # Per-mode poster PNGs for the static figures (before the user
        # clicks Interactive). The bundle's `fea.mesh.glb` is un-deformed
        # — the live viewer's `assembleAnimatedFeaGlb` adds displacement
        # client-side per mode_index. For the STATIC poster we want the
        # same deformed look per-mode.
        #
        # Previously this used `res.to_gltf(... warp_field=..., warp_step=...)`
        # in a loop. That worked for mode 1 but silently produced the
        # mode-1 result on every subsequent iteration regardless of the
        # field arg — every per-mode poster came out byte-identical to
        # mode 1's. Symptom seen in A.3.x: all `<case>_mode_N.png` had
        # the same md5.
        #
        # The bundle already carries everything needed to warp per
        # mode: `fea.mesh.glb` (base positions) + one AFBL displacement
        # blob per mode (in `case_dir`). Warp directly from those —
        # same logic the live viewer's `assembleAnimatedFeaGlb` runs
        # client-side — so there's a single source of truth and no
        # dependence on FEAResult's per-call state. End state per
        # case directory:
        #
        #   fea.mesh.glb            ← un-deformed (bundle, unchanged)
        #   fea.mesh.png            ← mode 1 deformed (canonical poster)
        #   fea.mesh.mode_2.png     ← mode 2 deformed
        #   fea.mesh.mode_3.png     ← mode 3 deformed
        #   ...
        n_modes, per_mode_posters = _bake_per_mode_posters_from_bundle(
            case_dir, bake.mesh_glb_path, bake.manifest_path,
        )

        # Canonical poster fallback if the warp pass produced nothing
        # — render the un-deformed mesh so the canonical row at least
        # has a thumbnail.
        canonical_png = bake.mesh_glb_path.with_suffix(".png")
        if not canonical_png.exists():
            _bake_glb_poster_png(bake.mesh_glb_path)

        manifest_rel = str(bake.manifest_path.relative_to(THIS_DIR))
        mesh_glb_rel = str(bake.mesh_glb_path.relative_to(THIS_DIR))
        bundle_dir_rel = str(case_dir.relative_to(THIS_DIR))

        rows.append(_fea_bundle_row(
            key=r.name,
            mesh_glb_path=bake.mesh_glb_path,
            bundle_dir_rel=bundle_dir_rel,
            manifest_rel=manifest_rel,
            caption=f"{r.fem_format} — {r.name} FEA results.",
        ))

        # Per-mode "view" rows — each references the SAME bundle files
        # but tags itself with a 0-based mode index + its own per-mode
        # poster PNG. The paradoc exporter recognises
        # `fea_artefact_bundle_mode_view` and registers only the
        # metadata (no extra bundle copy), so 10 modes per case = 10
        # manifest entries + 0 extra blobs (just N small PNGs).
        for mode_idx in range(n_modes):
            poster_path = per_mode_posters.get(mode_idx)
            rows.append(_fea_bundle_mode_view_row(
                bundle_key=r.name,
                mode_index=mode_idx,
                mesh_glb_path=bake.mesh_glb_path,
                poster_path=poster_path,
                caption=f"{r.fem_format} — {r.name} mode {mode_idx + 1}.",
            ))

        logger.info(
            f"{r.name}: baked FEA artefacts → {case_dir.name}/ "
            f"(mesh={mesh_glb_rel}, manifest={manifest_rel}, n_modes={n_modes})"
        )
    return rows


def _count_displacement_steps(res) -> int:
    """Count modes across all displacement field entries in this FEAResult.

    Two solver conventions to handle:
      * Calculix / Abaqus / Sesam: one field named ``U`` with
        n_steps entries (one per mode). Each step is a `NodalFieldData`
        record with name="U" and step=1, 2, ...
      * Code Aster: one field per mode named `result__DEPL[N]`,
        each with step=1. N records, all sharing step=1.

    Counting unique (step, name) pairs covers both. Falls back to 1
    if nothing's resolvable so the bake doesn't register zero
    mode-view rows for a case it just successfully baked.
    """
    seen: set[tuple[str, int]] = set()
    for r in getattr(res, "results", []):
        name = getattr(r, "name", "") or ""
        is_disp = (
            name == "U"
            or "DEPL" in name.upper()
            or "DISPLACEMENT" in name.upper()
        )
        if is_disp:
            step = getattr(r, "step", None)
            if isinstance(step, int):
                seen.add((name, step))
    return max(len(seen), 1)


# Legacy alias — older builds expect this symbol; keep it pointing at
# the new implementation so cached compile scripts don't import-error
# after a half-applied checkout.
_bake_mode_glbs = _bake_fea_bundles


def _three_d_row(key: str, glb_path: pathlib.Path, caption: str) -> ThreeDData:
    import hashlib

    sha = hashlib.sha256(glb_path.read_bytes()).hexdigest()
    # A sibling poster PNG (`<glb>.png`) lets paradoc's frontend show a
    # raster preview before mounting the interactive viewer. Encode the
    # PNG-on-disk hint in the `metadata` dict — paradoc's static-export
    # step looks for it there and copies the file alongside the GLB.
    metadata: dict = {}
    png_path = glb_path.with_suffix(".png")
    if png_path.exists():
        metadata["image_path"] = str(png_path.relative_to(THIS_DIR))
    return ThreeDData(
        key=key,
        glb_path=str(glb_path.relative_to(THIS_DIR)),
        format="glb",
        camera_pos="iso_3",
        caption=caption,
        sha256=sha,
        size=glb_path.stat().st_size,
        source_type="fea_mode_shape",
        metadata=metadata,
    )


def _fea_bundle_row(
    key: str,
    mesh_glb_path: pathlib.Path,
    bundle_dir_rel: str,
    manifest_rel: str,
    caption: str,
) -> ThreeDData:
    """ThreeDData row for an FEA artefact bundle.

    `glb_path` still points at the un-deformed `fea.mesh.glb` so the
    legacy single-GLB consumer path keeps working (the figure renders
    the reference mesh while paradoc-side artefact support is still
    landing). `metadata` carries:

      * ``fea_bundle_dir``  — bundle-relative directory the artefacts
        live in (frontend uses this to fetch the manifest + blobs).
      * ``fea_manifest_path`` — bundle-relative path to
        ``fea.manifest.json`` for one-shot lookup.
      * ``image_path``      — poster PNG (same convention as the
        legacy per-mode rows).
    """
    import hashlib

    sha = hashlib.sha256(mesh_glb_path.read_bytes()).hexdigest()
    metadata: dict = {
        "fea_bundle_dir": bundle_dir_rel,
        "fea_manifest_path": manifest_rel,
    }
    png_path = mesh_glb_path.with_suffix(".png")
    if png_path.exists():
        metadata["image_path"] = str(png_path.relative_to(THIS_DIR))
    return ThreeDData(
        key=key,
        glb_path=str(mesh_glb_path.relative_to(THIS_DIR)),
        format="glb",
        camera_pos="iso_3",
        caption=caption,
        sha256=sha,
        size=mesh_glb_path.stat().st_size,
        source_type="fea_artefact_bundle",
        metadata=metadata,
    )


def _fea_bundle_mode_view_row(
    bundle_key: str,
    mode_index: int,
    mesh_glb_path: pathlib.Path,
    caption: str,
    poster_path: pathlib.Path | None = None,
) -> ThreeDData:
    """ThreeDData row that points at an FEA artefact bundle but asks
    the embed to render a specific mode.

    Multiple mode-view rows share one set of bundle files: the
    paradoc exporter dedupes the bundle copy via the
    `fea_bundle_key` in metadata, so 10 modes per case = 10 manifest
    entries + 0 extra blobs on disk.

    `poster_path` (optional) points at a per-mode rendered PNG. When
    set, `metadata.image_path` carries the explicit path so the
    paradoc exporter copies it as `<key>.png` and the frontend
    surfaces that specific image. Without it, the exporter falls
    back to the `glb_path`'s `.png` sibling (the canonical mode-1
    poster), which makes every mode look the same in static-view.

    `glb_path` still points at the bundle's `fea.mesh.glb` so the
    canonical-row sha + size computation works the same way. The
    legacy single-GLB consumer path never fires for these (the
    renderer dispatches on `fea_bundle_dir`).
    """
    import hashlib

    sha = hashlib.sha256(mesh_glb_path.read_bytes()).hexdigest()
    metadata: dict = {
        "fea_bundle_key": bundle_key,
        "fea_mode_index": mode_index,
    }
    if poster_path is not None and poster_path.exists():
        metadata["image_path"] = str(poster_path.relative_to(THIS_DIR))
    return ThreeDData(
        key=f"{bundle_key}_mode_{mode_index + 1}",
        glb_path=str(mesh_glb_path.relative_to(THIS_DIR)),
        format="glb",
        camera_pos="iso_3",
        caption=caption,
        sha256=sha,
        size=mesh_glb_path.stat().st_size,
        source_type="fea_artefact_bundle_mode_view",
        metadata=metadata,
    )


def _collect_assets() -> list[ThreeDData]:
    """Register the standalone beam GLB + every committed FEA bundle.

    Two layouts coexist under ``_assets/``:

      * ``_assets/beam.glb`` — top-level un-deformed cantilever geometry.
        Single GLB, registered with the fixed key ``beam_geom``.
      * ``_assets/<case>/fea.manifest.json`` (+ siblings) — the FEA
        artefact bundle this script bakes for each case via
        ``_bake_fea_bundles``. We register one ThreeDData per bundle
        (keyed by ``<case>``); the metadata points the frontend at
        the manifest so it knows to dispatch to the artefact-aware
        mount path. ``fea.mesh.glb`` is the row's ``glb_path`` so the
        legacy single-GLB consumer path still shows the reference
        geometry while paradoc-side artefact support is mid-flight.

    Used in CI / cache-only runs where the regen path doesn't fire:
    whatever's already on disk gets served.
    """
    rows: list[ThreeDData] = []

    beam = assets_dir / "beam.glb"
    if beam.is_file():
        rows.append(
            _three_d_row(
                key="beam_geom",
                glb_path=beam,
                caption="Cantilever beam geometry.",
            )
        )

    for manifest_path in sorted(assets_dir.rglob("fea.manifest.json")):
        case_dir = manifest_path.parent
        case = case_dir.name
        mesh_glb = case_dir / "fea.mesh.glb"
        if not mesh_glb.is_file():
            logger.warning(
                f"FEA bundle at {case_dir} missing fea.mesh.glb — skipping registration"
            )
            continue
        rows.append(
            _fea_bundle_row(
                key=case,
                mesh_glb_path=mesh_glb,
                bundle_dir_rel=str(case_dir.relative_to(THIS_DIR)),
                manifest_rel=str(manifest_path.relative_to(THIS_DIR)),
                caption=f"{case} — FEA results.",
            )
        )

        # Mode-view rows: read the bundle's manifest to count
        # displacement steps, register one row per mode. Skipped
        # silently if the manifest is malformed; the canonical
        # bundle row above still serves the un-deformed mesh.
        try:
            import json as _json
            manifest_json = _json.loads(manifest_path.read_text(encoding="utf-8"))
            # Sum across all displacement fields × their n_steps —
            # Code Aster ships N fields × 1 step each, Calculix /
            # Abaqus ship 1 field × N steps. `max()` only worked for
            # the latter; summing is the right unifying count.
            n_modes = 0
            for f in manifest_json.get("fields", []) or []:
                cat = (f.get("category") or "").lower()
                name = (f.get("name_canonical") or "").upper()
                if cat == "displacement" or "DEPL" in name or name == "U":
                    n_modes += int(f.get("n_steps") or 0)
            for mode_idx in range(n_modes):
                # Look for the per-mode poster the bake wrote:
                # mode 1 lives at `fea.mesh.png` (canonical), modes
                # 2..N at `fea.mesh.mode_N.png`. Falls back to None
                # when absent so the canonical-row poster shows
                # instead (mode-1 deformed) — still wrong for modes
                # 2+, but the cached-asset path here is rare so the
                # degraded fallback is acceptable.
                if mode_idx == 0:
                    poster_candidate = case_dir / "fea.mesh.png"
                else:
                    poster_candidate = case_dir / f"fea.mesh.mode_{mode_idx + 1}.png"
                rows.append(
                    _fea_bundle_mode_view_row(
                        bundle_key=case,
                        mode_index=mode_idx,
                        mesh_glb_path=mesh_glb,
                        poster_path=poster_candidate if poster_candidate.is_file() else None,
                        caption=f"{case} mode {mode_idx + 1}.",
                    )
                )
        except Exception as exc:
            logger.warning(f"could not parse {manifest_path} for mode views: {exc}")
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

    Each case section now emits a single ``${ <case>.fea_3d }`` line
    (was: one ``${ <case>.mode_3d }`` per mode). The artefact bundle
    carries all modes inside — the live viewer's SimulationControls
    let the reader pick the mode without scrolling past N figures.
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
            # when the bundle baked, else a short "not available"
            # note (e.g. for cached-only Abaqus / Sesam runs where
            # the source result file wasn't bundled into CI).
            bundle_manifest = assets_dir / r.name / "fea.manifest.json"
            if bundle_manifest.exists():
                try:
                    import json as _json
                    manifest_json = _json.loads(bundle_manifest.read_text(encoding="utf-8"))
                    # Sum across all displacement fields × their
                    # n_steps. Code Aster ships N fields × 1 step
                    # each; Calculix / Abaqus ship 1 field × N steps;
                    # `max()` only worked for the latter and gave 1
                    # for every Code Aster case.
                    n_modes = 0
                    for f in manifest_json.get("fields", []) or []:
                        cat = (f.get("category") or "").lower()
                        name = (f.get("name_canonical") or "").upper()
                        if cat == "displacement" or "DEPL" in name or name == "U":
                            n_modes += int(f.get("n_steps") or 0)
                except Exception:
                    n_modes = 0
                if n_modes <= 0:
                    lines.append("_FEA bundle baked but no displacement modes detected; figures unavailable._")
                    lines.append("")
                else:
                    for mode_idx in range(n_modes):
                        lines.append(f"#### Mode {mode_idx + 1}")
                        lines.append("")
                        lines.append(f"${{ {r.name}.fea_3d_mode_{mode_idx + 1} }}")
                        lines.append("")
            else:
                lines.append(
                    "_Mode-shape figures unavailable for this case "
                    "(solver result file not bundled into CI)._"
                )
                lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"regenerated {target.name} with {len(results)} case sections")


def build_fea_report(bm: ada.Beam, results: list[ru.FeaVerificationResult], eig_modes: int, regen_assets: bool) -> OneDoc:
    """Hydrate `OneDoc` with filters + DB-backed tables/plots/3D assets."""
    # Bake the GLBs before regenerating the per-case markdown — the
    # generator emits a `${ <case>.mode_3d }` line only when the
    # matching GLB exists on disk, so the bake has to land first or
    # every case section ships without its 3D viewer.
    beam_glb = assets_dir / "beam.glb"
    if regen_assets or not beam_glb.exists():
        _bake_beam_glb(bm, beam_glb)
    if regen_assets:
        # New pipeline bakes one FEA artefact bundle per case (all
        # modes inside) — no per-mode GLB loop, so `eig_modes` is
        # no longer a parameter here.
        _bake_fea_bundles(results)

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
    # 3D assets: register the GLBs we baked above with the OneDoc db so
    # paradoc's static-export step copies them into the bundle.
    # ------------------------------------------------------------------
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
