from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

color_dict = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "light-gray": (204, 204, 204),
    "black": (0, 0, 0),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "gray": (128, 128, 128),
    "lightgray": (211, 211, 211),
    "brown": (165, 42, 42),
}

_col_vals = [(*x, 1) for x in color_dict.values()]


@dataclass
class Color:
    red: float
    green: float
    blue: float
    opacity: float = 1.0

    def __post_init__(self):
        # If any value is above 1, normalize
        if self.red > 1 or self.green > 1 or self.blue > 1:
            self.red /= 255
            self.green /= 255
            self.blue /= 255

        if self.opacity is None:
            self.opacity = 1.0

    def __iter__(self):
        return iter((self.red, self.green, self.blue, self.opacity))

    def __hash__(self):
        return hash((self.red, self.green, self.blue, self.opacity))

    def __gt__(self, other):
        return (
            self.red > other.red
            and self.green > other.green
            and self.blue > other.blue
            and self.opacity > other.opacity
        )

    def __lt__(self, other):
        return (
            self.red < other.red
            and self.green < other.green
            and self.blue < other.blue
            and self.opacity < other.opacity
        )

    @staticmethod
    def randomize() -> Color:
        res = _col_vals[random.randint(0, len(color_dict) - 1)]
        return Color(*res)

    @staticmethod
    def from_str(color_str: str, opacity: float = None) -> Color:
        if color_str.lower() not in color_dict.keys():
            raise ValueError(f"Color {color_str} not supported. Please use one of {color_dict.keys()}")
        return Color(*color_dict[color_str.lower()], opacity=opacity)

    @property
    def transparency(self):
        return 1.0 - self.opacity

    @property
    def transparent(self):
        return False if self.opacity == 1.0 else True

    @property
    def rgb(self) -> tuple[float, float, float]:
        return self.red, self.green, self.blue

    @property
    def rgb255(self) -> tuple[int, int, int]:
        return int(self.red * 255), int(self.green * 255), int(self.blue * 255)

    @property
    def hex(self) -> str:
        return f"#{self.rgb255[0]:02x}{self.rgb255[1]:02x}{self.rgb255[2]:02x}"


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
