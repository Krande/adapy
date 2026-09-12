"""FEA result decks → GLB (one (step, field) pair), FEA input-deck cross-conversion and the SIN
input-deck extraction.
"""

from __future__ import annotations

import pathlib
import re
from typing import TYPE_CHECKING

from ada.core.file_system import new_temp_path

from .keys import _FEA_RESULT_EXTS
from .registry import ConverterRegistry, ProgressFn, UnsupportedFormat, converter

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult


def _pick_default_step_field(result: "FEAResult") -> tuple[int, str]:
    """Choose a reasonable default (step, field) for a fresh SIF render.

    First step from the result's step list, first field name from the
    grouping. Caller surfaces a clean error when either list is empty
    (no result data → nothing to colorize)."""
    steps = result.get_steps()
    fields = result.get_results_grouped_by_field_value()
    if not steps:
        raise UnsupportedFormat("SIF contains no result steps to render")
    if not fields:
        raise UnsupportedFormat("SIF contains no nodal/element fields to render")
    return int(steps[0]), next(iter(fields.keys()))


def compute_fea_meta(src_path: pathlib.Path) -> dict:
    """Inspect a result deck and return a JSON-serializable description.

    Shape::
        {
            "steps": [int, ...],
            "fields": [{"name": str, "steps": [int, ...]}, ...],
            "default_step": int,
            "default_field": str,
        }

    Each field carries the list of steps it has data for, so the picker
    can disable invalid combinations. Sesam SIF typically reports every
    field at every step, but we don't assume.

    Caller is expected to run this in a threadpool — read_sif_file is
    synchronous and CPU-heavy on large decks.
    """
    from ada.fem.formats.sesam.results.read_sif import read_sif_file

    result = read_sif_file(str(src_path))
    grouped = result.get_results_grouped_by_field_value()
    steps_global = [int(s) for s in result.get_steps()]
    if not steps_global:
        raise UnsupportedFormat("SIF contains no result steps to render")
    if not grouped:
        raise UnsupportedFormat("SIF contains no nodal/element fields to render")

    fields_payload = []
    for name, datas in grouped.items():
        per_field_steps = sorted({int(d.step) for d in datas})
        fields_payload.append({"name": name, "steps": per_field_steps})

    default_step, default_field = _pick_default_step_field(result)
    return {
        "steps": steps_global,
        "fields": fields_payload,
        "default_step": int(default_step),
        "default_field": default_field,
    }


def _via_fea_result(
    src_path: pathlib.Path,
    target_format: str,
    on_progress: ProgressFn,
    *,
    step: int | None = None,
    field: str | None = None,
    source_uri: str | None = None,
) -> bytes:
    """Sesam SIF / SIN result deck → GLB tessellated visualisation.

    Routes by extension: ``.sif`` (text) → :func:`read_sif_file`;
    ``.sin`` (Norsam binary) → :func:`read_sin_file` (the pure-Python
    direct path, no SIF text intermediate). Both yield the same
    :class:`FEAResult` shape, then :meth:`FEAResult.to_gltf` writes a
    coloured/warped GLB. When the caller leaves step/field unset we
    fall back to the first available pair so an auto-convert at
    upload time still produces something viewable.

    ``source_uri`` (SIN only): a range-fetchable URI for the deck —
    ``open_sin`` reads pages of it straight from object storage, so a
    multi-GB deck is never downloaded in full and ``src_path`` may be an
    empty stub. The suffix routing still keys off ``src_path``.
    """
    if target_format != "glb":
        raise UnsupportedFormat(f"Sesam results can only target glb, got {target_format!r}")

    on_progress("parsing", 0.10)
    is_sin = src_path.suffix.lower() == ".sin"
    if is_sin:
        from ada.fem.formats.sesam.results.read_sin import (
            read_sin_file,
            read_sin_metadata,
        )

        # ``open_sin`` routes by scheme, so a presigned URI reads pages of
        # the deck straight from object storage instead of a local copy.
        sin_src = source_uri or str(src_path)
        # When the caller didn't pick a step, use the cheap metadata
        # path to pick one — avoids materialising every step's records
        # just to throw them away (a hundreds-of-modes eigen deck
        # would blow the 4 GiB worker budget). Then load only that
        # step.
        if step is None or field is None:
            meta = read_sin_metadata(sin_src)
            if not meta.fields or not meta.steps:
                raise UnsupportedFormat(f"SIN {src_path.name} has no RV* result fields")
            if step is None:
                step = meta.steps[0]
            if field is None:
                # Map SIN type name → FEAResult field name. read_sin
                # exposes the SIN names verbatim; the downstream
                # display layer remaps them.
                field = meta.fields[0]
        result = read_sin_file(sin_src, step=int(step))
    else:
        from ada.fem.formats.sesam.results.read_sif import read_sif_file

        # Bound peak RSS to one step the way the SIN path does: load only the
        # requested step (or the first step in the file when the caller didn't
        # pick one) instead of materialising every step's RV* records. The GLB
        # render only colours one (step, field); a 20-mode eigen SIF that used
        # to hit multi-GB now stays at one step's footprint.
        result = read_sif_file(str(src_path), step=(int(step) if step is not None else "first"))

    on_progress("selecting-field", 0.50)
    if step is None or field is None:
        step, field = _pick_default_step_field(result)
    else:
        # Guard against a stale picker selection — the user may have
        # uploaded a new SIF under the same name. Bail with an error
        # the worker will surface to the queued job's audit row.
        available = result.get_results_grouped_by_field_value()
        if field not in available:
            # On the SIN single-step path the field name may be the
            # SIN card name (RVNODDIS, RVFORCES, RVSTRESS) — let the
            # caller's picker remap to whatever the FEAResult emits.
            if is_sin and available:
                field = next(iter(available))
            else:
                raise UnsupportedFormat(f"field {field!r} not in SIF; available: {sorted(available)}")
        if int(step) not in {int(d.step) for d in available[field]}:
            avail_steps = sorted({int(d.step) for d in available[field]})
            raise UnsupportedFormat(f"field {field!r} has no data at step {step}; available: {avail_steps}")

    on_progress("tessellating", 0.65)
    out_path = new_temp_path(suffix=".glb")
    try:
        result.to_gltf(out_path, step=int(step), field=field)
        on_progress("ready", 1.0)
        return out_path.read_bytes()
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass


# NOTE: _INCLUDE_RE / _inline_abaqus_includes / _find_writer_output /
# _FEM_TARGET_TO_FORMAT below are superseded by ada.fem.formats.deck_convert
# (the single source now shared with the WASM path) and are no longer called.
# Kept temporarily; safe to delete in a follow-up.
_INCLUDE_RE = re.compile(
    r"^\s*\*INCLUDE\s*,\s*INPUT\s*=\s*(.+?)\s*$",
    re.IGNORECASE,
)


def _inline_abaqus_includes(top_inp: pathlib.Path, max_depth: int = 4) -> bytes:
    """Walk an Abaqus deck and inline every ``*INCLUDE,INPUT=...``
    statement into a single self-contained ``.inp``.

    Adapy's Abaqus writer (``write_parts.py`` / ``write_main_inp.py``)
    emits a multi-file deck — the main ``model.inp`` references
    ``bulk_<part>/aba_bulk.inp`` for mesh data and
    ``core_input_files/<bc|materials|…>.inp`` for the analysis
    surfaces. That layout is correct for running an analysis; it's
    wrong for the /convert contract of "one bytes blob per derived
    key" because anyone who downloads the bytes can't satisfy the
    relative-path includes.

    Resolution is relative to the directory of the file currently
    being walked, so nested includes (a core_input_files/<step>.inp
    that itself references another file) resolve correctly. Missing
    include targets get a passthrough ``** [missing: <path>]`` line
    rather than a hard raise — the writer emits placeholders for
    sections with no data, and we don't want a missing optional
    section file to nuke the conversion.

    ``max_depth`` caps recursion in case the writer ever emits a
    pathological self-referential include chain. Four levels is
    deeper than any layout the writer produces today.
    """

    def _walk(path: pathlib.Path, depth: int) -> str:
        if depth > max_depth:
            return f"** [/convert: include depth cap reached at {path.name}]\n"
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")

        out_lines: list[str] = []
        for line in text.splitlines(keepends=True):
            m = _INCLUDE_RE.match(line.rstrip("\r\n"))
            if not m:
                out_lines.append(line)
                continue
            # Abaqus paths use backslash on Windows-style emit; both
            # POSIX (.split("/")) and backslash-only paths normalize
            # through pathlib if we replace backslashes first.
            inc_rel = m.group(1).replace("\\", "/").strip().strip('"')
            inc_path = (path.parent / inc_rel).resolve()
            if not inc_path.is_file():
                out_lines.append(f"** [/convert: missing include {inc_rel}]\n")
                continue
            out_lines.append(f"** ─── inlined: {inc_rel} ───\n")
            out_lines.append(_walk(inc_path, depth + 1))
            if not out_lines[-1].endswith("\n"):
                out_lines.append("\n")
        return "".join(out_lines)

    return _walk(top_inp, 0).encode("utf-8")


def _via_fea_to_fem(
    src_path: pathlib.Path,
    source_ext: str,
    target_ext: str,
    on_progress: ProgressFn,
) -> bytes:
    """FEA input deck → FEA input deck.

    Both sides use adapy's general FEM dispatch: ``ada.from_fem(src)``
    materialises an ``Assembly`` carrying the full ``Part.fem`` (nodes,
    elements, materials, sections, BCs, loads), then
    ``Assembly.to_fem(name, fem_format, scratch_dir)`` runs the matching
    writer.

    Caveats by target:

    * **.inp** (Abaqus) — the writer emits ``model.inp`` + a sibling
      ``bulk_<part>/aba_bulk.inp`` + ``core_input_files/<...>.inp``
      tree. We inline every ``*INCLUDE`` so the returned bytes are a
      single self-contained deck — running Abaqus on the download
      doesn't need the sibling files.
    * **.fem** (Sesam) — single-file deck; bytes are returned as-is.
    * **.med** (Code_Aster) — the writer emits ``name.med`` (mesh +
      groups) plus ``name.comm`` (analysis-spec template) and a
      ``.adapy_fem.json`` sidecar. We return only the ``.med`` here;
      the ``.comm`` is template-driven and would round-trip a stub
      analysis the user didn't ask for. Honest mesh-export
      semantics; full multi-file deck packaging would be a separate
      zip-output target.
    """

    # Shared with the WASM path (ada.cadit.wasm_convert) — the deck-rewrite
    # logic (writer dispatch, output-file selection, *INCLUDE inlining) lives
    # in ada.fem.formats.deck_convert so server and browser can't diverge.
    from ada.fem.formats.deck_convert import fem_deck_to_bytes

    try:
        return fem_deck_to_bytes(src_path, target_ext, on_progress)
    except ValueError as exc:
        raise UnsupportedFormat(str(exc)) from exc


def _find_writer_output(
    directory: pathlib.Path,
    name: str,
    target_ext: str,
) -> pathlib.Path | None:
    """Locate the deck file an FEA writer dropped into ``directory``.

    Looks first for the exact ``{name}{target_ext}`` (lowercase
    case), then for any sibling matching ``*{target_ext}``
    case-insensitively. The fallback covers the Sesam writer's
    ``{name}T1.FEM`` convention (uppercase extension + super-element
    ``T1`` suffix) without each format needing its own special
    case here.
    """
    if not directory.is_dir():
        return None
    canonical = directory / f"{name}{target_ext}"
    if canonical.is_file():
        return canonical
    ext_lower = target_ext.lower()
    for p in directory.iterdir():
        if p.is_file() and p.suffix.lower() == ext_lower:
            return p
    return None


# Map M3 target extensions to the ``fem_format`` strings adapy's
# write-dispatcher understands. Source-side ada.from_fem auto-detects
# from the file extension so no symmetric map is needed.
_FEM_TARGET_TO_FORMAT: dict[str, str] = {
    ".inp": "abaqus",
    ".fem": "sesam",
    ".med": "code_aster",
}


def _register_fea_result_to_glb() -> None:
    for ext in _FEA_RESULT_EXTS:

        def _h(src, on_progress, *, _ext=ext, step=None, field=None, source_uri=None, **_kw):
            return _via_fea_result(
                src,
                "glb",
                on_progress,
                step=step,
                field=field,
                source_uri=source_uri,
            )

        ConverterRegistry.register(ext, "glb", _h)


@converter(
    accepts=[".inp", ".fem", ".med"],
    exports=[".inp", ".fem", ".med"],
    exclude_identity=True,
    # Schema-shipping with no entries: confirms the wire format
    # round-trips an empty options list and stays additive. Real
    # per-pair knobs (e.g. ``mesh_only``) land in a follow-up CL
    # together with the worker plumbing that forwards
    # conversion_options into the handler's kwargs (today they're
    # consumed env-var-style before convert() is invoked).
    options=[],
)
def _fea_to_fea(src, on_progress, *, source_ext, target_ext, **_):
    """Abaqus ↔ Sesam ↔ Code_Aster input-deck cross-conversion.

    All three formats have readers AND writers in adapy; cells where
    both ends support the same constructs (nodes, elements,
    materials, sections, BCs, loads) round-trip cleanly. Writers
    fail with adapy-internal errors on sources missing constructs
    they expect — e.g. the Sesam writer raises on inputs without a
    populated ``fem.sections`` table. Those surface as job errors in
    the conversion toast rather than being caught here, since the
    failure mode is informative and silently degrading would hide
    real adapy regressions.

    Code_Aster output is the ``.med`` (mesh + groups) only; the
    matching ``.comm`` analysis-spec template is dropped (see
    :func:`_via_fea_to_fem` for the rationale).
    """

    return _via_fea_to_fem(src, source_ext, target_ext, on_progress)


@converter(".sin", "fem")
def _sin_to_fem(src, on_progress, **_):
    """Extract the FEM input deck SESTRA echoed into a results file.

    A SIN carries the whole input deck beside its result records; this walks
    the binary record blocks, keeps every input record verbatim and drops the
    results — extraction, not reconstruction, so nothing an object-model
    writer fails to model can be silently lost. The product is a standard
    Input Interface File a Sesam tool (or this viewer) opens directly.
    """

    on_progress("extracting input deck", 0.2)
    # Worker-only import: the slim API container imports this module for the
    # registry but cannot carry ada.fem.
    from ada.fem.formats.sesam.results.export_fem import export_fem_text

    text = export_fem_text(src)
    on_progress("writing", 0.9)
    return text.encode("ascii")
