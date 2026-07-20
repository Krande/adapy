"""Regression: SAT/Genie curved plates that already mesh must NOT be ShapeFix-healed.

The rational B-spline p-curve heal (for STEP-stream faces that render blank) re-derives
p-curves and slightly perturbs a face that already meshes — which twisted SAT curved
plates carrying authored p-curves so they no longer fit their neighbours. The heal is now
gated on a trial mesh: a plate that already tessellates is left untouched.
"""

from __future__ import annotations

import pytest

pytest.importorskip("ada.occ.geom.surfaces")  # OCC-backend internals; skip where OCC isn't installed

import glob

import numpy as np

import ada
from ada.cad import active_backend
from ada.occ.geom.surfaces import consume_param_rebuild_stats


def test_genie_curved_plates_build_without_healing(example_files, monkeypatch):
    # This regression guards the BARE-FACE path: authored p-curves on the modeled surface must not
    # be perturbed by the rational ShapeFix heal. The default thickened solid adds ruled side
    # ribbons whose (legitimately) healed p-curves are not what this test is about — pin the config
    # off so solid_geom() returns the bare face this test was written against.
    monkeypatch.setenv("ADA_GEOM_THICKEN_CURVED_SHELLS", "false")

    xml = (example_files / "fem_files/sesam/curved_plates.xml").resolve()
    if not xml.exists():  # fall back to a recursive search when the fixture layout differs
        hits = glob.glob("**/fem_files/sesam/curved_plates.xml", recursive=True)
        if not hits:
            import pytest

            pytest.skip("curved_plates.xml fixture not found")
        xml = hits[0]

    asm = ada.from_genie_xml(str(xml))
    backend = active_backend()
    consume_param_rebuild_stats()  # reset

    n_plates = 0
    for obj in asm.get_all_physical_objects():
        if type(obj).__name__ == "PlateCurved":
            n_plates += 1
            mesh = backend.tessellate(backend.build(obj.solid_geom()), -1.0)
            assert len(np.asarray(mesh.faces).reshape(-1, 3)) > 0, f"{obj.name} tessellated to nothing"

    assert n_plates >= 1
    stats = consume_param_rebuild_stats()
    # a plate that meshes must not be perturbed by the rational ShapeFix heal
    assert stats.get("rational_bspline_healed", 0) == 0
