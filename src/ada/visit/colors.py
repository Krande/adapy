from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

color_dict = {
    # Basic colors
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    # Gray variations
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),  # Alternative spelling
    "light-gray": (204, 204, 204),
    "lightgray": (211, 211, 211),
    "dark-gray": (64, 64, 64),
    "darkgray": (64, 64, 64),
    "silver": (192, 192, 192),
    "dimgray": (105, 105, 105),
    "gainsboro": (220, 220, 220),
    # Red variations
    "darkred": (139, 0, 0),
    "crimson": (220, 20, 60),
    "firebrick": (178, 34, 34),
    "indianred": (205, 92, 92),
    "lightcoral": (240, 128, 128),
    "salmon": (250, 128, 114),
    "darksalmon": (233, 150, 122),
    "lightsalmon": (255, 160, 122),
    "rosybrown": (188, 143, 143),
    "pink": (255, 192, 203),
    "lightpink": (255, 182, 193),
    "hotpink": (255, 105, 180),
    "deeppink": (255, 20, 147),
    "palevioletred": (219, 112, 147),
    "mediumvioletred": (199, 21, 133),
    # Orange variations
    "orange": (255, 165, 0),
    "darkorange": (255, 140, 0),
    "orangered": (255, 69, 0),
    "tomato": (255, 99, 71),
    "coral": (255, 127, 80),
    "peachpuff": (255, 218, 185),
    "papayawhip": (255, 239, 213),
    "moccasin": (255, 228, 181),
    "navajowhite": (255, 222, 173),
    "wheat": (245, 222, 179),
    "sandybrown": (244, 164, 96),
    "tan": (210, 180, 140),
    "burlywood": (222, 184, 135),
    # Yellow variations
    "gold": (255, 215, 0),
    "khaki": (240, 230, 140),
    "darkkhaki": (189, 183, 107),
    "palegoldenrod": (238, 232, 170),
    "goldenrod": (218, 165, 32),
    "darkgoldenrod": (184, 134, 11),
    "lightyellow": (255, 255, 224),
    "lemonchiffon": (255, 250, 205),
    "lightgoldenrodyellow": (250, 250, 210),
    "cornsilk": (255, 248, 220),
    "ivory": (255, 255, 240),
    "beige": (245, 245, 220),
    # Green variations
    "darkgreen": (0, 100, 0),
    "forestgreen": (34, 139, 34),
    "seagreen": (46, 139, 87),
    "mediumseagreen": (60, 179, 113),
    "lightgreen": (144, 238, 144),
    "palegreen": (152, 251, 152),
    "springgreen": (0, 255, 127),
    "mediumspringgreen": (0, 250, 154),
    "lawngreen": (124, 252, 0),
    "chartreuse": (127, 255, 0),
    "greenyellow": (173, 255, 47),
    "lime": (0, 255, 0),
    "limegreen": (50, 205, 50),
    "yellowgreen": (154, 205, 50),
    "darkolivegreen": (85, 107, 47),
    "olivedrab": (107, 142, 35),
    "olive": (128, 128, 0),
    # Blue variations
    "navy": (0, 0, 128),
    "darkblue": (0, 0, 139),
    "mediumblue": (0, 0, 205),
    "royalblue": (65, 105, 225),
    "steelblue": (70, 130, 180),
    "dodgerblue": (30, 144, 255),
    "deepskyblue": (0, 191, 255),
    "skyblue": (135, 206, 235),
    "lightskyblue": (135, 206, 250),
    "lightblue": (173, 216, 230),
    "powderblue": (176, 224, 230),
    "lightcyan": (224, 255, 255),
    "paleturquoise": (175, 238, 238),
    "aquamarine": (127, 255, 212),
    "turquoise": (64, 224, 208),
    "mediumturquoise": (72, 209, 204),
    "darkturquoise": (0, 206, 209),
    "lightseagreen": (32, 178, 170),
    "cadetblue": (95, 158, 160),
    "darkcyan": (0, 139, 139),
    "teal": (0, 128, 128),
    # Purple variations
    "purple": (128, 0, 128),
    "darkviolet": (148, 0, 211),
    "darkorchid": (153, 50, 204),
    "darkmagenta": (139, 0, 139),
    "violet": (238, 130, 238),
    "plum": (221, 160, 221),
    "thistle": (216, 191, 216),
    "orchid": (218, 112, 214),
    "mediumorchid": (186, 85, 211),
    "mediumpurple": (147, 112, 219),
    "blueviolet": (138, 43, 226),
    "slateblue": (106, 90, 205),
    "mediumslateblue": (123, 104, 238),
    "indigo": (75, 0, 130),
    "lavender": (230, 230, 250),
    "lavenderblush": (255, 240, 245),
    # Brown variations
    "brown": (165, 42, 42),
    "maroon": (128, 0, 0),
    "saddlebrown": (139, 69, 19),
    "sienna": (160, 82, 45),
    "chocolate": (210, 105, 30),
    "peru": (205, 133, 63),
    # Misc colors
    "mintcream": (245, 255, 250),
    "honeydew": (240, 255, 240),
    "azure": (240, 255, 255),
    "aliceblue": (240, 248, 255),
    "ghostwhite": (248, 248, 255),
    "whitesmoke": (245, 245, 245),
    "seashell": (255, 245, 238),
    "oldlace": (253, 245, 230),
    "floralwhite": (255, 250, 240),
    "antiquewhite": (250, 235, 215),
    "linen": (250, 240, 230),
    "mistyrose": (255, 228, 225),
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


# Abaqus' default stress / displacement rainbow. Five piecewise-linear
# stops, matched verbatim against the frontend's
# `utils/scene/fea/colormaps.ts:abaqus` so the offscreen poster, the
# chromium snapshot, and the live viewer all paint identical colours
# from identical scalar fields. Used as the default colormap for the
# FEA GLB writer; callers can still override via `cfunc=`.
ABAQUS_COLORMAP_STOPS = (
    (0.00, (0.0, 0.0, 1.0)),  # blue
    (0.25, (0.0, 1.0, 1.0)),  # cyan
    (0.50, (0.0, 1.0, 0.0)),  # green
    (0.75, (1.0, 1.0, 0.0)),  # yellow
    (1.00, (1.0, 0.0, 0.0)),  # red
)


def apply_colormap(t_values: np.ndarray, stops=ABAQUS_COLORMAP_STOPS) -> np.ndarray:
    """Piecewise-linear colormap. `t_values` in [0, 1]; returns RGB float32."""
    t = np.clip(np.asarray(t_values, dtype=np.float32), 0.0, 1.0)
    out = np.zeros((t.size, 3), dtype=np.float32)
    for (t0, c0), (t1, c1) in zip(stops[:-1], stops[1:]):
        a = np.asarray(c0, dtype=np.float32)
        b = np.asarray(c1, dtype=np.float32)
        # Final segment is inclusive on the right so t == 1.0 lands on
        # the last stop instead of falling through and staying at zero.
        if t1 >= 1.0:
            mask = (t >= t0) & (t <= t1)
        else:
            mask = (t >= t0) & (t < t1)
        if not mask.any():
            continue
        local = (t[mask] - t0) / max(t1 - t0, 1e-12)
        out[mask] = a + (b - a) * local[:, None]
    return out


class DataColorizer:
    # Kept for back-compat. New code should rely on the abaqus stops
    # via `apply_colormap`; this two-stop palette only fires when a
    # caller explicitly passes a custom `palette=` and isn't using the
    # default FEA-write path.
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

        res = np.asarray([func(d) for d in data], dtype=np.float32)
        min_r = float(res.min())
        max_r = float(res.max())
        span = max_r - min_r
        # Normalise scalar range to [0, 1]. Zero-range (constant field)
        # would otherwise NaN out; map everything to the colormap's
        # midpoint so the user sees a flat "no variation" colour
        # instead of an unrendered mesh.
        t = (res - min_r) / span if span > 0 else np.full_like(res, 0.5)

        if palette is None:
            # Default: full abaqus rainbow — matches the frontend's
            # default colormap pick (`feaAnimationStore.colormap = "abaqus"`).
            return apply_colormap(t)

        # Legacy two-stop linear interp path. Preserved so callers that
        # opted into a custom palette keep getting their colours.
        start = np.asarray(palette[0], dtype=np.float32)
        end = np.asarray(palette[-1], dtype=np.float32)
        return start + (end - start) * t[:, None]


def magnitude(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)


def magnitude2d(u):
    return np.sqrt(u[0] ** 2 + u[1] ** 2)


def magnitude1d(u):
    return u
