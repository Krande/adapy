"""Streaming STEP → Genie XML.

Raw CAD B-rep solids have no Genie-XML concept representation — GXML models
structural *concepts* (beams / plates / equipment), not arbitrary B-rep — so the
XML for a raw STEP is the empty structural scaffold regardless of the file's
content (confirmed: a 5-solid assembly emits ~1.5 KB of scaffold, zero structures).

So this writer does NOT materialise the whole model the way ``ada.from_step`` →
``to_genie_xml`` did (the full-OCC / pure-Python-tokenizer load that OOM'd / timed
out the multi-GB assemblies — e.g. the Munin crane — for an *empty* output). It
stream-PARSES the STEP one solid at a time to validate that it reads (bounded
memory, native C++ parser, **no** per-solid ada.geom hydrate since the geometry is
discarded), then emits the scaffold.
"""

from __future__ import annotations

import pathlib
from typing import Callable

from ada.config import logger

ProgressFn = Callable[[str, float], None]


def _count_solids_streaming(src_path, prog: ProgressFn) -> int:
    """Stream-parse the STEP, counting solids, without hydrating ada.geom.

    Native path: iterate ``adacpp.cad.StepNgeomStream`` and drop each NGEOM buffer
    un-deserialised — the C++ parse alone, no Python object churn. Fallback: the
    pure-Python streaming reader (tolerant), which does hydrate but stays bounded."""
    from ada.cadit.step.read.native_reader import native_adacpp_step_available

    if native_adacpp_step_available():
        import adacpp

        total = 0
        for _nbytes, _meta in adacpp.cad.StepNgeomStream(str(src_path)):
            total += 1
            if total % 2000 == 0:
                prog(f"reading-step {total}", 0.1 + 0.75 * min(0.99, total / 10000.0))
        return total

    import ada

    return sum(1 for _ in ada.iter_from_step(src_path, reader="tolerant"))


def stream_step_to_xml(
    src_path: str | pathlib.Path,
    out_path: str | pathlib.Path,
    *,
    on_progress: ProgressFn | None = None,
) -> dict:
    """Stream a STEP file to a (scaffold-only) Genie XML. Returns
    ``{total, emitted, skipped}`` (``emitted`` is always 0 — raw B-rep carries no
    GXML concept; ``total`` is the parsed solid count)."""
    import ada

    prog = on_progress or (lambda *_: None)
    prog("reading-step", 0.1)
    total = _count_solids_streaming(src_path, prog)

    prog("writing-xml", 0.9)
    ada.Assembly("StepImport").to_genie_xml(destination_xml=str(out_path))
    logger.info(
        "stream STEP->XML: parsed %d solids; emitted scaffold only (raw B-rep has no Genie-XML concept)",
        total,
    )
    prog("ready", 1.0)
    return {"total": total, "emitted": 0, "skipped": total}
