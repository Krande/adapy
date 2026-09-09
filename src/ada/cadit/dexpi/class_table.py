"""Lookup over the vendored DEXPI class table.

``resources/dexpi_classes.json`` is generated from the DEXPI Specification repo by
``scripts/gen_dexpi_class_table.py`` -- see the ``_meta`` block in the JSON for the exact tag and
commit. It holds one entry per class: the package it lives in, its supertypes, the RDL symbol the
spec declared for it, and a model-scoped URI.

The point of vendoring it is :func:`is_a`. Both DEXPI flavours name a class and nothing else --
``ComponentClass="CentrifugalPump"`` or ``type="Plant/ProcessEquipment.CentrifugalPump"`` -- so
without the supertype graph every consumer would have to enumerate several hundred class names to
answer "is this a valve?". With it they ask ``is_a(name, "OperatedValve")``.

The JSON is loaded lazily and cached; nothing here runs at import time.
"""

from __future__ import annotations

import functools
import json
import pathlib

_TABLE_PATH = pathlib.Path(__file__).parent / "resources" / "dexpi_classes.json"

__all__ = [
    "class_names",
    "class_uri",
    "get",
    "is_a",
    "meta",
    "resolve",
    "supertypes",
]


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    with _TABLE_PATH.open(encoding="utf-8") as fp:
        return json.load(fp)


def meta() -> dict:
    """Provenance of the vendored table: source repo, tag, commit, licence."""
    return dict(_load().get("_meta", {}))


def class_names() -> list[str]:
    """Every class name in the table, sorted."""
    return sorted(_load().get("classes", {}))


def resolve(name: str) -> str:
    """Reduce a DEXPI type reference to a bare class name.

    Accepts anything the two flavours put on the wire, plus the URI form the table itself uses::

        resolve("Plant/Piping.BallValve")   -> "BallValve"
        resolve("Core/EngineeringModel")    -> "EngineeringModel"
        resolve("BallValve")                -> "BallValve"

    A DEXPI 2.0 ``type`` puts the model prefix before a single slash and the dotted path inside the
    model after it, so the class name is whatever follows the last dot -- or the last slash for a
    class that sits directly in a model.

    The slash is taken off *before* the dot, which matters for the one form neither flavour is
    supposed to use but emitters do anyway: a full RDL URI in ``ComponentClass``, as in
    ``http://sandbox.dexpi.org/rdl/ProcessInstrumentationFunction``. Splitting on the last dot
    first lands in the *host name* and yields ``org/rdl/ProcessInstrumentationFunction``. Doing it
    in this order also makes ``resolve`` idempotent -- its own output resolves to itself -- which a
    read/write round-trip needs, because the writer emits the resolved name and the reader resolves
    it again.
    """
    token = name.strip()
    if "#" in token:
        token = token.rsplit("#", 1)[-1]
    token = token.rsplit("/", 1)[-1]
    return token.rsplit(".", 1)[-1]


def get(name: str) -> dict | None:
    """The table entry for ``name``, or None if the class is unknown.

    ``name`` may be a bare class name or any qualified form :func:`resolve` accepts.
    """
    return _load().get("classes", {}).get(resolve(name))


def supertypes(name: str) -> list[str]:
    """Direct supertypes of ``name``, as bare class names."""
    entry = get(name)
    if entry is None:
        return []
    return [resolve(st) for st in entry.get("supertypes", [])]


def is_a(name: str, supertype: str) -> bool:
    """True if ``name`` is ``supertype`` or inherits from it.

    DEXPI uses multiple inheritance freely -- a ``Nozzle`` is a ``PipingNodeOwner`` *and* a
    ``SensingLocation`` and four other things -- so this walks the whole supertype DAG rather than
    a single chain. Unknown classes answer False rather than raising: files in the wild carry
    vendor classes the spec never declared, and a reader must not die on one.
    """
    target = resolve(supertype)
    start = resolve(name)
    if start == target:
        return True

    seen = {start}
    queue = supertypes(start)
    while queue:
        current = queue.pop()
        if current == target:
            return True
        if current in seen:
            continue
        seen.add(current)
        queue.extend(supertypes(current))
    return False


def class_uri(name: str) -> str | None:
    """Model-scoped URI of ``name``, e.g.
    ``https://data.dexpi.org/models/2.0.0/Plant.xml#Plant/Piping.BallValve``.

    This is *not* an RDL URI. The spec sources reference RDL classes by symbol
    (``JORD_RDL.BALL_VALVE``) and the symbol->URI table ships with the specificator toolchain
    rather than the Specification repo, so the symbol is kept verbatim under the entry's ``rdl``
    key and the URI here is the model identity DEXPI 2.0 itself uses: the imported model source
    plus the ``type`` string.
    """
    entry = get(name)
    return entry.get("uri") if entry else None
