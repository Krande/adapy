from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

from ada.comms.cli_node_editor_startup import NODE_EDITOR_CLI_PY
from ada.comms.fb_wrap_model_gen import MessageDC
from ada.config import logger

if TYPE_CHECKING:
    from ada.comms.wsock_server import ConnectedClient, WebSocketAsyncServer


def start_separate_node_editor(server: WebSocketAsyncServer, client: ConnectedClient, message: MessageDC) -> None:
    logger.info("Starting separate node editor")
    python_executable = sys.executable
    args = [
        python_executable,
        NODE_EDITOR_CLI_PY.as_posix(),
        "--target-instance",
        str(message.instance_id),
        "--auto-open",
    ]
    args_str = " ".join(args)
    command = f'start cmd.exe /K "{args_str}"'
    subprocess.run(command, shell=True, creationflags=subprocess.CREATE_NEW_CONSOLE)
