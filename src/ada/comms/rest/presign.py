"""Response-header overrides on a presigned S3 GET.

A browser that follows a presigned GET from another origin ignores the anchor's ``download``
attribute, so the saved file's name has to come from the object store's own response: the
``response-content-disposition`` query parameter of S3's GetObject. The same family of
parameters (``response-content-type``, ``response-content-encoding``) lets the URL fix what the
browser does with a gzip-at-rest object. They are part of the signed query string, so they
cannot be appended to a URL after signing.

obstore's ``sign`` takes no query parameters, so :func:`resign_with_params` re-signs the URL
obstore minted (AWS Signature Version 4, query form) with the extra parameters added. Everything
except the signature is taken from obstore's URL: host, path encoding, credential scope, date,
expiry and any session token; only the secret key is needed on top. Re-signing obstore's URL
with no extra parameters reproduces obstore's signature exactly, which the tests pin.
"""

from __future__ import annotations

import hashlib
import hmac
import posixpath
import re
import unicodedata
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

_ALGORITHM = "AWS4-HMAC-SHA256"

# Longest name we put in a Content-Disposition. Filesystems cap a name at 255 bytes; the
# percent-encoded filename* of a long non-ASCII name is three times its UTF-8 length.
_MAX_FILENAME_CHARS = 180

# Control characters, and the characters Windows refuses in a file name (browsers replace
# them anyway, but a name that is safe as-is reads the same on every platform).
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f"*:<>?|\\/]')


class PresignOverridesUnavailable(RuntimeError):
    """The store can presign, but not with response-header overrides (no secret key to sign
    them with). Callers that need the overrides fall back to streaming through the API."""


def sanitize_download_filename(name: str | None, fallback: str = "download") -> str:
    """A file name safe to put in a Content-Disposition header.

    Drops any directory part (either separator), control characters and characters a file
    system refuses, trims leading/trailing dots and spaces (a name of only dots would be a path),
    and caps the length while keeping the extension. An empty result becomes ``fallback``
    (itself sanitised, then ``"download"``)."""

    def _clean(raw: str) -> str:
        raw = unicodedata.normalize("NFC", raw)
        # Last path component under either separator: a name is never a path.
        raw = raw.replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = _UNSAFE_CHARS.sub("_", raw).strip(" .")
        if len(cleaned) > _MAX_FILENAME_CHARS:
            stem, ext = posixpath.splitext(cleaned)
            ext = ext if len(ext) <= 16 else ""
            cleaned = stem[: _MAX_FILENAME_CHARS - len(ext)].rstrip(" .") + ext
        return cleaned

    for candidate in (name, fallback):
        if candidate:
            cleaned = _clean(str(candidate))
            if cleaned and cleaned.strip("_"):
                return cleaned
    return "download"


def content_disposition(filename: str, *, attachment: bool = True) -> str:
    """``attachment; filename="..."; filename*=UTF-8''...`` (RFC 6266 / RFC 5987).

    ``filename`` is the ASCII fallback for old clients: non-ASCII characters become ``_``,
    and the quoted-string specials are already gone (``sanitize_download_filename`` removes
    ``"`` and ``\\``). ``filename*`` carries the exact UTF-8 name. Pass a sanitised name."""
    disposition = "attachment" if attachment else "inline"
    ascii_name = "".join(ch if 0x20 <= ord(ch) < 0x7F else "_" for ch in filename)
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    encoded = quote(filename, safe="!#$&+-.^_`|~")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _uri_encode(value: str) -> str:
    """SigV4's URI-encode for query keys and values: everything but the unreserved set."""
    return quote(value, safe="-_.~")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def resign_with_params(url: str, secret_access_key: str, params: Mapping[str, str]) -> str:
    """Re-sign an obstore-presigned S3 URL with ``params`` added to its query string.

    The URL must be one obstore's ``sign`` returned for this store (SigV4 query
    authentication, ``host`` the only signed header, unsigned payload). Its existing
    ``X-Amz-*`` parameters are kept as they are; ``X-Amz-Signature`` is recomputed."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "X-Amz-Signature"]
    fields = dict(query)
    if fields.get("X-Amz-Algorithm") != _ALGORITHM or fields.get("X-Amz-SignedHeaders") != "host":
        raise ValueError("not a SigV4 query-signed URL with only the host header signed")
    amz_date = fields["X-Amz-Date"]
    # <access key>/<yyyymmdd>/<region>/<service>/aws4_request — the access key may itself
    # contain no '/', so the scope is the last four fields.
    scope = fields["X-Amz-Credential"].split("/")[-4:]
    date, region, service, terminal = scope
    query.extend((str(k), str(v)) for k, v in params.items())

    canonical_query = "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query))
    canonical_request = "\n".join(
        [
            "GET",
            parts.path or "/",
            canonical_query,
            f"host:{parts.netloc}",
            "",
            "host",
            "UNSIGNED-PAYLOAD",
        ]
    )
    string_to_sign = "\n".join(
        [
            _ALGORITHM,
            amz_date,
            "/".join(scope),
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    key = _hmac(("AWS4" + secret_access_key).encode("utf-8"), date)
    for part in (region, service, terminal):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlunsplit((parts.scheme, parts.netloc, parts.path, f"{canonical_query}&X-Amz-Signature={signature}", ""))
