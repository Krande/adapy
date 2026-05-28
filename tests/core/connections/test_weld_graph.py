import pytest

from ada import Assembly, Beam, Plate, Weld
from ada.api.connections import Connection


def _fillet(name, p1, p2, members, xdir=(0, 1, 0)):
    return Weld(
        name,
        p1=p1,
        p2=p2,
        weld_type="FILLET",
        members=members,
        profile=[(0, 0), (-0.005, 0), (0, 0.005)],
        xdir=xdir,
    )


@pytest.fixture
def simple_join():
    """Two beams + plate joined by two welds, all inside one assembly."""
    bm_a = Beam("a", (0, 0, 0), (1, 0, 0), "IPE300")
    bm_b = Beam("b", (1, 0, 0), (1, 0, 1), "IPE300")
    pl = Plate("stiff", [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)], t=0.01)
    a = Assembly("root") / [bm_a, bm_b, pl]
    w1 = _fillet("w_ab", (1, 0, 0), (1, 0, 1), [bm_a, bm_b])
    w2 = _fillet("w_ap", (0, 0, 0), (0.1, 0, 0), [bm_a, pl], xdir=(0, 0, 1))
    a.add_weld(w1)
    a.add_weld(w2)
    return a, bm_a, bm_b, pl, w1, w2


def test_weld_other_members_excludes_self(simple_join):
    _, bm_a, bm_b, _, w1, _ = simple_join
    others = w1.other_members(of=bm_a)
    assert others == [bm_b]


def test_weld_members_is_immutable_tuple(simple_join):
    _, _, _, _, w1, _ = simple_join
    assert isinstance(w1.members, tuple)
    with pytest.raises(TypeError):
        w1.members[0] = None  # type: ignore[index]


def test_beam_welds_returns_attached_welds(simple_join):
    _, bm_a, _, _, w1, w2 = simple_join
    welds = bm_a.welds
    assert set(w.name for w in welds) == {"w_ab", "w_ap"}


def test_plate_welds_returns_single_weld(simple_join):
    _, _, _, pl, _, w2 = simple_join
    assert [w.name for w in pl.welds] == ["w_ap"]


def test_connected_members_dedup_and_excludes_self(simple_join):
    _, bm_a, bm_b, pl, _, _ = simple_join
    partners = bm_a.connected_members()
    assert len(partners) == 2
    partner_names = {p.name for p in partners}
    assert partner_names == {"b", "stiff"}


def test_connected_members_other_side(simple_join):
    _, _, bm_b, _, _, _ = simple_join
    partners = bm_b.connected_members()
    assert [p.name for p in partners] == ["a"]


def test_connected_members_filter_by_weld_type(simple_join):
    from ada.api.fasteners import WeldType

    _, bm_a, _, _, _, _ = simple_join
    # Both welds are FILLET; filter on a non-fillet type returns none.
    assert bm_a.connected_members(weld_type=WeldType.J_GROOVE_J_BUTT) == []
    # Filter on FILLET returns both partners.
    partners = bm_a.connected_members(weld_type=WeldType.FILLET)
    assert {p.name for p in partners} == {"b", "stiff"}


def test_weld_member_validation():
    bm = Beam("b1", (0, 0, 0), (1, 0, 0), "IPE300")
    with pytest.raises(TypeError, match="BackendGeom"):
        _fillet("bad", (0, 0, 0), (0, 0, 1), [bm, "not-a-backend-geom"])


def test_cache_invalidates_on_add_weld(simple_join):
    a, bm_a, _, pl, _, _ = simple_join
    before = bm_a.welds
    assert len(before) == 2

    bm_c = Beam("c", (2, 0, 0), (2, 0, 1), "IPE300")
    a.add_beam(bm_c)
    w3 = _fillet("w_ac", (0.5, 0, 0), (0.5, 0, 1), [bm_a, bm_c])
    a.add_weld(w3)

    after = bm_a.welds
    assert len(after) == 3
    assert "w_ac" in {w.name for w in after}


def test_welds_inside_connection_subpart_are_visible_from_root():
    bm_a = Beam("a", (0, 0, 0), (1, 0, 0), "IPE300")
    bm_b = Beam("b", (1, 0, 0), (1, 0, 1), "IPE300")
    a = Assembly("root") / [bm_a, bm_b]

    conn = Connection("c1")
    a.add_part(conn)
    w = _fillet("nested_w", (1, 0, 0), (1, 0, 1), [bm_a, bm_b])
    conn.add_weld(w)

    assert [x.name for x in bm_a.welds] == ["nested_w"]
    assert [p.name for p in bm_a.connected_members()] == ["b"]


def test_unattached_member_returns_empty_welds():
    bm = Beam("orphan", (0, 0, 0), (1, 0, 0), "IPE300")
    assert bm.welds == []
    assert bm.connected_members() == []
