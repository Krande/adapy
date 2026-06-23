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


def test_brep_with_voids_reads_void_shell_faces(tmp_path):
    """The void shells (each an ORIENTED_CLOSED_SHELL -> CLOSED_SHELL in arg 2) must be read
    too, not just the outer shell — step2glb tessellates every ADVANCED_FACE incl. cavities.

    Regression for two fixes: (1) ORIENTED_CLOSED_SHELL must deref arg 2 (the real base shell),
    not arg 1 (the DERIVED cfs_faces field emitted as ``*`` -> 0 faces); (2) _b_brep_with_voids
    must merge the void faces into the solid. A box with a fully-internal box cut out is a real
    BREP_WITH_VOIDS: 6 outer faces + 6 void faces = 12.
    """
    src = tmp_path / "voidbox.step"
    outer = ada.PrimBox("o", (0, 0, 0), (10, 10, 10))
    outer.add_boolean(ada.Boolean(ada.PrimBox("i", (3, 3, 3), (7, 7, 7))))  # internal cavity
    (ada.Assembly("a") / (ada.Part("p") / outer)).to_stp(src)
    assert src.read_text().count("BREP_WITH_VOIDS") == 1, "expected an internal-void solid"

    geoms = list(stream_read_step(src, local_pool=False, tolerant=True))
    assert len(geoms) == 1
    # 12 = 6 outer + 6 void. Before the fix the void shell resolved to a faceless '*' sentinel
    # and only the 6 outer faces were read.
    assert len(geoms[0].geometry.cfs_faces) == 12
