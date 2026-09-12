"""Converter handler families for the hosted viewer, all registered into
:class:`ConverterRegistry`. Importing this package populates the registry in the order the
original module did: the ``@converter``-decorated FEA deck pairs, then the programmatic
families (later registrations override earlier ones for the same pair — the STEP stream
exports deliberately replace the OCC registrations for STEP sources).
"""

from __future__ import annotations

from . import (  # noqa: F401
    ada_export,
    ada_pairs,
    fea,
    keys,
    mesh_step,
    pipelines,
    serializers,
    step_stream,
)
from .registry import (
    ConverterFn,
    ConverterRegistry,
    ProgressFn,
    UnsupportedFormat,
    converter,
)

ada_pairs._register_passthrough_glb()
ada_pairs._register_trimesh_to_glb()
ada_pairs._register_ada_loadable()
fea._register_fea_result_to_glb()
mesh_step._register_glb_to_mesh()
step_stream._register_step_stream_exports()

__all__ = ["ConverterFn", "ConverterRegistry", "ProgressFn", "UnsupportedFormat", "converter"]
