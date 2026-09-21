"""The asset key grammar: ``assets/<collection>/<subject>/<revision>/<file>``.

Five segments, always. Arity is the checksum -- a key with any other segment count is not an
asset key, and that check comes FIRST, before any segment is inspected. That is what keeps
``assets/_staging/<id>/<file>`` (four segments) from ever parsing as an asset no matter what the
staging id looks like.

Two rules are worth stating because they are easy to get subtly wrong later:

* **A subject is a node id, and core never interprets its shape.** The browser tells a
  collection-level subject from a node-level one by EQUALITY with the collection segment, never
  by a regex on the subject. Core cannot know any provider's id shape, so any shape test would be
  a guess that happens to hold for today's providers.
* **The alphabet is URL-path-safe on purpose.** A subject rides in a route path unescaped
  (``/assets/tree/{provider}/{collection}``, ``/assets/delivery/.../{node}``), so the segment
  alphabet is exactly what survives that without quoting. ``$`` is admitted because an IFC
  ``GlobalId`` uses the base64 alphabet ``0-9A-Za-z_$`` and we want a GlobalId to be a valid
  subject VERBATIM, not hex-expanded; ``$`` is an RFC 3986 sub-delimiter and legal in a path
  segment. A leading ``_`` is reserved for core so ``_staging`` and any future core-owned segment
  stay collision-free without banning words from real collections.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

__all__ = [
    "ASSET_PREFIX",
    "ASSET_KEY_SEGMENTS",
    "STAGING_SEGMENT",
    "AssetKey",
    "AssetKeyError",
    "asset_key",
    "is_valid_segment",
    "parse_asset_key",
    "revision_from_instant",
    "staging_prefix",
]

ASSET_PREFIX = "assets"
ASSET_KEY_SEGMENTS = 5  # assets/<collection>/<subject>/<revision>/<file>
STAGING_SEGMENT = "_staging"  # assets/_staging/<id>/<file> -- 4 segments, never an asset key

# Every segment. 128 chars is generous for a slug and still leaves a five-segment key far inside
# any object-store key limit. The leading char is deliberately narrower than the rest: no '_'
# (reserved for core), no '.' (so '.' and '..' cannot appear), no '-' (so a segment cannot be read
# as a CLI flag when a key is pasted into one).
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._$-]{0,127}$")

# UTC compact instant, e.g. 20260921T143001Z. Chosen so LEXICAL order is CHRONOLOGICAL order: the
# newest revision of a subject is max(revisions) with no parsing, which is what the index fold and
# every "latest" lookup rely on. A local-time or offset-bearing stamp would silently break that.
_REVISION_RE = re.compile(r"^\d{8}T\d{6}Z$")

_FILENAME_RE = _SEGMENT_RE  # a filename is a segment: same alphabet, same reservations


class AssetKeyError(ValueError):
    """A key or segment that does not meet the grammar.

    Always raised with a message that names the offending value AND the fix, because these
    surface to a provider author who cannot see this module.
    """


def is_valid_segment(value: str) -> bool:
    return bool(_SEGMENT_RE.match(value))


def _check_segment(value: str, what: str) -> str:
    if not isinstance(value, str) or not _SEGMENT_RE.match(value):
        raise AssetKeyError(
            f"invalid {what} {value!r}: must match {_SEGMENT_RE.pattern} "
            f"(start with a letter or digit; then letters, digits, '.', '_', '$' or '-'; "
            f"max 128 chars; no '/', no leading '_' -- that is reserved for core)"
        )
    return value


def _check_revision(value: str) -> str:
    if not isinstance(value, str) or not _REVISION_RE.match(value):
        raise AssetKeyError(
            f"invalid revision {value!r}: must be a compact UTC instant like '20260921T143001Z' "
            f"(use revision_from_instant() -- lexical order has to equal chronological order, "
            f"so a local-time or offset-bearing stamp is refused)"
        )
    return value


@dataclass(frozen=True)
class AssetKey:
    collection: str
    subject: str
    revision: str
    filename: str

    @property
    def is_collection_level(self) -> bool:
        """True when this key holds the COLLECTION's own artefacts rather than a node's.

        Equality with the collection segment, never a regex on the subject -- core cannot know a
        provider's id shape, so any shape test would be a guess.
        """
        return self.subject == self.collection

    @property
    def prefix(self) -> str:
        """The revision prefix this key lives under -- what an unpublish deletes."""
        return f"{ASSET_PREFIX}/{self.collection}/{self.subject}/{self.revision}/"

    def with_filename(self, filename: str) -> "AssetKey":
        return AssetKey(self.collection, self.subject, self.revision, _check_segment(filename, "filename"))

    def __str__(self) -> str:
        return asset_key(self.collection, self.subject, self.revision, self.filename)


def parse_asset_key(key: str) -> AssetKey:
    """Parse ``assets/<collection>/<subject>/<revision>/<file>``.

    Arity first: a key with any other segment count is refused before a segment is looked at.
    """
    if not isinstance(key, str) or not key:
        raise AssetKeyError(f"invalid asset key {key!r}: expected a non-empty string")
    parts = key.split("/")
    if len(parts) != ASSET_KEY_SEGMENTS:
        raise AssetKeyError(
            f"invalid asset key {key!r}: expected exactly {ASSET_KEY_SEGMENTS} segments "
            f"('{ASSET_PREFIX}/<collection>/<subject>/<revision>/<file>'), got {len(parts)}. "
            f"A {STAGING_SEGMENT} key has 4 segments and is never an asset key."
        )
    prefix, collection, subject, revision, filename = parts
    if prefix != ASSET_PREFIX:
        raise AssetKeyError(f"invalid asset key {key!r}: must start with '{ASSET_PREFIX}/', got {prefix!r}")
    return AssetKey(
        collection=_check_segment(collection, "collection"),
        subject=_check_segment(subject, "subject"),
        revision=_check_revision(revision),
        filename=_check_segment(filename, "filename"),
    )


def asset_key(collection: str, subject: str, revision: str, filename: str) -> str:
    """Compose a key, validating every segment. The only sanctioned way to build one."""
    return "/".join(
        (
            ASSET_PREFIX,
            _check_segment(collection, "collection"),
            _check_segment(subject, "subject"),
            _check_revision(revision),
            _check_segment(filename, "filename"),
        )
    )


def staging_prefix(staging_id: str) -> str:
    """``assets/_staging/<id>/`` -- four segments once a filename is appended, so a staged blob can
    never be mistaken for a published one by arity alone."""
    if not isinstance(staging_id, str) or not staging_id or "/" in staging_id:
        raise AssetKeyError(f"invalid staging id {staging_id!r}: expected a non-empty string with no '/'")
    return f"{ASSET_PREFIX}/{STAGING_SEGMENT}/{staging_id}/"


def revision_from_instant(instant: str | _dt.datetime) -> str:
    """Normalise an instant to UTC, then to the compact revision form.

    A naive datetime (or a naive ISO string) is REFUSED rather than assumed to be UTC: guessing
    would put a revision in the wrong chronological position, which is unrecoverable once other
    revisions exist around it.
    """
    if isinstance(instant, _dt.datetime):
        dt = instant
    else:
        text = str(instant).strip()
        # fromisoformat handles 'Z' only from 3.11; normalise it for the 3.10 floor.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            dt = _dt.datetime.fromisoformat(text)
        except ValueError as exc:
            raise AssetKeyError(
                f"invalid instant {instant!r}: expected an ISO-8601 instant with a UTC offset, "
                f"e.g. '2026-09-21T14:30:01Z' or '2026-09-21T16:30:01+02:00' ({exc})"
            ) from exc
    if dt.tzinfo is None:
        raise AssetKeyError(
            f"invalid instant {instant!r}: no timezone. Pass an aware datetime or an offset-bearing "
            f"string -- a naive instant would be silently placed in the wrong chronological order."
        )
    return dt.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
