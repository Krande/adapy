import ada


def build_and_show():
    dp = ada.Point(0.5, 0.5, 0.5)
    boxes = []
    for i, box_c in enumerate([(0, 0, 0), (3, 0, 0), (3, 3, 0), (0, 3, 0)], start=1):
        p1 = ada.Point(box_c) - dp
        p2 = ada.Point(box_c) + dp
        box = ada.PrimBox(f"box{i}", p1, p2)
        boxes.append(box)

    p = ada.Part("MyPart") / boxes
    p.show()


if __name__ == "__main__":
    build_and_show()
