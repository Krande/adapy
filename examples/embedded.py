import ada


def main():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")
    bm.show(embed_glb=True)


if __name__ == "__main__":
    main()
