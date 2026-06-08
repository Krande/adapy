"""Regression: the FEM-concepts mass *overlay* glyph must sit exactly where the mass *geometry*
is rendered. build_mass_glyphs used the raw MassPoint cog while PrimSphere.solid_geom renders the
sphere at cog + placement.get_absolute_placement(include_rotations=False).origin, so any mass with
a non-identity placement (e.g. parametric_models positions equipment via placement) drew the amber
overlay offset from the actual mass point.
"""

from ada import Part, Placement
from ada.api.mass import MassPoint
from ada.extension.fem_concepts_builder import build_mass_glyphs


def _coords(v):
    # MassGlyph.position is a pydantic Vec3 (RootModel); geometry.center is a Point. Normalise
    # both to a plain list of floats.
    root = getattr(v, "root", v)
    return [float(x) for x in root]


def _approx(a, b, tol=1e-9):
    a, b = _coords(a), _coords(b)
    return len(a) == len(b) and all(abs(x - y) <= tol for x, y in zip(a, b))


def _sphere_center(mp: MassPoint):
    return _coords(mp.solid_geom().geometry.center)


def test_mass_glyph_matches_geometry_identity_placement():
    p = Part("P")
    p._masses.append(MassPoint("m", (1.0, 2.0, 3.0), 50.0))

    (glyph,) = build_mass_glyphs(p)
    assert _approx(glyph.position, _sphere_center(p.masses[0]))
    assert _approx(glyph.position, [1.0, 2.0, 3.0])


def test_mass_glyph_matches_geometry_translated_placement():
    p = Part("P")
    p._masses.append(MassPoint("m", (1.0, 2.0, 3.0), 50.0, placement=Placement(origin=(10.0, 20.0, 30.0))))

    (glyph,) = build_mass_glyphs(p)
    # Overlay must land on the rendered sphere, i.e. cog shifted by the placement origin —
    # NOT the raw cog (the bug).
    assert _approx(glyph.position, _sphere_center(p.masses[0]))
    assert _approx(glyph.position, [11.0, 22.0, 33.0])
    assert not _approx(glyph.position, [1.0, 2.0, 3.0])
