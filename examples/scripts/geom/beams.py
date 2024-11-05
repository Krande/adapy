import ada


def build_and_show():
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    bm = ada.Beam('bm1', (0, 0, 0), (1, 0, 0), 'TG1000x300x20x30')
    p = ada.Part('MyPart') / (pl, bm)
    p.show()

    bm.up = ada.Direction((0, 0, -1))
    p.show()

    h = bm.section.h
    offset = 1 * bm.up * h / 2
    bm.e1 = offset
    bm.e2 = offset
    p.show()
    print('done')


if __name__ == '__main__':
    build_and_show()
