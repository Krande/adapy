"""BREP_WITH_VOIDS solids must be rendered, not silently dropped.

BREP_WITH_VOIDS is a MANIFOLD_SOLID_BREP subtype (a solid with internal void
shells). The streaming reader previously only recognised MANIFOLD_SOLID_BREP, so
these solids were skipped — losing real parts (flanges/washers with cavities).
"""

from __future__ import annotations

import re

import ada
from ada.cadit.step.read.stream_reader import _ROOT_BUILDERS, stream_read_step


def test_brep_with_voids_is_a_recognised_root():
    assert "BREP_WITH_VOIDS" in _ROOT_BUILDERS


def test_stream_reader_yields_brep_with_voids_solid(tmp_path):
    # A 1 m box written by OCC is a MANIFOLD_SOLID_BREP; retype it to
    # BREP_WITH_VOIDS('name', #shell, ()) so the reader must handle the subtype.
    src = tmp_path / "box.step"
    (ada.Assembly("m") / (ada.Part("p") / ada.PrimBox("bx", (0, 0, 0), (1, 1, 1)))).to_stp(src)
    txt = src.read_text()
    patched, n = re.subn(
        r"MANIFOLD_SOLID_BREP\((\s*'[^']*')\s*,\s*(#\d+)\s*\)",
        r"BREP_WITH_VOIDS(\1,\2,())",
        txt,
    )
    assert n == 1, "expected exactly one MANIFOLD_SOLID_BREP to retype"
    voids = tmp_path / "voids.step"
    voids.write_text(patched)

    geoms = list(stream_read_step(voids, local_pool=False, tolerant=True))
    assert len(geoms) == 1  # the solid is yielded, not dropped

    # and it builds to a real, correctly-sized box (~6 m^2 surface), not a sliver
    import numpy as np

    from ada.cad import active_backend
    from ada.cadit.diagnostics import _mesh_buffers, mesh_health

    b = active_backend()
    m = b.tessellate(b.build(geoms[0]))
    pos, idx = _mesh_buffers(m)
    h = mesh_health(np.asarray(pos).reshape(-1), np.asarray(idx).reshape(-1))
    assert h["area"] > 5.0  # unit cube ~6 m^2
