from __future__ import annotations

import pathlib
from dataclasses import dataclass
from itertools import groupby

import h5py
import numpy as np


@dataclass
class CCData:
    member_name: str
    code_check_name: str
    components: list[str]
    values: list[np.ndarray]

    def get_component_data(self, name):
        field_map = {x: i for i, x in enumerate(self.components)}
        index = field_map.get(name)
        results = []
        for value in self.values:
            results.append(value[index])
        return results

    def get_max_utilization(self, specific_code: str = None):
        field_map = {x: i for i, x in enumerate(self.components) if x.startswith("uf")}
        max_uf = (0.0, None, None)
        for key, index in field_map.items():
            if specific_code is not None and key != specific_code:
                continue
            for res_point, value in enumerate(self.values):
                uf = value[index]
                if uf > max_uf[0]:
                    max_uf = (uf, key, res_point)
        return max_uf


@dataclass
class CCSesamResult:
    cc_file: str | pathlib.Path

    def __enter__(self):
        self._f = h5py.File(self.cc_file, "r")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._f.close()

    def get_all_results(self, name: str = None) -> dict[str, CCData]:
        cc_data = dict()
        for item in h5py_cc_iterator(self._f):
            comps = item.dtype.names
            for mem, data in groupby(item, key=lambda x: x["Member"]):
                mem_name = str(mem).split("member(")[-1].replace(")", "")[:-1]
                if name is not None and mem_name != name:
                    continue
                data = list(data)
                cc_data[mem_name] = CCData(mem_name, item.name, comps, data)

        return cc_data


def h5py_cc_iterator(g, prefix=""):
    for key in g.keys():
        item = g[key]
        path = "{}/{}".format(prefix, key)
        if isinstance(item, h5py.Dataset):
            yield item
        elif isinstance(item, h5py.Group):
            yield from h5py_cc_iterator(item, path)


def read_cc_file(cc_h5_file) -> dict[str, CCData]:
    with CCSesamResult(cc_h5_file) as cc_ses:
        results = cc_ses.get_all_results()

    return results
