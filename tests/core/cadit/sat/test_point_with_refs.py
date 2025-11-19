"""
Test parsing of point entities with reference tokens.

This test verifies that point entities with reference tokens like $-1
can be parsed without errors by correctly skipping non-numeric tokens.
"""

from pathlib import Path

from src.ada.cadit.sat.parser.parser import AcisSatParser


def test_point_with_refs_parsing():
    """Test that point entities with $-1 reference tokens can be parsed without ValueError."""
    sat_file = Path(__file__).parent.parent.parent.parent.parent / "files" / "sat_files" / "point_with_refs.sat"

    assert sat_file.exists(), f"Test SAT file not found: {sat_file}"

    # Parse the file
    parser = AcisSatParser(sat_file)
    entities = parser.parse()

    # Check that we parsed the entities
    assert len(entities) > 0, "Should parse at least one entity"
    print(f"✓ Successfully parsed {len(entities)} entities")

    # Check for the point entities
    point1 = entities.get(18)
    assert point1 is not None, "Should find point entity 18"
    assert point1.entity_type == "point", "Entity should be point type"
    print(f"✓ Found point entity 18: ({point1.x}, {point1.y}, {point1.z})")

    point2 = entities.get(19)
    assert point2 is not None, "Should find point entity 19"
    assert point2.entity_type == "point", "Entity should be point type"
    print(f"✓ Found point entity 19: ({point2.x}, {point2.y}, {point2.z})")

    # Verify the coordinates were parsed correctly
    # Point 14: 145.30000000000001 31.000000000000004 20
    assert abs(point1.x - 145.30000000000001) < 1e-10, f"Point1 x should be 145.3, got {point1.x}"
    assert abs(point1.y - 31.000000000000004) < 1e-10, f"Point1 y should be 31.0, got {point1.y}"
    assert abs(point1.z - 20) < 1e-10, f"Point1 z should be 20, got {point1.z}"

    # Point 16: 150 35.699999999999989 16
    assert abs(point2.x - 150) < 1e-10, f"Point2 x should be 150, got {point2.x}"
    assert abs(point2.y - 35.699999999999989) < 1e-10, f"Point2 y should be 35.7, got {point2.y}"
    assert abs(point2.z - 16) < 1e-10, f"Point2 z should be 16, got {point2.z}"

    print("✓ Point coordinates verified successfully")

    # Verify vertices reference the points
    vertex1 = entities.get(14)
    assert vertex1 is not None, "Should find vertex entity 14"
    assert vertex1.point_ref == 18, "Vertex 14 should reference point 18"
    print("✓ Vertex 14 correctly references point 18")

    vertex2 = entities.get(15)
    assert vertex2 is not None, "Should find vertex entity 15"
    assert vertex2.point_ref == 19, "Vertex 15 should reference point 19"
    print("✓ Vertex 15 correctly references point 19")

    # Verify straight curves were parsed
    curve1 = entities.get(16)
    assert curve1 is not None, "Should find straight-curve entity 16"
    print(f"✓ Straight curve 16 parsed: origin={curve1.origin}, direction={curve1.direction}")

    curve2 = entities.get(17)
    assert curve2 is not None, "Should find straight-curve entity 17"
    print(f"✓ Straight curve 17 parsed: origin={curve2.origin}, direction={curve2.direction}")

    print("\n✓ Test PASSED: Points with reference tokens parsed successfully!")


if __name__ == "__main__":
    test_point_with_refs_parsing()
