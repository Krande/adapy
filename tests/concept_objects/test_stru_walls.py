import logging

import ifcopenshell.validate

from ada import Assembly, Part, Wall
from ada.param_models.basic_structural_components import Door, Window


def test_wall_simple(dummy_display):
    points = [(0, 0, 0), (5, 0, 0), (5, 5, 0)]
    w = Wall("MyWall", points, 3, 0.15, offset="LEFT")
    wi = Window("MyWindow1", 1.5, 1, 0.15)
    wi2 = Window("MyWindow2", 2, 1, 0.15)
    door = Door("Door1", 1.5, 2, 0.2)
    w.add_insert(wi, 0, 1, 1.2)
    w.add_insert(wi2, 1, 1, 1.2)
    w.add_insert(door, 0, 3.25, 0)

    a = Assembly("MyAssembly")
    p = Part("MyPart")
    a.add_part(p)
    p.add_wall(w)
    f = a.to_ifc(file_obj_only=True)
    ifcopenshell.validate.validate(f, logging)
    dummy_display(a)
