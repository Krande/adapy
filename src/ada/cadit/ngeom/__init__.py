"""NGEOM — neutral-geometry interchange between adapy (ada.geom) and adacpp.

Pure-Python serializer of ``ada.geom`` into the NGEOM binary buffer (see the spec at
the neutral-geometry schema spec). adapy has NO dependency on adacpp; the buffer
is the only contract. adacpp decodes the buffer into its neutral geometry layer and
tessellates it via the libtess2 or ifcopenshell-taxonomy pipelines.
"""

from .serialize import NGEOM_VERSION, serialize_geometries

__all__ = ["serialize_geometries", "NGEOM_VERSION"]
