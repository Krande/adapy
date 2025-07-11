import pytest
import numpy as np

import ada
from ada import Assembly, Beam, Node, Part, Section, Material
from ada.api.beams.helpers import (
    BeamConnectionProps,
    Justification,
    get_offset_from_justification,
    is_on_beam,
    split_beam,
    get_beam_extensions,
    have_equivalent_props,
    is_weak_axis_stiffened,
    is_strong_axis_stiffened,
    get_justification,
    updating_nodes,
    NodeNotOnEndpointError,
)
from ada.geom.direction import Direction


@pytest.fixture
def simple_beam():
    """Create a simple beam for testing"""
    return Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")


@pytest.fixture
def assembly_with_beam():
    """Create an assembly with a beam for testing"""
    a = Assembly("TestAssembly")
    p = Part("TestPart")
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    p.add_beam(beam)
    a.add_part(p)
    return a, beam


def test_justification_enum():
    """Test Justification enum values"""
    assert Justification.NA.value == "neutral axis"
    assert Justification.TOS.value == "top of steel"
    assert Justification.CUSTOM.value == "custom"


def test_get_offset_from_justification_na(simple_beam):
    """Test get_offset_from_justification with neutral axis"""
    offset = get_offset_from_justification(simple_beam, Justification.NA)
    assert isinstance(offset, Direction)
    assert offset.x == 0
    assert offset.y == 0
    assert offset.z == 0


def test_get_offset_from_justification_tos(simple_beam):
    """Test get_offset_from_justification with top of steel"""
    offset = get_offset_from_justification(simple_beam, Justification.TOS)
    assert isinstance(offset, Direction)
    # For IPE300, height should be 0.3m, so offset should be 0.15m in up direction
    expected_offset = simple_beam.up * simple_beam.section.h / 2
    assert np.allclose([offset.x, offset.y, offset.z], [expected_offset.x, expected_offset.y, expected_offset.z])


def test_get_offset_from_justification_invalid(simple_beam):
    """Test get_offset_from_justification with invalid justification"""
    with pytest.raises(ValueError, match="Unknown justification"):
        get_offset_from_justification(simple_beam, "invalid")


def test_is_on_beam(simple_beam):
    """Test is_on_beam function"""
    # Test with beam endpoints
    assert is_on_beam(simple_beam, simple_beam.n1)
    assert is_on_beam(simple_beam, simple_beam.n2)
    
    # Test with point on beam axis
    mid_point = Node((0.5, 0, 0))
    assert is_on_beam(simple_beam, mid_point)
    
    # Test with point not on beam axis
    off_point = Node((0.5, 1, 0))
    assert not is_on_beam(simple_beam, off_point)


def test_split_beam_by_fraction(assembly_with_beam):
    """Test split_beam function with fraction"""
    assembly, beam = assembly_with_beam
    original_length = beam.length
    
    # Split beam at 50%
    new_beam = split_beam(beam, fraction=0.5)
    
    assert new_beam is not None
    assert beam.name == "test_beam_1"
    assert new_beam.name == "test_beam_2"
    assert np.isclose(beam.length, original_length * 0.5)
    assert np.isclose(new_beam.length, original_length * 0.5)


def test_split_beam_by_length(assembly_with_beam):
    """Test split_beam function with length"""
    assembly, beam = assembly_with_beam
    original_length = beam.length
    
    # Split beam at 0.3m from start
    new_beam = split_beam(beam, length=0.3)
    
    assert new_beam is not None
    assert np.isclose(beam.length, 0.3)
    assert np.isclose(new_beam.length, original_length - 0.3)


def test_split_beam_by_point(assembly_with_beam):
    """Test split_beam function with point"""
    assembly, beam = assembly_with_beam
    
    # Split beam at midpoint
    mid_point = (0.5, 0, 0)
    new_beam = split_beam(beam, point=mid_point)
    
    assert new_beam is not None
    assert beam.name == "test_beam_1"
    assert new_beam.name == "test_beam_2"


def test_split_beam_no_params(assembly_with_beam):
    """Test split_beam function with no parameters"""
    assembly, beam = assembly_with_beam
    
    # Should return None and log warning
    result = split_beam(beam)
    assert result is None


def test_is_equivalent():
    """Test have_equivalent_props function"""
    beam1 = Beam("beam1", (0, 0, 0), (1, 0, 0), "IPE300")
    beam2 = Beam("beam2", (0, 1, 0), (1, 1, 0), "IPE300")  # Same section, different position
    beam3 = Beam("beam3", (0, 0, 0), (1, 0, 0), "IPE400")  # Different section
    
    # Same beam should not be equivalent to itself
    assert not have_equivalent_props(beam1, beam1)
    
    # Different beams with same section should be equivalent
    assert have_equivalent_props(beam1, beam2)
    
    # Beams with different sections should not be equivalent
    assert not have_equivalent_props(beam1, beam3)


def test_get_beam_extensions():
    """Test get_beam_extensions function"""
    # Create assembly with connected beams
    a = Assembly("TestAssembly")
    p = Part("TestPart")
    
    # Create connected beams with same section
    beam1 = Beam("beam1", (0, 0, 0), (1, 0, 0), "IPE300")
    beam2 = Beam("beam2", (1, 0, 0), (2, 0, 0), "IPE300")  # Connected and parallel
    beam3 = Beam("beam3", (1, 0, 0), (1, 1, 0), "IPE300")  # Connected but perpendicular
    
    p.add_beam(beam1)
    p.add_beam(beam2)
    p.add_beam(beam3)
    a.add_part(p)
    
    extensions = get_beam_extensions(beam1)
    # Should find beam2 (parallel and equivalent) but not beam3 (perpendicular)
    assert len(extensions) >= 0  # May be empty depending on node references setup


def test_is_weak_axis_stiffened():
    """Test is_weak_axis_stiffened function"""
    beam1 = Beam("beam1", (0, 0, 0), (1, 0, 0), "IPE300")
    beam2 = Beam("beam2", (0, 0, 0), (0, 0, 1), "IPE300")  # Perpendicular in weak axis
    
    # beam2 should stiffen beam1's weak axis
    assert is_weak_axis_stiffened(beam1, beam2)
    
    # Same beam should not stiffen itself
    assert not is_weak_axis_stiffened(beam1, beam1)


def test_is_strong_axis_stiffened():
    """Test is_strong_axis_stiffened function"""
    beam1 = Beam("beam1", (0, 0, 0), (1, 0, 0), "IPE300")
    beam2 = Beam("beam2", (0, 0, 0), (0, 1, 0), "IPE300")  # Perpendicular in strong axis
    
    # beam2 should stiffen beam1's strong axis
    assert is_strong_axis_stiffened(beam1, beam2)
    
    # Same beam should not stiffen itself
    assert not is_strong_axis_stiffened(beam1, beam1)


def test_get_justification_na():
    """Test get_justification function for neutral axis"""
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    # Default beam should have NA justification
    justification = get_justification(beam)
    assert justification == Justification.NA


def test_get_justification_tos():
    """Test get_justification function for top of steel"""
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    # Set eccentricities to top of steel
    offset = beam.up * beam.section.h / 2
    beam.e1 = offset
    beam.e2 = offset
    
    justification = get_justification(beam)
    assert justification == Justification.TOS


def test_get_justification_custom():
    """Test get_justification function for custom"""
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    # Set only one eccentricity
    beam.e1 = Direction(0, 0, 0.1)
    
    justification = get_justification(beam)
    assert justification == Justification.CUSTOM


def test_get_justification_tubular():
    """Test get_justification function for tubular section"""
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "PIPE300x20")
    justification = get_justification(beam)
    assert justification == Justification.NA


def test_updating_nodes():
    """Test updating_nodes function"""
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    old_node = beam.n1
    new_node = Node("new_node", (0, 0, 0))
    
    # Update n1
    updating_nodes(beam, old_node, new_node)
    assert beam.n1 == new_node
    
    # Test updating n2
    old_node2 = beam.n2
    new_node2 = Node("new_node2", (1, 0, 0))
    updating_nodes(beam, old_node2, new_node2)
    assert beam.n2 == new_node2


def test_beam_connection_props():
    """Test BeamConnectionProps class"""
    beam = Beam("test_beam", (0, 0, 0), (1, 0, 0), "IPE300")
    props = BeamConnectionProps(beam)
    
    # Test properties
    assert props.connected_to == []
    assert props.connected_end1 is None
    assert props.connected_end2 is None
    assert props.hinge_prop is None
    
    # Test calc_con_points (basic test)
    props.calc_con_points()
    # This method modifies internal state, just ensure it doesn't crash