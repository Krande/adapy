"""Display names for asset providers -- what the UI SHOWS for a provider id, never the id itself.

A provider id is an identity: it is written into manifests and storage keys, saved sets and URLs,
and it never changes. What a person reads beside a row or on a button is a separate question, with
three answers in order:

1. An admin's alias for the provider IN ONE SCOPE -- the ``public.assets.provider_labels`` setting::

       {"<scope wire form>": {"<provider id>": "<display name>"}}

   keyed by the same scope strings as ``public.assets.scope_collections`` (``shared``,
   ``project:<uuid>``). Per scope because what a provider's output is called is a project's
   vocabulary, not the deployment's.
2. The provider's own label: ``asset_provider_label`` on a backend plugin spec that declares
   ``asset_provider_id`` (or a ``{provider id: label}`` mapping, for a spec naming several), or
   ``register_asset_provider(..., label=...)``.
3. Nothing. An absent label stays absent and the UI shows the id: a made-up name is worse than the
   honest one.

Pure, so the route and the tests share it.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

__all__ = [
    "MAX_PROVIDER_LABEL_LENGTH",
    "PROVIDER_LABELS_SETTING",
    "ProviderLabelError",
    "clean_provider_label",
    "declared_provider_labels",
    "normalise_provider_aliases",
    "parse_provider_aliases",
    "resolve_provider_labels",
]

#: The public setting holding the per-scope admin aliases. ``public.`` so every viewer may read it
#: (writes stay admin-only, like every setting).
PROVIDER_LABELS_SETTING = "public.assets.provider_labels"

#: Long enough for a descriptive name, short enough to fit a chip or a button.
MAX_PROVIDER_LABEL_LENGTH = 64


class ProviderLabelError(ValueError):
    """A stored or submitted display name that is not one."""


def clean_provider_label(value: Any) -> str:
    """``value`` as a display name: a string, trimmed, non-empty, at most the maximum length.

    Inner whitespace runs are collapsed to one space -- a name with a tab or a newline in it would
    break every one-line place it is shown.
    """
    if not isinstance(value, str):
        raise ProviderLabelError(f"a display name must be a string, not {type(value).__name__}")
    label = " ".join(value.split())
    if not label:
        raise ProviderLabelError("a display name may not be empty -- remove the entry to fall back")
    if len(label) > MAX_PROVIDER_LABEL_LENGTH:
        raise ProviderLabelError(
            f"display name {label[:20]!r}... is {len(label)} characters; at most {MAX_PROVIDER_LABEL_LENGTH}"
        )
    return label


def _decode(raw: Any) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        return json.loads(raw)
    return raw


def parse_provider_aliases(raw: Any) -> dict[str, dict[str, str]]:
    """The stored setting, read LENIENTLY: missing or malformed is an empty map, and an entry that
    is not a valid display name is dropped (that provider falls back to its own label). A hand-edited
    row must not take the Sources tab down."""
    try:
        parsed = _decode(raw)
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for scope, row in parsed.items():
        if not isinstance(scope, str) or not isinstance(row, dict):
            continue
        kept: dict[str, str] = {}
        for provider, label in row.items():
            if not isinstance(provider, str) or not provider:
                continue
            try:
                kept[provider] = clean_provider_label(label)
            except ProviderLabelError:
                continue
        if kept:
            out[scope] = kept
    return out


def normalise_provider_aliases(raw: Any) -> str:
    """The submitted setting, read STRICTLY, as the JSON text to store.

    Every alias must be a valid display name -- a write that would be silently dropped on read is
    refused instead, so the admin sees why. Scopes left with no aliases are removed, so clearing the
    last one leaves the setting as if nobody had touched it.
    """
    try:
        parsed = _decode(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderLabelError(f"not JSON: {exc}") from exc
    if parsed is None:
        parsed = {}
    if not isinstance(parsed, dict):
        raise ProviderLabelError("expected an object of scope -> {provider id: display name}")
    out: dict[str, dict[str, str]] = {}
    for scope, row in parsed.items():
        if not isinstance(scope, str) or not scope.strip():
            raise ProviderLabelError(f"invalid scope key {scope!r}")
        if not isinstance(row, dict):
            raise ProviderLabelError(f"scope {scope!r}: expected an object of provider id -> display name")
        kept: dict[str, str] = {}
        for provider, label in row.items():
            if not isinstance(provider, str) or not provider.strip():
                raise ProviderLabelError(f"scope {scope!r}: invalid provider id {provider!r}")
            try:
                kept[provider] = clean_provider_label(label)
            except ProviderLabelError as exc:
                raise ProviderLabelError(f"scope {scope!r}, provider {provider!r}: {exc}") from exc
        if kept:
            out[scope] = kept
    return json.dumps(out, sort_keys=True)


def declared_provider_labels(specs: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Provider id -> label, as backend plugin specs declare it.

    ``asset_provider_label`` is either a string, naming the spec's own ``asset_provider_id``, or a
    ``{provider id: label}`` mapping for a spec that speaks for several. The first valid declaration
    of an id wins: several workers advertising one plugin carry the same one.
    """
    out: dict[str, str] = {}

    def _take(provider: Any, label: Any) -> None:
        if not isinstance(provider, str) or not provider or provider in out:
            return
        try:
            out[provider] = clean_provider_label(label)
        except ProviderLabelError:
            return

    for spec in specs:
        if not isinstance(spec, Mapping):
            continue
        declared = spec.get("asset_provider_label")
        if isinstance(declared, Mapping):
            for provider, label in declared.items():
                _take(provider, label)
        elif declared is not None:
            _take(spec.get("asset_provider_id"), declared)
    return out


def resolve_provider_labels(
    scope_key: str | None,
    aliases: Mapping[str, Mapping[str, str]],
    *provider_labels: Mapping[str, str],
) -> dict[str, str]:
    """The labels one scope's viewer shows: the scope's admin aliases over each provider-declared
    source in the order given. A provider no source names is absent."""
    out: dict[str, str] = {}
    for source in reversed(provider_labels):
        out.update(source)
    if scope_key is not None:
        out.update(aliases.get(scope_key, {}))
    return out
