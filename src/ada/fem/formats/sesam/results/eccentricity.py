"""Beam eccentricities from ``GECCEN`` + ``GELREF1``.

A Sesam stiffener is modelled on the plate's own nodes and pushed onto the plate
by an eccentricity vector. Drop the vector and the profile is drawn centred on
the element axis -- floating beside the plate it is welded to, which is what the
viewer showed for every T and I stiffener in a deck.

The results path never read these records at all: ``GECCEN`` was defined only as
a regex for the SIF *text* reader, and the SIN reader's card list did not mention
it, so the model-conversion path placed beams correctly while the result bake did
not.

``GELREF1``'s ``eccno`` follows the format's OPT convention: a positive value is
one eccentricity shared by every node of the element, and ``-1`` means the
per-node list is carried in the record's variable-length tail. The tail holds, in
this order and only for the fields whose OPT slot is ``-1``: ``geono``, ``fixno``,
``eccno``, ``transno`` -- each ``NNOD`` entries long. Getting that order wrong
reads a fixation number as an eccentricity number, so the offsets are computed by
walking the same sequence the writer used.
"""

from __future__ import annotations

import numpy as np

from ada.fem.formats.sesam.read import cards

__all__ = ["element_eccentricities", "geccen_vectors"]


def geccen_vectors(records) -> dict[int, np.ndarray]:
    """``{eccno: (ex, ey, ez)}`` from the deck's ``GECCEN`` records."""
    out: dict[int, np.ndarray] = {}
    if not records:
        return out
    for row in records:
        if len(row) < 4:
            continue
        out[int(row[0])] = np.array([float(row[1]), float(row[2]), float(row[3])], dtype=float)
    return out


def element_eccentricities(
    gelref_rows,
    geccen_records,
    node_counts: dict[int, int],
) -> dict[int, list[np.ndarray | None]]:
    """``{elno: [vec_per_node, ...]}`` in global coordinates.

    ``node_counts`` gives ``NNOD`` per element, which the tail's layout depends
    on. Elements with no eccentricity are absent from the result rather than
    present with zeros, so a caller can tell "no offset" from "offset of zero"
    without comparing floats.

    The vectors are returned exactly as the file gives them: a global offset to be
    ADDED to the node position. Verified against this deck's geometry -- an
    x=15.6 wall stiffener offsets inboard to 15.179, a z=7.8 deck stiffener hangs
    to 7.525, and an x=0 wall stiffener offsets to +0.421 rather than outside the
    model. Do not negate components here.
    """
    vectors = geccen_vectors(geccen_records)
    if not vectors or not gelref_rows:
        return {}

    elno_i, geono_i, fixno_i, eccno_i, transno_i = cards.GELREF1.get_indices_from_names(
        ["elno", "geono", "fixno", "eccno", "transno"]
    )
    tail_start = transno_i + 1

    out: dict[int, list[np.ndarray | None]] = {}
    for row in gelref_rows:
        elno = int(row[elno_i])
        nnod = node_counts.get(elno)
        if not nnod:
            continue
        eccno = int(row[eccno_i])
        if eccno == 0:
            continue

        if eccno > 0:
            vec = vectors.get(eccno)
            if vec is None:
                continue
            out[elno] = [vec] * nnod
            continue

        # eccno == -1: the per-node list is in the tail, after any geono and
        # fixno lists that the same convention put there first.
        cursor = tail_start
        if int(row[geono_i]) == -1:
            cursor += nnod
        if int(row[fixno_i]) == -1:
            cursor += nnod
        entries = row[cursor : cursor + nnod]
        if len(entries) < nnod:
            # A truncated record is a decode error, not an element without an
            # offset -- skip it rather than silently placing the beam on its axis.
            continue
        per_node = [vectors.get(int(v)) if int(v) != 0 else None for v in entries]
        if any(v is not None for v in per_node):
            out[elno] = per_node

    return out
