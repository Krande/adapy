import ada


def deck(p0, w, l):
    return ada.Part('deck') / ada.Plate("pl1", [(0, 0), (w, 0), (w, l), (0, l)], 0.01, origin=p0)


def main():
    dck = deck((0, 0, 0), 10, 20)

    a = ada.Assembly("MyBaseStructure") / dck
    a.show(add_ifc_backend=True)


if __name__ == '__main__':
    main()
