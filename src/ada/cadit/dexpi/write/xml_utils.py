"""Deterministic XML emission, shared by both DEXPI writers.

Two files that say the same thing must come out byte-identical, because the checked-in example
fixtures are re-generated and compared in CI. ``ElementTree`` gives no help there -- it writes
attributes in insertion order, ``repr``-formats nothing, and indents nothing -- so the three things
that would otherwise drift are settled here:

* **attribute order** is the order the writer lists them in, and a ``None`` value is dropped rather
  than written as the string ``"None"``;
* **floats** are rendered to six significant digits, exactly the rendering
  :func:`ada.cadit.dexpi.canonical.format_float` compares on, so a coordinate that survives a
  round trip compares equal instead of merely close;
* **layout** is two-space indentation, an XML declaration and LF line endings, on every platform.

Nothing here knows anything about DEXPI. It is the thin layer that makes ``ElementTree`` output
reproducible.
"""

from __future__ import annotations

import copy
import pathlib
import xml.etree.ElementTree as ET
from typing import Iterable, Mapping, Sequence

__all__ = [
    "DECLARATION",
    "INDENT",
    "copy_of",
    "element",
    "format_float",
    "point_element",
    "sub_element",
    "text_element",
    "to_text",
    "write_element",
]

INDENT = "  "
DECLARATION = '<?xml version="1.0" encoding="utf-8"?>'

# Six significant digits: enough to distinguish anything a P&ID actually holds, coarse enough that
# a value re-parsed from text does not differ from the one that was written.
_FLOAT_FORMAT = "{:.6g}"


def format_float(value: float | int | None) -> str | None:
    """Render a number the way both writers and the canonical oracle do, or None for None."""
    if value is None:
        return None
    return _FLOAT_FORMAT.format(float(value))


def element(tag: str, attributes: Mapping[str, object] | None = None) -> ET.Element:
    """A new element with ``attributes`` in the given order, skipping the ``None`` ones.

    Skipping is the point: an optional DEXPI attribute is expressed by its absence, and writing
    ``Units="None"`` would be read back as a unit called "None".
    """
    out = ET.Element(tag)
    for key, value in (attributes or {}).items():
        if value is None:
            continue
        out.set(key, value if isinstance(value, str) else str(value))
    return out


def sub_element(parent: ET.Element, tag: str, attributes: Mapping[str, object] | None = None) -> ET.Element:
    """:func:`element`, appended to ``parent``."""
    out = element(tag, attributes)
    parent.append(out)
    return out


def text_element(parent: ET.Element, tag: str, text: str | None) -> ET.Element:
    """A leaf element carrying ``text`` -- a DEXPI 2.0 ``<String>``, ``<Double>`` and friends."""
    out = sub_element(parent, tag)
    out.text = text
    return out


def point_element(parent: ET.Element, tag: str, point: Sequence[float] | None) -> ET.Element | None:
    """An ``X``/``Y``/``Z`` triple, or nothing at all when ``point`` is None.

    ``Z`` is always written even on a 2D sheet, where it is zero: the reader defaults it, but a
    document that states it is one fewer thing for the next consumer to assume.
    """
    if point is None:
        return None
    values = list(point) + [0.0, 0.0, 0.0]
    return sub_element(
        parent,
        tag,
        {"X": format_float(values[0]), "Y": format_float(values[1]), "Z": format_float(values[2])},
    )


def copy_of(source: ET.Element) -> ET.Element:
    """A detached deep copy of ``source``, for echoing a subtree the model does not hold."""
    return copy.deepcopy(source)


def append_all(parent: ET.Element, children: Iterable[ET.Element]) -> None:
    """Append deep copies of ``children`` -- they may still be owned by the source document."""
    for child in children:
        parent.append(copy_of(child))


def to_text(root: ET.Element) -> str:
    """Serialize ``root`` to an indented document with an XML declaration.

    Works on a copy, because indenting rewrites the ``text`` and ``tail`` of every element and the
    caller's tree may hold subtrees still owned by a parsed source document.
    """
    indented = copy_of(root)
    ET.indent(indented, space=INDENT)
    return f"{DECLARATION}\n{ET.tostring(indented, encoding='unicode')}\n"


def write_element(root: ET.Element, destination: str | pathlib.Path) -> pathlib.Path:
    """Write ``root`` to ``destination`` as UTF-8 with LF line endings, and return the path.

    The line endings are forced rather than left to the platform so that a fixture generated on
    Windows and one generated on Linux are the same file.
    """
    path = pathlib.Path(destination)
    if path.parent != pathlib.Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        fp.write(to_text(root))
    return path
