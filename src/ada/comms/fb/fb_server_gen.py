from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import List, Optional

from ada.comms.fb.fb_base_gen import ErrorDC, FileObjectDC
from ada.comms.fb.fb_commands_gen import CommandTypeDC


@dataclass
class ServerProcessInfoDC:
    pid: int = None
    thread_id: int = None
    log_file_path: pathlib.Path | str = ""


@dataclass
class ProceduralModelSaveDC:
    model_id: str = ""
    doc_json: str = ""
    expected_content_hash: str = ""


@dataclass
class ProceduralModelSaveReplyDC:
    model_id: str = ""
    content_hash: str = ""


@dataclass
class ProceduralModelListEntryDC:
    model_id: str = ""
    content_hash: str = ""
    modified_at: int = None
    size_bytes: int = None


@dataclass
class ProceduralModelListReplyDC:
    entries: Optional[List[ProceduralModelListEntryDC]] = None


@dataclass
class ProceduralModelLoadDC:
    model_id: str = ""


@dataclass
class ProceduralModelLoadReplyDC:
    model_id: str = ""
    doc_json: str = ""
    content_hash: str = ""


@dataclass
class ServerReplyDC:
    message: str = ""
    file_objects: Optional[List[FileObjectDC]] = None
    reply_to: Optional[CommandTypeDC] = None
    error: Optional[ErrorDC] = None
    process_info: Optional[ServerProcessInfoDC] = None
    save_procedural_model: Optional[ProceduralModelSaveReplyDC] = None
    list_procedural_models: Optional[ProceduralModelListReplyDC] = None
    load_procedural_model: Optional[ProceduralModelLoadReplyDC] = None


@dataclass
class ServerDC:
    new_file_object: Optional[FileObjectDC] = None
    all_file_objects: Optional[List[FileObjectDC]] = None
    get_file_object_by_name: str = ""
    get_file_object_by_path: pathlib.Path | str = ""
    delete_file_object: Optional[FileObjectDC] = None
    start_file_in_local_app: Optional[FileObjectDC] = None
    save_procedural_model: Optional[ProceduralModelSaveDC] = None
    load_procedural_model: Optional[ProceduralModelLoadDC] = None
