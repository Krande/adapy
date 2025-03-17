import ada


def main():
    w = 5
    pl = ada.Plate('pl1', [(0, 0), (w, 0), (w, w), (0, w)], 0.01)
    beams = ada.Beam.array_from_list_of_coords(pl.poly.points3d, 'IPE300', make_closed=True)
    p = ada.Part('myPart') / (pl, *beams)
    p.placement = p.placement.rotate((0,0,1), 45)
    p.show(embed_glb=True)


if __name__ == "__main__":
    main()
