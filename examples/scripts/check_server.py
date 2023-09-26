from ada.visit.websocket_server import WebSocketServer


def main():
    ws = WebSocketServer(port=8765)
    result = ws.check_server_running()
    print(result)


if __name__ == '__main__':
    main()
