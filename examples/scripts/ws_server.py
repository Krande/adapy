from ada.visit.websocket_server import WebSocketServer


def start_server():
    host = "localhost"
    port = 8765
    origins_list = []
    origins_list.append("http://localhost:5173")  # development server
    for i in range(8888, 8899):  # local jupyter servers
        origins_list.append(f"http://localhost:{i}")

    server = WebSocketServer(host=host, port=port, client_origins=origins_list, debug_mode=False)
    server.start()


if __name__ == '__main__':
    start_server()
