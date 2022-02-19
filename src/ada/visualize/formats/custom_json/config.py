from dataclasses import dataclass


@dataclass
class ExportConfig:
    quality: float = 1.0
    threads: int = 1
    parallel: bool = True
    merge_by_colour: bool = False
