"""
Test script to verify the lawintcur parsing fix.
"""

import os
import tempfile

from src.ada.cadit.sat.parser.parser import AcisSatParser

# Create a minimal SAT file with the problematic lawintcur line
sat_content = """700 0 1 0
18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 Mon Nov 17 12:39:41 2025
1000 9.9999999999999995e-07 1.0000000000000001e-10
-1 body $-1 $2 $-1 $-1 #
-2 lump $-1 $-1 $3 $1 #
-3 shell $-1 $-1 $-1 $4 $-1 $2 #
-4 face $-1 $-1 $-1 $3 $-1 $5 forward double out $10 #
-5 loop $-1 $-1 $6 $4 #
-6 coedge $-1 $7 $7 $-1 $-1 $5 $8 forward #
-7 coedge $-1 $6 $6 $-1 $-1 $5 $9 reversed #
-8 edge $-1 $-1 $-1 $-1 $-1 $6 $131 forward unknown #
-9 edge $-1 $-1 $-1 $-1 $-1 $7 $132 reversed unknown #
-10 plane-surface $-1 0 0 0 0 0 1 1 0 0 forward I I I I #
-131 intcurve-curve $-1 -1 -1 $-1 forward { lawintcur full nubs 3 open 4 \t0 3 4.0199502484483425 1 10.049875621120814 1 11.557356964288923 3 \t-152.93449314192418 28.065506858075835 30 \t-152.84021223785959 28.159787762227708 31.333333333359068 \t-152.60450997731138 28.39549002263551 34.666666666656639 \t-152.33345237791579 28.666547622062751 38.500000000033317 \t-152.15667568262194 28.843324317391726 40.999999999984404 \t-152.12132034355963 28.878679656440347 41.5 \tnull_surface \tnull_surface \tnullbs \tnullbs } #
-132 straight-curve $-1 0 0 0 1 0 0 I I #
End-of-ACIS-data
"""


def test_lawintcur_parsing():
    """Test that lawintcur entities can be parsed without ValueError."""
    # Create a temporary SAT file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sat", delete=False, encoding="utf-8") as f:
        f.write(sat_content)
        temp_file = f.name

    try:
        # Try to parse the file
        parser = AcisSatParser(temp_file)
        entities = parser.parse()

        # Check that we parsed the entities
        print(f"✓ Successfully parsed {len(entities)} entities")
        assert len(entities) > 0, "Should parse at least one entity"

        # Check for the intcurve-curve entity
        intcurve = entities.get(131)
        assert intcurve is not None, "Should find intcurve entity 131"
        print(f"✓ Found intcurve-curve entity: {intcurve}")

        # Verify spline data exists
        assert hasattr(intcurve, "spline_data"), "Intcurve should have spline_data attribute"
        assert intcurve.spline_data is not None, "Spline data should not be None"

        spline_data = intcurve.spline_data
        print("✓ Spline data parsed successfully:")
        print(f"  - Subtype: {spline_data.subtype}")
        print(f"  - Curve type: {spline_data.curve_type}")
        print(f"  - Degree: {spline_data.degree}")
        print(f"  - Knots: {len(spline_data.knots)} knots - {spline_data.knots}")
        print(f"  - Control points: {len(spline_data.control_points)} points")

        # Verify parsed values
        assert spline_data.subtype == "lawintcur", "Subtype should be lawintcur"
        assert spline_data.degree == 3, "Degree should be 3"

        # Verify knots were parsed (should have 4 knots based on the SAT data)
        assert len(spline_data.knots) == 4, f"Expected 4 knots, got {len(spline_data.knots)}"
        assert (
            len(spline_data.knot_multiplicities) == 4
        ), f"Expected 4 knot multiplicities, got {len(spline_data.knot_multiplicities)}"

        # Verify the knot values (from the test data)
        expected_knots = [0, 4.0199502484483425, 10.049875621120814, 11.557356964288923]
        expected_multiplicities = [3, 1, 1, 3]
        for i, (knot, mult) in enumerate(zip(expected_knots, expected_multiplicities)):
            assert abs(spline_data.knots[i] - knot) < 1e-10, f"Knot {i} should be {knot}, got {spline_data.knots[i]}"
            assert (
                spline_data.knot_multiplicities[i] == mult
            ), f"Multiplicity {i} should be {mult}, got {spline_data.knot_multiplicities[i]}"

        # Verify control points were parsed (should have 6 control points)
        assert (
            len(spline_data.control_points) >= 6
        ), f"Expected at least 6 control points, got {len(spline_data.control_points)}"

        if spline_data.control_points:
            print(f"    First CP: {spline_data.control_points[0]}")
            print(f"    Last CP: {spline_data.control_points[-1]}")

            # Verify first control point
            expected_first_cp = [-152.93449314192418, 28.065506858075835, 30]
            for i, val in enumerate(expected_first_cp):
                assert (
                    abs(spline_data.control_points[0][i] - val) < 1e-10
                ), f"First CP coord {i} should be {val}, got {spline_data.control_points[0][i]}"

            # Verify last control point
            expected_last_cp = [-152.12132034355963, 28.878679656440347, 41.5]
            for i, val in enumerate(expected_last_cp):
                assert (
                    abs(spline_data.control_points[-1][i] - val) < 1e-10
                ), f"Last CP coord {i} should be {val}, got {spline_data.control_points[-1][i]}"

        # Verify other entities were parsed correctly too
        plane_surface = entities.get(10)
        assert plane_surface is not None, "Should find plane-surface entity 10"
        print(f"✓ Plane surface parsed: origin={plane_surface.origin}, normal={plane_surface.normal}")

        straight_curve = entities.get(132)
        assert straight_curve is not None, "Should find straight-curve entity 132"
        print(f"✓ Straight curve parsed: origin={straight_curve.origin}, direction={straight_curve.direction}")

        print("\n✓ Test PASSED: No ValueError occurred!")
        return True

    except ValueError as e:
        print(f"✗ Test FAILED with ValueError: {e}")
        return False
    except Exception as e:
        print(f"✗ Test FAILED with unexpected error: {e}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        # Clean up
        if os.path.exists(temp_file):
            os.remove(temp_file)


if __name__ == "__main__":
    success = test_lawintcur_parsing()
    exit(0 if success else 1)
