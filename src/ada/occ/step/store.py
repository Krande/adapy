from __future__ import annotations

import pathlib

from OCC.Extend.DataExchange import read_step_file


class StepStore:
    def __init__(self, step_file: str | pathlib.Path = None):
        self.step_file = step_file

    def iter_shapes(self):
        for shp in read_step_file(self.step_file, as_compound=False):
            yield shp

    @staticmethod
    def get_writer():
        from .writer import StepWriter

        return StepWriter()
