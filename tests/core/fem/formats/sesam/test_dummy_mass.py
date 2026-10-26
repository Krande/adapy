"""GeniE dummy-mass beams: found by their arrangement, set apart by role, drawn on their own axis.

GeniE hangs an equipment's nodal mass (at its COG) off the deck with a beam from each
footprint node. Each beam's eccentricity at the COG end moves that element end back to
just below its footprint node, so in Sesam the beam is a short vertical stub and the
offset is a rigid link. Built on the node-to-node axis instead, it drew as a long,
tilted tube towards the COG, and the raw node lines drew a web into one point.
"""

import numpy as np
import pytest

from ada.fem import FemSet
from ada.fem.formats.sesam.results.dummy_mass import dummy_mass_elements
from ada.fem.formats.sesam.results.sets import role_groups

COG = np.array([5.0, 5.0, 9.0])
FOOTPRINT = [np.array(p, dtype=float) for p in ((0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10))]


def _footprint():
    """Four dummies from footprint nodes 1..4 to COG node 10, each a 1 m stub down."""
    coords = {i + 1: p for i, p in enumerate(FOOTPRINT)}
    coords[10] = COG
    elements, ecc = [], {}
    for i, p in enumerate(FOOTPRINT):
        elem_id = 100 + i
        elements.append((elem_id, True, [i + 1, 10]))
        # From the COG node to the element end, 1 m below the footprint node.
        ecc[elem_id] = [None, (p - np.array([0, 0, 1.0])) - COG]
    return elements, coords, ecc


def test_a_footprint_hung_off_one_node_is_dummy_mass():
    elements, coords, ecc = _footprint()
    assert dummy_mass_elements(elements, coords, ecc) == [100, 101, 102, 103]


def test_an_eccentric_joint_whose_ends_meet_is_structure():
    # Three members framing into node 10, all offset to the same centre-line point.
    elements, coords, _ = _footprint()
    elements = elements[:3]
    ecc = {e: [None, np.array([0.0, 0.0, 0.3])] for e, _, _ in elements}
    assert dummy_mass_elements(elements, coords, ecc) == []


def test_a_node_a_shell_also_touches_is_not_a_cog():
    elements, coords, ecc = _footprint()
    elements.append((200, False, [10, 1, 2]))
    assert dummy_mass_elements(elements, coords, ecc) == []


def test_two_beams_are_not_a_footprint():
    elements, coords, ecc = _footprint()
    assert dummy_mass_elements(elements[:2], coords, ecc) == []


def test_role_groups_carry_the_role_and_shown_numbers():
    class _Mesh:
        sets = {
            "Dummy masses": FemSet("Dummy masses", [100, 101], set_type="elset", role=FemSet.ROLES.DUMMY_MASS),
            "deck": FemSet("deck", [1, 2], set_type="elset"),
        }

    assert role_groups(_Mesh(), {100: 9100}) == [
        {"name": "Dummy masses", "members": ["EL9100", "EL101"], "fe_object_type": "element", "role": "dummy_mass"}
    ]


def test_an_unknown_role_is_refused():
    with pytest.raises(ValueError):
        FemSet("x", [1], set_type="elset", role="ballast")


def test_solid_beam_runs_between_the_element_ends_not_the_nodes():
    from ada import Material, Section
    from ada.api.beams.geom_beams import straight_beam_frame
    from ada.fem.results.artefacts import FEAResultStreamAdapter, beam_compact
    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEAResult,
        FEATypes,
        FemNodes,
        Mesh,
    )
    from ada.fem.shapes.definitions import LineShapes

    elements, coords, ecc = _footprint()
    node_ids = sorted(coords)
    mesh = Mesh(
        elements=[
            ElementBlock(
                elem_info=ElementInfo(type=LineShapes.LINE, source_software=FEATypes.SESAM, source_type=15),
                node_refs=np.array([n for _, _, n in elements], dtype=int),
                identifiers=np.array([e for e, _, _ in elements], dtype=int),
            )
        ],
        nodes=FemNodes(coords=np.array([coords[i] for i in node_ids]), identifiers=np.array(node_ids)),
        sections={1: Section("Dummy_EQbox", sec_type="PIPE", r=0.05, wt=0.01)},
        materials={1: Material("DummyMatEQ")},
        # GUNIVEC: local z along global x, perpendicular to the true (vertical) axis
        # and NOT to the node-to-node one.
        vectors={1: [1.0, 0.0, 0.0]},
        elem_data=np.array([[e, 1, 1, 1] for e, _, _ in elements], dtype=float),
        eccentricities=ecc,
    )
    adapter = FEAResultStreamAdapter(FEAResult("r", FEATypes.SESAM, [], mesh))

    seen = {}

    def _capture(beams, **_kw):
        seen.update({label: beam for beam, label, *_ in beams})

    original = beam_compact.collect_beam_solid_instances
    beam_compact.collect_beam_solid_instances = _capture
    try:
        adapter.try_solid_beams(format="compact")
    finally:
        beam_compact.collect_beam_solid_instances = original

    assert sorted(seen) == [100, 101, 102, 103]
    for i, p in enumerate(FOOTPRINT):
        frame = straight_beam_frame(seen[100 + i])
        np.testing.assert_allclose(frame.origin, p, atol=1e-9)
        np.testing.assert_allclose(frame.xvec, [0.0, 0.0, -1.0], atol=1e-9)
        assert frame.length == pytest.approx(1.0)


def _footprint_result():
    from ada import Material, Section
    from ada.fem.results.common import (
        ElementBlock,
        ElementInfo,
        FEAResult,
        FEATypes,
        FemNodes,
        Mesh,
    )
    from ada.fem.shapes.definitions import LineShapes

    elements, coords, ecc = _footprint()
    node_ids = sorted(coords)
    members = [e for e, _, _ in elements]
    mesh = Mesh(
        elements=[
            ElementBlock(
                elem_info=ElementInfo(type=LineShapes.LINE, source_software=FEATypes.SESAM, source_type=15),
                node_refs=np.array([n for _, _, n in elements], dtype=int),
                identifiers=np.array(members, dtype=int),
            )
        ],
        nodes=FemNodes(coords=np.array([coords[i] for i in node_ids]), identifiers=np.array(node_ids)),
        sections={7: Section("Dummy_EQbox", sec_type="PIPE", r=0.05, wt=0.01)},
        materials={3: Material("DummyMatEQ")},
        vectors={1: [1.0, 0.0, 0.0]},
        elem_data=np.array([[e, 3, 7, 1] for e in members], dtype=float),
        eccentricities=ecc,
        sets={"Dummy masses": FemSet("Dummy masses", members, set_type="elset", role=FemSet.ROLES.DUMMY_MASS)},
    )
    return FEAResult("r", FEATypes.SESAM, [], mesh)


def test_dummy_mass_node_lines_are_left_out_of_the_edge_sidecars():
    from ada.fem.results.artefacts import FEAResultStreamAdapter
    from ada.fem.results.artefacts.mesh import _without_pairs

    adapter = FEAResultStreamAdapter(_footprint_result())
    drop = adapter.try_suppressed_line_edges()
    # Footprint nodes 1..4 are rows 0..3, the COG node 10 is row 4.
    assert sorted(map(tuple, drop.tolist())) == [(0, 4), (1, 4), (2, 4), (3, 4)]
    edges = np.array([[0, 1], [0, 4], [3, 4]], dtype=np.uint32)
    assert _without_pairs(edges, drop).tolist() == [[0, 1]]
    assert [g["role"] for g in adapter.try_groups()] == ["dummy_mass"]


def test_element_properties_round_trip_through_the_sidecar(tmp_path):
    import struct

    from ada.fem.results.artefacts import FEAResultStreamAdapter
    from ada.fem.results.artefacts.mesh import write_element_properties

    props = FEAResultStreamAdapter(_footprint_result()).try_element_properties()
    assert props["tables"]["sections"]["7"]["name"] == "Dummy_EQbox"
    assert props["tables"]["materials"]["3"]["name"] == "DummyMatEQ"
    assert props["tables"]["element_types"] == {"15": "BEAS"}

    path = tmp_path / "props.bin"
    assert write_element_properties(props, path) == 4
    raw = path.read_bytes()
    assert raw[:4] == b"AFEP" and struct.unpack_from("<II", raw, 4) == (1, 4)
    rec = np.frombuffer(raw, dtype=[("l", "<u4"), ("t", "<u4"), ("m", "<u4"), ("s", "<u4"), ("th", "<f4")], offset=16)
    assert rec["l"].tolist() == [100, 101, 102, 103]
    assert set(rec["t"].tolist()) == {15} and set(rec["m"].tolist()) == {3} and set(rec["s"].tolist()) == {7}
    assert np.isnan(rec["th"]).all()
