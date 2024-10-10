import argparse
import pathlib

from ada.comms.web_ui import start_serving

NODE_EDITOR_CLI_PY = pathlib.Path(__file__)


def start_node_editor_app():
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-port", type=int, default=5174)
    parser.add_argument("--target-instance", type=int)
    parser.add_argument("--auto-open", action="store_true")
    args = parser.parse_args()

    start_serving(
        web_port=args.web_port, node_editor_only=True, target_instance=args.target_instance, auto_open=args.auto_open
    )


if __name__ == "__main__":
    start_node_editor_app()
