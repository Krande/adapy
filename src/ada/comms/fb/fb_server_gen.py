from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import List, Optional

from ada.comms.fb.fb_base_gen import ErrorDC, FileObjectDC
from ada.comms.fb.fb_commands_gen import CommandTypeDC


@dataclass
class ServerReplyDC:
    message: str = ""
    file_objects: Optional[List[FileObjectDC]] = None
    reply_to: Optional[CommandTypeDC] = None
    error: Optional[ErrorDC] = None


@dataclass
class ServerDC:
    new_file_object: Optional[FileObjectDC] = None
    all_file_objects: Optional[List[FileObjectDC]] = None
    get_file_object_by_name: str = ""
    get_file_object_by_path: pathlib.Path | str = ""
    delete_file_object: Optional[FileObjectDC] = None
    start_file_in_local_app: Optional[FileObjectDC] = None
