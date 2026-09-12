"""The ``(from_ext, to_ext)`` → handler table and the ``@converter`` decorator that populates it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    pass


# Progress contract: stage name (str), fraction (0..1).
ProgressFn = Callable[[str, float], None]


class UnsupportedFormat(ValueError):
    pass


# ── Converter registry ─────────────────────────────────────────────
#
# Each ``(from_ext, to_ext)`` pair lands one entry here at module
# load. Adding a new pair is one line:
#
#     @converter(".step", ".stl")
#     def _step_to_stl(src, on_progress, **_): ...
#
# Two things consume the registry:
#
#  * :func:`convert` dispatches the worker side — no more if-elif
#    chain on the (ext, target) pair.
#  * :meth:`ConverterRegistry.matrix` is what the worker publishes
#    to NATS KV (``conversions: [{from, to: [...]}, ...]``). The
#    API merges every live worker's matrix and surfaces it through
#    ``/api/config``; the SPA's /convert page uses it to populate
#    the target dropdown per-source-extension. New pairs light up
#    in the UI as soon as the registering worker registers.
#
# Both keys carry the leading dot ("." prefix) to stay symmetric
# with the rest of the module (`_ext()` returns suffix with dot).
# The target side is also recorded with a leading dot internally;
# the matrix JSON strips the dot on serialization so the wire
# format matches what /api/config already publishes for
# ``source_exts``.


ConverterFn = Callable[..., bytes]


# Per-pair option schema. Each option is described by a small dict so
# the SPA can render the right widget without baking option names into
# its source. Same shape used both in :class:`ConverterRegistry` and
# on the wire via ``/api/config["conversionMatrix"]``.
#
#   {
#       "name": "mesh_only",        # passed back through the convert
#                                   # body's ``conversion_options`` map
#       "type": "bool",             # bool | string | int | enum
#       "default": False,
#       "description": "...",
#       "enum": [...]               # only when type == "enum"
#   }


class ConverterRegistry:
    """Module-level (from_ext, to_ext) → handler table.

    Population happens at import time via the :func:`converter`
    decorator (or :meth:`register` for programmatic registrations
    that fan one handler across multiple source extensions). The
    table is intentionally a plain dict — the worker's job loop
    looks up once per job, so atomicity is not a concern.

    Each registered pair carries an optional option schema
    (``options_for(from_ext, to_ext)``) describing per-job knobs
    the SPA can surface. The schema is the source of truth — the
    API allowlist and the worker's env-mapping both derive from
    it instead of repeating hardcoded enum lists.
    """

    _entries: dict[tuple[str, str], ConverterFn] = {}
    _options: dict[tuple[str, str], list[dict]] = {}

    @staticmethod
    def _norm_key(from_ext: str, to_ext: str) -> tuple[str, str]:
        """Canonical registry key: source extension WITH leading dot
        (matches ``_ext()`` output), target extension WITHOUT
        (matches the ``target_format`` convention used everywhere
        else — ``"glb"`` not ``".glb"``). Caller can pass either
        form on either side; we normalise both."""
        f = from_ext.lower()
        if not f.startswith("."):
            f = "." + f
        t = to_ext.lower().lstrip(".")
        return (f, t)

    @classmethod
    def register(
        cls,
        from_ext: str,
        to_ext: str,
        fn: ConverterFn,
        *,
        options: list[dict] | None = None,
    ) -> None:
        key = cls._norm_key(from_ext, to_ext)
        cls._entries[key] = fn
        if options:
            cls._options[key] = list(options)

    @classmethod
    def lookup(cls, from_ext: str, to_ext: str) -> ConverterFn | None:
        return cls._entries.get(cls._norm_key(from_ext, to_ext))

    @classmethod
    def options_for(cls, from_ext: str, to_ext: str) -> list[dict]:
        """Option schema for one (from, to) pair. Empty list when
        the pair has no per-job knobs. Returned list is a shallow
        copy so callers can mutate without poisoning the registry."""
        return list(cls._options.get(cls._norm_key(from_ext, to_ext), ()))

    @classmethod
    def all_options(cls) -> set[str]:
        """Union of option names across every registered pair.

        Used by the API's per-job ``conversion_options`` validator
        (replaces the hardcoded allowlist) — any name that no
        registered converter declares gets dropped from the body.
        """
        out: set[str] = set()
        for opts in cls._options.values():
            for opt in opts:
                name = opt.get("name")
                if isinstance(name, str):
                    out.add(name)
        return out

    @classmethod
    def all_sources(cls) -> frozenset[str]:
        return frozenset(f for (f, _) in cls._entries)

    @classmethod
    def all_targets(cls) -> frozenset[str]:
        """Target extensions WITHOUT the leading dot — matches the
        rest of the codebase's ``target_format`` convention
        (``"glb"`` not ``".glb"``). Stored that way already; this
        is just a projection over the key space.
        """
        return frozenset(t for (_, t) in cls._entries)

    @classmethod
    def targets_for(cls, from_ext: str) -> list[str]:
        """Sorted list of target_format values viable for the given
        source extension. Targets are returned without the leading
        dot (``["glb", "ifc"]`` not ``[".glb", ".ifc"]``).
        """
        f = from_ext.lower()
        if not f.startswith("."):
            f = "." + f
        return sorted({t for (frm, t) in cls._entries if frm == f})

    @classmethod
    def matrix(cls) -> list[dict]:
        """JSON-serialisable rollup. Wire shape::

            [{
                "from": ".step",
                "to": ["glb", "ifc", "stl"],
                "options": {
                    "glb": [{"name": ..., "type": ..., ...}, ...],
                    "ifc": [...],
                    "stl": [...],
                },
             }, ...]

        ``options`` is always present; pairs with no per-job knobs
        get an empty list, so a frontend can render unconditionally
        without testing for the key.
        """
        by_from: dict[str, set[str]] = {}
        for f, t in cls._entries:
            by_from.setdefault(f, set()).add(t)
        rows: list[dict] = []
        for f in sorted(by_from):
            targets = sorted(by_from[f])
            opts: dict[str, list[dict]] = {}
            for t in targets:
                opts[t] = list(cls._options.get((f, t), ()))
            rows.append({"from": f, "to": targets, "options": opts})
        return rows


def converter(
    *args,
    accepts: list[str] | None = None,
    exports: list[str] | None = None,
    exclude_identity: bool = True,
    options: list[dict] | None = None,
):
    """Decorator that registers ``fn`` for one or more (from, to)
    conversion pairs.

    Two equivalent invocation styles:

    * **Single pair (positional, legacy):**

      .. code-block:: python

          @converter(".step", ".stl")
          def step_to_stl(src, on_progress, **_): ...

    * **Multi-format with options (new):**

      .. code-block:: python

          @converter(
              accepts=[".inp", ".fem", ".med"],
              exports=[".inp", ".fem", ".med"],
              exclude_identity=True,
              options=[
                  {"name": "mesh_only", "type": "bool", "default": False,
                   "description": "Skip BCs / loads / sections; mesh only."},
              ],
          )
          def fea_to_fea(src, on_progress, *, source_ext, target_ext,
                         mesh_only=False, **_): ...

    Handler signature in the multi-format form receives ``source_ext``
    and ``target_ext`` kwargs so one function body can serve every
    cell in the cartesian product. ``exclude_identity=True`` drops
    same-from-same-to pairs (``.inp → .inp`` etc.) — flip to
    ``False`` if a self-conversion is actually meaningful (e.g.
    re-export through a parser for normalisation).

    ``options`` is a list of schema dicts (see the module-level
    comment on the wire shape) shared across every registered cell
    — declare per-target options by splitting the registrations.
    """

    # Legacy positional form: @converter(".step", ".stl")
    if args:
        if accepts is not None or exports is not None:
            raise TypeError("@converter: pass either positional (from, to) OR " "accepts/exports, not both")
        if len(args) != 2:
            raise TypeError("@converter(positional) expects exactly (from_ext, to_ext)")
        accepts = [args[0]]
        exports = [args[1]]

    if not accepts or not exports:
        raise TypeError("@converter: need at least one accepts and one exports entry")

    # Source extensions are stored WITH a leading dot to match
    # ``_ext()`` output; target extensions are stored WITHOUT
    # (mirrors the ``target_format`` convention everywhere else in
    # the module — ``"glb"``, not ``".glb"``). Normalise the inputs
    # here so caller can pass either form.
    accepts_norm = [("." + a.lstrip(".").lower()) for a in accepts]
    exports_norm = [e.lstrip(".").lower() for e in exports]

    def deco(fn: ConverterFn) -> ConverterFn:
        for src_ext in accepts_norm:
            # ``src_ext`` carries the leading dot (``.inp``);
            # ``tgt_ext`` does not (``fem``). The identity check
            # strips both to the bare extension so ``.inp`` and
            # ``inp`` count as the same format.
            src_bare = src_ext.lstrip(".")
            for tgt_ext in exports_norm:
                if exclude_identity and src_bare == tgt_ext:
                    continue

                # Wrap the bare handler so the registry's uniform
                # ``(src, on_progress, **kw)`` shape gets the
                # source/target pair injected — the user-side body
                # then reads them off kwargs (or named params).
                # Bind src_ext / tgt_ext via default args so each
                # closure captures its own values instead of the
                # loop's last iteration. The handler receives the
                # canonical ``.<ext>`` form for both since user code
                # typically does suffix-based dispatch.
                def _adapter(src, on_progress, *, _s=src_ext, _t=tgt_ext, **kw):
                    return fn(
                        src,
                        on_progress,
                        source_ext=_s,
                        target_ext=f".{_t}",
                        **kw,
                    )

                ConverterRegistry.register(
                    src_ext,
                    tgt_ext,
                    _adapter,
                    options=options,
                )
        return fn

    return deco
