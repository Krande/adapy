import ada
from ada.base.ifc_types import SpatialTypes


def build_and_show():
    dp = ada.Point(0.5, 0.5, 0.5)
    boxes = []
    coords = [(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)]
    for i, box_c in enumerate(coords, start=1):
        p1 = ada.Point(box_c) - dp
        p2 = ada.Point(box_c) + dp
        box = ada.PrimBox(f"box{i}", p1, p2)
        boxes.append(box)

    p = ada.Part("MySpaces", ifc_class=SpatialTypes.IfcSpace) / boxes
    p.show()
    # p_storey = ada.Part("MyStorey") / p
    p.add_group('MyBoxes', boxes)
    a = ada.Assembly("MySite") / p
    layer = a.presentation_layers.add_layer('Hidden', 'Hidden Layer')
    layer.members.extend(boxes)

    a.to_ifc("temp/space_boxes.ifc")


if __name__ == "__main__":
    build_and_show()
