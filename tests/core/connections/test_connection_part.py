import pytest

from ada import Beam, Plate, Weld
from ada.api.connections import Connection
from ada.api.spatial.part import Part


def test_connection_is_part_subclass():
    assert issubclass(Connection, Part)


def test_connection_default_init():
    conn = Connection("c1")
    assert isinstance(conn, Part)
    assert conn.name == "c1"
    assert conn.spec_name is None
    assert conn.spec_inputs is None


def test_connection_with_lineage_attrs():
    conn = Connection("c2", spec_name="box.box_to_box", spec_inputs={"angle_deg": 90.0})
    assert conn.spec_name == "box.box_to_box"
    assert conn.spec_inputs == {"angle_deg": 90.0}


def test_connection_accepts_part_kwargs():
    conn = Connection("c3", color=(0.1, 0.2, 0.3))
    assert conn.color is not None


def test_connection_holds_beams_plates_welds():
    conn = Connection("c4")
    bm1 = Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300")
    bm2 = Beam("b2", (1, 0, 0), (1, 0, 1), "IPE300")
    pl = Plate("stiff", [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)], t=0.01)
    weld = Weld(
        "w1",
        p1=(0, 0, 0),
        p2=(0, 0, 1),
        weld_type="FILLET",
        members=[bm1, bm2],
        profile=[(0, 0), (-0.005, 0), (0, 0.005)],
        xdir=(1, 0, 0),
    )

    conn.add_beam(bm1)
    conn.add_beam(bm2)
    conn.add_plate(pl)
    conn.add_weld(weld)

    assert len(conn.beams) == 2
    assert len(conn.plates) == 1
    assert len(conn.welds) == 1
    assert conn.welds[0].name == "w1"


def test_connection_can_be_added_to_assembly():
    from ada import Assembly

    conn = Connection("nested", spec_name="test.box_to_box")
    a = Assembly("root") / conn
    assert "nested" in a.parts
    assert a.parts["nested"].spec_name == "test.box_to_box"
