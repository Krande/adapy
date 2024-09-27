import argparse

from ada.visit.deprecated.websocket_server import WebSocketServer


def ws_cli_app():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--origins", type=str, default="localhost")
    parser.add_argument("--host", type=str, default="localhost")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--open-viewer", action="store_true")
    args = parser.parse_args()

    origins_list = []
    for origin in args.origins.split(";"):
        if origin == "localhost":
            origins_list.append("http://localhost:5173")  # development server
            for i in range(8888, 8899):  # local jupyter servers
                origins_list.append(f"http://localhost:{i}")
            origins_list.append("null")  # local html
        else:
            origins_list.append(origin)

    if args.open_viewer:
        from ada.visit.rendering.renderer_react import RendererReact

        RendererReact().show()

    server = WebSocketServer(host=args.host, port=args.port, client_origins=origins_list, debug_mode=args.debug)
    server.start()


if __name__ == "__main__":
    ws_cli_app()
