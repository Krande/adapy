import ada
from ada.base.ifc_types import SpatialTypes
from ada.visit.renderer_manager import RenderParams


def build_and_show():
    dp = ada.Point(0.5, 0.5, 0.5)
    boxes = []
    coords = [(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)]
    for i, box_c in enumerate(coords, start=1):
        p1 = ada.Point(box_c) - dp
        p2 = ada.Point(box_c) + dp
        box = ada.PrimBox(f"SpaceBox{i}", p1, p2)
        boxes.append(box)

    p = ada.Part("MySpaces", ifc_class=SpatialTypes.IfcSpace) / boxes
    p.add_group('MyBoxes', boxes)

    boxes = []
    offset = ada.Point(0, 0, 3)
    dp = ada.Point(0.5, 0.5, 0.5)
    for i, box_c in enumerate(coords, start=1):
        p1 = offset + ada.Point(box_c) - dp
        p2 = offset +ada.Point(box_c) + dp
        box = ada.PrimBox(f"StoreyBox{i}", p1, p2, color='red', opacity=0.5)
        boxes.append(box)
    p2 = ada.Part("MyStorey") / boxes

    a = ada.Assembly("MySite") / (p, p2)
    layer = a.presentation_layers.add_layer('Hidden', 'Hidden Layer')
    layer.members.extend(boxes)

    a.show(
        params_override=RenderParams(gltf_export_to_file="temp/demo.glb", gltf_asset_extras_dict={"web3dversion": "2"})
    )
    a.to_ifc("temp/space_boxes.ifc")


if __name__ == "__main__":
    build_and_show()
