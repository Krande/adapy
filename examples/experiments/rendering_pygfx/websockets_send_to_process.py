import io
import time
from websockets.sync.client import connect

import ada


# a decorator that times a function and passes the args and kwargs to it
def time_it(func):
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"Function {func.__name__} elapsed: {end - start}")
        return result

    return wrapper


@time_it
def build_it() -> ada.Assembly:
    objects = []
    xnum = 10
    ynum = 10
    znum = 10
    i = 0
    for x in range(0, xnum):
        for y in range(0, ynum):
            for z in range(0, znum):
                objects.append(ada.Beam(f"my_beam_{x=}{y=}{z=}", (x, y, z + 0.2), (x, y, z + 0.8), "IPE300"))
                i += 1

    a = ada.Assembly() / objects

    return a


@time_it
def tessellate_it(a: ada.Assembly) -> io.BytesIO:
    data = io.BytesIO()
    a.to_trimesh_scene().export(data, file_type="glb")
    return data


@time_it
def send_using_websockts(data: io.BytesIO):
    with connect("ws://localhost:8765") as websocket:
        websocket.send(data.getvalue())


def send_to_viewer():
    a = build_it()
    data = tessellate_it(a)
    send_using_websockts(data)


if __name__ == "__main__":
    send_to_viewer()
