"""
Test parsing of exactcur entities with 'full' keyword.

This test verifies that intcurve-curve entities with exactcur spline data
containing the 'full' keyword can be parsed without errors.
"""

from pathlib import Path
from src.ada.cadit.sat.parser.parser import AcisSatParser


def test_exactcur_full_nubs_parsing():
    """Test that exactcur entities with 'full' keyword can be parsed without ValueError."""
    sat_file = Path(__file__).parent.parent.parent.parent.parent / "files" / "sat_files" / "exactcur_full_nubs.sat"

    assert sat_file.exists(), f"Test SAT file not found: {sat_file}"

    # Parse the file
    parser = AcisSatParser(sat_file)
    entities = parser.parse()

    # Check that we parsed the entities
    assert len(entities) > 0, "Should parse at least one entity"
    print(f"✓ Successfully parsed {len(entities)} entities")

    # Check for the intcurve-curve entity
    intcurve = entities.get(13)
    assert intcurve is not None, "Should find intcurve entity 13"
    assert intcurve.entity_type == "intcurve-curve", "Entity should be intcurve-curve type"
    print(f"✓ Found intcurve-curve entity: {intcurve}")

    # Verify spline data exists and was parsed correctly
    assert hasattr(intcurve, "spline_data"), "Intcurve should have spline_data attribute"
    assert intcurve.spline_data is not None, "Spline data should not be None"

    spline_data = intcurve.spline_data
    print("✓ Spline data parsed successfully:")
    print(f"  - Subtype: {spline_data.subtype}")
    print(f"  - Curve type: {spline_data.curve_type}")
    print(f"  - Degree: {spline_data.degree}")
    print(f"  - Knots: {len(spline_data.knots)} knots - {spline_data.knots}")
    print(f"  - Multiplicities: {spline_data.knot_multiplicities}")
    print(f"  - Control points: {len(spline_data.control_points)} points")

    # Verify parsed values
    assert spline_data.subtype == "exactcur", f"Subtype should be exactcur, got {spline_data.subtype}"
    assert spline_data.degree == 3, f"Degree should be 3, got {spline_data.degree}"

    # Verify knots were parsed (should have 2 knots)
    assert len(spline_data.knots) == 2, f"Expected 2 knots, got {len(spline_data.knots)}"
    assert (
        len(spline_data.knot_multiplicities) == 2
    ), f"Expected 2 knot multiplicities, got {len(spline_data.knot_multiplicities)}"

    # Verify the knot values and multiplicities
    expected_knots = [0, 4]
    expected_multiplicities = [3, 3]
    for i, (knot, mult) in enumerate(zip(expected_knots, expected_multiplicities)):
        assert abs(spline_data.knots[i] - knot) < 1e-10, f"Knot {i} should be {knot}, got {spline_data.knots[i]}"
        assert (
            spline_data.knot_multiplicities[i] == mult
        ), f"Multiplicity {i} should be {mult}, got {spline_data.knot_multiplicities[i]}"

    # Verify control points were parsed (should have 2 control points)
    assert (
        len(spline_data.control_points) >= 2
    ), f"Expected at least 2 control points, got {len(spline_data.control_points)}"

    if spline_data.control_points:
        print(f"    First CP: {spline_data.control_points[0]}")
        print(f"    Last CP: {spline_data.control_points[-1]}")

        # Verify first control point
        expected_first_cp = [145.30000000000001, 31.000000000000004, 20]
        for i, val in enumerate(expected_first_cp):
            assert (
                abs(spline_data.control_points[0][i] - val) < 1e-10
            ), f"First CP coord {i} should be {val}, got {spline_data.control_points[0][i]}"

        # Verify last control point
        expected_last_cp = [150, 35.699999999999989, 20]
        for i, val in enumerate(expected_last_cp):
            assert (
                abs(spline_data.control_points[-1][i] - val) < 1e-10
            ), f"Last CP coord {i} should be {val}, got {spline_data.control_points[-1][i]}"

    # Verify other entities were parsed correctly too
    plane_surface = entities.get(8)
    assert plane_surface is not None, "Should find plane-surface entity 8"
    print(f"✓ Plane surface parsed: origin={plane_surface.origin}, normal={plane_surface.normal}")

    vertex = entities.get(12)
    assert vertex is not None, "Should find vertex entity 12"
    print("✓ Vertex parsed correctly")

    print("\n✓ Test PASSED: exactcur with 'full' keyword parsed successfully!")


if __name__ == "__main__":
    test_exactcur_full_nubs_parsing()
