"""A test-only procedural engine: the document's cells as raw boxes.

Not shipped with adapy. Tests that exercise engine selection (resolve a slug or a
``module:callable`` entrypoint, then dispatch to a NON-default engine) register it
in-process through the ``raw_box_engine`` fixture (``tests/conftest.py``), exactly
as a tool's package registers its own engine at import. Its output (the
``blueprint_name="none"`` render) differs visibly from the default engine's, so a
test can tell which engine compiled the model.
"""

from __future__ import annotations

import json

SLUG = "raw-box"
ENTRYPOINT = "tests.procedural_engine_fixture:compile_doc"

__all__ = ["SLUG", "ENTRYPOINT", "compile_doc"]


def compile_doc(doc: str | dict, name: str = "ProceduralModel", **_ignored) -> bytes:
    """The engine contract ``compile(doc, **options) -> bytes``: the cells as boxes.
    Other options (``lod``, ``source_xlsx``, ...) are accepted and ignored."""
    from ada.topo_model.compile import compile_procedural_doc

    parsed = json.loads(doc) if isinstance(doc, str) else dict(doc)
    return compile_procedural_doc(parsed, name=name, blueprint_name="none")
