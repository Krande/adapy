from __future__ import annotations
from typing import Optional, List
from enum import Enum
from dataclasses import dataclass

from ada.comms.fb.fb_base_gen import ProcedureStartDC, ParameterDC, FileArgDC, FileArgDC



class ProcedureStateDC(Enum):
    IDLE = 0
    RUNNING = 1
    FINISHED = 2
    ERROR = 3

@dataclass
class ProcedureStoreDC:
    procedures: Optional[List[ProcedureDC]] = None
    start_procedure: Optional[ProcedureStartDC] = None

@dataclass
class ProcedureDC:
    name: str = ""
    description: str = ""
    script_file_location: str = ""
    parameters: Optional[List[ParameterDC]] = None
    file_inputs: Optional[List[FileArgDC]] = None
    file_outputs: Optional[List[FileArgDC]] = None
    state: Optional[ProcedureStateDC] = None
    is_component: bool = None
