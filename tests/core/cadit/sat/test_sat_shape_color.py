"""SAT-imported shapes must carry a renderable colour, not render black.

``from_acis`` builds ``Shape(f"shape{i}", Geometry(i, geom))`` with no colour, so
the Geometry's ``color`` is None. The tessellator reads ``geom.color`` (not the
Shape's), so without a fallback the body renders black. ``Shape.solid_geom``
now syncs the Shape's colour (light-gray by default) onto the Geometry.
"""

import ada


def test_sat_shape_solid_geom_has_color(example_files):
    a = ada.from_acis(example_files / "sat_files/curved_plate.sat")
    shapes = list(a.get_all_physical_objects())
    assert shapes, "no shapes imported"
    for s in shapes:
        assert s.color is not None
        g = s.solid_geom()
        # the Geometry the tessellator meshes must carry the colour too
        assert g.color is not None, f"{s.name}: solid_geom colour is None (would render black)"
        assert tuple(g.color) == tuple(s.color)
