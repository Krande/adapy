#!/usr/bin/env python

from websockets.sync.client import connect


def hello():
    with connect("ws://localhost:8765") as websocket:
        websocket.send("Hello world! 2")


if __name__ == "__main__":
    hello()
