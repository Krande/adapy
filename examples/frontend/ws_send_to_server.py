import ada


def send_ada_model_to_ws_server():
    bm1 = ada.Beam("MyBm", (0, 0, 0), (1, 0, 0), 'IPE300', color='red')
    bm2 = ada.Beam("MyBm2", (0, 1, 0), (1, 1, 0), 'IPE300', color='green')
    a = ada.Assembly() / (ada.Part("MyPart") / [bm1, bm2])
    b = ada.Assembly()
    b.fem = a.to_fem_obj(0.1, 'solid')
    a.show()


if __name__ == '__main__':
    send_ada_model_to_ws_server()
