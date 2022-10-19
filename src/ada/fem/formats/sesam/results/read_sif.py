from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

import numpy as np

if TYPE_CHECKING:
    from ada.fem.results.common import FEAResult


@dataclass
class SifReader:
    file: Iterator

    nodes: np.ndarray = None
    elements: list[tuple] = None

    def read_coords(self, first_line: str):
        first_vals = [float(x) for x in first_line.split()]
        yield first_vals

        while True:
            next_line = next(self.file)
            stripped = next_line.strip()

            if stripped.startswith("GCOORD") is False:
                break

            yield [float(x) for x in stripped.split()]

    def eval_flags(self, data: str):
        stripped = data.strip()

        self.nodes = np.array(list(self.read_coords(stripped)))

    def load(self):
        while True:
            try:
                curr = next(self.file)
                self.eval_flags(curr)
            except StopIteration:
                break


def read_sif_file(sif_file: str | pathlib.Path) -> FEAResult:
    from ada.fem.results.common import FEAResult

    with open(sif_file, "r") as f:
        sr = SifReader(f)
        sr.load()

    return FEAResult()
