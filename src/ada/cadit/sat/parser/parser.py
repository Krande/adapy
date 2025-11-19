"""
ACIS SAT Parser

Core parser for reading and parsing ACIS SAT files into structured entity models.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, TextIO, Tuple

from ada.cadit.sat.parser.acis_entities import (
    AcisBody,
    AcisCoedge,
    AcisConeSurface,
    AcisCylinderSurface,
    AcisEdge,
    AcisEllipseCurve,
    AcisEntity,
    AcisFace,
    AcisHeader,
    AcisIntcurveCurve,
    AcisLoop,
    AcisLump,
    AcisNameAttrib,
    AcisPCurve,
    AcisPlaneSurface,
    AcisPoint,
    AcisPositionAttrib,
    AcisRgbColorAttrib,
    AcisShell,
    AcisSphereSurface,
    AcisSplineCurveData,
    AcisSplineSurface,
    AcisSplineSurfaceData,
    AcisStraightCurve,
    AcisStringAttrib,
    AcisSubshell,
    AcisTorusSurface,
    AcisTransform,
    AcisVersion,
    AcisVertex,
    NurbsType,
    SenseType,
)
from ada.config import logger


class AcisSatParser:
    """
    Parser for ACIS SAT files.

    Reads SAT files and constructs a database of ACIS entities with proper
    type safety and validation using Pydantic models.
    """

    def __init__(self, sat_file: str | Path):
        self.sat_file = Path(sat_file) if isinstance(sat_file, str) else sat_file
        self.header: Optional[AcisHeader] = None
        self.entities: Dict[int, AcisEntity] = {}
        self._raw_lines: List[str] = []
        self._current_line_no = 0

    def parse(self) -> Dict[int, AcisEntity]:
        """
        Parse the SAT file and return dictionary of entities indexed by their ID.

        Returns:
            Dictionary mapping entity index to AcisEntity instances
        """
        logger.info(f"Parsing ACIS SAT file: {self.sat_file}")

        with open(self.sat_file, 'r', encoding='utf-8', errors='ignore') as f:
            # Parse header
            self.header = self._parse_header(f)
            logger.info(f"ACIS Version: {self.header.acis_version}")

            # Parse entities
            self._parse_entities(f)

        logger.info(f"Parsed {len(self.entities)} entities")
        return self.entities

    def _parse_header(self, f: TextIO) -> AcisHeader:
        """Parse the SAT file header (first 3 lines)."""
        line1 = f.readline().strip()
        line2 = f.readline().strip()
        line3 = f.readline().strip()

        # Parse first line: version_code num_records num_entities flags
        parts1 = line1.split()
        version_code = int(parts1[0])
        num_records = int(parts1[1]) if len(parts1) > 1 else 0
        num_entities = int(parts1[2]) if len(parts1) > 2 else 0
        flags = int(parts1[3]) if len(parts1) > 3 else 0

        # Parse second line: product_id ACIS version date
        # Example: "18 SESAM - gmGeometry 14 ACIS 33.0.1 NT 24 Mon Nov 17 12:39:41 2025"
        acis_version = None
        product_id = ""
        date = ""

        if "ACIS" in line2:
            parts2 = line2.split("ACIS")
            product_id = parts2[0].strip()
            remainder = parts2[1].strip().split()
            if len(remainder) > 0:
                try:
                    acis_version = AcisVersion.from_string(remainder[0])
                except:
                    pass
            # Date is the rest
            date = " ".join(remainder[2:]) if len(remainder) > 2 else ""
        else:
            product_id = line2

        # Parse third line: units_code resolution tolerance
        parts3 = line3.split()
        units_code = int(parts3[0]) if len(parts3) > 0 else 1000
        resolution = float(parts3[1]) if len(parts3) > 1 else 1e-6
        tolerance = float(parts3[2]) if len(parts3) > 2 else 1e-10

        return AcisHeader(
            version_code=version_code,
            num_records=num_records,
            num_entities=num_entities,
            flags=flags,
            product_id=product_id,
            acis_version=acis_version,
            date=date,
            units_code=units_code,
            resolution=resolution,
            tolerance=tolerance
        )

    def _parse_entities(self, f: TextIO):
        """Parse all entity records from the file."""
        while True:
            pos = f.tell()
            line = f.readline()
            if not line:
                break

            line = line.strip()
            if not line or line.startswith("End-of-ACIS"):
                continue

            # Each entity starts with a negative index followed by entity type
            # Need to distinguish from negative numbers (control points)
            if line.startswith("-"):
                # Check if this is actually an entity line (has entity type after index)
                parts = line[1:].split(None, 2)
                if not parts or not parts[0].replace('.', '').replace('-', '').isdigit():
                    continue

                # Must have at least 2 parts: index and entity_type
                if len(parts) < 2:
                    continue

                # Check if the index is an integer (entity) vs float (control point data)
                try:
                    int(parts[0])
                except ValueError:
                    # This is a float (control point), skip it
                    continue

                # Check if parts[1] is a valid entity type (not a number)
                # Valid entity types are strings like "body", "lump", "shell", "face", etc.
                try:
                    float(parts[1])
                    # If parts[1] can be parsed as a number, this is not an entity line
                    continue
                except ValueError:
                    # Good, parts[1] is not a number, so it's likely an entity type
                    pass

                entity_str = line

                # Check if this entity contains braces (spline data)
                has_open_brace = "{" in line
                has_close_brace = "}" in line

                # If it has open brace but no close brace, read until we find the close brace
                if has_open_brace and not has_close_brace:
                    while True:
                        next_line = f.readline()
                        if not next_line:
                            break
                        entity_str += "\n" + next_line.rstrip()
                        if "}" in next_line:
                            break
                elif not line.rstrip().endswith("#"):
                    # Only read continuation lines if current line doesn't end with #
                    # Read continuation lines (lines without - or # marker at start)
                    while True:
                        pos = f.tell()
                        next_line = f.readline()
                        if not next_line:
                            break
                        next_line_stripped = next_line.strip()
                        if next_line_stripped.startswith("-") or next_line_stripped.startswith("#") or not next_line_stripped:
                            # This is a new entity or end, backtrack
                            f.seek(pos)
                            break
                        entity_str += " " + next_line_stripped
                        # Check if we've reached the end marker
                        if entity_str.rstrip().endswith("#"):
                            break

                # Parse the entity
                entity = self._parse_entity_line(entity_str)
                if entity:
                    self.entities[entity.index] = entity

    def _parse_entity_line(self, line: str) -> Optional[AcisEntity]:
        """Parse a single entity line and create appropriate entity object."""
        try:
            # Remove the leading marker and split
            if line.startswith("-"):
                parts = line[1:].split(None, 2)  # Split into index, type, and rest
                index = int(parts[0])
                entity_type = parts[1]
                data = parts[2] if len(parts) > 2 else ""
            else:
                return None

            # Parse based on entity type
            if entity_type == "body":
                return self._parse_body(index, data)
            elif entity_type == "lump":
                return self._parse_lump(index, data)
            elif entity_type == "shell":
                return self._parse_shell(index, data)
            elif entity_type == "subshell":
                return self._parse_subshell(index, data)
            elif entity_type == "face":
                return self._parse_face(index, data)
            elif entity_type == "loop":
                return self._parse_loop(index, data)
            elif entity_type == "coedge":
                return self._parse_coedge(index, data)
            elif entity_type == "edge":
                return self._parse_edge(index, data)
            elif entity_type == "vertex":
                return self._parse_vertex(index, data)
            elif entity_type == "point":
                return self._parse_point(index, data)
            elif entity_type == "straight-curve":
                return self._parse_straight_curve(index, data)
            elif entity_type == "ellipse-curve":
                return self._parse_ellipse_curve(index, data)
            elif entity_type == "intcurve-curve":
                return self._parse_intcurve_curve(index, data)
            elif entity_type == "plane-surface":
                return self._parse_plane_surface(index, data)
            elif entity_type == "cone-surface":
                return self._parse_cone_surface(index, data)
            elif entity_type == "cylinder-surface":
                return self._parse_cylinder_surface(index, data)
            elif entity_type == "sphere-surface":
                return self._parse_sphere_surface(index, data)
            elif entity_type == "torus-surface":
                return self._parse_torus_surface(index, data)
            elif entity_type == "spline-surface":
                return self._parse_spline_surface(index, data)
            elif "attrib" in entity_type or "gen-attrib" in entity_type:
                return self._parse_attrib(index, entity_type, data)
            elif entity_type == "pcurve":
                return self._parse_pcurve(index, data)
            elif entity_type == "transform":
                return self._parse_transform(index, data)
            else:
                # Generic entity for unsupported types
                logger.debug(f"Unsupported entity type: {entity_type}")
                return AcisEntity(index=index, entity_type=entity_type)

        except Exception as e:
            logger.warning(f"Failed to parse entity line: {line[:100]}... Error: {e}")
            return None

    def _parse_ref(self, ref_str: str) -> Optional[int]:
        """Parse a reference string (e.g., '$5', '-1', '123') to an integer."""
        if not ref_str or ref_str == "$-1" or ref_str == "-1":
            return None
        ref_str = ref_str.replace("$", "").strip()
        try:
            return int(ref_str)
        except:
            return None

    def _parse_sense(self, sense_str: str) -> SenseType:
        """Parse sense/direction string."""
        sense_str = sense_str.lower().strip()
        if sense_str == "forward":
            return SenseType.FORWARD
        elif sense_str == "reversed":
            return SenseType.REVERSED
        elif sense_str == "both":
            return SenseType.BOTH
        return SenseType.UNKNOWN

    def _parse_bbox(self, parts: List[str], start_idx: int) -> Optional[List[float]]:
        """Parse bounding box from parts list starting at start_idx."""
        try:
            if start_idx + 5 < len(parts):
                return [float(parts[i]) for i in range(start_idx, start_idx + 6)]
        except:
            pass
        return None

    # Entity-specific parsers

    def _parse_body(self, index: int, data: str) -> AcisBody:
        """Parse body entity.

        Format: $<attrib> <next> <prev> $<owner> $<lump> $<wire> $<transform> <flags> [bbox]
        """
        parts = data.split()
        return AcisBody(
            index=index,
            entity_type="body",
            lump_ref=self._parse_ref(parts[4]) if len(parts) > 4 else None,
            wire_ref=self._parse_ref(parts[5]) if len(parts) > 5 else None,
            transform_ref=self._parse_ref(parts[6]) if len(parts) > 6 else None,
            bounding_box=self._parse_bbox(parts, 8) if len(parts) > 13 else None
        )

    def _parse_lump(self, index: int, data: str) -> AcisLump:
        """Parse lump entity.

        Format: $<next_lump> <next> <prev> $<owner> $<unknown> $<shell> $<body> <flags> [bbox]
        """
        parts = data.split()
        return AcisLump(
            index=index,
            entity_type="lump",
            next_lump_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            shell_ref=self._parse_ref(parts[5]) if len(parts) > 5 else None,
            body_ref=self._parse_ref(parts[6]) if len(parts) > 6 else None,
            bounding_box=self._parse_bbox(parts, 8) if len(parts) > 13 else None
        )


    def _parse_shell(self, index: int, data: str) -> AcisShell:
        """Parse shell entity.

        Format: $<next_shell> <next> <prev> $<owner> $<subshell> $<...> $<face> $<wire> $<lump> <flags> [bbox]
        """
        parts = data.split()
        return AcisShell(
            index=index,
            entity_type="shell",
            next_shell_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            subshell_ref=self._parse_ref(parts[4]) if len(parts) > 4 else None,
            face_ref=self._parse_ref(parts[6]) if len(parts) > 6 else None,
            wire_ref=self._parse_ref(parts[7]) if len(parts) > 7 else None,
            lump_ref=self._parse_ref(parts[8]) if len(parts) > 8 else None,
            bounding_box=self._parse_bbox(parts, 10) if len(parts) > 15 else None
        )

    def _parse_subshell(self, index: int, data: str) -> AcisSubshell:
        """Parse subshell entity."""
        parts = data.split()
        return AcisSubshell(
            index=index,
            entity_type="subshell",
            next_subshell_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            face_ref=self._parse_ref(parts[2]) if len(parts) > 2 else None,
            shell_ref=self._parse_ref(parts[3]) if len(parts) > 3 else None
        )

    def _parse_face(self, index: int, data: str) -> AcisFace:
        """Parse face entity.

        ACIS face format varies by version but generally:
        face attrib -1 -1 $-1 next_face loop shell $-1 surface sense sided containment flags...

        Based on analysis of real files:
        [0]: attrib (or next_face in some versions)
        [1-3]: various refs including -1 markers
        [4]: next_face (or other ref)
        [5]: loop
        [6]: shell (or other ref)
        [7]: reference
        [8]: surface <- THE SURFACE REFERENCE
        [9]: sense (forward/reversed)
        [10]: sided (double/single)
        [11]: containment (out/in)
        """
        parts = data.split()

        return AcisFace(
            index=index,
            entity_type="face",
            next_face_ref=self._parse_ref(parts[4]) if len(parts) > 4 else None,
            attrib_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            shell_ref=self._parse_ref(parts[6]) if len(parts) > 6 else None,
            subshell_ref=None,  # Not clearly identified in this format
            loop_ref=self._parse_ref(parts[5]) if len(parts) > 5 else None,
            sense=self._parse_sense(parts[9]) if len(parts) > 9 else SenseType.FORWARD,
            double_sided=parts[10].lower() == "double" if len(parts) > 10 else False,
            containment=parts[11] if len(parts) > 11 else "out",
            surface_ref=self._parse_ref(parts[8]) if len(parts) > 8 else None,
            bounding_box=self._parse_bbox(parts, 14) if len(parts) > 19 else None
        )

    def _parse_loop(self, index: int, data: str) -> AcisLoop:
        """Parse loop entity."""
        parts = data.split()
        return AcisLoop(
            index=index,
            entity_type="loop",
            next_loop_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            attrib_ref=self._parse_ref(parts[1]) if len(parts) > 1 else None,
            face_ref=self._parse_ref(parts[3]) if len(parts) > 3 else None,
            coedge_ref=self._parse_ref(parts[5]) if len(parts) > 5 else None,
            bounding_box=self._parse_bbox(parts, 7) if len(parts) > 12 else None
        )

    def _parse_coedge(self, index: int, data: str) -> AcisCoedge:
        """Parse coedge entity.

        Format: $<attrib> -1 -1 $-1 $<next> $<prev> $<partner> $<edge> <sense> $<loop> $<pcurve> #
        """
        parts = data.split()
        return AcisCoedge(
            index=index,
            entity_type="coedge",
            next_coedge_ref=self._parse_ref(parts[4]) if len(parts) > 4 else None,
            previous_coedge_ref=self._parse_ref(parts[5]) if len(parts) > 5 else None,
            partner_coedge_ref=self._parse_ref(parts[6]) if len(parts) > 6 else None,
            attrib_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            loop_ref=self._parse_ref(parts[9]) if len(parts) > 9 else None,
            edge_ref=self._parse_ref(parts[7]) if len(parts) > 7 else None,
            sense=self._parse_sense(parts[8]) if len(parts) > 8 else SenseType.FORWARD
        )

    def _parse_edge(self, index: int, data: str) -> AcisEdge:
        """Parse edge entity."""
        parts = data.split()
        return AcisEdge(
            index=index,
            entity_type="edge",
            next_edge_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            attrib_ref=self._parse_ref(parts[1]) if len(parts) > 1 else None,
            start_vertex_ref=self._parse_ref(parts[4]) if len(parts) > 4 else None,
            end_vertex_ref=self._parse_ref(parts[6]) if len(parts) > 6 else None,
            coedge_ref=self._parse_ref(parts[8]) if len(parts) > 8 else None,
            curve_ref=self._parse_ref(parts[9]) if len(parts) > 9 else None,
            sense=self._parse_sense(parts[10]) if len(parts) > 10 else SenseType.FORWARD,
            convexity=parts[11] if len(parts) > 11 else "unknown",
            bounding_box=self._parse_bbox(parts, 13) if len(parts) > 18 else None
        )

    def _parse_vertex(self, index: int, data: str) -> AcisVertex:
        """Parse vertex entity."""
        parts = data.split()
        return AcisVertex(
            index=index,
            entity_type="vertex",
            attrib_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
            edge_ref=self._parse_ref(parts[2]) if len(parts) > 2 else None,
            point_ref=self._parse_ref(parts[5]) if len(parts) > 5 else None
        )

    def _parse_point(self, index: int, data: str) -> AcisPoint:
        """Parse point entity."""
        parts = data.split()
        # point format: $attrib -1/-1/$-1 -1/-1/$-1 -1/-1/$-1 x y z #
        # The first 4 tokens are references, coordinates are the last 3 numbers before #
        # Collect all numeric values, skip $ tokens and keywords
        numeric_values = []
        for part in parts:
            if part.startswith('$') or part in ['I', 'F', '#']:
                continue
            try:
                numeric_values.append(float(part))
            except ValueError:
                continue

        # The coordinates are the LAST 3 numeric values (skip first refs which could be -1)
        # Point format has 4 references followed by x, y, z coordinates
        if len(numeric_values) >= 3:
            # Take the last 3 values as coordinates
            x = numeric_values[-3]
            y = numeric_values[-2]
            z = numeric_values[-1]
        else:
            x, y, z = 0.0, 0.0, 0.0

        return AcisPoint(
            index=index,
            entity_type="point",
            x=x,
            y=y,
            z=z
        )

    def _parse_straight_curve(self, index: int, data: str) -> AcisStraightCurve:
        """Parse straight-curve entity."""
        parts = data.split()
        # straight-curve format: $attrib origin(x,y,z) direction(x,y,z) I I #
        # Skip reference tokens (starting with $) and find numeric values
        numeric_values = []
        for part in parts:
            if part.startswith('$') or part in ['I', 'F', '#']:
                continue
            try:
                numeric_values.append(float(part))
            except ValueError:
                continue

        origin = numeric_values[0:3] if len(numeric_values) >= 3 else [0, 0, 0]
        direction = numeric_values[3:6] if len(numeric_values) >= 6 else [0, 0, 1]

        return AcisStraightCurve(
            index=index,
            entity_type="straight-curve",
            origin=origin,
            direction=direction
        )

    def _parse_ellipse_curve(self, index: int, data: str) -> AcisEllipseCurve:
        """Parse ellipse-curve entity."""
        parts = data.split()
        return AcisEllipseCurve(
            index=index,
            entity_type="ellipse-curve",
            center=[float(parts[i]) for i in range(4, 7)] if len(parts) > 6 else [0, 0, 0],
            normal=[float(parts[i]) for i in range(7, 10)] if len(parts) > 9 else [0, 0, 1],
            major_axis=[float(parts[i]) for i in range(10, 13)] if len(parts) > 12 else [1, 0, 0],
            radius_ratio=float(parts[13]) if len(parts) > 13 else 1.0
        )

    def _parse_intcurve_curve(self, index: int, data: str) -> AcisIntcurveCurve:
        """Parse intcurve-curve entity (B-spline curve)."""
        # Extract the part before { if present
        if "{" in data:
            header_part = data[:data.index("{")].strip()
        else:
            header_part = data

        parts = header_part.split()

        # Extract spline data if present (enclosed in {})
        spline_data = None
        if "{" in data and "}" in data:
            spline_str = data[data.index("{")+1:data.rindex("}")]
            spline_data = self._parse_spline_curve_data(spline_str)

        # intcurve format: $attrib $edge $vertex sense { ... }
        # Find 'forward' or 'reversed' in parts
        sense = SenseType.FORWARD
        for part in parts:
            if part.lower() in ['forward', 'reversed', 'both']:
                sense = self._parse_sense(part)
                break

        return AcisIntcurveCurve(
            index=index,
            entity_type="intcurve-curve",
            sense=sense,
            surface_ref=None,  # Will parse if needed
            pcurve_ref=None,   # Will parse if needed
            spline_data=spline_data
        )

    def _parse_spline_curve_data(self, spline_str: str) -> Optional[AcisSplineCurveData]:
        """Parse B-spline curve data from spline string."""
        # First try splitting by newlines, but if we get only one line, try tabs
        lines = [line.strip() for line in spline_str.split('\n') if line.strip()]

        # If we have only one line, it might be tab-separated (common in lawintcur)
        if len(lines) == 1 and '\t' in lines[0]:
            lines = [line.strip() for line in lines[0].split('\t') if line.strip()]

        if not lines:
            return None


        first_line = lines[0].split()
        if not first_line:
            return None

        subtype = first_line[0]

        # Handle different subtypes
        if subtype == "exppc":
            # exppc format: exppc nubs degree closure num_knots
            # This is a simplified parametric curve representation
            # We'll parse what we can but may not have full control point data
            curve_type = NurbsType.NUBS if len(first_line) > 1 and first_line[1] == "nubs" else NurbsType.NURBS
            degree = int(first_line[2]) if len(first_line) > 2 else 1

            # Parse knots if present
            knots = []
            multiplicities = []
            if len(lines) > 1:
                # Try to parse knot data, but handle if it's in a different format
                try:
                    knot_data = []
                    for part in lines[1].split():
                        if part not in ['spline']:  # Skip keywords
                            try:
                                knot_data.append(float(part))
                            except ValueError:
                                continue
                    if len(knot_data) >= 2:
                        knots = knot_data[0::2]
                        multiplicities = knot_data[1::2]
                except:
                    pass

            return AcisSplineCurveData(
                subtype=subtype,
                curve_type=curve_type,
                degree=degree,
                rational=curve_type == NurbsType.NURBS,
                knots=knots,
                knot_multiplicities=multiplicities,
                control_points=[]
            )

        elif subtype == "lawintcur":
            # lawintcur format: lawintcur full nubs/nurbs degree closure num_knots
            # Example: lawintcur full nubs 3 open 4
            curve_type_str = first_line[2] if len(first_line) > 2 else "nurbs"
            curve_type = NurbsType.NURBS if curve_type_str == "nurbs" else NurbsType.NUBS
            degree = int(first_line[3]) if len(first_line) > 3 else 3

            # Parse knots (line 1 contains knot data, but ACIS may split across two lines)
            knots: List[float] = []
            multiplicities: List[int] = []
            ctrl_point_line_idx = 2
            if len(lines) > 1:
                try:
                    # First knot line
                    knot_data: List[float] = []
                    for part in lines[1].split():
                        try:
                            knot_data.append(float(part))
                        except ValueError:
                            continue

                    # Some SAT exports continue the knot list on the next line.
                    # Heuristic from legacy reader: if the following line does NOT look like a 3-value CP row,
                    # treat it as continuation of the knot list.
                    if len(lines) > 2:
                        maybe_next = lines[2].split()
                        # If it doesn't have exactly 3 numeric values, consider it a continuation of knots
                        is_three_numeric = False
                        if len(maybe_next) == 3:
                            try:
                                _ = [float(x) for x in maybe_next]
                                is_three_numeric = True
                            except Exception:
                                is_three_numeric = False
                        if not is_three_numeric:
                            for part in maybe_next:
                                try:
                                    knot_data.append(float(part))
                                except ValueError:
                                    continue
                            ctrl_point_line_idx = 3

                    if len(knot_data) >= 2:
                        knots = [knot_data[i] for i in range(0, len(knot_data), 2)]
                        multiplicities = [int(round(knot_data[i])) for i in range(1, len(knot_data), 2)]
                except Exception:
                    pass

            # Compute expected number of control points from knots and degree
            n_poles = 0
            try:
                n_poles = sum(multiplicities) - degree - 1 if multiplicities else 0
            except Exception:
                n_poles = 0

            # Parse control points (take exactly n_poles from remaining lines after knot line)
            control_points: List[List[float]] = []
            if n_poles <= 0:
                logger.error(f"Invalid B-spline definition for lawintcur: degree={degree}, mults={multiplicities}")
            else:
                for i in range(ctrl_point_line_idx, len(lines)):
                    if len(control_points) >= n_poles:
                        break
                    try:
                        line_parts = lines[i].split()
                        # Skip lines that are clearly not CP rows
                        if not line_parts or line_parts[0] in ['null_surface', 'nullbs', 'I', 'F', 'none', 'spline']:
                            continue
                        if line_parts[0].startswith('@'):
                            continue

                        # Parse numeric tuple
                        nums: List[float] = []
                        for part in line_parts:
                            try:
                                nums.append(float(part))
                            except ValueError:
                                nums = []
                                break
                        if len(nums) >= 3:
                            # keep optional weight if present (4th value)
                            control_points.append(nums[:4])
                    except Exception:
                        continue

            return AcisSplineCurveData(
                subtype=subtype,
                curve_type=curve_type,
                degree=degree,
                rational=curve_type == NurbsType.NURBS,
                knots=knots,
                knot_multiplicities=multiplicities,
                control_points=control_points
            )

        # Handle exactcur and other standard formats
        # exactcur format: exactcur [full] [0] nubs/nurbs degree closure num_knots
        # Find the curve type by looking for "nubs" or "nurbs" keyword
        curve_type = NurbsType.NURBS
        degree = 3
        curve_type_idx = -1

        for i, token in enumerate(first_line):
            if token in ["nubs", "nurbs"]:
                curve_type = NurbsType.NURBS if token == "nurbs" else NurbsType.NUBS
                curve_type_idx = i
                break

        # Degree comes after the curve type
        if curve_type_idx >= 0 and len(first_line) > curve_type_idx + 1:
            try:
                degree = int(first_line[curve_type_idx + 1])
            except ValueError:
                degree = 3

        # Parse knots (line 1); ACIS exactcur may also split knots across two lines
        knots: List[float] = []
        multiplicities: List[int] = []
        ctrl_point_line_idx = 2
        if len(lines) > 1:
            try:
                knot_data: List[float] = []
                for part in lines[1].split():
                    try:
                        knot_data.append(float(part))
                    except ValueError:
                        continue

                # Check possible continuation on line 2 using the same heuristic as legacy reader
                if len(lines) > 2:
                    maybe_next = lines[2].split()
                    is_three_numeric = False
                    if len(maybe_next) == 3:
                        try:
                            _ = [float(x) for x in maybe_next]
                            is_three_numeric = True
                        except Exception:
                            is_three_numeric = False
                    if not is_three_numeric:
                        for part in maybe_next:
                            try:
                                knot_data.append(float(part))
                            except ValueError:
                                continue
                        ctrl_point_line_idx = 3

                if len(knot_data) >= 2:
                    knots = [knot_data[i] for i in range(0, len(knot_data), 2)]
                    multiplicities = [int(round(knot_data[i])) for i in range(1, len(knot_data), 2)]
            except Exception:
                pass

        # Compute expected number of control points
        n_poles = 0
        try:
            n_poles = sum(multiplicities) - degree - 1 if multiplicities else 0
        except Exception:
            n_poles = 0

        # Parse control points (take exactly n_poles)
        control_points: List[List[float]] = []
        if n_poles <= 0:
            logger.error(f"Invalid B-spline definition for exactcur: degree={degree}, mults={multiplicities}")
        else:
            for i in range(ctrl_point_line_idx, len(lines)):
                if len(control_points) >= n_poles:
                    break
                try:
                    line_parts = lines[i].split()
                    if not line_parts or line_parts[0] in ['null_surface', 'nullbs', 'I', 'F', 'none', 'spline']:
                        continue
                    if line_parts[0].startswith('@'):
                        continue

                    nums: List[float] = []
                    for part in line_parts:
                        try:
                            nums.append(float(part))
                        except ValueError:
                            nums = []
                            break
                    if len(nums) >= 3:
                        control_points.append(nums[:4])
                except Exception:
                    continue

        return AcisSplineCurveData(
            subtype=subtype,
            curve_type=curve_type,
            degree=degree,
            rational=curve_type == NurbsType.NURBS,
            knots=knots,
            knot_multiplicities=multiplicities,
            control_points=control_points
        )

    def _parse_plane_surface(self, index: int, data: str) -> AcisPlaneSurface:
        """Parse plane-surface entity."""
        parts = data.split()
        # plane-surface format: $attrib origin(x,y,z) normal(x,y,z) u_direction(x,y,z) sense I I I I #
        # Skip reference tokens (starting with $) and non-numeric flags
        numeric_values = []
        for part in parts:
            if part.startswith('$') or part in ['I', 'F', '#', 'forward', 'reversed', 'both', 'in', 'out', 'double', 'single']:
                continue
            try:
                numeric_values.append(float(part))
            except ValueError:
                continue

        origin = numeric_values[0:3] if len(numeric_values) >= 3 else [0, 0, 0]
        normal = numeric_values[3:6] if len(numeric_values) >= 6 else [0, 0, 1]
        u_direction = numeric_values[6:9] if len(numeric_values) >= 9 else [1, 0, 0]

        return AcisPlaneSurface(
            index=index,
            entity_type="plane-surface",
            origin=origin,
            normal=normal,
            u_direction=u_direction
        )

    def _parse_cone_surface(self, index: int, data: str) -> AcisConeSurface:
        """Parse cone-surface entity."""
        parts = data.split()
        return AcisConeSurface(
            index=index,
            entity_type="cone-surface",
            origin=[float(parts[i]) for i in range(4, 7)] if len(parts) > 6 else [0, 0, 0],
            axis=[float(parts[i]) for i in range(7, 10)] if len(parts) > 9 else [0, 0, 1],
            major_axis=[float(parts[i]) for i in range(10, 13)] if len(parts) > 12 else [1, 0, 0],
            radius_ratio=float(parts[13]) if len(parts) > 13 else 1.0,
            sine_angle=float(parts[14]) if len(parts) > 14 else 0.0,
            cosine_angle=float(parts[15]) if len(parts) > 15 else 1.0
        )

    def _parse_cylinder_surface(self, index: int, data: str) -> AcisCylinderSurface:
        """Parse cylinder-surface entity."""
        parts = data.split()
        return AcisCylinderSurface(
            index=index,
            entity_type="cylinder-surface",
            origin=[float(parts[i]) for i in range(4, 7)] if len(parts) > 6 else [0, 0, 0],
            axis=[float(parts[i]) for i in range(7, 10)] if len(parts) > 9 else [0, 0, 1],
            major_axis=[float(parts[i]) for i in range(10, 13)] if len(parts) > 12 else [1, 0, 0],
            radius=float(parts[13]) if len(parts) > 13 else 1.0
        )

    def _parse_sphere_surface(self, index: int, data: str) -> AcisSphereSurface:
        """Parse sphere-surface entity."""
        parts = data.split()
        return AcisSphereSurface(
            index=index,
            entity_type="sphere-surface",
            center=[float(parts[i]) for i in range(4, 7)] if len(parts) > 6 else [0, 0, 0],
            radius=float(parts[7]) if len(parts) > 7 else 1.0,
            pole=[float(parts[i]) for i in range(8, 11)] if len(parts) > 10 else [0, 0, 1],
            equator=[float(parts[i]) for i in range(11, 14)] if len(parts) > 13 else [1, 0, 0]
        )

    def _parse_torus_surface(self, index: int, data: str) -> AcisTorusSurface:
        """Parse torus-surface entity."""
        parts = data.split()
        return AcisTorusSurface(
            index=index,
            entity_type="torus-surface",
            center=[float(parts[i]) for i in range(4, 7)] if len(parts) > 6 else [0, 0, 0],
            axis=[float(parts[i]) for i in range(7, 10)] if len(parts) > 9 else [0, 0, 1],
            major_axis=[float(parts[i]) for i in range(10, 13)] if len(parts) > 12 else [1, 0, 0],
            major_radius=float(parts[13]) if len(parts) > 13 else 1.0,
            minor_radius=float(parts[14]) if len(parts) > 14 else 0.5
        )

    def _parse_spline_surface(self, index: int, data: str) -> AcisSplineSurface:
        """Parse spline-surface entity (B-spline surface)."""
        parts = data.split()

        # Extract sense
        sense = SenseType.FORWARD
        if len(parts) > 3:
            sense = self._parse_sense(parts[3])

        # Extract spline data if present (enclosed in {})
        spline_data = None
        if "{" in data and "}" in data:
            spline_str = data[data.index("{")+1:data.rindex("}")]
            spline_data = self._parse_spline_surface_data(spline_str)

        return AcisSplineSurface(
            index=index,
            entity_type="spline-surface",
            sense=sense,
            spline_data=spline_data
        )

    def _parse_spline_surface_data(self, spline_str: str) -> Optional[AcisSplineSurfaceData]:
        """Parse B-spline surface data from spline string."""
        lines = [line.strip() for line in spline_str.split('\n') if line.strip()]
        if not lines:
            return None

        first_line = lines[0].split()
        subtype = first_line[0]

        # Check for extra zero
        has_extra_zero = len(first_line) > 1 and first_line[1] == "0"
        surface_type_idx = 3 if has_extra_zero else 2
        u_degree_idx = 4 if has_extra_zero else 3
        v_degree_idx = 5 if has_extra_zero else 4

        surface_type_str = first_line[surface_type_idx] if len(first_line) > surface_type_idx else "nurbs"
        surface_type = NurbsType.NURBS if surface_type_str == "nurbs" else NurbsType.NUBS

        u_degree = int(first_line[u_degree_idx]) if len(first_line) > u_degree_idx else 3
        v_degree = int(first_line[v_degree_idx]) if len(first_line) > v_degree_idx else 3

        # Parse U knots (line 1)
        u_knots = []
        u_multiplicities = []
        if len(lines) > 1:
            u_knot_data = [float(x) for x in lines[1].split()]
            u_knots = u_knot_data[0::2]
            u_multiplicities = u_knot_data[1::2]

        # Parse V knots (line 2)
        v_knots = []
        v_multiplicities = []
        if len(lines) > 2:
            v_knot_data = [float(x) for x in lines[2].split()]
            v_knots = v_knot_data[0::2]
            v_multiplicities = v_knot_data[1::2]

        # Calculate control point counts
        control_points_u = int(sum(u_multiplicities)) + 1 - u_degree if u_multiplicities else 0
        control_points_v = int(sum(v_multiplicities)) + 1 - v_degree if v_multiplicities else 0

        # Parse control points (remaining lines)
        control_points = []
        for _ in range(control_points_u):
            control_points.append([])

        cp_line_start = 3
        for v in range(control_points_v):
            for u in range(control_points_u):
                if cp_line_start < len(lines):
                    cp_data = [float(x) for x in lines[cp_line_start].split()]
                    control_points[u].append(cp_data)
                    cp_line_start += 1

        return AcisSplineSurfaceData(
            subtype=subtype,
            has_extra_zero=has_extra_zero,
            surface_type=surface_type,
            u_degree=u_degree,
            v_degree=v_degree,
            rational=surface_type == NurbsType.NURBS,
            u_knots=u_knots,
            u_knot_multiplicities=u_multiplicities,
            v_knots=v_knots,
            v_knot_multiplicities=v_multiplicities,
            control_points=control_points
        )

    def _parse_attrib(self, index: int, entity_type: str, data: str) -> AcisEntity:
        """Parse attribute entities."""
        parts = data.split()

        if "name_attrib" in entity_type or "name-attrib" in entity_type:
            # Extract name (usually in quotes or at the end)
            name = ""
            if "@" in data:
                name_parts = data.split("@")
                if len(name_parts) > 1:
                    name = name_parts[1].strip().split()[1] if len(name_parts[1].strip().split()) > 1 else ""

            return AcisNameAttrib(
                index=index,
                entity_type=entity_type,
                next_attrib_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
                owner_ref=self._parse_ref(parts[2]) if len(parts) > 2 else None,
                name=name
            )
        elif "string" in entity_type.lower():
            return AcisStringAttrib(
                index=index,
                entity_type=entity_type,
                next_attrib_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
                owner_ref=self._parse_ref(parts[2]) if len(parts) > 2 else None,
                value=parts[-1] if parts else ""
            )
        elif "position" in entity_type.lower():
            return AcisPositionAttrib(
                index=index,
                entity_type=entity_type,
                next_attrib_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
                owner_ref=self._parse_ref(parts[2]) if len(parts) > 2 else None
            )
        elif "rgb_color" in entity_type.lower():
            return AcisRgbColorAttrib(
                index=index,
                entity_type=entity_type,
                next_attrib_ref=self._parse_ref(parts[0]) if len(parts) > 0 else None,
                owner_ref=self._parse_ref(parts[2]) if len(parts) > 2 else None
            )
        else:
            # Generic attribute
            return AcisEntity(index=index, entity_type=entity_type)

    def _parse_pcurve(self, index: int, data: str) -> AcisPCurve:
        """Parse pcurve entity."""
        # Extract the part before { if present
        if "{" in data:
            header_part = data[:data.index("{")].strip()
        else:
            header_part = data

        parts = header_part.split()

        # Extract spline data if present
        spline_data = None
        if "{" in data and "}" in data:
            spline_str = data[data.index("{")+1:data.rindex("}")]
            spline_data = self._parse_spline_curve_data(spline_str)

        return AcisPCurve(
            index=index,
            entity_type="pcurve",
            surface_ref=None,  # Will parse if needed
            intcurve_ref=None,  # Will parse if needed
            spline_data=spline_data
        )

    def _parse_transform(self, index: int, data: str) -> AcisTransform:
        """Parse transform entity."""
        parts = data.split()

        return AcisTransform(
            index=index,
            entity_type="transform",
            scale=float(parts[1]) if len(parts) > 1 else 1.0
        )

    def get_entity(self, ref: int) -> Optional[AcisEntity]:
        """Get entity by reference index."""
        return self.entities.get(ref)

    def get_bodies(self) -> List[AcisBody]:
        """Get all body entities."""
        return [e for e in self.entities.values() if isinstance(e, AcisBody)]

    def get_faces(self) -> List[AcisFace]:
        """Get all face entities."""
        return [e for e in self.entities.values() if isinstance(e, AcisFace)]

