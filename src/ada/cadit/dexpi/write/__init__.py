"""Writers turning a :class:`~ada.cadit.dexpi.model.DexpiDocument` back into a DEXPI file.

One writer per serialization, both fed by the same neutral model, each the exact inverse of the
reader beside it. :func:`write_dexpi` picks between them.

Never compare two DEXPI files byte for byte. Attribute order, self-closing form, float formatting
and the unreliable ``@NumPoints``/``@Number`` counters all differ legitimately between two
serializations of the same document -- and this writer deliberately recomputes the counters rather
than echoing them. The round-trip claim is
``read(write(doc)) == doc`` under :func:`ada.cadit.dexpi.canonical.canonicalize`, which is what
``tests/core/cadit/dexpi/test_write_proteus.py`` and its DEXPI 2.0 twin assert.
"""

from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

from ..model import DexpiDocument, DexpiFlavour
from . import xml_utils
from .write_dexpi20 import write_dexpi20
from .write_proteus import write_proteus

__all__ = ["to_element", "write_dexpi", "write_dexpi20", "write_proteus", "xml_utils"]

_WRITERS = {
    DexpiFlavour.PROTEUS: write_proteus,
    DexpiFlavour.DEXPI20: write_dexpi20,
}


def to_element(doc: DexpiDocument, flavour: DexpiFlavour | str | None = None) -> ET.Element:
    """Render ``doc`` as the root element of the requested flavour.

    ``flavour`` defaults to the one the document was read in, so a re-write is a re-write and a
    conversion has to be asked for.
    """
    resolved = DexpiFlavour(flavour) if flavour is not None else doc.flavour
    return _WRITERS[resolved](doc)


def write_dexpi(
    doc: DexpiDocument,
    destination: str | pathlib.Path,
    flavour: DexpiFlavour | str | None = None,
) -> pathlib.Path:
    """Write ``doc`` to ``destination`` and return the path it was written to.

    ::

        write_dexpi(doc, "plant.xml")                      # same flavour it was read in
        write_dexpi(doc, "plant_2_0.xml", flavour="dexpi20")   # converted

    Converting is lossy in one specific way, and the writers say so where it happens: the verbatim
    echo of everything adapy does not model is an echo of *elements*, and the two flavours have
    different vocabularies. A Proteus document's shape catalogue, labels and presentation cannot be
    carried into a DEXPI 2.0 file, so they are dropped rather than mistranslated.
    """
    return xml_utils.write_element(to_element(doc, flavour), destination)
