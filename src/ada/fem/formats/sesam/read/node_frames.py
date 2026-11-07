"""Transformed nodes on the way in: BNDOF / BNTRCOS, the frame a node's records are in.

``BNDOF NODENO TRANSD TRANSR`` names a ``BNTRCOS`` (SIF 7.2.18, 7.2.25): ``C``, written column by column, takes global
to local (``r0 = C r``), its rows the local axes. At such a node the deck's BNBCD codes and BNDISPL values are in that
frame, and its MGSPRNG stiffness too (GeniE writes them so; ``genie_supports_frames_T1.FEM``). They used to be read as
if global: a support GeniE turned 45 degrees came in holding the wrong directions, without a word.

They are read into the frame now -- a ``Bc`` with a ``csys`` (``read_constraints.get_bcs``), a grounded spring turned
back to global axes (``read_elements.get_springs``) -- the writer's ``write.node_frames`` in reverse. A node whose
translations and rotations are given different frames (``TRANSD != TRANSR``) has no ``Bc`` form and is reported.
"""

from __future__ import annotations

import re

import numpy as np

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_re_bntrcos = re.compile(rf"^BNTRCOS((?:\s+{_NUM}){{10}})", re.MULTILINE)
_re_bndof = re.compile(rf"^BNDOF((?:\s+{_NUM}){{3}})", re.MULTILINE)


def node_frames(text: str) -> dict[int, tuple[int, np.ndarray]]:
    """``{node id: (TRANSNO, C)}`` for every BNDOF node (the translations' frame; the rotations' when it has none).

    Reported: a node whose rotations are in another frame than its translations, which is read in its translations'.
    """
    from .read_loads import report

    if "BNDOF" not in text:
        return {}
    matrices = {}
    for m in _re_bntrcos.finditer(text):
        v = [float(x) for x in m.group(1).split()]
        matrices[int(v[0])] = np.asarray(v[1:10]).reshape(3, 3, order="F")
    out = {}
    for m in _re_bndof.finditer(text):
        nid, transd, transr = (int(float(x)) for x in m.group(1).split())
        t = transd or transr
        if t == 0:
            continue
        if t not in matrices:
            raise ValueError(f"BNDOF node {nid} references missing BNTRCOS {t}")
        if transd and transr and transd != transr and not np.allclose(matrices[transd], matrices[transr]):
            report().approximated(
                "sesam reader",
                "BNDOF",
                f"node {nid}",
                "translations and rotations in different frames; a Bc has one, and the translations' is read",
                transd=transd,
                transr=transr,
            )
        out[nid] = (t, matrices[t])
    return out


def csys_of(cache: dict, fem, transno: int, c: np.ndarray):
    """The ``Csys`` of BNTRCOS ``transno``, made once per read (in ``cache``); its axes are ``C``'s rows."""
    from ada.fem.common import Csys

    if transno not in cache:
        coords = [tuple(float(x) for x in c[0]), tuple(float(x) for x in c[1])]
        cache[transno] = Csys(f"bntrcos{transno}", coords=coords, parent=fem)
    return cache[transno]


def stiffness_to_global(c: np.ndarray, k: np.ndarray) -> np.ndarray:
    """A stiffness given in the frame ``C`` in global axes: ``T^T K T``, ``T = diag(C, C)``."""
    n = k.shape[0]
    t = np.eye(n)
    t[:3, :3] = c
    if n >= 6:
        t[3:6, 3:6] = c
    return t.T @ k @ t
