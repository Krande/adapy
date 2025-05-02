from __future__ import annotations
from typing import Optional, List
from dataclasses import dataclass
import pathlib

from ada.comms.fb_base_gen import FileObjectDC, ErrorDC, FileObjectDC, FileObjectDC, FileObjectDC, FileObjectDC
from ada.comms.fb_commands_gen import CommandTypeDC



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
