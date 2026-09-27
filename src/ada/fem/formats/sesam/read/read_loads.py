"""The Sesam load block on the way in: BNDISPL and BEUSLO.

Both records are written by ``sesam/write`` and neither was read, so a model round-tripped
through a Sesam deck came back with a settlement turned into a rigid clamp and a pressure gone
altogether. This module is the way back, and it is deliberately the *inverse* of the writer
rather than an independent reading of the format: every constant it needs
(:data:`~ada.fem.formats.sesam.write.write_bcs.DTYPE_DISPLACEMENT`, ``PRESSURE_LOTYP``,
``SIDE_POSITIVE`` / ``SIDE_NEGATIVE``, and the sign each face's intensity is written with) is
imported from the module that writes it, so the two cannot drift apart silently.

**BNDISPL** (``LLC DTYPE COMPLX 0`` / ``NODENO NDOF D1 D2`` / ``D3..Dndof``) carries the
*value* of a prescribed displacement; BNBCD FIX code 2 carries *which* DOFs are prescribed.
Measured against Sestra V11.3-00 (``tests/fem/test_sesam_prescribed_displacement.py``), the two
cards are one statement:

* FIX code 2 and a BNDISPL record: the node moves by the value.
* FIX code 2, no BNDISPL: "WARNING ... No load is specified", no displacement result at all.
* a BNDISPL record on a DOF with FIX code 1: the value is ignored, the node does not move.

So the reader pairs them the same way -- a value is read onto a DOF only if BNBCD marked that
DOF prescribed, and each of the other two combinations is reported rather than guessed at.
A prescribed displacement is *loading* in Sesam, so the record names a load case (``LLC``); an
ada ``Bc`` belongs to no load case, so the case name is recorded in the BC's metadata under
:data:`SESAM_LOAD_CASE` -- which is also the writer's own approximation, in reverse (it writes
every settlement into the first case).

**BEUSLO** (``LLC LOTYP COMPLX LAYER`` / ``ELNO NDOF INTNO SIDE`` / ``RLOAD1..RLOADn``) is a
surface pressure, one intensity per node of the element. The sign is the whole of the direction:
measured, a positive RLOAD pushes along the element's *positive* normal (the one its node order
gives by the right-hand rule) for every legal SIDE, because Sestra computes the load in the
element's mid-plane and SIDE therefore cannot carry a direction. On one 1 x 1 m FQUS, three
corners clamped, Sestra V11.3-00 puts the free corner at u3 = +3.9252336E-03 for RLOAD = +1000
with the nodes counter-clockwise seen from +z, and at -3.9252336E-03 with the order reversed or
the sign flipped. A positive ``*Dsload P`` pushes *into* the face it names, so the writer
records the face the model meant in SIDE and puts the direction in the sign (``-q`` on the
positive face, ``+q`` and SIDE 2 on the negative one); this reads that back, so a ``Load``
written positive on ``SPOS`` comes back positive on ``SPOS``. The signs are the writer's own
(:data:`_SIDE_FACE`), not restated here.

**BNLOAD and BGRAV** are the two the writer also emits and this still has no card for. They are
counted and named (:func:`report_unread_load_records`) rather than passed over, because a step
holding only the pressures of a deck that also has gravity is a wrong answer where an empty
model was merely an empty one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator

from ada.fem import FemSet, Surface
from ada.fem.formats import conversion_report
from ada.fem.formats.utils import str_to_int
from ada.fem.loads import LoadCase, LoadPressure
from ada.fem.steps import StepImplicitStatic

from ..write.write_bcs import DTYPE_DISPLACEMENT
from ..write.write_loads import (
    PRESSURE_INTNO,
    PRESSURE_LAYER,
    PRESSURE_LOTYP,
    SIDE_NEGATIVE,
    SIDE_POSITIVE,
    _pressure_side_and_sign,
)
from . import cards
from .read_sets import text_record

if TYPE_CHECKING:
    from ada.api.nodes import Node
    from ada.fem import FEM, Elem
    from ada.fem.steps import Step

#: The ``stage`` every finding of the Sesam reader is filed under -- the same string
#: ``read_elements`` uses.
STAGE = "sesam reader"

#: COMPLX 0: no phase shift, so no imaginary half follows the real values. It is the only
#: value the writer emits and, for both records, the only one an ada object can hold: a ``Bc``
#: magnitude and a ``Load`` magnitude are single real numbers.
NO_PHASE = 0

#: ``Bc.metadata`` key holding the name of the load case a BNDISPL record was in. A ``Bc``
#: belongs to no load case in ada, and a Sesam deck keeps a settlement in one, so the name has
#: nowhere else to go; the writer puts every settlement in the *first* case, which is the same
#: gap seen from the other side (``write_loads.step_loads_str``).
SESAM_LOAD_CASE = "sesam_load_case"

#: The step the deck's load cases are read into. A Sesam FEM file holds no analysis step at all
#: -- Sestra's control data is a separate ``sestra.inp``, which is not read -- so the step is
#: the reader's own container for the load cases, named rather than numbered so a deck that
#: gains one is obvious. Static, because that is the analysis a BEUSLO load case is written for,
#: and with no output requests: the deck asks for none, and inventing some would be invention.
READ_STEP_NAME = "sesam_loads"

#: BEUSLO ``SIDE`` -> (the shell face index an ada ``Surface`` names it by, the sign the writer
#: put on RLOAD for that face), read off ``write_loads._pressure_side_and_sign`` itself rather
#: than restated: the sign is the writer's decision, and a copy of it here is what let the two
#: disagree once already. The sign is +-1, so multiplying by it again undoes it under either
#: convention. Nothing else is in here -- SIDE 3 is legal to Sestra but names no face (it is the
#: mid-plane), and 0 and 4 and up are "Illegal side index specified on BEUSLO record".
_SIDE_FACE = {
    SIDE_POSITIVE: (1, _pressure_side_and_sign(1)[1]),
    SIDE_NEGATIVE: (-1, _pressure_side_and_sign(-1)[1]),
}


def report():
    return conversion_report.current()


def load_case_names(bulk_str: str) -> dict[int, str]:
    """``{LLC: name}`` from the deck's TDLOAD records."""
    names = {}
    for m in cards.re_tdload.finditer(bulk_str):
        d = m.groupdict()
        name, _ = text_record(d, "name")
        if name:
            names[str_to_int(d["llc"])] = name
    return names


def case_name(names: dict[int, str], llc: int) -> str:
    """The load case's name, or ``LC<llc>`` when no TDLOAD named it -- which is also the name
    the writer gives the one case it invents (``write_loads.DEFAULT_CASE`` is ``"LC1"``), so a
    deck written without TDLOAD still round-trips its case name."""
    return names.get(llc) or f"LC{llc}"


# --- BNDISPL: the value of a prescribed displacement ----------------------------------------


def prescribed_displacements(bulk_str: str) -> tuple[dict[int, dict[int, float]], dict[int, str]]:
    """``({node id: {dof: value}}, {node id: load case name})`` from the BNDISPL records.

    Every DOF the record lists is returned, zeros included: which of them is *prescribed* is
    BNBCD's statement, not this one, and a genuinely prescribed zero is a value like any other.
    :func:`prescribed_magnitudes` makes that pairing.

    DTYPE 3 (acceleration) and COMPLX 1 (a phase shift) are refused by name rather than read as
    a displacement: both would put a number that is not a displacement into ``Bc.magnitudes``,
    where every other format writer would then emit it as one.
    """
    names = load_case_names(bulk_str)
    rep = report()
    by_node: dict[int, dict[int, dict[int, float]]] = {}
    for m in cards.re_bndispl.finditer(bulk_str):
        d = m.groupdict()
        llc = str_to_int(d["llc"])
        nodeno = str_to_int(d["nodeno"])
        subject = f"node {nodeno} in load case {case_name(names, llc)}"
        dtype = str_to_int(d["dtype"])
        if dtype != DTYPE_DISPLACEMENT:
            rep.omitted(
                STAGE,
                "BNDISPL",
                subject,
                f"DTYPE {dtype} is not a displacement (Sestra: 1 = displacement, 3 = acceleration), and a "
                "Bc magnitude is a displacement; reading it as one would prescribe a movement nobody asked for",
                dtype=dtype,
            )
            continue
        complx = str_to_int(d["complx"])
        if complx != NO_PHASE:
            rep.omitted(
                STAGE,
                "BNDISPL",
                subject,
                "a complex prescribed displacement (COMPLX 1) carries a phase shift, which a real Bc "
                "magnitude has no room for; its imaginary half would be read as further real dofs",
                complx=complx,
            )
            continue
        ndof = str_to_int(d["ndof"])
        values = [float(x) for x in d["content"].split()[:ndof]]
        if len(values) < ndof:
            rep.omitted(
                STAGE,
                "BNDISPL",
                subject,
                f"the record declares NDOF {ndof} but carries {len(values)} values",
                ndof=ndof,
                n_values=len(values),
            )
            continue
        by_node.setdefault(nodeno, {})[llc] = {dof: value for dof, value in enumerate(values, start=1)}

    prescribed: dict[int, dict[int, float]] = {}
    cases: dict[int, str] = {}
    for nodeno in sorted(by_node):
        per_case = by_node[nodeno]
        llc = min(per_case)
        if len(per_case) > 1:
            # A Bc belongs to no load case, so a node prescribed differently in two of them
            # cannot be held as two BCs either. The first case is the choice, said out loud --
            # the mirror of the writer, which puts every settlement in the first case.
            rep.approximated(
                STAGE,
                "BNDISPL",
                f"node {nodeno}",
                "a displacement is prescribed in several load cases; a Bc belongs to none, so only the "
                "first case's values are read",
                load_cases=[case_name(names, x) for x in sorted(per_case)],
            )
        prescribed[nodeno] = per_case[llc]
        cases[nodeno] = case_name(names, llc)
    return prescribed, cases


def prescribed_magnitudes(
    records: list[tuple[Node, tuple[int, ...], tuple[int, ...]]], bulk_str: str
) -> list[tuple[Node, tuple[int, ...], tuple[float | None, ...], str | None]]:
    """``[(node, dofs, magnitudes, load case)]`` -- the BNBCD records with their BNDISPL values.

    ``records`` is what :func:`~.read_constraints.grab_bc` produces: the node, the DOFs its
    BNBCD record constrains, and the subset of those carrying FIX code 2 (prescribed).
    ``magnitudes`` lines up with ``dofs``, ``None`` on a DOF that is merely fixed.

    The two halves disagreeing is reported rather than resolved silently, in both directions:

    * FIX code 2 with no value for that DOF -- Sestra's "No load is specified", which leaves a
      static run with no displacement result at all. Read as a prescribed **zero**, i.e. the
      support the deck actually behaves as, and reported as a note. (Written back out it
      becomes FIX code 1: ``write_bcs.prescribed_displacements`` keeps only nonzero
      magnitudes, a prescribed zero *being* a fixed support.)
    * a nonzero BNDISPL value on a DOF without code 2 -- measured to be ignored by Sestra, so
      it is ignored here too, and named.
    """
    prescribed, cases = prescribed_displacements(bulk_str)
    rep = report()
    out = []
    for node, dofs, settled in records:
        values = prescribed.get(node.id, {})
        missing = [dof for dof in settled if dof not in values]
        if missing:
            rep.note(
                STAGE,
                "BNDISPL",
                f"node {node.id}",
                "BNBCD prescribes these dofs (FIX code 2) and no BNDISPL record gives them a value, which "
                'Sestra solves as "No load is specified"; they are read as a prescribed zero',
                dofs=missing,
            )
        ignored = sorted(dof for dof, value in values.items() if dof not in settled and value != 0.0)
        if ignored:
            rep.omitted(
                STAGE,
                "BNDISPL",
                f"node {node.id}",
                "a displacement is prescribed on dofs BNBCD does not give FIX code 2, and Sestra ignores "
                "the value there (measured); it is not read",
                dofs=ignored,
                values=[values[dof] for dof in ignored],
            )
        magnitudes = tuple(values.get(dof, 0.0) if dof in settled else None for dof in dofs)
        out.append((node, dofs, magnitudes, cases.get(node.id) if settled else None))
    return out


# --- BEUSLO: a surface pressure -------------------------------------------------------------


@dataclass(frozen=True)
class BeusloRecord:
    """One BEUSLO record the reader can hold: its load case, element, face and magnitude.

    ``magnitude`` is already the pressure the model meant -- RLOAD with the writer's sign
    convention undone -- and ``face`` the shell face index an ada ``Surface`` names it by.
    """

    llc: int
    element: Elem
    face: int
    magnitude: float


def beuslo_records(bulk_str: str, fem: FEM) -> Iterator[BeusloRecord]:
    """The BEUSLO records of ``bulk_str``, in file order, skipping the ones ada cannot hold.

    Everything skipped is reported by name. LOTYP, LAYER and INTNO are checked against the
    writer's own constants, each of which carries the Sestra diagnostic that fixed it: LOTYP 2
    is a three-component vector load and LOTYP 3 no load type at all, and a nonzero LAYER or
    INTNO is *ignored* by Sestra ("Layered elements is not supported in this version" / "Only
    the default rule is supported in this version") -- so a deck carrying one does not mean what
    it says, and reading it as a plain pressure would put that misreading into the model.
    """
    names = load_case_names(bulk_str)
    rep = report()
    for m in cards.re_beuslo.finditer(bulk_str):
        d = m.groupdict()
        llc = str_to_int(d["llc"])
        elno = str_to_int(d["elno"])
        subject = f"element {elno} in load case {case_name(names, llc)}"

        for field, value, expected, reason in (
            (
                "LOTYP",
                str_to_int(d["lotyp"]),
                PRESSURE_LOTYP,
                "only LOTYP 1, a surface pressure, is a load ada can hold (LOTYP 2 is a three-component "
                "vector load; LOTYP 3 is no load type at all and Sestra drops the record)",
            ),
            (
                "COMPLX",
                str_to_int(d["complx"]),
                NO_PHASE,
                "a complex pressure (COMPLX 1) carries a phase shift, which a real Load magnitude has no "
                "room for; its imaginary half would be read as further intensities",
            ),
            (
                "LAYER",
                str_to_int(d["layer"]),
                PRESSURE_LAYER,
                "a load on a layered element, which Sestra warns about and then applies without the layer "
                'attribute ("Layered elements is not supported in this version"), so the deck does not '
                "mean what it says and ada has no layer to put it on",
            ),
            (
                "INTNO",
                str_to_int(d["intno"]),
                PRESSURE_INTNO,
                'a non-default integration rule, which Sestra warns about and then ignores ("Only the '
                'default rule is supported in this version"); ada has no form for it',
            ),
        ):
            if value != expected:
                rep.omitted(STAGE, "BEUSLO", subject, f"{field} {value}: {reason}", **{field.lower(): value})
                break
        else:
            record = _beuslo_record(d, llc, elno, subject, fem, rep)
            if record is not None:
                yield record


def _beuslo_record(d: dict, llc: int, elno: int, subject: str, fem: FEM, rep) -> BeusloRecord | None:
    """One well-formed BEUSLO record, or ``None`` with the reason reported.

    The element, the face and the one magnitude a ``Load`` carries. A record whose intensities
    differ across the element's nodes is a *non-uniform* pressure, which a single magnitude
    cannot hold: their mean is read, which for a bilinear quad keeps the resultant exactly
    (the consistent load vector is ``q_i A / 4`` per node, so the total is ``A`` times the
    mean), and the values themselves go in the report.
    """
    side = str_to_int(d["side"])
    face_sign = _SIDE_FACE.get(side)
    if face_sign is None:
        rep.omitted(
            STAGE,
            "BEUSLO",
            subject,
            f"SIDE {side} names no shell face ada can hold: only 1 (the positive normal) and 2 (the "
            "negative one) do. Sestra also takes 3, the mid-plane, and refuses everything else as an "
            '"Illegal side index"',
            side=side,
        )
        return None

    try:
        element = fem.elements.from_id(elno)
    except (ValueError, KeyError):
        rep.omitted(STAGE, "BEUSLO", subject, "the element the load names is not in the deck")
        return None

    ndof = str_to_int(d["ndof"])
    intensities = [float(x) for x in d["rload"].split()[:ndof]]
    n_nodes = len(element.nodes)
    if len(intensities) < ndof or ndof != n_nodes:
        # Sestra's own check: "Load intensity vector size does not match dof count for load".
        rep.omitted(
            STAGE,
            "BEUSLO",
            subject,
            f"the record declares NDOF {ndof} and carries {len(intensities)} intensities for an element "
            f"with {n_nodes} nodes; one intensity per node is what the record means",
            ndof=ndof,
            n_intensities=len(intensities),
            n_nodes=n_nodes,
        )
        return None

    face, sign = face_sign
    if len(set(intensities)) > 1:
        rep.approximated(
            STAGE,
            "BEUSLO",
            subject,
            "the intensities differ across the element's nodes -- a non-uniform pressure, which one Load "
            "magnitude cannot hold; their mean is read, which keeps the resultant of a bilinear quad",
            intensities=intensities,
            mean=sum(intensities) / len(intensities),
        )
    # The writer wrote ``sign * magnitude``; ``sign`` is +-1, so multiplying by it again undoes it.
    return BeusloRecord(llc, element, face, sign * sum(intensities) / len(intensities))


#: Load records the Sesam *writer* emits and this reader has no card for -> the adapy construct
#: that is therefore missing. Both are silently lost on the way in, and that mattered less while
#: nothing at all came back: now a deck of gravity plus a pressure reads as a model whose only
#: loading is the pressure, which is a wrong answer rather than an empty one. Counted and named
#: until they are read too. BNLOAD is ``write_loads.load_force``'s record and BGRAV
#: ``load_gravity``'s; a distributed load other than BEUSLO is not written by adapy at all.
_UNREAD_LOAD_RECORDS = {
    "BNLOAD": "a nodal force or moment",
    "BGRAV": "a gravity or acceleration field",
}

#: One pass for all of them: the deck this is run over is the whole non-mesh remainder of a
#: superelement, up to hundreds of megabytes.
_re_unread_loads = re.compile(rf"^({'|'.join(sorted(_UNREAD_LOAD_RECORDS))})\s", re.MULTILINE)


def report_unread_load_records(bulk_str: str) -> dict[str, int]:
    """Name every load record the reader has no card for, with how many the deck holds.

    Returns the counts, so a caller can assert on them; the report is the point.
    """
    counts: dict[str, int] = {}
    for m in _re_unread_loads.finditer(bulk_str):
        flag = m.group(1).upper()
        counts[flag] = counts.get(flag, 0) + 1
    rep = report()
    for flag in sorted(counts):
        # ``count`` is the finding's own occurrence count, which is exactly what this is: one
        # finding per record type, saying how many of them the deck holds.
        rep.omitted(
            STAGE,
            flag,
            f"{counts[flag]} record(s)",
            f"{_UNREAD_LOAD_RECORDS[flag]}; the Sesam reader has no card for it yet, so this loading does "
            "not reach the model even though the deck's other load records do",
            count=counts[flag],
        )
    return counts


def get_loads(bulk_str: str, fem: FEM) -> Step | None:
    """The deck's BEUSLO records as one static step of pressure loads, or ``None``.

    Nothing is added to ``fem`` when the deck carries no BEUSLO record it can hold, so a deck
    without one -- every deck this reader has ever read -- comes back exactly as before, step
    table and all.

    One ``LoadPressure`` per (load case, face, magnitude): those three are all a ``Load``
    distinguishes, and a deck writes one record per element, so the records have to be grouped
    back the way ``write_loads.load_pressure`` split them up. The element set is the deck's own
    when one holds exactly those elements -- which is the usual case, the writer having written
    the surface's set as GSETMEMB -- and a generated one otherwise, the same fallback
    ``read_constraints.group_bcs`` makes for a node set.

    The load records this reader still has no card for are named first
    (:func:`report_unread_load_records`), because a step holding only the pressures is a model
    that *looks* loaded.
    """
    report_unread_load_records(bulk_str)
    records = list(beuslo_records(bulk_str, fem))
    if not records:
        return None

    names = load_case_names(bulk_str)
    # One pass over the deck's element sets, not one per load: a jacket model has thousands.
    by_members = _elsets_by_membership(fem)
    step = fem.add_step(StepImplicitStatic(READ_STEP_NAME, use_default_outputs=False))
    for llc in sorted({r.llc for r in records}):
        lc_name = case_name(names, llc)
        groups: dict[tuple[int, float], list[Elem]] = {}
        for record in (r for r in records if r.llc == llc):
            groups.setdefault((record.face, record.magnitude), []).append(record.element)
        loads = []
        # In the order each group's first record appears in the deck, which is the order the
        # writer emitted the loads in: written back out, the load block comes out unchanged.
        for i, (face, magnitude) in enumerate(groups, start=1):
            elements = sorted(groups[(face, magnitude)], key=lambda e: e.id)
            side = "SPOS" if face == _SIDE_FACE[SIDE_POSITIVE][0] else "SNEG"
            fem_set = _elset_for(fem, by_members, elements, _unique(f"{lc_name}_{side}", fem.elsets))
            surface = fem.add_surface(
                Surface(
                    _unique(f"{fem_set.name}_{side}", fem.surfaces),
                    Surface.TYPES.ELEMENT,
                    fem_set,
                    el_face_index=face,
                    parent=fem,
                )
            )
            name = f"{lc_name}_pressure" if len(groups) == 1 else f"{lc_name}_pressure{i}"
            loads.append(LoadPressure(name, magnitude, surface))
        step.add_loadcase(LoadCase(lc_name, None, loads=loads))
    return step


def _unique(name: str, taken) -> str:
    """``name``, suffixed with a counter if ``taken`` (a name-keyed mapping) already has it."""
    if name not in taken:
        return name
    return next(f"{name}_{i}" for i in range(2, len(taken) + 3) if f"{name}_{i}" not in taken)


def _elsets_by_membership(fem: FEM) -> dict[frozenset, FemSet]:
    """``{frozenset of element ids: the first element set holding exactly them}``.

    First in file order wins, which is how ``read_constraints.group_bcs`` breaks the same tie
    for a node set.
    """
    index: dict[frozenset, FemSet] = {}
    for fs in fem.sets.sets:
        if fs.type == FemSet.TYPES.ELSET:
            index.setdefault(frozenset(m.id for m in fs.members), fs)
    return index


def _elset_for(fem: FEM, by_members: dict[frozenset, FemSet], elements: list[Elem], name: str) -> FemSet:
    """The deck's element set holding exactly ``elements``, or a new one called ``name``.

    A generated set is registered with ``parent=fem`` so the array-backed reader's internal ->
    external renumber pass reaches it (``reader._remap_id_backed_sets`` only remaps the sets it
    finds in ``fem.sets``), and is added to the index so a second group of the same elements --
    the two faces of one plate, say -- shares it rather than creating a duplicate.
    """
    ids = frozenset(el.id for el in elements)
    found = by_members.get(ids)
    if found is not None:
        return found
    fem_set = fem.sets.add(FemSet(name, elements, FemSet.TYPES.ELSET, parent=fem))
    by_members[ids] = fem_set
    return fem_set
