"""Standard ACIS Binary (SAB) <-> text (SAT), record for record.

The workspace reader renders a binary body to text with :func:`sat_text_from_sab`
(through ``sab.sat_text_of_body``) and the workspace writer packs adapy's text body
with :func:`pack` when asked for a binary workspace. Both rest on the token encoding
and the per-record field programs below, proven against GeniE V9.3-00 twins -- the
same model saved once as text and once as binary. Two oracles hold on every
committed twin (``tests/core/cadit/sat/test_sab_codec.py``):

  sat_text_from_sab(binary) == the text twin's body, byte for byte (CRLF line ends,
                               GeniE's line breaks inside spline data, ``%.17g``
                               numbers), the header's save timestamp aside, and
  pack(text)                == the binary twin's bytes after the header, byte for
                               byte (the header differs only in its timestamp).

Why a program per record type: the tags are self-describing for pointers, ints,
doubles, strings and entity types, but a logical is one byte (0x0A/0x0B) whose
text word depends on the field -- ``F``/``T`` for a box flag, ``forward``/
``reversed`` for a sense, ``I`` or ``F <value>`` for an interval, ``out``/``in``,
``single``/``double`` -- and an enum (0x15) is an int whose text word depends on
the field too (``periphery`` is 1 on a loop, ``none`` is 2 at the end of an
intcurve and 0 as a surface singularity). Positions and directions are one tagged
token of three doubles (0x13/0x14) where the text has three numbers, and the 18
action ints of a generic attribute are one packed int. So each record type lists
its fields once, and the same list drives both directions.

Record types covered: body, lump, shell, wire, face, loop, coedge, edge, vertex,
point, straight-curve, ellipse-curve, plane-surface, cone-surface, intcurve-curve
(exactcur), spline-surface (exactsur, and ``ref``), pcurve (exppc), and the
string_attrib-name_attrib-gen-attrib, position_attrib-name_attrib-gen-attrib,
CachedPlaneAttribute-DNV-attrib and vertedge-sys-attrib attributes -- every type
GeniE wrote for the twins and every type adapy's own SAT writer emits (its
FusedFace/FusedEdge attribute classes are never instantiated). Anything else
is refused by name (:class:`SabUnsupported`): ``rulesur`` from a skinned surface, a
transform, an unseen subtype, attribute flag word or enum value, a periodic spline,
a long string (tag 0x12), a kernel other than ACIS 33.0.1, a history section.

Token encoding, measured (little-endian throughout):
  0x04 int32 | 0x06 double | 0x07 string (1-byte length) | 0x0A true | 0x0B false
  0x0C pointer int32 (-1 is null) | 0x0E/0x0D entity type parts, hyphen-joined,
  0x0D last | 0x0F '{' | 0x10 '}' | 0x11 '#' | 0x12 string (int32 length) |
  0x13 position (3 doubles) | 0x14 direction (3 doubles) | 0x15 enum int32.
Header: ``ACIS BinaryFile`` + 4 int32 (version 2000, records, bodies, flags)
+ 3 strings (product, ACIS version, date) + 3 doubles (units, resolution,
tolerance). End marker: one type token ``End-of-ACIS-data``.
"""

from __future__ import annotations

import re
import struct

from ada.cadit.sat.sab import SAB_SIGNATURE as SIGNATURE

# tag -> kind
T_INT, T_DBL, T_STR, T_TRUE, T_FALSE, T_PTR, T_TYPE, T_TYPEX = 0x04, 0x06, 0x07, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E
T_SUB_OPEN, T_SUB_CLOSE, T_END, T_LSTR, T_POS, T_DIR, T_ENUM = 0x0F, 0x10, 0x11, 0x12, 0x13, 0x14, 0x15

# ---------------------------------------------------------------- tokenizer


def tokenize(data: bytes):
    """SAB bytes -> (header dict, records as lists of (tag, value) tokens)."""
    if not data.startswith(SIGNATURE):
        raise SabUnsupported("not a SAB body (no 'ACIS BinaryFile' signature)")
    try:
        return _tokenize(data)
    except (struct.error, IndexError) as e:
        raise SabUnsupported(f"the body is truncated ({len(data)} bytes): {e}") from e


def _tokenize(data: bytes):
    i = len(SIGNATURE)
    ver, nrec, nent, flags = struct.unpack_from("<4i", data, i)
    i += 16
    hdr = {"version": ver, "n_records": nrec, "n_entities": nent, "flags": flags, "strings": [], "doubles": []}
    while True:
        tag = data[i]
        if tag == T_STR:
            n = data[i + 1]
            hdr["strings"].append(data[i + 2 : i + 2 + n].decode("latin1"))
            i += 2 + n
        elif tag == T_DBL:
            hdr["doubles"].append(struct.unpack_from("<d", data, i + 1)[0])
            i += 9
        else:
            break
    recs, cur, tname = [], [], []
    n = len(data)
    while i < n:
        tag = data[i]
        i += 1
        if tag in (T_INT, T_PTR, T_ENUM):
            cur.append((tag, struct.unpack_from("<i", data, i)[0]))
            i += 4
        elif tag == T_DBL:
            cur.append((tag, struct.unpack_from("<d", data, i)[0]))
            i += 8
        elif tag in (T_STR, T_TYPEX, T_TYPE):
            ln = data[i]
            s = data[i + 1 : i + 1 + ln].decode("latin1")
            i += 1 + ln
            if tag == T_TYPEX:
                tname.append(s)
            elif tag == T_TYPE:
                tname.append(s)
                cur.append((T_TYPE, "-".join(tname)))
                tname = []
            else:
                cur.append((tag, s))
        elif tag == T_LSTR:
            # A string with an int32 length: what a transform record or a name over 255 bytes
            # would carry. Neither occurs in any twin, so its text form is unverified.
            raise SabUnsupported(f"tag 0x12 (long string) at offset {i - 1} (record {len(recs)}): not observed")
        elif tag in (T_POS, T_DIR):
            cur.append((tag, struct.unpack_from("<3d", data, i)))
            i += 24
        elif tag in (T_TRUE, T_FALSE, T_SUB_OPEN, T_SUB_CLOSE):
            cur.append((tag, None))
        elif tag == T_END:
            recs.append(cur)
            cur = []
        else:
            raise SabUnsupported(f"unknown SAB tag 0x{tag:02x} at offset {i - 1} (record {len(recs)})")
    if cur:
        recs.append(cur)
    return hdr, recs


# ---------------------------------------------------------------- programs
# Field codes:  p ptr | i int | d double | s @-string | pos | dir | box (F | T pos pos)
#               ival (I | F d) | B:<pair> bool word pair | E:<table> enum | flags (18 ints <-> 1 int)
#               sub:<name> brace block | * end
HDR = ["p", "i", "i", "p"]  # general entity header: $attrib -1 -1 $-1
AHDR = ["p", "i", "p", "p", "p"]  # attribute header: $next -1 $prev $-1 $owner

BOOL_WORDS = {
    "SENSE": ("forward", "reversed"),
    "SENSE_V": ("forward_v", "reversed_v"),
    "SIDES": ("single", "double"),
    "CONT": ("out", "in"),
    "LOG": ("F", "T"),
}
ENUMS = {
    # ACIS has hole / u_separation / v_separation / uv_separation too; none is in a twin, and a loop's
    # tail after its type word is measured only for these two, so the others are refused.
    "LOOP": {0: "unknown", 1: "periphery"},
    "FULL": {0: "full"},
    "CLOSURE": {0: "open", 1: "closed", 2: "periodic"},
    "NONE": {2: "none"},
    "SING": {0: "none"},
}
# Observed packings of the 18 generic-attribute action ints into one SAB int. The bit layout is
# not decoded (three samples), so only these words are read or written.
FLAGS = {
    "2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 1": 14675622,  # name attributes (string_attrib)
    "1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1": 14413477,  # CachedPlaneAttribute
    "2 0 0 0 1 1 1 1 1 1 1 1 1 1 0 1 1 1": 14675458,  # ExactBoxLow/High (position_attrib)
}
FLAGS_INV = {v: k for k, v in FLAGS.items()}

PROGRAMS = {
    "body": HDR + ["p", "p", "p", "box"],
    "lump": HDR + ["p", "p", "p", "box"],
    "shell": HDR + ["p", "p", "p", "p", "p", "box"],
    "wire": HDR + ["p", "p", "p", "p", "B:CONT", "box"],
    "face": HDR + ["p", "p", "p", "p", "p", "B:SENSE", "B:SIDES", "B:CONT", "box", "B:LOG"],
    "loop": HDR + ["p", "p", "p", "box", "E:LOOP", "loop_tail"],
    "coedge": HDR + ["p", "p", "p", "p", "B:SENSE", "p", "p"],
    "edge": HDR + ["p", "d", "p", "d", "p", "p", "B:SENSE", "s", "box"],
    "vertex": HDR + ["p", "p"],
    "point": HDR + ["pos"],
    "straight-curve": HDR + ["pos", "dir", "ival", "ival"],
    "ellipse-curve": HDR + ["pos", "dir", "dir", "d", "ival", "ival"],
    "plane-surface": HDR + ["pos", "dir", "dir", "B:SENSE_V", "ival", "ival", "ival", "ival"],
    "cone-surface": HDR
    + ["pos", "dir", "dir", "d", "ival", "ival", "d", "d", "d", "B:SENSE", "ival", "ival", "ival", "ival"],
    "intcurve-curve": HDR + ["B:SENSE", "sub:intcurve", "ival", "ival"],
    "spline-surface": HDR + ["B:SENSE", "sub:surface", "ival", "ival", "ival", "ival"],
    "pcurve": HDR + ["i", "B:SENSE", "sub:pcurve", "d", "d"],
    "string_attrib-name_attrib-gen-attrib": AHDR + ["flags", "s", "s"],
    # GeniE's ExactBoxLow / ExactBoxHigh on a wire body (a beam's): a name and one position.
    "position_attrib-name_attrib-gen-attrib": AHDR + ["flags", "s", "pos"],
    "CachedPlaneAttribute-DNV-attrib": AHDR + ["flags", "d", "d", "d", "d", "d", "d", "i"],
    "vertedge-sys-attrib": AHDR + ["flags", "i", "p", "p", "p", "p"],
}


class SabUnsupported(Exception):
    """A record, subtype, enum value or attribute flag word outside the verified table."""


def _check(ok: bool, expected: str, got) -> None:
    # Not an assert: a body the programs do not fit must be refused by name even under -O.
    if not ok:
        raise SabUnsupported(f"expected {expected}, found {got!r}")


# -------- sources (what a field reads from) and sinks (what it writes to)


class TextSrc:
    def __init__(self, words):
        self.w, self.i = words, 0

    def next(self):
        if self.i >= len(self.w):
            raise SabUnsupported("the record ends before its field program does")
        v = self.w[self.i]
        self.i += 1
        return v

    def peek(self):
        return self.w[self.i] if self.i < len(self.w) else None

    def done(self):
        return self.i >= len(self.w)


class BinSrc:
    def __init__(self, toks):
        self.t, self.i = toks, 0

    def next(self):
        if self.i >= len(self.t):
            raise SabUnsupported("the record ends before its field program does")
        v = self.t[self.i]
        self.i += 1
        return v

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def done(self):
        return self.i >= len(self.t)


def fnum(x: float) -> str:
    """Text form of a double, as GeniE's text writer prints it: C's ``%.17g`` (17 significant
    digits, trailing zeros dropped), so ``4``, ``1.6000000000000001``, ``9.9999999999999995e-07``
    and ``1e-10``."""
    return format(x, ".17g")


#: A line break inside a subtype. GeniE's text writer breaks a spline's data over lines --
#: knots on one line, one control point per line, one trailing field per line -- each new line
#: indented by a tab, and adapy's spline readers read those lines, so the rendered text keeps them.
NL = object()


def _is_num(w):
    return re.fullmatch(r"-?\d+(\.\d*)?([eE][-+]?\d+)?|-?\.\d+([eE][-+]?\d+)?", w) is not None


class Codec:
    """One pass over a record: `mode` is 'b2t' (binary tokens -> text words) or 't2b'."""

    def __init__(self, mode, src, out):
        self.mode, self.src, self.out = mode, src, out

    # primitives ------------------------------------------------------
    def ptr(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            _check(tag == T_PTR, "a pointer", (tag, v))
            self.out.append(f"${v}")
        else:
            w = self.src.next()
            _check(w.startswith("$"), "a pointer", w)
            self.out.append((T_PTR, int(w[1:])))

    def int_(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            _check(tag == T_INT, "an int", (tag, v))
            self.out.append(str(v))
        else:
            w = self.src.next()
            self.out.append((T_INT, int(w)))

    def dbl(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            _check(tag == T_DBL, "a double", (tag, v))
            self.out.append(fnum(v))
        else:
            w = self.src.next()
            self.out.append((T_DBL, float(w)))

    def str_(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            _check(tag == T_STR, "a string", (tag, v))
            self.out.append(f"@{len(v)}")
            self.out.append(v)
        else:
            w = self.src.next()
            _check(w.startswith("@"), "a string", w)
            n = int(w[1:])
            s = self.src.next()
            _check(len(s) == n, "a string of the stated length", (w, s))
            self.out.append((T_STR, s))

    def vec(self, tag):
        if self.mode == "b2t":
            t, v = self.src.next()
            _check(t == tag, "a position" if tag == T_POS else "a direction", (t, v))
            self.out.extend(fnum(x) for x in v)
        else:
            self.out.append((tag, tuple(float(self.src.next()) for _ in range(3))))

    def bool_(self, pair):
        f, t = pair
        if self.mode == "b2t":
            tag, _ = self.src.next()
            _check(tag in (T_TRUE, T_FALSE), "a logical", tag)
            self.out.append(t if tag == T_TRUE else f)
            return tag == T_TRUE
        w = self.src.next()
        _check(w in pair, f"one of {pair}", w)
        self.out.append((T_TRUE if w == t else T_FALSE, None))
        return w == t

    def box(self):
        if self.bool_(("F", "T")):
            self.vec(T_POS)
            self.vec(T_POS)

    def ival(self):
        if self.mode == "b2t":
            tag, _ = self.src.next()
            _check(tag in (T_TRUE, T_FALSE), "a logical", tag)
            if tag == T_TRUE:
                self.out.append("F")
                self.dbl()
            else:
                self.out.append("I")
        else:
            w = self.src.next()
            if w == "I":
                self.out.append((T_FALSE, None))
            else:
                _check(w == "F", "an interval (F or I)", w)
                self.out.append((T_TRUE, None))
                self.dbl()

    def enum(self, table):
        tbl = ENUMS[table]
        if self.mode == "b2t":
            tag, v = self.src.next()
            _check(tag == T_ENUM, "an enum", (tag, v))
            if v not in tbl:
                raise SabUnsupported(f"enum {table} value {v}")
            self.out.append(tbl[v])
            return tbl[v]
        w = self.src.next()
        inv = {n: k for k, n in tbl.items()}
        if w not in inv:
            raise SabUnsupported(f"enum {table} word {w!r}")
        self.out.append((T_ENUM, inv[w]))
        return w

    def flags(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            _check(tag == T_INT, "an int", (tag, v))
            if v not in FLAGS_INV:
                raise SabUnsupported(f"gen-attrib flag word {v} ({v:#x}) not in the observed table")
            self.out.extend(FLAGS_INV[v].split())
        else:
            key = " ".join(self.src.next() for _ in range(18))
            if key not in FLAGS:
                raise SabUnsupported(f"gen-attrib flags {key!r} not in the observed table")
            self.out.append((T_INT, FLAGS[key]))

    def keyword(self, expect=None):
        """A bare identifier inside a subtype (exactcur, nubs, null_surface ...): text word <-> 0x0D type token."""
        if self.mode == "b2t":
            tag, v = self.src.next()
            _check(tag == T_TYPE, "a type name", (tag, v))
            if expect is not None and v not in expect:
                raise SabUnsupported(f"subtype keyword {v!r}, expected {expect}")
            self.out.append(v)
            return v
        w = self.src.next()
        if expect is not None and w not in expect:
            raise SabUnsupported(f"subtype keyword {w!r}, expected {expect}")
        self.out.append((T_TYPE, w))
        return w

    def brace(self, open_):
        if self.mode == "b2t":
            tag, _ = self.src.next()
            _check(tag == (T_SUB_OPEN if open_ else T_SUB_CLOSE), "a subtype brace", tag)
            self.out.append("{" if open_ else "}")
        else:
            w = self.src.next()
            _check(w == ("{" if open_ else "}"), "a subtype brace", w)
            self.out.append((T_SUB_OPEN if open_ else T_SUB_CLOSE, None))

    def nl(self):
        if self.mode == "b2t":
            self.out.append(NL)

    # composite --------------------------------------------------------
    def bs3_curve(self):
        """nubs|nurbs deg closure nknots (knot mult)* ctrlpts* fit_tol  -- the bs3_curve block."""
        kind = self.keyword(("nubs", "nurbs"))
        deg = self._int_value()
        self._closure()
        nknots = self._int_value()
        self.nl()
        summult = 0
        for _ in range(nknots):
            self.dbl()
            summult += self._int_value()
        self.nl()
        # GeniE stores the end knots with multiplicity deg (the clamped extra knot is implied):
        # measured deg1/sum2 -> 2 cps, deg2/sum4 -> 3, deg2/sum6 -> 5, closed deg2/sum10 -> 9.
        ncp = summult - deg + 1
        for _ in range(ncp):
            self.dbl()
            self.dbl()
            self.dbl()
            if kind == "nurbs":
                self.dbl()
            self.nl()
        self.dbl()  # fit tolerance
        self.nl()

    def _closure(self) -> None:
        # The control-point count below is measured for open and closed splines only; a periodic
        # one stores its wrap-around differently in ACIS and no twin has one.
        if self.enum("CLOSURE") == "periodic":
            raise SabUnsupported("a periodic spline (control-point count for periodic closure unverified)")

    def _int_value(self) -> int:
        before = len(self.out)
        self.int_()
        v = self.out[before]
        return int(v) if self.mode == "b2t" else v[1]

    def sub_intcurve(self):
        self.brace(True)
        kw = self.keyword()
        if kw != "exactcur":
            raise SabUnsupported(f"intcurve subtype {kw!r}")
        self.enum("FULL")
        self.bs3_curve()
        for kw in ("null_surface", "null_surface", "nullbs", "nullbs"):
            self.keyword((kw,))
            self.nl()
        self.dbl()
        self.nl()
        self.dbl()
        self.nl()
        self.ival()
        self.ival()
        self.nl()
        for _ in range(3):
            self.int_()
            self.nl()
        self.nl()  # GeniE leaves this line empty
        self.dbl()
        self.nl()
        self.enum("NONE")
        self.bool_(("F", "T"))
        self.ival()
        self.ival()
        self.brace(False)

    def bs3_surface(self):
        """nubs|nurbs udeg vdeg both closure_u closure_v sing_u sing_v nu nv (knots) cps fit_tol."""
        kind = self.keyword(("nubs", "nurbs"))
        udeg = self._int_value()
        vdeg = self._int_value()
        self.keyword(("both",))  # rational-in-both-directions marker; only 'both' observed
        self._closure()
        self._closure()
        self.enum("SING")
        self.enum("SING")
        nu = self._int_value()
        nv = self._int_value()
        self.nl()
        su = sv = 0
        for _ in range(nu):
            self.dbl()
            su += self._int_value()
        self.nl()
        for _ in range(nv):
            self.dbl()
            sv += self._int_value()
        self.nl()
        for _ in range((su - udeg + 1) * (sv - vdeg + 1)):
            self.dbl()
            self.dbl()
            self.dbl()
            if kind == "nurbs":
                self.dbl()
            self.nl()
        self.dbl()  # fit tolerance
        self.nl()

    def sub_surface(self):
        self.brace(True)
        kw = self.keyword()
        if kw == "ref":
            self.int_()
            self.brace(False)
            return
        if kw != "exactsur":
            raise SabUnsupported(f"surface subtype {kw!r}")
        self.enum("FULL")
        self.bs3_surface()
        for _ in range(6):
            self.int_()
            self.nl()
        for _ in range(4):
            self.ival()
        self.brace(False)

    def sub_pcurve(self):
        self.brace(True)
        kw = self.keyword()
        if kw != "exppc":
            raise SabUnsupported(f"pcurve subtype {kw!r}")
        kind = self.keyword(("nubs", "nurbs"))
        deg = self._int_value()
        self._closure()
        nknots = self._int_value()
        self.nl()
        summult = 0
        for _ in range(nknots):
            self.dbl()
            summult += self._int_value()
        self.nl()
        for _ in range(summult - deg + 1):
            self.dbl()
            self.dbl()
            if kind == "nurbs":
                self.dbl()
            self.nl()
        self.dbl()  # fit tolerance
        self.nl()
        self.dbl()  # observed -1
        self.nl()
        self.keyword(("spline",))
        self.bool_(BOOL_WORDS["SENSE"])
        self.sub_surface()
        for _ in range(4):
            self.ival()
        self.nl()
        self.brace(False)

    def run(self, program):
        for f in program:
            if f == "p":
                self.ptr()
            elif f == "i":
                self.int_()
            elif f == "d":
                self.dbl()
            elif f == "s":
                self.str_()
            elif f == "pos":
                self.vec(T_POS)
            elif f == "dir":
                self.vec(T_DIR)
            elif f == "box":
                self.box()
            elif f == "ival":
                self.ival()
            elif f.startswith("B:"):
                self.bool_(BOOL_WORDS[f[2:]])
            elif f.startswith("E:"):
                self._last_enum = self.enum(f[2:])
            elif f == "loop_tail":
                if self._last_enum == "periphery":
                    self.ptr()
                    self.bool_(("F", "T"))
            elif f == "flags":
                self.flags()
            elif f == "sub:intcurve":
                self.sub_intcurve()
            elif f == "sub:surface":
                self.sub_surface()
            elif f == "sub:pcurve":
                self.sub_pcurve()
            else:
                raise ValueError(f)
        if not self.src.done():
            raise SabUnsupported(f"trailing data after program: {self.src.peek()!r}")


# ---------------------------------------------------------------- record level

END_TEXT = "End-of-ACIS-data"

#: The header GeniE V9.3 writes in both formats: save-file version 2000, no history (flags 0),
#: the ACIS 33.0.1 kernel. The field programs above were measured on exactly that, so any other
#: header is refused rather than trusted.
SAVE_VERSION = 2000
ACIS_VERSION = "ACIS 33.0.1"


def check_header(version: int, flags: int, strings: list[str], doubles: list) -> None:
    """Refuse a header the field programs were not measured on, by what differs."""
    if version != SAVE_VERSION:
        raise SabUnsupported(f"save-file version {version} (only {SAVE_VERSION} is verified)")
    if flags != 0:
        raise SabUnsupported(f"header flags {flags}: a history section (GeniE writes 0)")
    if len(strings) != 3 or len(doubles) != 3:
        raise SabUnsupported(f"a header with {len(strings)} strings and {len(doubles)} doubles (3 and 3 expected)")
    if not strings[1].startswith(ACIS_VERSION):
        raise SabUnsupported(f"kernel {strings[1]!r} (only {ACIS_VERSION} is verified)")


def _unsupported_type(typ: str) -> SabUnsupported:
    if typ == "transform":
        return SabUnsupported("record type 'transform' (a body placed by a transformation; not observed)")
    return SabUnsupported(f"record type {typ!r}")


def render(records):
    """Binary token records -> text record strings ('-i type ... #'), plus the end marker."""
    out = []
    for n, toks in enumerate(records):
        tag, typ = toks[0]
        _check(tag == T_TYPE, "a type name", toks[0])
        if typ == END_TEXT:
            if n != len(records) - 1:
                raise SabUnsupported(f"{len(records) - 1 - n} records after {END_TEXT}")
            out.append(END_TEXT)
            break
        prog = PROGRAMS.get(typ)
        if prog is None:
            raise _unsupported_type(typ)
        c = Codec("b2t", BinSrc(toks[1:]), [])
        try:
            c.run(prog)
        except SabUnsupported as e:
            raise SabUnsupported(f"record {n} ({typ}): {e}") from e
        out.append(f"-{n} {typ} " + _layout(c.out) + "#")
    else:
        raise SabUnsupported(f"no {END_TEXT} marker: the body is truncated")
    return out


def _layout(words) -> str:
    # Every word is followed by a space, a line break inside a subtype is CRLF + tab: GeniE's form.
    return "".join("\r\n\t" if w is NL else f"{w} " for w in words)


def sat_text_from_sab(data: bytes) -> str:
    """A whole SAB body as the SAT text GeniE would have saved for it, CRLF line ends.

    Header lines first (the same three fields, the strings with their lengths), then one line per
    record, then the end marker: the shape of GeniE's own ``acisGeometry.sat``, so whatever reads a
    text body reads this one without knowing where it came from. Refuses with
    :class:`SabUnsupported` anything outside the measured tables.
    """
    hdr, records = tokenize(data)
    check_header(hdr["version"], hdr["flags"], hdr["strings"], hdr["doubles"])
    lines = [
        # GeniE pads the first line to 21 characters (room to rewrite the counts in place).
        f"{hdr['version']} {hdr['n_records']} {hdr['n_entities']} {hdr['flags']}".ljust(21),
        " ".join(f"{len(s)} {s}" for s in hdr["strings"]) + " ",
        " ".join(fnum(x) for x in hdr["doubles"]) + " ",
    ]
    lines += render(records)
    return "\r\n".join(lines) + " "


def record_words(rec: str) -> list[str]:
    """Whitespace-separated words of a text record, except that ``@n`` takes the next ``n``
    characters as one word: a string may hold spaces, and its length is what delimits it."""
    words: list[str] = []
    i, n = 0, len(rec)
    while i < n:
        if rec[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not rec[j].isspace():
            j += 1
        word = rec[i:j]
        words.append(word)
        m = re.fullmatch(r"@(\d+)", word)
        if m:
            k = int(m.group(1))
            words.append(rec[j + 1 : j + 1 + k])
            j += 1 + k
        i = j
    return words


def text_records(sat_text: str):
    lines = sat_text.replace("\r", "").split("\n")
    body = "\n".join(lines[3:])
    recs = re.findall(r"-\d+ .*?#", body, flags=re.S)
    return lines[:3], [r.replace("\n", " ").strip() for r in recs]


def pack_record(rec: str, index: int | None = None) -> bytes:
    words = record_words(rec)
    _check(len(words) >= 3 and words[-1] == "#", "a record ending in #", rec)
    # The binary record's index is its position; a text index that disagrees would move every
    # pointer to it.
    if index is not None and words[0] != f"-{index}":
        raise SabUnsupported(f"record {words[0]!r} at position {index}: text indices must be sequential")
    typ = words[1]
    prog = PROGRAMS.get(typ)
    if prog is None:
        raise _unsupported_type(typ)
    c = Codec("t2b", TextSrc(words[2:-1]), [])
    try:
        c.run(prog)
    except SabUnsupported as e:
        raise SabUnsupported(f"record {words[0][1:]} ({typ}): {e}") from e
    return encode_type(typ) + b"".join(encode_token(t, v) for t, v in c.out) + bytes([T_END])


def _short_string(tag: int, s: str) -> bytes:
    b = s.encode("latin1")
    if len(b) > 255:
        raise SabUnsupported(f"a {len(b)}-byte string (long strings, tag 0x12, not observed)")
    return bytes([tag, len(b)]) + b


def encode_type(name: str) -> bytes:
    parts = name.split("-")
    out = b""
    for p in parts[:-1]:
        out += _short_string(T_TYPEX, p)
    return out + _short_string(T_TYPE, parts[-1])


def encode_token(tag, v) -> bytes:
    if tag in (T_INT, T_PTR, T_ENUM):
        return struct.pack("<Bi", tag, v)
    if tag == T_DBL:
        return struct.pack("<Bd", tag, v)
    if tag == T_STR:
        return _short_string(tag, v)
    if tag == T_TYPE:
        return encode_type(v)
    if tag in (T_POS, T_DIR):
        return struct.pack("<B3d", tag, *v)
    if tag in (T_TRUE, T_FALSE, T_SUB_OPEN, T_SUB_CLOSE):
        return bytes([tag])
    raise ValueError(tag)


def pack_header(line1: str, line2: str, line3: str) -> bytes:
    """Text header lines -> SAB header bytes. Line 2 is '<n> product <n> acis <n> date'."""
    ver, nrec, nent, flags = (int(x) for x in line1.split())
    strings = []
    rest = line2.strip()
    for _ in range(3):
        m = re.match(r"(\d+) ", rest)
        if m is None:
            raise SabUnsupported(f"header line 2 {line2!r}: expected three length-prefixed strings")
        n = int(m.group(1))
        strings.append(rest[m.end() : m.end() + n])
        rest = rest[m.end() + n :].lstrip()
    doubles = [float(x) for x in line3.split()]
    check_header(ver, flags, strings, doubles)
    out = SIGNATURE + struct.pack("<4i", ver, nrec, nent, flags)
    for s in strings:
        out += _short_string(T_STR, s)
    for x in doubles:
        out += struct.pack("<Bd", T_DBL, x)
    return out


def pack(sat_text: str) -> bytes:
    """A SAT text body -> the SAB bytes GeniE V9.3 would save for it. Refuses with
    :class:`SabUnsupported` anything outside the measured tables."""
    hdr, recs = text_records(sat_text)
    out = pack_header(*hdr)
    for n, r in enumerate(recs):
        out += pack_record(r, n)
    # GeniE writes the end marker as ONE type token (0x0d 0x10 "End-of-ACIS-data"), hyphens kept.
    return out + bytes([T_TYPE, len(END_TEXT)]) + END_TEXT.encode()


def header_end(data: bytes) -> int:
    """Offset of the first record (after signature, 4 ints, 3 strings, 3 doubles)."""
    i = len(SIGNATURE) + 16
    for _ in range(3):
        i += 2 + data[i + 1]
    return i + 27


def normalize(rec: str) -> str:
    return " ".join(fnum(float(w)) if _is_num(w) else w for w in record_words(rec))
