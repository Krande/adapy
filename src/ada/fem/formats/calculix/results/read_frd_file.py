# Calculix reader inspired by the wonderful work by https://github.com/rsmith-nl/calculix-frdconvert
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import meshio
import numpy as np


@dataclass
class CcxResult:
    name: str
    step: int
    components: list[str]
    values: list[tuple] = field(repr=False)


@dataclass
class CcxResultModel:
    file: Iterator

    nodes: list[tuple] = None
    elements: list[tuple] = None
    results: list[CcxResult] = field(default_factory=list)

    _curr_step: int = None

    def collect_nodes(self):
        nodes = []
        while True:
            data = next(self.file)
            stripped = data.strip()
            if stripped.startswith("-1") is False:
                break
            split = stripped.split()
            nid = int(float(split[1]))
            coords = [float(x) for x in split[2:]]
            nodes.append((nid, *coords))

        self.nodes = nodes
        self.eval_flags(data)

    def collect_elements(self):
        elements = []
        curr_element = []
        while True:
            data = next(self.file)

            stripped = data.strip()
            if stripped.startswith("-1") is False:
                if stripped.startswith("-2") is False:
                    break

            split = stripped.split()

            if stripped.startswith("-1"):
                data = [int(x) for x in split[1:]]
                curr_element = data
            elif stripped.startswith("-2"):
                data = [int(x) for x in split[1:]]
                curr_element += data
                elements.append(tuple(curr_element))
                curr_element = []
            else:
                break

        self.elements = elements
        self.eval_flags(data)

    def collect_results(self, first_line):
        name = first_line.split()[1]

        component_names = []
        component_data = []
        while True:
            data = next(self.file)
            stripped = data.strip()

            if stripped.startswith("-5"):
                split = stripped.split()
                component_names.append(split[1])
            elif stripped.startswith("-1"):
                slen = len(stripped)
                x = 12
                res = [stripped[i : i + x] for i in range(0, slen, x)]
                first_batch = res[0].split()
                nid = int(float(first_batch[1]))
                component_data.append((nid, *[float(x) for x in res[1:]]))
            else:
                break

        self.results.append(CcxResult(name, self._curr_step, component_names, component_data))
        self.eval_flags(data)

    def eval_flags(self, data):
        stripped = data.strip()
        if stripped.startswith("2C"):
            self.collect_nodes()

        if stripped.startswith("3C"):
            self.collect_elements()

        if stripped.startswith("1PSTEP"):
            split_data = stripped.split()
            self._curr_step = int(float(split_data[2]))

        if stripped.startswith("-4"):
            self.collect_results(stripped)

    def load(self):
        while True:
            try:
                curr = next(self.file)
                self.eval_flags(curr)
            except StopIteration:
                break


def read_from_frd_file(frd_file) -> meshio.Mesh:
    with open(frd_file, "r") as f:
        ccx_res_model = CcxResultModel(f)
        ccx_res_model.load()

    nodes = np.asarray(ccx_res_model.nodes)
    cells = np.asarray(ccx_res_model.elements)
    mesh = meshio.Mesh(points=nodes, cells=cells)
    return mesh
