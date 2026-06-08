"""Bundle a multi-file Abaqus deck into a single self-contained ``.inp``.

The Abaqus writer emits a main ``<name>.inp`` that ``*INCLUDE``s sibling files
(``bulk_<part>/aba_bulk.inp`` for mesh data, ``core_input_files/<...>.inp`` for the analysis
sections). That layout runs fine, but a lone ``<name>.inp`` copied elsewhere can't resolve the
relative includes. Bundling inlines every include into the main file so it stands alone — the
default, so ``Assembly.to_fem(..., "abaqus")`` produces one portable deck.
"""

from __future__ import annotations

import pathlib
import re
import shutil

_INCLUDE_RE = re.compile(r"^\s*\*INCLUDE\s*,\s*INPUT\s*=\s*(.+?)\s*$", re.IGNORECASE)


def inline_includes(top_inp: pathlib.Path, max_depth: int = 4) -> str:
    """Return the contents of ``top_inp`` with every ``*INCLUDE,INPUT=...`` recursively inlined.

    Include paths resolve relative to the file currently being walked (so nested includes work).
    A missing target becomes a ``**`` comment rather than a hard error — the writer emits
    placeholder includes for empty sections. ``max_depth`` guards against a pathological cycle.
    """

    def _walk(path: pathlib.Path, depth: int) -> str:
        if depth > max_depth:
            return f"** [bundle: include depth cap reached at {path.name}]\n"
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")

        out: list[str] = []
        for line in text.splitlines(keepends=True):
            m = _INCLUDE_RE.match(line.rstrip("\r\n"))
            if not m:
                out.append(line)
                continue
            inc_rel = m.group(1).replace("\\", "/").strip().strip('"')
            inc_path = (path.parent / inc_rel).resolve()
            if not inc_path.is_file():
                out.append(f"** [bundle: missing include {inc_rel}]\n")
                continue
            out.append(f"** --- inlined: {inc_rel} ---\n")
            chunk = _walk(inc_path, depth + 1)
            out.append(chunk)
            if not chunk.endswith("\n"):
                out.append("\n")
        return "".join(out)

    return _walk(top_inp, 0)


def bundle_deck(analysis_dir: pathlib.Path, name: str) -> None:
    """Rewrite ``<analysis_dir>/<name>.inp`` as a single self-contained deck and remove the now
    redundant ``bulk_*/`` and ``core_input_files/`` sub-trees."""
    top = pathlib.Path(analysis_dir) / f"{name}.inp"
    if not top.is_file():
        return
    bundled = inline_includes(top)
    top.write_text(bundled, encoding="utf-8")

    core = pathlib.Path(analysis_dir) / "core_input_files"
    if core.is_dir():
        shutil.rmtree(core, ignore_errors=True)
    for sub in pathlib.Path(analysis_dir).glob("bulk_*"):
        if sub.is_dir():
            shutil.rmtree(sub, ignore_errors=True)
