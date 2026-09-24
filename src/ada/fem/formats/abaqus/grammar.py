"""The Abaqus input-file grammar, for reading AND writing.

One definition of the format, used in both directions:

* **reading** -- :func:`tokenize` / :func:`iter_keywords` split a deck into
  :class:`KeywordBlock` s (see :mod:`.read.lexer`), and :func:`validate` checks a block against
  the keyword table (:mod:`.read.keywords`);
* **writing** -- :func:`render_keyword` is the inverse: a keyword, its parameters, its data
  lines and the comments above it, as the text :func:`tokenize` reads back into the same block.

Writing through the same grammar is what keeps the two from drifting. The reader decides what a
keyword line means (order-free ``NAME=VALUE`` parameters, flags, quoting, continuation lines),
and the writer can then only produce lines that mean what it intended: a name with a comma or a
space is quoted, because Abaqus ignores blanks on a keyword line and splits on commas; a line
past the 256-character limit is continued the way the reader continues it. The keyword table
covers the output too -- a test renders every construct adapy writes and validates each block
with the same :func:`validate` the reader uses.

Spelling is the caller's. Abaqus is case-insensitive, so ``*Shell Section, elset=A`` and
``*SHELL SECTION, ELSET=A`` are one block to the reader; :func:`render_keyword` writes exactly
the spelling it is given, so a writer's output does not change by being routed through here.
"""

from __future__ import annotations

from typing import Iterable, Sequence, Union

from .read.keywords import KEYWORDS, NO_MODEL_EFFECT, SOLVER_CONTROLS, lookup, validate
from .read.lexer import (
    KeywordBlock,
    Params,
    iter_enclosed,
    iter_keywords,
    mark_read,
    normalize,
    stream_file,
    stream_keywords,
    tokenize,
    track_reads,
)

__all__ = [
    "KEYWORDS",
    "NO_MODEL_EFFECT",
    "SOLVER_CONTROLS",
    "KeywordBlock",
    "MAX_LINE_LENGTH",
    "Params",
    "format_value",
    "iter_enclosed",
    "iter_keywords",
    "lookup",
    "mark_read",
    "normalize",
    "render_keyword",
    "stream_file",
    "stream_keywords",
    "tokenize",
    "track_reads",
    "validate",
]

#: Abaqus reads at most 256 characters of a keyword or data line.
MAX_LINE_LENGTH = 256

#: A parameter: ``(name, value)``, with ``value=None`` for a flag (``, generate``).
Param = tuple[str, Union[str, int, float, bool, None]]
#: A data line: either already-formatted text, or a row of values joined with ``", "``.
DataLine = Union[str, Sequence[Union[str, int, float]]]


def format_value(value) -> str:
    """One parameter value as it must appear on a keyword line.

    Quoted when Abaqus would otherwise misread it: it splits parameters on commas and ignores
    blanks on a keyword line, so a name like ``Beam 1`` or ``a,b`` must be quoted, and is.
    Anything already quoted is left alone.
    """
    if isinstance(value, bool):
        text = "YES" if value else "NO"
    else:
        text = str(value)
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text
    if any(ch in text for ch in ', \t"') or text != text.strip():
        return '"' + text.replace('"', "") + '"'
    return text


def _keyword_line_parts(keyword: str, params: Iterable[Param]) -> list[str]:
    parts = [f"*{keyword}"]
    for name, value in params:
        parts.append(name if value is None else f"{name}={format_value(value)}")
    return parts


def _join_keyword_line(parts: list[str], sep: str) -> str:
    """The keyword line, continued onto further lines if it would pass the length limit.

    Continuation is a trailing comma with the next parameter starting the next line -- the
    form the reader recognises as a continuation (the first token must be a parameter the
    keyword accepts), not as the block's first data line.
    """
    line = parts[0]
    lines = []
    for part in parts[1:]:
        candidate = line + sep + part
        if len(candidate) > MAX_LINE_LENGTH and line != parts[0]:
            lines.append(line + ",")
            line = part
        else:
            line = candidate
    lines.append(line)
    return "\n".join(lines)


def render_keyword(
    keyword: str,
    params: Iterable[Param] = (),
    data: Iterable[DataLine] = (),
    comments: Iterable[str] = (),
    *,
    sep: str = ", ",
) -> str:
    """One keyword block as deck text, newline-terminated.

    ``keyword`` and parameter names are written exactly as given. A parameter whose value is
    ``None`` is a flag. ``data`` lines are written as given when they are strings, or joined
    with ``", "`` when they are rows of values. ``comments`` go above the keyword line, each as
    ``** <text>`` -- the reader takes a block's name from exactly those lines. ``sep`` is the
    separator between keyword and parameters; ``", "`` unless a writer's established output
    uses another (``*INCLUDE,INPUT=``).
    """
    out = [f"** {c}" if c else "**" for c in comments]
    out.append(_join_keyword_line(_keyword_line_parts(keyword, params), sep))
    for row in data:
        out.append(row if isinstance(row, str) else ", ".join(str(v) for v in row))
    return "\n".join(out) + "\n"
