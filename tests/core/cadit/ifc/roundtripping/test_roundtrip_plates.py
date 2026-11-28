import ada


def test_roundtrip_plate(tmp_path):
    plate = ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3)

    fp = (ada.Assembly() / (ada.Part("MyPart") / plate)).to_ifc(tmp_path / "plate1.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    pl: ada.Plate = a.get_by_name("MyPlate")

    assert isinstance(pl, ada.Plate)
    assert pl.parent.name == "MyPart"
    # geometry preserved: thickness + the 4 distinct polygon corners (the reader may emit
    # duplicate consecutive points when closing the loop, so dedup before counting)
    assert pl.t == 20e-3
    corners = list(dict.fromkeys(tuple(round(c, 6) for c in p) for p in pl.poly.points2d))
    assert len(corners) == 4

    # still a valid, meshable plate after the round-trip
    pl.parent.fem = pl.to_fem_obj(0.1, "shell")


def test_roundtrip_plate_with_fillet(tmp_path):
    # 3rd coord on a corner = fillet radius; the arc survives the round-trip (extra edge point).
    plate = ada.Plate("FilletPlate", [(0, 0), (1, 0, 0.2), (1, 1), (0, 1)], 20e-3)
    fp = (ada.Assembly() / (ada.Part("MyPart") / plate)).to_ifc(tmp_path / "plate2.ifc", file_obj_only=True)

    pl: ada.Plate = ada.from_ifc(fp).get_by_name("FilletPlate")
    assert isinstance(pl, ada.Plate)
    assert pl.t == 20e-3


def _segments(pl) -> list[tuple]:
    import numpy as np

    out = [
        (
            type(s).__name__,
            tuple(np.round(s.p1, 6)[:2]),
            tuple(np.round(s.p2, 6)[:2]),
            round(float(getattr(s, "radius", None) or 0), 6),
        )
        for s in pl.poly.segments3d
    ]
    # The reader may start the loop elsewhere: compare from the lowest start point on.
    k = out.index(min(out, key=lambda s: s[1]))
    return out[k:] + out[:k]


def _fillet_plates():
    import pytest

    return [
        pytest.param([(0, 0), (1, 0, 0.2), (1, 1), (0, 1)], id="fillet"),
        # Each radius takes up a whole 0.25 m edge, so no straight part of that edge is left.
        pytest.param(
            [(0.0, 0.5), (0.0, -0.5), (-0.03, -0.5), (-0.03, -0.25, 0.25), (-0.35, -0.25), (-0.35, 0.25)]
            + [(-0.03, 0.25, 0.25), (-0.03, 0.5)],
            id="radius-takes-a-whole-edge",
        ),
    ]


import pytest  # noqa: E402


@pytest.mark.parametrize("points", _fillet_plates())
@pytest.mark.parametrize("reader", ["ifcopenshell", "native"])
def test_roundtrip_plate_fillets_keep_their_outline(tmp_path, points, reader):
    from ada.cadit.ifc.read.native_reader import native_adacpp_ifc_available

    if reader == "native" and not native_adacpp_ifc_available():
        pytest.skip("adacpp without IfcNgeomStream")
    plate = ada.Plate("pl", points, 20e-3)
    fp = tmp_path / "fillets.ifc"
    (ada.Assembly() / (ada.Part("p") / plate)).to_ifc(fp, streaming=False)
    back = next(iter(ada.from_ifc(fp, reader=reader).get_all_physical_objects()))
    if reader == "native" and not isinstance(back, ada.Plate):
        pytest.skip("this adacpp's native reader does not rebuild plates")
    assert _segments(back) == _segments(plate)
