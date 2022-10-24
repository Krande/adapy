from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class VisColor:
    name: str
    pbrMetallicRoughness: PbrMetallicRoughness
    used_by: list[str]


@dataclass
class PbrMetallicRoughness:
    baseColorFactor: list[float]
    metallicFactor: float
    roughnessFactor: float


class DataColorizer:
    default_palette = [(0, 149 / 255, 239 / 255), (1, 0, 0)]

    @staticmethod
    def colorize_data(data: np.ndarray, func=None, palette=None):
        if func is None:
            shape = data.shape
            num_cols = shape[1]
            if num_cols in (3, 6):
                func = magnitude
            elif num_cols == 1:
                func = magnitude1d

        palette = DataColorizer.default_palette if palette is None else palette

        res = [func(d) for d in data]
        sorte = sorted(res)
        min_r = sorte[0]
        max_r = sorte[-1]

        start = np.array(palette[0])
        end = np.array(palette[-1])

        def curr_p(t):
            return start + (end - start) * t / (max_r - min_r)

        colors = np.asarray(list([curr_p(x) for x in res]), dtype="float32")
        return colors


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)


def magnitude2d(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2)


def magnitude1d(u):
    return u
