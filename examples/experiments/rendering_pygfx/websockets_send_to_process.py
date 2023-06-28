#!/usr/bin/env python
import io

from websockets.sync.client import connect

import ada


def hello():
    bm = ada.Beam("my_beam_x", (2, 0, 0), (2, 0, 1), "IPE300", color="red")
    a = ada.Assembly() / bm
    data = io.BytesIO()
    a.to_trimesh_scene().export(data, file_type="glb")
    with connect("ws://localhost:8765") as websocket:
        websocket.send(data.getvalue())


if __name__ == "__main__":
    hello()
