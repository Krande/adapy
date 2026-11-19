"""A weld whose bead is a solid of its own (no profile swept along its line) writes to IFC as that solid and reads
back with it.

A bead built station by station -- a fillet whose section changes along its line, as round a tubular brace -- is a
faceted B-rep; the weld's profile and line still describe its nominal section, but there is no sweep to write."""

import numpy as np
import pytest

import ada
from ada.api.curves import CurveOpen3d
from ada.api.fasteners import WeldBead
from ada.cad import active_backend
from ada.geom import Geometry
from ada.geom.curves import PolyLoop
from ada.geom.points import Point
from ada.geom.solids import FacetedBrep
from ada.geom.surfaces import ClosedShell, Face, FaceBound

LEG = 0.01
LENGTH = 0.4
N_STATIONS = 9


def _fillet_bead(leg: float, length: float, n: int) -> FacetedBrep:
    """A right-angled fillet ``leg`` x ``leg`` along x from 0 to ``length``, as triangles between ``n`` stations."""
    xs = np.linspace(0.0, length, n)
    st = np.array([[(x, 0.0, 0.0), (x, leg, 0.0), (x, 0.0, leg)] for x in xs])
    centre = st.reshape(-1, 3).mean(axis=0)
    tris = []
    for i in range(n - 1):
        for a in range(3):
            b = (a + 1) % 3
            quad = [st[i][a], st[i][b], st[i + 1][b], st[i + 1][a]]
            tris += [[quad[0], quad[1], quad[2]], [quad[0], quad[2], quad[3]]]
    tris += [[st[0][0], st[0][2], st[0][1]], [st[-1][0], st[-1][1], st[-1][2]]]
    faces = []
    for tri in tris:  # outward
        n_ = np.cross(tri[1] - tri[0], tri[2] - tri[0])
        if np.dot(n_, np.mean(tri, axis=0) - centre) < 0:
            tri = tri[::-1]
        faces.append(Face([FaceBound(PolyLoop([Point(*p) for p in tri]), True)]))
    return FacetedBrep(ClosedShell(faces))


def _line():
    return CurveOpen3d([(float(x), 0.0, 0.0) for x in np.linspace(0.0, LENGTH, N_STATIONS)])


class _SubclassedBeadWeld(ada.Weld):
    """A weld subclass whose ``geometry`` is its own bead Shape (how an application may build one)."""

    def __init__(self, *args, solid, **kwargs):
        super().__init__(*args, **kwargs)
        self._own_bead = WeldBead(f"{self.name}_own_bead", geom=Geometry("bead-geom-0001", solid), parent=self)

    @property
    def geometry(self):
        return self._own_bead

    def solid_geom(self):
        return self._own_bead.solid_geom()


def _bead_weld(kind: str):
    common = dict(weld_type="FILLET", profile=[(0, 0), (LEG, 0), (0, LEG)], xdir=(0, 1, 0), sweep_curve=_line())
    if kind == "bead":
        return ada.Weld("w1", bead=_fillet_bead(LEG, LENGTH, N_STATIONS), **common)
    return _SubclassedBeadWeld("w1", solid=_fillet_bead(LEG, LENGTH, N_STATIONS), **common)


@pytest.mark.parametrize("kind", ["bead", "subclass"])
def test_a_weld_with_a_bead_solid_writes_to_ifc_and_reads_back_with_it(tmp_path, kind):
    import ifcopenshell.geom

    weld = _bead_weld(kind)
    assert not isinstance(weld.geometry, (ada.PrimSweep, ada.PrimExtrude))
    expected = 0.5 * LEG * LEG * LENGTH

    a = ada.Assembly() / (ada.Part("P") / weld)
    fp = a.to_ifc(tmp_path / "bead_weld.ifc", file_obj_only=True)

    fasteners = fp.by_type("IfcFastener")
    assert len(fasteners) == 1
    (body,) = [r for r in fasteners[0].Representation.Representations if r.RepresentationIdentifier == "Body"]
    assert [item.is_a() for item in body.Items] == ["IfcFacetedBrep"]

    # The written body is the bead: its volume, as the IFC geometry kernel builds it.
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    mesh = ifcopenshell.geom.create_shape(settings, fasteners[0]).geometry
    v = np.asarray(mesh.verts, dtype=float).reshape(-1, 3)
    t = np.asarray(mesh.faces, dtype=int).reshape(-1, 3)
    kernel_volume = abs(np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum()) / 6.0
    assert kernel_volume == pytest.approx(expected, rel=1e-6)

    # Read back: one weld, whose bead is the solid written (not the profile rebuilt along a chord).
    b = ada.from_ifc(fp)
    welds = [w for p in b.get_all_parts_in_assembly() for w in p.welds]
    assert len(welds) == 1
    got = welds[0]
    assert isinstance(got, ada.Weld) and got.name == "w1"
    assert got.guid == weld.guid
    assert not isinstance(got.geometry, (ada.PrimSweep, ada.PrimExtrude))
    assert len(got.sweep_curve.points3d) == N_STATIONS
    assert active_backend().volume(got.geometry.solid_occ()) == pytest.approx(expected, rel=1e-6)


def test_a_bead_shape_placed_off_the_world_origin_is_written_and_read_where_it_is(tmp_path):
    import ifcopenshell.geom

    bead = ada.Shape("bead", geom=Geometry("bead-geom-0002", _fillet_bead(LEG, LENGTH, N_STATIONS)))
    bead.placement = ada.Placement((0.0, 0.0, 1.0))
    weld = ada.Weld(
        "w1", weld_type="FILLET", profile=[(0, 0), (LEG, 0), (0, LEG)], xdir=(0, 1, 0), sweep_curve=_line(), bead=bead
    )
    fp = (ada.Assembly() / (ada.Part("P") / weld)).to_ifc(tmp_path / "placed_bead.ifc", file_obj_only=True)

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    v = np.asarray(ifcopenshell.geom.create_shape(settings, fp.by_type("IfcFastener")[0]).geometry.verts)
    z = v.reshape(-1, 3)[:, 2]
    assert z.min() == pytest.approx(1.0) and z.max() == pytest.approx(1.0 + LEG)

    got = ada.from_ifc(fp).get_by_name("w1")
    assert tuple(float(c) for c in got.geometry.placement.origin) == pytest.approx((0.0, 0.0, 1.0))


def test_a_weld_swept_along_its_line_is_still_written_as_the_sweep(tmp_path):
    weld = ada.Weld("w1", weld_type="FILLET", profile=[(0, 0), (LEG, 0), (0, LEG)], xdir=(0, 1, 0), sweep_curve=_line())
    fp = (ada.Assembly() / (ada.Part("P") / weld)).to_ifc(tmp_path / "swept_weld.ifc", file_obj_only=True)
    (body,) = [
        r for r in fp.by_type("IfcFastener")[0].Representation.Representations if r.RepresentationIdentifier == "Body"
    ]
    assert body.RepresentationType == "AdvancedSweptSolid"
