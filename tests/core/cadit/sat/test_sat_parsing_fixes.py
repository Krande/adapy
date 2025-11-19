"""
Comprehensive test for all problematic SAT entity types found in dssdfsdfT11.SAT

This test covers:
1. intcurve-curve with exactcur full nubs
2. point entities with reference tokens
3. Various edge cases in parsing
"""

from src.ada.cadit.sat.parser.parser import AcisSatParser
from pathlib import Path


def test_comprehensive_parsing():
    """Test parsing all problematic entity types from the SAT file."""

    # Test 1: exactcur full nubs
    print("\n=== Test 1: exactcur full nubs ===")
    sat_file = Path(__file__).parent.parent.parent.parent.parent / "files" / "sat_files" / "exactcur_full_nubs.sat"
    parser = AcisSatParser(sat_file)
    entities = parser.parse()

    assert len(entities) > 0, "Should parse entities from exactcur_full_nubs.sat"
    print(f"✓ Parsed {len(entities)} entities from exactcur_full_nubs.sat")

    # Verify intcurve with exactcur full nubs
    intcurve = entities.get(13)
    assert intcurve is not None, "Should find intcurve-curve entity"
    assert intcurve.spline_data is not None, "Should have spline data"
    assert intcurve.spline_data.degree == 3, "Degree should be 3"
    print(f"✓ Intcurve parsed: degree={intcurve.spline_data.degree}, knots={len(intcurve.spline_data.knots)}")

    # Test 2: point with reference tokens
    print("\n=== Test 2: point with reference tokens ===")
    sat_file = Path(__file__).parent.parent.parent.parent.parent / "files" / "sat_files" / "point_with_refs.sat"
    parser = AcisSatParser(sat_file)
    entities = parser.parse()

    assert len(entities) > 0, "Should parse entities from point_with_refs.sat"
    print(f"✓ Parsed {len(entities)} entities from point_with_refs.sat")

    # Verify points
    point1 = entities.get(18)
    assert point1 is not None, "Should find point entity 18"
    assert abs(point1.x - 145.30000000000001) < 1e-10, f"Point x should be 145.3, got {point1.x}"
    assert abs(point1.y - 31.000000000000004) < 1e-10, f"Point y should be 31.0, got {point1.y}"
    assert abs(point1.z - 20) < 1e-10, f"Point z should be 20, got {point1.z}"
    print(f"✓ Point 18 parsed correctly: ({point1.x}, {point1.y}, {point1.z})")

    point2 = entities.get(19)
    assert point2 is not None, "Should find point entity 19"
    assert abs(point2.x - 150) < 1e-10, f"Point x should be 150, got {point2.x}"
    assert abs(point2.y - 35.699999999999989) < 1e-10, f"Point y should be 35.7, got {point2.y}"
    assert abs(point2.z - 16) < 1e-10, f"Point z should be 16, got {point2.z}"
    print(f"✓ Point 19 parsed correctly: ({point2.x}, {point2.y}, {point2.z})")

    print("\n✅ ALL TESTS PASSED!")


if __name__ == "__main__":
    test_comprehensive_parsing()
