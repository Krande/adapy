from __future__ import annotations

import pathlib
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class ParameterTypeDC(Enum):
    UNKNOWN = 0
    STRING = 1
    FLOAT = 2
    INTEGER = 3
    BOOLEAN = 4
    ARRAY = 6


class ArrayTypeDC(Enum):
    TUPLE = 0
    LIST = 1
    SET = 2


class FilePurposeDC(Enum):
    DESIGN = 0
    ANALYSIS = 1
    FABRICATE = 2


class FileTypeDC(Enum):
    IFC = 0
    GLB = 1
    SQLITE = 2
    XLSX = 3
    CSV = 4


@dataclass
class ValueDC:
    string_value: str = ""
    float_value: float = None
    integer_value: int = None
    boolean_value: bool = None
    array_value: Optional[List[ValueDC]] = None
    array_value_type: Optional[ParameterTypeDC] = None
    array_length: int = None
    array_type: Optional[ArrayTypeDC] = None
    array_any_length: bool = None


@dataclass
class ParameterDC:
    name: str = ""
    type: Optional[ParameterTypeDC] = None
    value: Optional[ValueDC] = None
    default_value: Optional[ValueDC] = None
    options: Optional[List[ValueDC]] = None


@dataclass
class ErrorDC:
    code: int = None
    message: str = ""


@dataclass
class ProcedureStartDC:
    procedure_name: str = ""
    procedure_id_string: str = ""
    parameters: Optional[List[ParameterDC]] = None


@dataclass
class FileObjectDC:
    name: str = ""
    file_type: Optional[FileTypeDC] = None
    purpose: Optional[FilePurposeDC] = None
    filepath: pathlib.Path | str = ""
    filedata: bytes = None
    glb_file: Optional[FileObjectDC] = None
    ifcsqlite_file: Optional[FileObjectDC] = None
    is_procedure_output: bool = None
    procedure_parent: Optional[ProcedureStartDC] = None
    compressed: bool = False


@dataclass
class FileObjectRefDC:
    name: str = ""
    file_type: Optional[FileTypeDC] = None
    purpose: Optional[FilePurposeDC] = None
    filepath: pathlib.Path | str = ""
    glb_file: Optional[FileObjectRefDC] = None
    ifcsqlite_file: Optional[FileObjectRefDC] = None
    is_procedure_output: bool = None
    procedure_parent: Optional[ProcedureStartDC] = None


@dataclass
class FileArgDC:
    arg_name: str = ""
    file_type: Optional[FileTypeDC] = None
