"""The Abaqus input-file grammar, parsed once.

An Abaqus deck has exactly three kinds of line, and every keyword in the format shares them:

* a **comment line**, starting with ``**``;
* a **keyword line**, starting with a single ``*``: the keyword, then comma-separated
  parameters, each either ``NAME=VALUE`` or a bare flag;
* a **data line**: anything else, belonging to the keyword line above it.

Everything the reader needs follows from those three rules, so they are implemented here once
rather than re-encoded in a regex per card. That is the point of this module. A regex that
spells out one card's parameters *in order* also silently decides that no other parameter may
appear, and Abaqus does not work that way -- parameters are order-free and most keywords
accept a dozen optional ones. A pattern like ``elset=(.*?)\\s*,\\s*material=(.*?)`` does not
reject ``*Solid Section, elset=s, orientation=O, material=M``; it captures
``elset='s, orientation=O'`` and builds a section against a set that does not exist. Reading
parameters by name from a parsed card cannot do that.

**Cost.** Card boundaries are found by a single compiled scan for lines beginning with ``*``,
which runs in C over the whole buffer. Only keyword lines are then parsed in Python, and a
card's data block is kept as a ``(start, end)`` span into the source: no per-line work and no
copying happens for data unless a reader asks for it, and :attr:`Card.data_text` is then a
plain slice. One pass replaces the two dozen separate ``DOTALL`` scans the card regexes used
to make over the same buffer.

**Streaming.** :func:`stream_cards` is the same grammar driven from a line iterator, for
callers that must not hold the deck in memory (see :func:`stream_file`). It yields the same
:class:`Card`, with data lines materialized per card rather than spanned, so peak memory is
one card rather than one file.
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator, Mapping

__all__ = [
    "Card",
    "Params",
    "tokenize",
    "stream_cards",
    "stream_file",
    "iter_cards",
    "iter_blocks",
    "comment_property",
    "normalize",
]

_COMMENT_PREFIX = "**"
_WS = re.compile(r"\s+")
# Every card boundary: a line whose first non-blank character is '*'. A comment line starts
# with '*' too, and ends a data block exactly as the previous regexes' ``(?=\*|\Z)`` did.
_CARD_START = re.compile(r"^[ \t]*\*", re.M)


def normalize(name: str) -> str:
    """A keyword or parameter name in comparable form: upper case, single spaces.

    Abaqus is case-insensitive and tolerant about internal spacing, so ``ref node``,
    ``REF  NODE`` and ``Ref Node`` are one parameter.
    """
    return _WS.sub(" ", name.strip()).upper()


def _split_outside_quotes(line: str) -> list[str]:
    """Split on commas, ignoring those inside double quotes.

    Set and file names are quoted when they contain a comma or a space, so a naive
    ``line.split(",")`` would cut a name in half. The quick path avoids the character loop
    for the overwhelming majority of lines, which carry no quotes at all.
    """
    if '"' not in line:
        return line.split(",")
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for ch in line:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


class Params(Mapping):
    """The parameters of one keyword line, looked up by name in any casing.

    A flag parameter (``, generate``, ``, internal``) maps to ``None``; membership is what
    carries its meaning, so test it with ``in`` rather than for truthiness.
    """

    __slots__ = ("_d",)

    def __init__(self, pairs: Iterable[tuple[str, str | None]] = ()) -> None:
        self._d: dict[str, str | None] = {normalize(k): v for k, v in pairs}

    def __getitem__(self, key: str) -> str | None:
        return self._d[normalize(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and normalize(key) in self._d

    def get(self, key: str, default=None):
        return self._d.get(normalize(key), default)

    def first(self, *names: str, default=None):
        """The value of whichever of ``names`` is present -- for spelling variants.

        ``*Beam Section`` accepts both ``SECTION=`` and the abbreviation ``SECT=``; a reader
        should ask for the property, not for one spelling of it.
        """
        for name in names:
            key = normalize(name)
            if key in self._d:
                return self._d[key]
        return default

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Params({self._d!r})"


class Card:
    """One keyword line with the data lines that belong to it.

    Two views of the data, and the difference matters:

    ``data_text``
        The block exactly as written, comments included. A slice of the source in the
        in-memory case, so free. Readers that flatten a whole numeric block (element
        connectivity, coupling DOFs) want this.
    ``data_lines``
        The same block as individual lines with comments and blanks removed, computed on
        first use. Readers that index lines (``*Shell Section``'s thickness line,
        ``*Beam Section``'s two lines) want this.
    """

    __slots__ = ("keyword", "params", "comments", "lineno", "start", "_src", "_span", "_lines", "_text")

    def __init__(
        self,
        keyword: str,
        params: Params,
        comments: tuple[str, ...] = (),
        lineno: int = 0,
        start: int = 0,
        src: str | None = None,
        span: tuple[int, int] = (0, 0),
        lines: tuple[str, ...] | None = None,
    ) -> None:
        self.keyword = keyword
        self.params = params
        self.comments = comments
        """The unbroken run of comment lines directly above the keyword line, ``**`` stripped.

        Abaqus/CAE writes a card's name there and nowhere else -- ``** Section: Cast node``
        above a ``*Solid Section``, ``** Name: BC-1  Type: Displacement/Rotation`` above a
        ``*Boundary``. Binding those to the card they sit on, rather than searching the deck
        for them, is what makes it impossible for one card to pick up another card's name.
        """
        self.lineno = lineno
        self.start = start
        self._src = src
        self._span = span
        self._lines = lines
        self._text: str | None = None

    @property
    def data_start(self) -> int:
        """Offset just past this card's keyword line (in-memory tokenization only)."""
        return self._span[0]

    @property
    def data_end(self) -> int:
        """Offset where this card's data block ends (in-memory tokenization only)."""
        return self._span[1]

    @property
    def keyword_line(self) -> str:
        """The card's keyword line as written, continuations included."""
        if self._src is None:
            return f"*{self.keyword}"
        return self._src[self.start : self._span[0]].rstrip("\n")

    @property
    def data_text(self) -> str:
        if self._text is None:
            if self._src is not None:
                self._text = self._src[self._span[0] : self._span[1]]
            else:
                self._text = "\n".join(self._lines or ())
        return self._text

    @property
    def data_lines(self) -> tuple[str, ...]:
        if self._lines is None:
            self._lines = tuple(
                stripped
                for stripped in (line.strip() for line in self.data_text.splitlines())
                if stripped and not stripped.startswith(_COMMENT_PREFIX)
            )
        return self._lines

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"Card(*{self.keyword}, line {self.lineno}, params={dict(self.params)!r})"


def _parse_keyword_line(line: str) -> tuple[str, list[tuple[str, str | None]]]:
    tokens = _split_outside_quotes(line)
    keyword = normalize(tokens[0].lstrip("*"))
    params: list[tuple[str, str | None]] = []
    for token in tokens[1:]:
        if not token.strip():
            continue
        # Partition on the FIRST '=' only: a path or an expression may contain more.
        name, sep, value = token.partition("=")
        params.append((name, _strip_quotes(value) if sep else None))
    return keyword, params


def _is_parameter_continuation(keyword: str, next_line: str) -> bool:
    """Is ``next_line`` more parameters for this keyword, or the card's first data line?

    A keyword line ending in a comma is *usually* continued, but a deck may also write a
    redundant trailing comma before its data. The registered parameter names settle it: a
    continuation carries parameters, so its first token must be one this keyword accepts.
    With no registered spec, assume data -- the conservative reading, and what the previous
    regex-based reader did.
    """
    from .keywords import lookup

    spec = lookup(keyword)
    if spec is None:
        return False
    token = _split_outside_quotes(next_line)[0]
    return spec.accepts(normalize(token.partition("=")[0]))


def tokenize(bulk_str: str) -> tuple[Card, ...]:
    """Every card in ``bulk_str``, in the order it appears.

    Keyword-line continuation is applied; data-line continuation deliberately is not. A
    keyword line ending in a comma unambiguously continues, because the next line cannot
    start a new keyword. A *data* line ending in a comma is not the same promise -- decks
    write a trailing comma on a complete final value all the time (``*Mass`` is one value
    followed by a comma, ``*Friction`` another), so joining on it would swallow the
    following line. Readers with genuinely multi-line data flatten the whole block instead,
    which is correct either way.
    """
    cards: list[Card] = []
    n = len(bulk_str)

    pending: Card | None = None
    comments: list[str] = []
    comments_end = -1

    # Line numbers, tracked incrementally: positions only move forward, so the newline
    # counting is linear over the whole buffer rather than per card.
    counted_to = 0
    counted_lines = 1

    pos = 0
    while pos < n:
        match = _CARD_START.search(bulk_str, pos)
        if match is None:
            break
        start = match.start()
        eol = bulk_str.find("\n", start)
        if eol == -1:
            eol = n

        if pending is not None:
            pending._span = (pending._span[0], start)
            cards.append(pending)
            pending = None

        line = bulk_str[start:eol].strip()
        pos = eol + 1

        if line.startswith(_COMMENT_PREFIX):
            if comments_end != start:
                comments.clear()
            comments.append(line[len(_COMMENT_PREFIX) :].strip())
            comments_end = pos
            continue

        counted_lines += bulk_str.count("\n", counted_to, start)
        counted_to = start
        lineno = counted_lines

        keyword_line = line
        keyword = normalize(_split_outside_quotes(line)[0].lstrip("*"))
        while keyword_line.rstrip().endswith(",") and pos < n:
            nxt_eol = bulk_str.find("\n", pos)
            if nxt_eol == -1:
                nxt_eol = n
            nxt = bulk_str[pos:nxt_eol].strip()
            if not nxt or nxt.startswith("*") or not _is_parameter_continuation(keyword, nxt):
                break
            keyword_line = keyword_line.rstrip() + nxt
            pos = nxt_eol + 1

        keyword, params = _parse_keyword_line(keyword_line)
        attached = tuple(comments) if comments_end == start else ()
        comments.clear()
        comments_end = -1
        pending = Card(
            keyword=keyword,
            params=Params(params),
            comments=attached,
            lineno=lineno,
            start=start,
            src=bulk_str,
            span=(pos, n),
        )

    if pending is not None:
        pending._span = (pending._span[0], n)
        cards.append(pending)

    return tuple(cards)


def stream_cards(lines: Iterable[str]) -> Iterator[Card]:
    """The same grammar, driven from a line iterator instead of a whole buffer.

    Peak memory is one card's data block rather than the file. Cards are yielded as each is
    completed -- that is, when the next keyword or comment line is reached -- so a consumer
    that discards what it has read never holds more than that.
    """
    pending_keyword: str | None = None
    pending_params: list[tuple[str, str | None]] = []
    pending_comments: tuple[str, ...] = ()
    pending_lineno = 0
    data: list[str] = []

    comments: list[str] = []
    comments_lineno = -1
    continuing = False
    keyword_line = ""

    def build() -> Card:
        return Card(
            keyword=pending_keyword,
            params=Params(pending_params),
            comments=pending_comments,
            lineno=pending_lineno,
            lines=tuple(data),
        )

    lineno = 0
    for raw in lines:
        lineno += 1
        stripped = raw.strip()

        if continuing:
            if stripped and not stripped.startswith("*") and _is_parameter_continuation(pending_keyword, stripped):
                keyword_line = keyword_line.rstrip() + stripped
                if keyword_line.rstrip().endswith(","):
                    continue
                # The continuation ended: re-parse the joined line so the parameters it
                # carried are on the card. Falling through without this dropped them.
                pending_keyword, pending_params = _parse_keyword_line(keyword_line)
                continuing = False
                continue
            pending_keyword, pending_params = _parse_keyword_line(keyword_line)
            continuing = False
            # fall through: this line is the card's first data/comment/keyword line

        if not stripped:
            continue

        if stripped.startswith("*"):
            if pending_keyword is not None:
                yield build()
                pending_keyword = None
                data = []
            if stripped.startswith(_COMMENT_PREFIX):
                if comments_lineno != lineno - 1:
                    comments.clear()
                comments.append(stripped[len(_COMMENT_PREFIX) :].strip())
                comments_lineno = lineno
                continue
            pending_comments = tuple(comments) if comments_lineno == lineno - 1 else ()
            comments.clear()
            comments_lineno = -1
            pending_lineno = lineno
            keyword_line = stripped
            pending_keyword, pending_params = _parse_keyword_line(keyword_line)
            continuing = keyword_line.rstrip().endswith(",")
            continue

        if pending_keyword is not None:
            data.append(stripped)

    if continuing:
        pending_keyword, pending_params = _parse_keyword_line(keyword_line)
    if pending_keyword is not None:
        yield build()


def stream_file(path, encoding: str = "utf-8") -> Iterator[Card]:
    """Stream the cards of a deck straight off disk, never holding it as one string."""
    with open(path, "r", encoding=encoding) as fh:
        yield from stream_cards(fh)


_TOKEN_CACHE: dict[int, tuple[str, tuple[Card, ...]]] = {}


def _tokenize_cached(bulk_str: str) -> tuple[Card, ...]:
    """Tokenize ``bulk_str``, reusing the result across the reader's many passes.

    The reader calls a dozen ``get_*_from_bulk`` functions on the same few strings (the whole
    deck, one part's body, the assembly's tail), so caching turns N passes into one. Keyed by
    ``id()`` with the string itself held alongside: that keeps the key cheap for multi-megabyte
    decks, and holding the reference means the id cannot be recycled under us.
    """
    key = id(bulk_str)
    hit = _TOKEN_CACHE.get(key)
    if hit is not None and hit[0] is bulk_str:
        return hit[1]
    cards = tokenize(bulk_str)
    if len(_TOKEN_CACHE) > 16:
        _TOKEN_CACHE.clear()
    _TOKEN_CACHE[key] = (bulk_str, cards)
    return cards


def iter_cards(bulk_str: str, *keywords: str) -> Iterator[Card]:
    """Cards whose keyword is one of ``keywords`` (all of them when none is given)."""
    cards = _tokenize_cached(bulk_str)
    if not keywords:
        return iter(cards)
    wanted = {normalize(k) for k in keywords}
    return (card for card in cards if card.keyword in wanted)


def iter_blocks(bulk_str: str, keyword: str, end: str) -> Iterator[tuple[Card, str]]:
    """``(opening card, the text between it and its ``end`` keyword)``.

    For the container keywords -- ``*Part``/``*End Part``, ``*Instance``/``*End Instance`` --
    whose body is another whole deck and is handed on to the readers as a string. Nesting is
    counted, so an inner block never closes an outer one.
    """
    open_kw, end_kw = normalize(keyword), normalize(end)
    depth = 0
    body_start = 0
    start_card: Card | None = None
    for card in _tokenize_cached(bulk_str):
        if card.keyword == open_kw:
            if depth == 0:
                start_card = card
                body_start = card._span[0]
            depth += 1
        elif card.keyword == end_kw and depth:
            depth -= 1
            if depth == 0 and start_card is not None:
                yield start_card, bulk_str[body_start : card.start]
                start_card = None


_COMMENT_PROP: dict[tuple[str, ...], re.Pattern] = {}


def comment_property(card: Card, *names: str) -> dict[str, str]:
    """Named properties out of a card's own comment lines.

    ``** Name: BC-1  Type: Displacement/Rotation`` yields
    ``{"Name": "BC-1", "Type": "Displacement/Rotation"}``. Only this card's comments are read,
    so a value can never run past the line it was written on.
    """
    out: dict[str, str] = {}
    if not card.comments:
        return out
    key = tuple(names)
    pattern = _COMMENT_PROP.get(key)
    if pattern is None:
        alternatives = "|".join(re.escape(n) for n in names)
        pattern = re.compile(rf"({alternatives})\s*:\s*(.*?)(?=\s+(?:{alternatives})\s*:|$)", re.IGNORECASE)
        _COMMENT_PROP[key] = pattern
    for comment in card.comments:
        for match in pattern.finditer(comment):
            name = next(n for n in names if n.lower() == match.group(1).lower())
            value = match.group(2).strip()
            if value:
                out.setdefault(name, value)
    return out
