"""Which DEXPI serialization a file is in.

The two flavours are told apart by their root element and nothing else: Proteus XML (1.3/1.4) is
rooted at ``<PlantModel>``, DEXPI XML (2.0.0) at ``<Model>``. There is no version attribute worth
trusting -- Proteus files in the wild carry a ``SchemaVersion`` that says 1.3 when they mean 1.4,
and 2.0 documents carry none at all.

Sniffing reads only the start tag, so pointing it at a 400 KB export costs nothing.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

from .model import DexpiFlavour

__all__ = ["DexpiFlavour", "ROOT_TAGS", "sniff_flavour"]

# Root element -> flavour. Namespaced roots are matched on the local name.
ROOT_TAGS: dict[str, DexpiFlavour] = {
    "PlantModel": DexpiFlavour.PROTEUS,
    "Model": DexpiFlavour.DEXPI20,
}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def sniff_flavour(source: str | pathlib.Path | ET.Element | ET.ElementTree) -> DexpiFlavour:
    """Return the flavour of ``source``: a path, an already-parsed element, or a tree.

    Raises ``ValueError`` naming the root tag it actually found, because "this is not a DEXPI file"
    is far less useful than "this file is rooted at <IFC>".
    """
    if isinstance(source, ET.ElementTree):
        root = source.getroot()
    elif isinstance(source, ET.Element):
        root = source
    else:
        path = pathlib.Path(source)
        if not path.exists():
            raise FileNotFoundError(path)
        root = _read_root(path)

    if root is None:
        raise ValueError("empty XML document: no root element to identify a DEXPI flavour from")

    name = _local_name(root.tag)
    flavour = ROOT_TAGS.get(name)
    if flavour is None:
        expected = ", ".join(f"<{tag}>" for tag in ROOT_TAGS)
        raise ValueError(f"not a DEXPI document: root element is <{name}>, expected one of {expected}")
    return flavour


def _read_root(path: pathlib.Path) -> ET.Element | None:
    """Pull the root element out of ``path`` without parsing the rest of the file."""
    with path.open("rb") as fp:
        for _event, element in ET.iterparse(fp, events=("start",)):
            return element
    return None
