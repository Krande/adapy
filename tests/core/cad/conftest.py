"""Backend fixtures shared by the CAD parity modules.

Both parity files ask this environment the same two questions — "which kernels are
installed?" and "are BOTH installed?" — and one answer keeps them from drifting
apart. ``select_backend`` tries adacpp before pythonocc, so merely having adacpp
installed moves the process onto it; every backend handed out here is pinned by
name, and nothing calls ``active_backend()``.
"""

from __future__ import annotations

import pytest

from ada.cad import CadBackendName, backend_available, select_backend

BACKEND_NAMES = ("occ", "adacpp")


@pytest.fixture(params=BACKEND_NAMES)
def backend(request):
    """One installed backend, pinned by name — never the ``select_backend`` default."""
    if not backend_available(CadBackendName(request.param)):
        pytest.skip(f"{request.param} backend not installed")
    return select_backend(prefer=request.param)


@pytest.fixture
def both_backends():
    """``(occ, adacpp)``, or a skip when this environment carries only one kernel."""
    missing = [n for n in BACKEND_NAMES if not backend_available(CadBackendName(n))]
    if missing:
        pytest.skip(f"cross-backend comparison needs both kernels; missing: {', '.join(missing)}")
    return select_backend(prefer="occ"), select_backend(prefer="adacpp")
