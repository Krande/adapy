"""Worker utility implementations (auto-registered via ``@utility``).

Importing this package registers every bundled utility. Workers preload it via
``ADA_WORKER_PRELOAD`` (or it is imported by the worker bootstrap) so the
utilities are advertised to the frontend.
"""

from __future__ import annotations

from . import diff  # noqa: F401  (import for @utility registration side-effect)
from . import merge_preview  # noqa: F401  (import for @utility registration side-effect)

__all__ = ["diff", "merge_preview"]
