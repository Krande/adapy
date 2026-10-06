"""Standard ACIS Binary (SAB) <-> text (SAT), record for record.

PROTOTYPE -- not wired into the workspace reader or writer, which still refuse a
binary body by name (see ``sab.py``). What is here is the token encoding and the
per-record field programs, proven against GeniE V9.3-00 twins: the same model
saved once as text and once as binary. Two oracles hold on every committed twin
(``tests/core/cadit/sat/test_sab_codec.py``):

  render(tokenize(binary)) == the text twin's records, token for token
                              (numbers compared by value, words exactly), and
  pack(text)               == the binary twin's bytes after the header, byte for
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
string_attrib-name_attrib-gen-attrib, CachedPlaneAttribute-DNV-attrib and
vertedge-sys-attrib attributes -- every type GeniE wrote for the twins and every
type adapy's own SAT writer emits. Anything else (``rulesur`` from a skinned
surface, an unseen attribute flag word, an unseen enum value) is refused by name.

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
    if not data.startswith(SIGNATURE):
        raise ValueError("not a SAB body")
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
            ln = struct.unpack_from("<i", data, i)[0]
            cur.append((tag, data[i + 4 : i + 4 + ln].decode("latin1")))
            i += 4 + ln
        elif tag in (T_POS, T_DIR):
            cur.append((tag, struct.unpack_from("<3d", data, i)))
            i += 24
        elif tag in (T_TRUE, T_FALSE, T_SUB_OPEN, T_SUB_CLOSE):
            cur.append((tag, None))
        elif tag == T_END:
            recs.append(cur)
            cur = []
        else:
            raise ValueError(f"unknown SAB tag 0x{tag:02x} at offset {i - 1} (record {len(recs)})")
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
    "LOOP": {0: "unknown", 1: "periphery", 2: "hole", 3: "u_separation", 4: "v_separation", 5: "uv_separation"},
    "FULL": {0: "full"},
    "CLOSURE": {0: "open", 1: "closed", 2: "periodic"},
    "NONE": {2: "none"},
    "SING": {0: "none"},
}
# Observed packings of the 18 generic-attribute action ints into one SAB int.
FLAGS = {
    "2 1 1 1 1 1 1 1 1 1 1 1 1 1 0 1 1 1": 14675622,
    "1 1 1 1 1 1 1 1 1 1 1 1 0 1 0 1 1 1": 14413477,
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
    "CachedPlaneAttribute-DNV-attrib": AHDR + ["flags", "d", "d", "d", "d", "d", "d", "i"],
    "vertedge-sys-attrib": AHDR + ["flags", "i", "p", "p", "p", "p"],
}


class SabUnsupported(Exception):
    """A record, subtype, enum value or attribute flag word outside the verified table."""


# -------- sources (what a field reads from) and sinks (what it writes to)


class TextSrc:
    def __init__(self, words):
        self.w, self.i = words, 0

    def next(self):
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
        v = self.t[self.i]
        self.i += 1
        return v

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def done(self):
        return self.i >= len(self.t)


def fnum(x: float) -> str:
    """Text form of a double. GeniE's text writer prints 17 significant digits; a value whose
    repr is shorter prints as the shortest round-trip form in Python. Compared by value."""
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return repr(x)


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
            assert tag == T_PTR, (tag, v)
            self.out.append(f"${v}")
        else:
            w = self.src.next()
            assert w.startswith("$"), w
            self.out.append((T_PTR, int(w[1:])))

    def int_(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            assert tag == T_INT, (tag, v)
            self.out.append(str(v))
        else:
            w = self.src.next()
            self.out.append((T_INT, int(w)))

    def dbl(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            assert tag == T_DBL, (tag, v)
            self.out.append(fnum(v))
        else:
            w = self.src.next()
            self.out.append((T_DBL, float(w)))

    def str_(self):
        if self.mode == "b2t":
            tag, v = self.src.next()
            assert tag == T_STR, (tag, v)
            self.out.append(f"@{len(v)}")
            self.out.append(v)
        else:
            w = self.src.next()
            assert w.startswith("@"), w
            n = int(w[1:])
            s = self.src.next()
            assert len(s) == n, (w, s)
            self.out.append((T_STR, s))

    def vec(self, tag):
        if self.mode == "b2t":
            t, v = self.src.next()
            assert t == tag, (t, v)
            self.out.extend(fnum(x) for x in v)
        else:
            self.out.append((tag, tuple(float(self.src.next()) for _ in range(3))))

    def bool_(self, pair):
        f, t = pair
        if self.mode == "b2t":
            tag, _ = self.src.next()
            assert tag in (T_TRUE, T_FALSE), tag
            self.out.append(t if tag == T_TRUE else f)
            return tag == T_TRUE
        w = self.src.next()
        assert w in pair, (w, pair)
        self.out.append((T_TRUE if w == t else T_FALSE, None))
        return w == t

    def box(self):
        if self.bool_(("F", "T")):
            self.vec(T_POS)
            self.vec(T_POS)

    def ival(self):
        if self.mode == "b2t":
            tag, _ = self.src.next()
            assert tag in (T_TRUE, T_FALSE), tag
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
                assert w == "F", w
                self.out.append((T_TRUE, None))
                self.dbl()

    def enum(self, table):
        tbl = ENUMS[table]
        if self.mode == "b2t":
            tag, v = self.src.next()
            assert tag == T_ENUM, (tag, v)
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
            assert tag == T_INT, (tag, v)
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
            assert tag == T_TYPE, (tag, v)
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
            assert tag == (T_SUB_OPEN if open_ else T_SUB_CLOSE), tag
            self.out.append("{" if open_ else "}")
        else:
            w = self.src.next()
            assert w == ("{" if open_ else "}"), w
            self.out.append((T_SUB_OPEN if open_ else T_SUB_CLOSE, None))

    # composite --------------------------------------------------------
    def bs3_curve(self):
        """nubs|nurbs deg closure nknots (knot mult)* ctrlpts* fit_tol  -- the bs3_curve block."""
        kind = self.keyword(("nubs", "nurbs"))
        deg = self._int_value()
        self.enum("CLOSURE")
        nknots = self._int_value()
        summult = 0
        for _ in range(nknots):
            self.dbl()
            summult += self._int_value()
        # GeniE stores the end knots with multiplicity deg (the clamped extra knot is implied):
        # measured deg1/sum2 -> 2 cps, deg2/sum4 -> 3, deg2/sum6 -> 5, closed deg2/sum10 -> 9.
        ncp = summult - deg + 1
        for _ in range(ncp):
            self.dbl()
            self.dbl()
            self.dbl()
            if kind == "nurbs":
                self.dbl()
        self.dbl()  # fit tolerance

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
        self.keyword(("null_surface",))
        self.keyword(("null_surface",))
        self.keyword(("nullbs",))
        self.keyword(("nullbs",))
        self.dbl()
        self.dbl()
        self.ival()
        self.ival()
        self.int_()
        self.int_()
        self.int_()
        self.dbl()
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
        self.enum("CLOSURE")
        self.enum("CLOSURE")
        self.enum("SING")
        self.enum("SING")
        nu = self._int_value()
        nv = self._int_value()
        su = sv = 0
        for _ in range(nu):
            self.dbl()
            su += self._int_value()
        for _ in range(nv):
            self.dbl()
            sv += self._int_value()
        for _ in range((su - udeg + 1) * (sv - vdeg + 1)):
            self.dbl()
            self.dbl()
            self.dbl()
            if kind == "nurbs":
                self.dbl()
        self.dbl()  # fit tolerance

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
        self.enum("CLOSURE")
        nknots = self._int_value()
        summult = 0
        for _ in range(nknots):
            self.dbl()
            summult += self._int_value()
        for _ in range(summult - deg + 1):
            self.dbl()
            self.dbl()
            if kind == "nurbs":
                self.dbl()
        self.dbl()  # fit tolerance
        self.dbl()  # observed -1
        self.keyword(("spline",))
        self.bool_(BOOL_WORDS["SENSE"])
        self.sub_surface()
        for _ in range(4):
            self.ival()
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


def render(records):
    """Binary token records -> text record strings ('-i type ... #'), plus the end marker."""
    out = []
    for n, toks in enumerate(records):
        tag, typ = toks[0]
        assert tag == T_TYPE, toks[0]
        if typ == END_TEXT:
            out.append(END_TEXT)
            break
        prog = PROGRAMS.get(typ)
        if prog is None:
            raise SabUnsupported(f"record type {typ!r}")
        c = Codec("b2t", BinSrc(toks[1:]), [])
        c.run(prog)
        out.append(f"-{n} {typ} " + " ".join(c.out) + " #")
    return out


def text_records(sat_text: str):
    lines = sat_text.replace("\r", "").split("\n")
    body = "\n".join(lines[3:])
    recs = re.findall(r"-\d+ .*?#", body, flags=re.S)
    return lines[:3], [re.sub(r"\s+", " ", r).strip() for r in recs]


def pack_record(rec: str) -> bytes:
    words = rec.split()
    assert words[-1] == "#", rec
    typ = words[1]
    prog = PROGRAMS.get(typ)
    if prog is None:
        raise SabUnsupported(f"record type {typ!r}")
    c = Codec("t2b", TextSrc(words[2:-1]), [])
    c.run(prog)
    return encode_type(typ) + b"".join(encode_token(t, v) for t, v in c.out) + bytes([T_END])


def encode_type(name: str) -> bytes:
    parts = name.split("-")
    out = b""
    for p in parts[:-1]:
        out += bytes([T_TYPEX, len(p)]) + p.encode("latin1")
    return out + bytes([T_TYPE, len(parts[-1])]) + parts[-1].encode("latin1")


def encode_token(tag, v) -> bytes:
    if tag in (T_INT, T_PTR, T_ENUM):
        return struct.pack("<Bi", tag, v)
    if tag == T_DBL:
        return struct.pack("<Bd", tag, v)
    if tag == T_STR:
        b = v.encode("latin1")
        return bytes([tag, len(b)]) + b
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
    out = SIGNATURE + struct.pack("<4i", ver, nrec, nent, flags)
    rest = line2.strip()
    for _ in range(3):
        m = re.match(r"(\d+) ", rest)
        n = int(m.group(1))
        s = rest[m.end() : m.end() + n]
        out += bytes([T_STR, n]) + s.encode("latin1")
        rest = rest[m.end() + n :].lstrip()
    for x in line3.split():
        out += struct.pack("<Bd", T_DBL, float(x))
    return out


def pack(sat_text: str) -> bytes:
    hdr, recs = text_records(sat_text)
    out = pack_header(*hdr)
    for r in recs:
        out += pack_record(r)
    # GeniE writes the end marker as ONE type token (0x0d 0x10 "End-of-ACIS-data"), hyphens kept.
    return out + bytes([T_TYPE, len(END_TEXT)]) + END_TEXT.encode()


def header_end(data: bytes) -> int:
    """Offset of the first record (after signature, 4 ints, 3 strings, 3 doubles)."""
    i = len(SIGNATURE) + 16
    for _ in range(3):
        i += 2 + data[i + 1]
    return i + 27


def normalize(rec: str) -> str:
    return " ".join(fnum(float(w)) if _is_num(w) else w for w in rec.split())
