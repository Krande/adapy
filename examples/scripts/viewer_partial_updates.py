import ada
from ada.visit.deprecated.websocket_server import SceneAction


def main():
    p0 = ada.Point(0, 0, 0)
    p1 = p0 + 1
    a = ada.Assembly() / (ada.Part("Box12Parent") / ada.PrimBox("box1", p0, p1))
    a.show(scene_action=SceneAction.NEW)

    input("Press Enter to continue")
    p0 = p1 + 1
    p1 = p0 + 1
    a = ada.Assembly() / (ada.Part("Box22Parent") / ada.PrimBox("box2", p0, p1))
    a.show(scene_action=SceneAction.ADD, auto_reposition=False, merge_meshes=False)


if __name__ == "__main__":
    main()
