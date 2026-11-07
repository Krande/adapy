"""Nodes in a rotated frame: BNTRCOS and BNDOF, for a support that holds dofs along skewed axes.

A ``Bc`` with a ``csys`` whose axes are not the global ones in another order (:func:`ada.fem.frames.in_global_axes`)
turns its nodes: ``BNDOF NODENO TRANSD TRANSR`` names a ``BNTRCOS TRANSNO c11 c21 c31 c12 ... c33`` for the
translations and the rotations, ``C`` taking global to local -- "r0 = C r where r0 refers to the local co-ordinate
system and r to the global" (SIF 7.2.25, written column by column), so its rows are the local axes, as GeniE writes
them (``genie_supports_frames_T1.FEM``: a support with ``x' = (0.6, 0, 0.8)`` has that as the first row).

Everything the deck gives at a transformed node is then in its frame (SIF: BNBCD's dofs, BNDISPL and BNMASS "refer
to the transformed co-ordinate system if the node NODENO is transformed"; BNLOAD gives its components "with respect
to the degrees of freedom at NODENO", and GeniE's MGSPRNG at such a node is in its frame too). So at a transformed
node:

* the frame ``Bc``'s codes and prescribed values are written as given -- they are in its frame already;
* a nodal load is turned into it (``f_local = C f_global``, :meth:`NodeFrames.to_local`), and a grounded spring's
  stiffness too (``K_local = T K T^T``, ``T = diag(C, C)``);
* anything that does not turn with it is refused, the frame ``Bc`` reported by name and left out, never written in
  the wrong axes: a second frame on the node, a support in global axes that holds some translations or rotations but
  not all three (all or none reads the same in any frame), a linear dependency (BLDEP) on the node, a two-node
  spring, or a mass whose matrix is not the same in both frames.

Results at a transformed node come back in its frame and are turned back by the SIF reader
(``results.read_sif.SifReader.get_nodal_transforms``).
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator

import numpy as np

from ada.fem.frames import COSINE_TOL, bc_axes, in_global_axes, local_conditions

from .write_utils import write_ff

if TYPE_CHECKING:
    from ada import FEM


@dataclass
class NodeFrames:
    #: ``{node id: C}``, rows the local axes in global components
    axes: dict = field(default_factory=dict)
    #: the distinct frames, in the order met, each one BNTRCOS
    frames: list = field(default_factory=list)
    #: ``{node id: index into frames}``
    index: dict = field(default_factory=dict)
    #: BNTRCOS numbers start after the deck's GUNIVECs (the SIF asks for separate numbering)
    first_transno: int = 1

    def of(self, nid: int) -> np.ndarray | None:
        return self.axes.get(int(nid))

    def transno(self, nid: int) -> int:
        return self.first_transno + self.index[int(nid)]

    def to_local(self, nid: int, six) -> list[float]:
        """Six global components at ``nid`` in its frame (unchanged at a node with none)."""
        c = self.of(nid)
        v = [float(x) for x in six]
        if c is None:
            return v
        return [float(x) for x in np.concatenate([c @ v[:3], c @ v[3:6]])] + v[6:]

    def stiffness_to_local(self, nid: int, k: np.ndarray) -> np.ndarray:
        c = self.of(nid)
        if c is None:
            return k
        n = k.shape[0]
        t = np.zeros((n, n))
        t[:3, :3] = c
        if n == 6:
            t[3:, 3:] = c
        return t @ k @ t.T

    def bntrcos_str(self) -> str:
        out = ""
        for i, c in enumerate(self.frames):
            cols = [float(c[i_, j]) for j in range(3) for i_ in range(3)]  # c11, c21, c31, c12, ...
            out += write_ff("BNTRCOS", [(self.first_transno + i, *cols[:3]), tuple(cols[3:7]), tuple(cols[7:])])
        return out

    def bndof_str(self, ndofs=None) -> str:
        out = ""
        for nid in sorted(self.axes):
            t = self.transno(nid)
            rotations = ndofs is None or ndofs.ndof(nid) > 3
            out += write_ff("BNDOF", [(nid, t, t if rotations else 0)])
        return out


_current: list[NodeFrames] = []


@contextlib.contextmanager
def deck(frames: NodeFrames) -> Iterator[NodeFrames]:
    _current.append(frames)
    try:
        yield frames
    finally:
        _current.pop()


def current() -> NodeFrames:
    return _current[-1] if _current else NodeFrames()


def _invariant(conditions: list) -> bool:
    """Whether a global support's six conditions read the same in any frame: each group of three all free or all
    held at zero."""
    for group in (conditions[:3], conditions[3:]):
        if len({c if c is None else float(c) for c in group}) > 1 or any(c not in (None, 0.0) for c in group):
            return False
    return True


def _turns_the_same(m: np.ndarray, c: np.ndarray) -> bool:
    t = np.zeros((6, 6))
    t[:3, :3] = c
    t[3:, 3:] = c
    return bool(np.allclose(t @ m @ t.T, m, atol=1e-9 * max(1.0, float(np.abs(m).max()))))


def _mass_matrices_by_node(fem: FEM) -> dict[int, list[tuple[str, np.ndarray]]]:
    from .write_masses import _bnmass_components
    from .write_point_elements import mass_matrix

    out: dict[int, list] = {}
    for mass in fem.elements.masses:
        members = [m for m in mass.members if hasattr(m, "p")]
        if not members:
            continue
        try:
            m = mass_matrix(mass)
        except Exception:  # noqa: BLE001 - a kind mass_matrix has no form for: its BNMASS diagonal
            m = np.diag(_bnmass_components(mass, len(members)))
        for node in members:
            out.setdefault(int(node.id), []).append((mass.name, m))
    return out


def node_frames(held_bcs, lin_deps, part_fem: FEM, first_transno: int = 1):
    """``(frames, bcs)``: the deck's transformed nodes, and per FEM the supports to write -- in global axes where a
    frame maps onto them, in their frame where not, less the frames refused (reported; see the module docstring).

    ``held_bcs`` is the writer's ``[SimpleNamespace(bcs=[...]), ...]``, one per FEM; the result has its shape."""
    from types import SimpleNamespace

    from .not_held import STAGE, report

    rep = report()
    resolved = []
    framed: list = []  # (holder index, bc, axes)
    for holder in held_bcs:
        out = []
        for bc in holder.bcs:
            g = in_global_axes(bc)
            if g is not None:
                out.append(g)
            else:
                framed.append((len(resolved), bc, bc_axes(bc)))
                out.append(bc)
        resolved.append(out)
    frames = NodeFrames(first_transno=first_transno)
    if not framed:
        return frames, [SimpleNamespace(bcs=bcs) for bcs in resolved]

    global_conditions: dict[int, list] = {}
    for bcs in resolved:
        for bc in bcs:
            if bc_axes(bc) is None:
                for node in bc.fem_set.members:
                    global_conditions.setdefault(int(node.id), []).append((bc.name, local_conditions(bc)))
    linked = {r.slave for r in lin_deps} | {r.master for r in lin_deps}
    two_node = {int(n.id) for sp in part_fem.springs.values() if len(sp.nodes) > 1 for n in sp.nodes}
    masses = _mass_matrices_by_node(part_fem)

    refused = set()
    for k, bc, c in framed:
        why, where = None, None
        for node in bc.fem_set.members:
            nid = int(node.id)
            have = frames.axes.get(nid)
            if have is not None and not np.allclose(have, c, atol=COSINE_TOL):
                why = "a node already in another support's rotated frame; a node has one frame (BNDOF)"
            elif any(not _invariant(cond) for _, cond in global_conditions.get(nid, [])):
                why = (
                    "a node another support holds in global axes in some translations or rotations only; at a "
                    "transformed node every code is in its frame, and that support's would turn with it"
                )
            elif nid in linked:
                why = "a node in a linear dependency (BLDEP), whose terms are in global axes"
            elif nid in two_node:
                why = "a node of a two-node spring, whose stiffness is in global axes"
            elif any(not _turns_the_same(m, c) for _, m in masses.get(nid, [])):
                why = "a node with a mass that is not the same in every direction; its BNMASS/MGMASS would turn"
            if why:
                where = nid
                break
        if why:
            rep.omitted(
                STAGE,
                "Bc",
                bc.name,
                f"a support in a rotated frame on {why}; it is not written",
                node=where,
                local_axes=np.round(c, 9).tolist(),
            )
            refused.add(id(bc))
            continue
        for node in bc.fem_set.members:
            nid = int(node.id)
            if nid in frames.axes:
                continue
            match = next((i for i, f in enumerate(frames.frames) if np.allclose(f, c, atol=COSINE_TOL)), None)
            if match is None:
                frames.frames.append(np.asarray(c, dtype=float))
                match = len(frames.frames) - 1
            frames.axes[nid] = frames.frames[match]
            frames.index[nid] = match
    return frames, [SimpleNamespace(bcs=[bc for bc in bcs if id(bc) not in refused]) for bcs in resolved]
