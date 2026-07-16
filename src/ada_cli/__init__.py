"""adapy ``ada`` CLI package."""

from __future__ import annotations

import os
import pathlib


def load_dotenv_cwd(path: str | os.PathLike | None = None) -> bool:
    """Load ``KEY=VALUE`` pairs from a ``.env`` in the current directory.

    Best-effort and dependency-free: blank lines and ``#`` comments are
    skipped, a leading ``export`` is tolerated, and surrounding quotes are
    stripped. Existing environment variables win (so a real env var or one
    exported in the shell is never clobbered). Returns True if a file was read.
    """
    env_path = pathlib.Path(path) if path is not None else pathlib.Path.cwd() / ".env"
    if not env_path.is_file():
        return False
    try:
        lines = env_path.read_text().splitlines()
    except OSError:
        return False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val
    return True


def resolve_remote_config(
    url: str | None = None,
    token: str | None = None,
    scope: str | None = None,
) -> tuple[str, str, str]:
    """Resolve viewer URL / token / scope from explicit args or the environment.

    Accepts the env names the admin panel's *CLI token* dialog suggests
    (``ADAPY_API_BASE`` / ``ADAPY_API_TOKEN`` / ``ADAPY_API_SCOPE``) as well as
    the older ``ADAPY_VIEWER_*`` / ``ADAPY_BASE_URL`` names, preferring the
    ``ADAPY_API_*`` pair so a straight copy-paste from the panel works across
    every ``ada`` subcommand (``files``, ``build``, ``audit``). Explicit
    arguments always win. Missing values come back as empty strings; callers
    decide which are required and report what is absent.
    """

    def _first(explicit: str | None, *names: str) -> str:
        if explicit:
            return explicit.strip()
        for name in names:
            val = os.environ.get(name, "").strip()
            if val:
                return val
        return ""

    base = _first(url, "ADAPY_API_BASE", "ADAPY_BASE_URL", "ADAPY_VIEWER_URL").rstrip("/")
    tok = _first(token, "ADAPY_API_TOKEN", "ADAPY_VIEWER_TOKEN")
    scp = _first(scope, "ADAPY_API_SCOPE", "ADAPY_VIEWER_SCOPE")
    return base, tok, scp
