from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ada import Point, RotationalAccelerationField


def add_line_load(
    global_elem: ET.Element,
    lc_elem: ET.Element,
    name: str,
    start_point: Point,
    end_point: Point,
    intensity_start: tuple,
    intensity_end: tuple,
    system: str = "local",
) -> ET.Element:
    """
    Adds a <line_load> under <global><loads><explicit_loads> for a given load case.

    Args:
        global_elem (ET.Element): The <global> element in the XML.
        lc_elem (ET.Element): The <loadcase_basic> element.
        name (str): Name of the line load.
        start_point (tuple): (x, y, z) of line start.
        end_point (tuple): (x, y, z) of line end.
        intensity_start (tuple): (fx, fy, fz) at start.
        intensity_end (tuple): (fx, fy, fz) at end.
        system (str): "local" or "global" coordinate system.

    Returns:
        ET.Element: The created <line_load> element.
    """
    loadcase_ref = lc_elem.attrib["name"]

    # Ensure <loads>/<explicit_loads> structure
    loads_elem = global_elem.find("loads")
    if loads_elem is None:
        loads_elem = ET.SubElement(global_elem, "loads")

    explicit_loads_elem = loads_elem.find("explicit_loads")
    if explicit_loads_elem is None:
        explicit_loads_elem = ET.SubElement(loads_elem, "explicit_loads")

    # Create <line_load>
    line_load = ET.SubElement(explicit_loads_elem, "line_load", {"loadcase_ref": loadcase_ref, "name": name})

    # Add footprint
    footprint = ET.SubElement(line_load, "footprint")
    footprint_line = ET.SubElement(footprint, "footprint_line")
    line = ET.SubElement(footprint_line, "line")
    ET.SubElement(
        line, "position", {"end": "1", "x": str(start_point[0]), "y": str(start_point[1]), "z": str(start_point[2])}
    )
    ET.SubElement(
        line, "position", {"end": "2", "x": str(end_point[0]), "y": str(end_point[1]), "z": str(end_point[2])}
    )

    # Add intensity
    intensity = ET.SubElement(line_load, "intensity")
    component = ET.SubElement(intensity, "component1d_linear", {"intensity_system": system, "position_system": system})

    ET.SubElement(
        component,
        "intensity",
        {"end": "1", "fx": str(intensity_start[0]), "fy": str(intensity_start[1]), "fz": str(intensity_start[2])},
    )
    ET.SubElement(
        component,
        "intensity",
        {"end": "2", "fx": str(intensity_end[0]), "fy": str(intensity_end[1]), "fz": str(intensity_end[2])},
    )

    return line_load


def add_point_load(
    global_elem: ET.Element,
    lc_elem: ET.Element,
    name: str,
    position: tuple,
    force: tuple,
    moment: tuple = (0, 0, 0),
    system: str = "local",
) -> ET.Element:
    """
    Adds a <point_load> under <global><loads><explicit_loads> for a given load case.

    Args:
        global_elem (ET.Element): The <global> element in the XML.
        lc_elem (ET.Element): The <loadcase_basic> element.
        name (str): Name of the point load.
        position (tuple): (x, y, z) of the point.
        force (tuple): (fx, fy, fz) force vector.
        moment (tuple): (mx, my, mz) moment vector (optional).
        system (str): Coordinate system ("local" or "global").

    Returns:
        ET.Element: The created <point_load> element.
    """
    loadcase_ref = lc_elem.attrib["name"]

    # Ensure <loads>/<explicit_loads> structure
    loads_elem = global_elem.find("loads")
    if loads_elem is None:
        loads_elem = ET.SubElement(global_elem, "loads")

    explicit_loads_elem = loads_elem.find("explicit_loads")
    if explicit_loads_elem is None:
        explicit_loads_elem = ET.SubElement(loads_elem, "explicit_loads")

    # Create <point_load>
    point_load = ET.SubElement(explicit_loads_elem, "point_load", {"loadcase_ref": loadcase_ref, "name": name})

    # Add footprint
    footprint = ET.SubElement(point_load, "footprint")
    footprint_point = ET.SubElement(footprint, "footprint_point")
    ET.SubElement(footprint_point, "point", {"x": str(position[0]), "y": str(position[1]), "z": str(position[2])})

    # Add intensity
    intensity = ET.SubElement(point_load, "intensity")
    component = ET.SubElement(
        intensity, "component0d_constant", {"intensity_system": system, "position_system": system}
    )
    intensity_values = ET.SubElement(component, "intensity")

    ET.SubElement(intensity_values, "force", {"fx": str(force[0]), "fy": str(force[1]), "fz": str(force[2])})
    ET.SubElement(intensity_values, "moment", {"mx": str(moment[0]), "my": str(moment[1]), "mz": str(moment[2])})

    return point_load


def add_surface_load_polygon(
    global_elem: ET.Element,
    lc_elem: ET.Element,
    name: str,
    points: list,
    pressure: float,
    system: Literal["local", "global"] = "local",
) -> ET.Element:
    """
    Adds a <surface_load> with a direct polygon footprint.

    Args:
        global_elem (ET.Element): The <global> element.
        lc_elem (ET.Element): The <loadcase_basic> element.
        name (str): Name of the surface load.
        points (list): List of (x, y, z) tuples defining the polygon.
        pressure (float): Pressure value.
        system (str): Coordinate system for intensity and position.

    Returns:
        ET.Element: The created <surface_load> element.
    """
    loadcase_ref = lc_elem.attrib["name"]

    # Ensure <loads>/<explicit_loads> structure
    loads_elem = global_elem.find("loads")
    if loads_elem is None:
        loads_elem = ET.SubElement(global_elem, "loads")

    explicit_loads_elem = loads_elem.find("explicit_loads")
    if explicit_loads_elem is None:
        explicit_loads_elem = ET.SubElement(loads_elem, "explicit_loads")

    # Create <surface_load>
    surface_load = ET.SubElement(explicit_loads_elem, "surface_load", {"loadcase_ref": loadcase_ref, "name": name})

    # Add polygon footprint
    footprint = ET.SubElement(surface_load, "footprint")
    footprint_poly = ET.SubElement(footprint, "footprint_polygon")
    points_elem = ET.SubElement(footprint_poly, "points")
    for p in points:
        ET.SubElement(points_elem, "point", {"x": str(p[0]), "y": str(p[1]), "z": str(p[2])})

    # Add intensity
    intensity = ET.SubElement(surface_load, "intensity")
    pressure_elem = ET.SubElement(
        intensity, "pressure2d_constant", {"intensity_system": system, "position_system": system}
    )
    ET.SubElement(pressure_elem, "intensity", {"pressure": str(pressure)})

    return surface_load


def add_surface_load_plate(
    global_elem: ET.Element,
    lc_elem: ET.Element,
    name: str,
    plate_ref: str,
    pressure: float,
    side: Literal["front", "back"] = "front",
    system: Literal["local", "global"] = "local",
) -> ET.Element:
    """
    Adds a <surface_load> that references a plate.

    Args:
        global_elem (ET.Element): The <global> element.
        lc_elem (ET.Element): The <loadcase_basic> element.
        name (str): Name of the surface load.
        plate_ref (str): Reference to the plate name.
        pressure (float): Pressure value.
        side (str): Plate side ("front" or "back").
        system (str): Coordinate system for intensity and position.

    Returns:
        ET.Element: The created <surface_load> element.
    """
    loadcase_ref = lc_elem.attrib["name"]

    # Ensure <loads>/<explicit_loads> structure
    loads_elem = global_elem.find("loads")
    if loads_elem is None:
        loads_elem = ET.SubElement(global_elem, "loads")

    explicit_loads_elem = loads_elem.find("explicit_loads")
    if explicit_loads_elem is None:
        explicit_loads_elem = ET.SubElement(loads_elem, "explicit_loads")

    # Create <surface_load>
    surface_load = ET.SubElement(explicit_loads_elem, "surface_load", {"loadcase_ref": loadcase_ref, "name": name})

    # Add footprint using plate reference
    footprint = ET.SubElement(surface_load, "footprint")
    ET.SubElement(footprint, "footprint_plate", {"plate_ref": plate_ref, "side": side})

    # Add intensity
    intensity = ET.SubElement(surface_load, "intensity")
    pressure_elem = ET.SubElement(
        intensity, "pressure2d_constant", {"intensity_system": system, "position_system": system}
    )
    ET.SubElement(pressure_elem, "intensity", {"pressure": str(pressure)})

    return surface_load


def add_acceleration_field_load(
    global_elem: ET.Element,
    lc_elem: ET.Element,
    acceleration: tuple[float, float, float] = (0.0, 0.0, -9.80665),
    include_selfweight: bool = True,
    rotational_field: RotationalAccelerationField = None,
) -> ET.Element:
    """
    Adds a <gravity_load> element with self-weight to the loadcase in <environmental_loads>.

    Args:
        global_elem (ET.Element): The <global> analysis element.
        lc_elem (ET.Element): The <loadcase_basic> element.
        acceleration (tuple): Acceleration vector (x, y, z).
        include_selfweight (bool): Whether to include selfweight.
        rotational_field (RotationalAccelerationField): Whether to include a rotational field.

    Returns:
        ET.Element: The created <gravity_load> element.
    """
    loadcase_ref = lc_elem.attrib["name"]

    # Ensure <loads>/<environmental_loads> structure
    loads_elem = global_elem.find("loads")
    if loads_elem is None:
        loads_elem = ET.SubElement(global_elem, "loads")

    env_loads_elem = loads_elem.find("environmental_loads")
    if env_loads_elem is None:
        env_loads_elem = ET.SubElement(loads_elem, "environmental_loads")

    # Add <gravity_load> (always with selfweight)
    self_weight_str = str(include_selfweight).lower()
    gravity_elem = ET.SubElement(
        env_loads_elem,
        "gravity_load",
        {"loadcase_ref": loadcase_ref, "include_selfweight": self_weight_str},
    )

    ET.SubElement(
        env_loads_elem,
        "dummy_rotation_field",
        {"loadcase_ref": loadcase_ref, "include_selfweight": self_weight_str},
    )
    ET.SubElement(
        gravity_elem, "acceleration", {"x": str(acceleration[0]), "y": str(acceleration[1]), "z": str(acceleration[2])}
    )
    if rotational_field:
        rot_elem = ET.SubElement(
            env_loads_elem,
            "rotation_field",
            {
                "loadcase_ref": loadcase_ref,
                "include_selfweight": self_weight_str,
                "angular_velocity": str(rotational_field.angular_velocity),
                "angular_acceleration": str(rotational_field.angular_acceleration),
            },
        )
        base = rotational_field.rotational_point
        axis = rotational_field.rotational_axis
        ET.SubElement(
            rot_elem,
            "base",
            {"x": str(base.x), "y": str(base.y), "z": str(base.z)},
        )
        ET.SubElement(
            rot_elem,
            "axis",
            {"x": str(axis.x), "y": str(axis.y), "z": str(axis.z)},
        )

    return gravity_elem
