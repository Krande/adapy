"""Bake plus static poster rendering."""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Iterable, Literal

from .bake import BakeResult, bake_artefacts
from .readers import make_stream_reader
from .stream_adapter import FEAResultStreamAdapter

# ---------------------------------------------------------------------------
# Bake + static posters
# ---------------------------------------------------------------------------
#
# Static counterpart figures are mandatory in this codebase: the
# downstream consumer (paradoc) exports to PDF / DOCX / ODT, none of
# which can run the interactive deformation-scale slider. Every FEA
# figure that ships through the docs pipeline therefore needs a PNG
# poster per mode it could ever surface interactively. `bake_with_posters`
# wraps `bake_artefacts` + the existing `render_fea_mode_from_bundle`
# pass so callers get the bundle and the per-mode PNG set in one shot,
# instead of hand-rolling the mode-enumeration + filename-convention
# loop in every report script.


@dataclass
class BakeWithPostersResult(BakeResult):
    """Extends :class:`BakeResult` with per-mode static PNG posters.

    Convention (matches the verification report's existing layout so
    the paradoc exporter's ``<glb>.png`` sibling lookup keeps working):

      * Mode 1 → ``<out_dir>/fea.mesh.png`` (also recorded on
        ``canonical_poster_path`` so callers don't have to reconstruct
        the filename).
      * Mode N (N ≥ 2) → ``<out_dir>/fea.mesh.mode_<N>.png``.

    ``poster_paths`` is keyed by the 0-based global mode index — same
    indexing the embed's ``mountFeaArtefactViewer(modeIndex=…)`` uses,
    so a downstream loop ``for i, png in poster_paths.items(): …`` lines
    up directly with mode-view rows that pin ``modeIndex=i``.
    """

    poster_paths: dict[int, pathlib.Path] = dc_field(default_factory=dict)
    canonical_poster_path: pathlib.Path | None = None


def _resolve_mode_selection(
    modes: "str | int | Iterable[int] | None",
    n_available: int,
) -> list[int]:
    """Turn the ``modes`` knob into a concrete list of 0-based indices.

    ``"all"`` → every mode the bundle has.
    ``int N`` → modes 0..min(N, n_available)-1 (i.e. the first N).
    Iterable  → take each value that falls inside ``[0, n_available)``.
    ``None``  → ``[]`` (caller should skip rendering entirely; kept here
    for symmetry so ``modes=None`` in tests behaves predictably).
    """
    if modes is None or n_available <= 0:
        return []
    if modes == "all":
        return list(range(n_available))
    if isinstance(modes, int):
        if modes < 0:
            return []
        return list(range(min(modes, n_available)))
    return [i for i in modes if 0 <= i < n_available]


def bake_with_posters(
    reader_or_result,
    out_dir: os.PathLike,
    *,
    src: str = "",
    modes: "str | int | Iterable[int] | None" = "all",
    poster_backend: Literal["pygfx", "chromium"] = "pygfx",
    legacy_glb_url_template: str | None = None,
    nodal_only: bool = True,
    include_element_fields: bool = True,
    include_beam_solids: bool = True,
) -> BakeWithPostersResult:
    """Bake the artefact bundle AND render per-mode static PNG posters.

    Accepts either a :class:`FEAStreamReader` (the streaming bake's
    native input) or an in-memory :class:`FEAResult` (auto-wrapped via
    :class:`FEAResultStreamAdapter`) — the same ergonomic split as
    ``bake_fea_artefacts_from_source``'s reader-discovery, but driven
    by the object the caller already holds.

    ``modes``:
        * ``"all"`` (default) — one PNG per displacement-field step.
          The docs pipeline needs every mode it might surface
          interactively, so this is the right default for the
          PDF/DOCX/ODT-aware use case.
        * ``int N`` — render the first N modes only.
        * iterable of int — render those specific 0-based modes.
        * ``None`` — bundle only, no posters. Use when the caller is
          handling poster rendering itself (or the figure is
          interactive-only in a context where that's acceptable).

    ``poster_backend``:
        * ``"pygfx"`` (default) — fast, no browser. Camera framing is
          the same `iso_3` math the embed uses.
        * ``"chromium"`` — drives the production embed headless via
          playwright. Bit-identical to the live viewer, ~5 s/PNG.

    Per-mode render failures are logged but don't abort the bake —
    the bundle is still valid even if a few posters are missing, and
    surfacing them as warnings keeps a single bad mode from blowing
    up the whole docs build. Modes that render successfully appear in
    the returned ``poster_paths``; missing modes mean either the
    selection excluded them or that mode's render raised.
    """

    # Auto-wrap FEAResult. We check for the FEAStreamReader Protocol's
    # core method rather than `isinstance(FEAResult)` so future result
    # types (in-progress migration) don't need wiring here — anything
    # that quacks like a reader passes through, everything else gets
    # the FEAResult adapter treatment.
    if hasattr(reader_or_result, "read_mesh_geometry"):
        reader = reader_or_result
    else:
        reader = FEAResultStreamAdapter(reader_or_result)

    bake = bake_artefacts(
        reader,
        out_dir,
        src=src,
        legacy_glb_url_template=legacy_glb_url_template,
        nodal_only=nodal_only,
        include_element_fields=include_element_fields,
        include_beam_solids=include_beam_solids,
    )

    if modes is None:
        return BakeWithPostersResult(
            out_dir=bake.out_dir,
            manifest_path=bake.manifest_path,
            mesh_glb_path=bake.mesh_glb_path,
            field_blob_paths=bake.field_blob_paths,
        )

    # Lazy import — the renderer pulls trimesh + pygfx + numpy heavy
    # machinery, which a slimmer caller (a smoke test that just wants
    # the bundle, say) shouldn't pay for.
    from ada.visit.rendering.fea_offscreen import (
        _list_displacement_entries,
        render_fea_mode_from_bundle,
    )

    manifest = json.loads(bake.manifest_path.read_text(encoding="utf-8"))
    available = _list_displacement_entries(manifest)
    wanted = _resolve_mode_selection(modes, len(available))

    poster_paths: dict[int, pathlib.Path] = {}
    canonical: pathlib.Path | None = None
    for mode_idx in wanted:
        mode_n = mode_idx + 1
        # Mode 1 lands at the `<glb>.png` sibling slot the paradoc
        # exporter (and the cad_model_file figure-source) already looks
        # up for canonical-row posters. Modes ≥ 2 use a per-mode
        # filename so multiple rows referencing the same bundle don't
        # collide on disk.
        if mode_n == 1:
            dest_png = bake.mesh_glb_path.with_suffix(".png")
            canonical = dest_png
        else:
            dest_png = bake.mesh_glb_path.with_name(f"fea.mesh.mode_{mode_n}.png")
        try:
            img = render_fea_mode_from_bundle(
                bake.out_dir,
                mode_index=mode_idx,
                backend=poster_backend,
            )
            img.save(str(dest_png))
            poster_paths[mode_idx] = dest_png
        except Exception as exc:  # noqa: BLE001 — render failure is non-fatal
            import logging

            logging.getLogger(__name__).warning(
                "bake_with_posters: mode %d poster failed: %s",
                mode_n,
                exc,
                exc_info=True,
            )

    return BakeWithPostersResult(
        out_dir=bake.out_dir,
        manifest_path=bake.manifest_path,
        mesh_glb_path=bake.mesh_glb_path,
        field_blob_paths=bake.field_blob_paths,
        poster_paths=poster_paths,
        canonical_poster_path=canonical,
    )


def bake_with_posters_from_source(
    src_path: os.PathLike,
    out_dir: os.PathLike,
    *,
    src_key: str = "",
    modes: "str | int | Iterable[int] | None" = "all",
    poster_backend: Literal["pygfx", "chromium"] = "pygfx",
    legacy_glb_url_template: str | None = None,
    include_beam_solids: bool = True,
) -> BakeWithPostersResult:
    """End-to-end bake + per-mode posters from a result file path.

    Mirrors ``bake_fea_artefacts_from_source``: pick the right stream
    reader for the extension (``.frd`` / ``.odb`` / ``.rmed`` / ``.sif``
    / ``.sin``), drive the streaming bake, then render the static
    posters for the modes selected. Raises :class:`ValueError` for
    unsupported extensions; same policy as the non-poster variant.
    """

    src_path = pathlib.Path(src_path)
    src = src_key or src_path.stem
    with make_stream_reader(src_path) as reader:
        return bake_with_posters(
            reader,
            out_dir,
            src=src,
            modes=modes,
            poster_backend=poster_backend,
            legacy_glb_url_template=legacy_glb_url_template,
            include_beam_solids=include_beam_solids,
        )
