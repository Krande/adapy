"""FEA input-deck ↔ input-deck conversion, as a single self-contained bytes
blob.

Extracted from ``ada.comms.rest.converter`` so the *same* logic backs the
server worker AND the in-browser / node WASM paths (``ada.cadit.wasm_convert``)
— a deck rewrite must never diverge between the two. Pure stdlib + ada
(``ada.from_fem`` is imported lazily), so it loads under pyodide.

``ada.from_fem(src)`` materialises an Assembly carrying the full ``Part.fem``
(nodes, elements, materials, sections, BCs, loads); ``Assembly.to_fem`` runs
the matching writer. Output is reduced to one blob per target:

* ``.inp`` (Abaqus) — the writer emits ``model.inp`` plus sibling
  ``bulk_<part>/`` and ``core_input_files/`` trees; we inline every
  ``*INCLUDE,INPUT=...`` so the returned bytes are a self-contained deck.
* ``.fem`` (Sesam) — single-file deck; returned as-is.
* ``.med`` (Code_Aster) — the writer emits ``name.med`` (mesh + groups) plus a
  ``.comm`` template + sidecar; we return only the ``.med`` (honest mesh
  export; full multi-file packaging would be a separate zip target).
"""

from __future__ import annotations

import pathlib
import re
import tempfile
from typing import Callable

_INCLUDE_RE = re.compile(
    r"^\s*\*INCLUDE\s*,\s*INPUT\s*=\s*(.+?)\s*$",
    re.IGNORECASE,
)

# Target extension → the ``fem_format`` string adapy's write dispatcher knows.
# Source-side ada.from_fem auto-detects from the file extension.
_FEM_TARGET_TO_FORMAT: dict[str, str] = {
    ".inp": "abaqus",
    ".fem": "sesam",
    ".med": "code_aster",
}

FEM_DECK_EXTS = frozenset(_FEM_TARGET_TO_FORMAT)


def _inline_abaqus_includes(top_inp: pathlib.Path, max_depth: int = 4) -> bytes:
    """Inline every ``*INCLUDE,INPUT=...`` into one self-contained ``.inp``.

    Resolution is relative to the directory of the file being walked, so nested
    includes resolve. Missing targets become a ``** [missing: ...]`` passthrough
    rather than a hard error (the writer emits placeholders for empty sections).
    ``max_depth`` caps recursion against a pathological self-referential chain.
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


def _find_writer_output(directory: pathlib.Path, name: str, target_ext: str) -> pathlib.Path | None:
    """Locate the deck file a writer dropped into ``directory``.

    Exact ``{name}{target_ext}`` first, then any sibling matching
    ``*{target_ext}`` case-insensitively — covering the Sesam writer's
    ``{name}T1.FEM`` (uppercase ext + ``T1`` super-element suffix).
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


def fem_deck_to_bytes(
    src_path: str | pathlib.Path,
    target_ext: str,
    on_progress: Callable[[str, float], None] | None = None,
) -> bytes:
    """Convert an FEA input deck to ``target_ext`` (``.inp``/``.fem``/``.med``)
    and return a single bytes blob. Raises ValueError for an unknown target."""

    def _progress(stage: str, frac: float) -> None:
        if on_progress is not None:
            on_progress(stage, frac)

    import ada  # lazy: keeps this module import-safe without the full ada surface

    target_ext = target_ext if target_ext.startswith(".") else f".{target_ext}"
    fmt = _FEM_TARGET_TO_FORMAT.get(target_ext.lower())
    if fmt is None:
        raise ValueError(f"no FEA writer for target {target_ext!r}")

    _progress("parsing", 0.15)
    assembly = ada.from_fem(str(src_path))
    _progress("writing", 0.6)

    out_dir = pathlib.Path(tempfile.mkdtemp(prefix="ada-deck-"))
    name = "model"
    try:
        assembly.to_fem(
            name,
            fem_format=fmt,
            scratch_dir=out_dir,
            overwrite=True,
            write_input_files_only=True,
        )
        deck = _find_writer_output(out_dir / name, name, target_ext) or _find_writer_output(out_dir, name, target_ext)
        if deck is None:
            raise ValueError(
                f"FEA writer ran but no {target_ext} appeared under {out_dir} "
                "— adapy writer layout may have changed."
            )
        _progress("ready", 1.0)
        if target_ext.lower() == ".inp":
            return _inline_abaqus_includes(deck)
        return deck.read_bytes()
    finally:
        import shutil

        shutil.rmtree(out_dir, ignore_errors=True)
