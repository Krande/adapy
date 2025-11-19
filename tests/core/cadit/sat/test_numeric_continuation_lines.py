"""
Test to verify that standalone numeric lines like "-1" don't cause parser errors.

This test ensures that the parser correctly skips continuation lines that are
just numbers (like control point data) and don't have an entity type.
"""

from src.ada.cadit.sat.parser.parser import AcisSatParser
import tempfile


def test_parser_handles_numeric_continuation_lines():
    """Test that lines like '-1' without entity type don't crash the parser."""

    # Create a SAT file with a standalone "-1" line (simulating control point data)
    sat_content = """700 0 1 0
17 Test 12 ACIS 33.0.1 NT 24 Mon Nov 17 12:39:41 2025
1 9.9999999999999995e-07 1e-10
-1 body $-1 -1 -1 $-1 $2 $-1 $3 #
-2 lump $-1 -1 -1 $-1 $-1 $4 $1 #
-3 transform $-1 -1 1 0 0 0 1 0 0 0 1 0 0 0 1 no_rotate no_reflect no_shear #
-4 shell $-1 -1 -1 $-1 $-1 $-1 $5 $-1 $2 #
-5 face $-1 -1 -1 $-1 $-1 $7 $4 $-1 $8 forward single in #
-7 loop $-1 -1 -1 $-1 $-1 $10 $5 #
-8 plane-surface $-1 -1 -1 $-1 0 0 0 0 0 1 1 0 0 forward_v I I I I #
-10 coedge $-1 -1 -1 $-1 $11 $11 $-1 $12 forward $7 $-1 #
-11 coedge $-1 -1 -1 $-1 $10 $10 $-1 $13 forward $7 $-1 #
-12 edge $-1 -1 -1 $-1 $14 $15 $10 $16 forward @7 unknown #
-13 edge $-1 -1 -1 $-1 $15 $14 $11 $17 forward @7 unknown #
-14 vertex $-1 -1 -1 $-1 $12 $18 #
-15 vertex $-1 -1 -1 $-1 $13 $19 #
-16 straight-curve $-1 -1 -1 $-1 145.3 31.0 20 1 0 0 I I #
-17 straight-curve $-1 -1 -1 $-1 150 35.7 16 0 1 0 I I #
-18 point $-1 -1 -1 $-1 145.3 31.0 20 #
-19 point $-1 -1 -1 $-1 150 35.7 16 #
-1
-2.5
145.3
End-of-ACIS-data
"""

    # Create temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sat", delete=False, encoding="utf-8") as f:
        f.write(sat_content)
        temp_file = f.name

    try:
        # Parse the file - should not crash
        parser = AcisSatParser(temp_file)
        entities = parser.parse()

        print(f"✓ Successfully parsed {len(entities)} entities")
        print("✓ Parser handled standalone numeric lines without crashing")

        # Verify we got the expected entities
        assert len(entities) > 0, "Should have parsed some entities"
        assert 1 in entities, "Should have parsed body entity 1"
        assert 18 in entities, "Should have parsed point entity 18"
        assert 19 in entities, "Should have parsed point entity 19"

        # Verify points are parsed correctly
        point18 = entities[18]
        assert abs(point18.x - 145.3) < 1e-6, f"Point 18 x should be 145.3, got {point18.x}"
        assert abs(point18.y - 31.0) < 1e-6, f"Point 18 y should be 31.0, got {point18.y}"
        assert abs(point18.z - 20) < 1e-6, f"Point 18 z should be 20, got {point18.z}"

        print(f"✓ Point 18 coordinates: ({point18.x}, {point18.y}, {point18.z})")

        point19 = entities[19]
        assert abs(point19.x - 150) < 1e-6, f"Point 19 x should be 150, got {point19.x}"
        assert abs(point19.y - 35.7) < 1e-6, f"Point 19 y should be 35.7, got {point19.y}"
        assert abs(point19.z - 16) < 1e-6, f"Point 19 z should be 16, got {point19.z}"

        print(f"✓ Point 19 coordinates: ({point19.x}, {point19.y}, {point19.z})")
        print("\n✅ TEST PASSED: Parser correctly handles standalone numeric lines!")

    finally:
        # Cleanup
        import os

        if os.path.exists(temp_file):
            os.unlink(temp_file)


if __name__ == "__main__":
    test_parser_handles_numeric_continuation_lines()
