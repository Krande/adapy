"""Element keys: one string per model element that every association row is filed under.

THREE FORMS, best first::

    node:<provider>/<collection>/<node_id>              a published node (the provider's own id)
    file:<source_key>#g:<guid>                          an element of a file that persists a GlobalId
    file:<source_key>#p:<normalised name path>          any other element of a file

and one for an element of a published subject whose GLB does not say which node each element is
(no ``node_ids``): the subject's key with the element's name path under it::

    node:<provider>/<collection>/<subject node id>#p:<normalised name path>

A NAME PATH is the element's names from the top of its GLB hierarchy down to itself, each
normalised (:func:`normalise_segment`) and joined by ``/``. It survives a re-import that keeps the
names; a rename makes the old key an orphan (``ada.assoc.records`` flags it), the same limit a
name binding has always had.

A link is UNDER an element X when its row's key is X or X is one of the row's ancestors (``anc``):
a hierarchy level and everything beneath it is one query, with no row per ancestor.

THE CONNECTION KEY (:func:`connection_key`) names a connection independently of the detail run and
the clash run it came from: the spec, its members by role and its centre to the millimetre. It is
the unit of "simulate once".

STDLIB ONLY: the slim API files rows with these keys.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "ElementKey",
    "connection_key",
    "file_key",
    "is_element_key",
    "key_ancestors",
    "node_key",
    "normalise_path",
    "normalise_segment",
    "parse_key",
]

NODE = "node"
FILE = "file"

_ESCAPES = (("%", "%25"), ("/", "%2F"), ("#", "%23"))


def normalise_segment(name: object) -> str:
    """One name of a path, in the one spelling keys use: blanks collapsed and trimmed, upper case
    (the element-name join of ``ada.geom_compare`` is case-blind too), and ``%``, ``/`` and ``#``
    escaped so a name holding them cannot split the path or the key."""
    out = " ".join(str(name or "").split()).upper()
    for raw, esc in _ESCAPES:
        out = out.replace(raw, esc)
    return out


def normalise_path(names: Sequence[object] | str) -> str:
    """A name path: a list of names top-down, or a ``/``-joined string (whose ``/`` split it).
    Empty names are dropped."""
    if isinstance(names, str):
        names = names.split("/")
    return "/".join(s for s in (normalise_segment(n) for n in names) if s)


def _clean(part: object, what: str) -> str:
    text = str(part or "").strip()
    if not text:
        raise ValueError(f"an element key needs its {what}")
    return text


def node_key(
    provider: object, collection: object, node_id: object, *, path: Sequence[object] | str | None = None
) -> str:
    """``node:<provider>/<collection>/<node_id>``, with ``#p:<path>`` for an element under it that is
    known by name only. ``provider`` may be empty (a deployment's only provider)."""
    prov = str(provider or "").strip()
    key = f"{NODE}:{prov}/{_clean(collection, 'collection')}/{_clean(node_id, 'node id')}"
    if path is not None:
        p = normalise_path(path)
        if p:
            key += f"#p:{p}"
    return key


def file_key(source_key: object, *, guid: object = None, path: Sequence[object] | str | None = None) -> str:
    """``file:<source_key>#g:<guid>`` when the file persists the element's guid, else
    ``file:<source_key>#p:<path>``; the bare ``file:<source_key>`` is the file itself."""
    key = f"{FILE}:{_clean(source_key, 'source key').lstrip('/')}"
    if guid not in (None, ""):
        return f"{key}#g:{str(guid).strip()}"
    if path is not None:
        p = normalise_path(path)
        if p:
            return f"{key}#p:{p}"
    return key


@dataclass(frozen=True)
class ElementKey:
    """A parsed element key."""

    form: str  # "node" or "file"
    #: node: provider, collection and node id; file: the source key.
    provider: str = ""
    collection: str = ""
    node_id: str = ""
    source_key: str = ""
    guid: str | None = None
    #: The normalised name path (``#p:``), if any.
    path: str | None = None

    @property
    def base(self) -> str:
        """The key without its ``#`` part: the node or the file."""
        if self.form == NODE:
            return f"{NODE}:{self.provider}/{self.collection}/{self.node_id}"
        return f"{FILE}:{self.source_key}"

    def __str__(self) -> str:
        if self.guid is not None:
            return f"{self.base}#g:{self.guid}"
        if self.path:
            return f"{self.base}#p:{self.path}"
        return self.base


def parse_key(key: str) -> ElementKey:
    """The parts of an element key; ValueError for a string that is not one."""
    text = str(key or "")
    form, sep, rest = text.partition(":")
    if not sep or form not in (NODE, FILE) or not rest:
        raise ValueError(f"{key!r} is not an element key (node:... or file:...)")
    base, _, frag = rest.partition("#")
    guid = path = None
    if frag:
        tag, sep2, value = frag.partition(":")
        if not sep2 or tag not in ("g", "p") or not value:
            raise ValueError(f"{key!r}: the part after '#' must be g:<guid> or p:<path>")
        if tag == "g":
            if form != FILE:
                raise ValueError(f"{key!r}: a guid belongs to a file key")
            guid = value
        else:
            path = value
    if form == NODE:
        bits = base.split("/")
        if len(bits) < 3 or not bits[-1] or not bits[-2]:
            raise ValueError(f"{key!r}: a node key is node:<provider>/<collection>/<node id>")
        # A collection never holds '/', a provider never does either: the node id is what is left.
        provider, collection, node_id = bits[0], bits[1], "/".join(bits[2:])
        return ElementKey(NODE, provider=provider, collection=collection, node_id=node_id, path=path)
    if not base:
        raise ValueError(f"{key!r}: a file key needs its source key")
    return ElementKey(FILE, source_key=base, guid=guid, path=path)


def is_element_key(key: object) -> bool:
    try:
        parse_key(str(key))
    except ValueError:
        return False
    return True


def key_ancestors(key: str) -> list[str]:
    """The ancestors a key itself spells, nearest last: for a name path, the key of each shorter
    path and the bare node or file. A published node's ancestors are not in its key (they are the
    spine's) and are not listed here."""
    parsed = parse_key(key)
    out = [parsed.base] if parsed.path or parsed.guid is not None else []
    if parsed.path:
        segs = parsed.path.split("/")
        out += [f"{parsed.base}#p:{'/'.join(segs[:i])}" for i in range(1, len(segs))]
    return out


def _mm(v: object) -> int:
    return int(round(float(v) * 1000.0))


def connection_key(spec: str, members: Iterable[tuple[str, str]], centre: Sequence[float] | None) -> str:
    """``sha256(spec | sorted role=key | centre in whole mm)[:16]``: the same connection whichever
    detail run or clash run it was made in. ``centre`` is in metres; None is a connection without one."""
    roles = ",".join(sorted(f"{str(r)}={str(k)}" for r, k in members))
    where = ",".join(str(_mm(c)) for c in centre) if centre is not None else "-"
    return hashlib.sha256(f"{spec}|{roles}|{where}".encode("utf-8")).hexdigest()[:16]
