"""The deployment's default procedural engine: which engine a new procedural model gets when
nobody names one.

adapy's own default is the built-in ``adapy-default``. A deployment that ships with another
engine (a tool's capability worker, say) names it once:

* ``ADA_DEFAULT_PROCEDURAL_ENGINE`` -- the deployment default, by slug (``my-engine``) or by the
  id the engine list hands out (``worker:my-engine``, ``local:my-engine``, a registered row's
  id). Read into :attr:`ada.comms.rest.config.Settings.default_procedural_engine`.
* the ``procedural_default_engine`` app setting (Admin > Conversion settings) -- overrides the
  env when set, so an admin can change it without a redeploy. Empty means "the env's".

Whatever is configured is resolved against the engines this scope actually offers (built-ins,
the scope's registered rows, live workers' advertisements, a local server's own registrations;
see ``routes/procedural_engines.py``). A configured engine that is not offered -- a typo, a
worker that is down -- resolves to the built-in with a logged warning and a ``note`` the admin
panel shows: a create never fails, or names an engine nothing can compile, because of it.

Only creation reads this. A stored model keeps the engine it was recorded with: nothing here
rewrites an existing row or document.
"""

from __future__ import annotations

from ada.config import logger

#: The deployment env (see :func:`ada.comms.rest.config.load_settings`).
DEFAULT_ENGINE_ENV = "ADA_DEFAULT_PROCEDURAL_ENGINE"
#: The admin-editable override (``app_settings``); empty = the env's.
DEFAULT_ENGINE_SETTING = "procedural_default_engine"
#: adapy's own default engine, the fallback for anything unresolvable.
BUILTIN_DEFAULT_SLUG = "adapy-default"
BUILTIN_DEFAULT_ID = "builtin:adapy-default"

# Warn once per unresolvable value, not on every engine listing.
_warned: set[str] = set()


def _matches(engine: dict, wanted: str) -> bool:
    return wanted in (engine.get("slug"), engine.get("id"))


def resolve_default_engine(configured: str, source: str, engines: list[dict]) -> dict:
    """The effective default among ``engines`` (the scope's engine list) for a ``configured``
    slug or id that came from ``source`` (``"setting"``, ``"env"`` or ``""`` for none).

    Returns ``{id, slug, name, configured, source, fallback, note}``: ``fallback`` is True (and
    ``note`` says why) when something was configured but is not offered here, in which case
    the built-in is the answer. Nothing configured is the built-in, ``source="builtin"``."""
    configured = (configured or "").strip()
    builtin = next((e for e in engines if e.get("slug") == BUILTIN_DEFAULT_SLUG), None) or {
        "id": BUILTIN_DEFAULT_ID,
        "slug": BUILTIN_DEFAULT_SLUG,
        "name": "adapy default",
    }

    def _out(engine: dict, *, fallback: bool = False, note: str | None = None) -> dict:
        return {
            "id": engine.get("id"),
            "slug": engine.get("slug"),
            "name": engine.get("name"),
            "configured": configured,
            "source": source if configured else "builtin",
            "fallback": fallback,
            "note": note,
        }

    if not configured:
        return _out(builtin)
    match = next((e for e in engines if _matches(e, configured)), None)
    if match is not None:
        _warned.discard(configured)
        return _out(match)
    where = "the procedural_default_engine setting" if source == "setting" else DEFAULT_ENGINE_ENV
    note = (
        f"default procedural engine {configured!r} (from {where}) is not available here; "
        f"new models use {BUILTIN_DEFAULT_SLUG!r} until it is"
    )
    if configured not in _warned:
        _warned.add(configured)
        logger.warning("procedural: %s", note)
    return _out(builtin, fallback=True, note=note)


async def configured_default_engine(pool, env_value: str) -> tuple[str, str]:
    """What is configured, and where: the admin setting when set (and a database is there to
    hold it), else the deployment env. ``("", "")`` when neither names one."""
    if pool is not None:
        try:
            from . import db as db_module

            value = (await db_module.get_setting(pool, DEFAULT_ENGINE_SETTING) or "").strip()
        except Exception:
            logger.warning("procedural: could not read %s; using the deployment default", DEFAULT_ENGINE_SETTING)
            value = ""
        if value:
            return value, "setting"
    env_value = (env_value or "").strip()
    return (env_value, "env") if env_value else ("", "")


def is_builtin_default(slug: str | None) -> bool:
    return not slug or slug == BUILTIN_DEFAULT_SLUG


__all__ = [
    "BUILTIN_DEFAULT_ID",
    "BUILTIN_DEFAULT_SLUG",
    "DEFAULT_ENGINE_ENV",
    "DEFAULT_ENGINE_SETTING",
    "configured_default_engine",
    "is_builtin_default",
    "resolve_default_engine",
]
