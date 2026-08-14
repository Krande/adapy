"""Echo procedural engine: a diagnostic external-engine stand-in.

Renders the document's spaces as raw boxes (the ``blueprint_name="none"`` path)
— no structural blueprint, no systems, no openings. Selecting it produces a
visibly-different model from the default engine, which makes it a cheap
end-to-end proof that engine selection resolved a ``module:callable`` entrypoint
and dispatched to it — on whichever path (server ``procedural_build`` worker or
in-browser Pyodide) ran the compile.

It is OCC-free (boxes only), so it runs in the browser as well as server-side.
Its signature matches the engine contract ``compile(doc, **options) -> bytes``.
"""

from __future__ import annotations

import json

__all__ = ["compile_doc"]


def compile_doc(doc: str | dict, name: str = "ProceduralModel", **_ignored) -> bytes:
    """Compile a procedural document to GLB bytes by rendering its cells as raw
    boxes. Extra options (``lod``, ``blueprint_name``, …) are accepted and
    ignored — the echo engine has a single behaviour."""
    from ada.topo_model.compile import compile_procedural_doc

    parsed = json.loads(doc) if isinstance(doc, str) else dict(doc)
    return compile_procedural_doc(parsed, name=name, blueprint_name="none")
