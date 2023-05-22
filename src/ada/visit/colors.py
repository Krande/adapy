from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

color_dict = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "gray": (128, 128, 128),
    "brown": (165, 42, 42),
}

_col_vals = [(*x, 1) for x in color_dict.values()]


def random_color() -> Color:
    res = _col_vals[random.randint(0, len(color_dict) - 1)]
    return Color(*res)


@dataclass
class Color:
    red: float
    green: float
    blue: float
    opacity: float = 1.0

    def __iter__(self):
        return iter((self.red, self.green, self.blue, self.opacity))

    def __hash__(self):
        return hash((self.red, self.green, self.blue, self.opacity))

    def __gt__(self, other):
        return self.red > other.red and self.green > other.green and self.blue > other.blue and self.opacity > other.opacity

    def __lt__(self, other):
        return self.red < other.red and self.green < other.green and self.blue < other.blue and self.opacity < other.opacity


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


def color_name_to_rgb(color_name, normalize=True) -> list[float, float, float] | None:
    color_name_lower = color_name.lower()
    if color_name_lower in color_dict:
        if normalize:
            return [x / 255 for x in color_dict[color_name_lower]]
        else:
            return color_dict[color_name_lower]
    else:
        print(f"Color '{color_name}' not found. Please use a supported color name.")
        return None
