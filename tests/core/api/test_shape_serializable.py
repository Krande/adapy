"""Lock down the invariant that ``Shape`` and its parametric
subclasses don't hold raw OCC objects as persistent attributes.

Background: prior to the ``_occ_cache`` refactor, STEP / SAT read
paths stored a ``TopoDS_Shape`` directly in ``Shape._geom``. That
broke every "move this Part across a process boundary" use case:

* pickling (multiprocessing, joblib, distributed task queues)
* the audit pipeline's forked-subprocess pattern that pickles the
  Part across the parent/child boundary on some platforms
* on-disk caches that JSON-encode the geom
* simple ``copy.deepcopy`` for stateful UI flows

OCC bodies now live in the transient ``_occ_cache`` slot which is
explicitly dropped in ``__getstate__``. Tests below pickle each
common shape kind and round-trip; they don't assert that the OCC
body survives (we don't have a serialisable representation for raw
OCC), only that pickling itself succeeds — which is the property
the rest of the stack relies on.
"""

from __future__ import annotations

import pickle


def _assert_picklable(obj):
    blob = pickle.dumps(obj)
    restored = pickle.loads(blob)
    assert restored.__class__ is obj.__class__


def test_prim_cyl_is_picklable():
    from ada import PrimCyl

    cyl = PrimCyl("c", (0, 0, 0), (0, 0, 1), 0.5)
    _assert_picklable(cyl)


def test_prim_cyl_pickles_after_units_change():
    """The units setter caches an OCC body in ``_occ_cache``. The
    restored object should still pickle — proving the cache is
    purged on dump."""
    from ada import PrimCyl

    cyl = PrimCyl("c", (0, 0, 0), (0, 0, 1), 0.5)
    cyl.units = "mm"
    cyl.units = "m"
    _assert_picklable(cyl)


def test_prim_sphere_pickles_after_units_change():
    from ada import PrimSphere

    s = PrimSphere("s", (0, 0, 0), 0.5)
    s.units = "mm"
    s.units = "m"
    _assert_picklable(s)


def test_prim_box_pickles_after_units_change():
    from ada import PrimBox

    b = PrimBox("b", (0, 0, 0), (1, 1, 1))
    b.units = "mm"
    b.units = "m"
    _assert_picklable(b)


def test_shape_constructed_with_occ_body_pickles():
    """The Shape constructor's OCC-detect branch routes a raw
    ``TopoDS_Shape`` to ``_occ_cache`` so the object stays
    picklable. The parametric ``_geom`` is None — that's the
    honest answer (we don't have a parametric description), but
    the OBJECT must still survive a pickle round trip so it can
    be sent to a worker."""
    try:
        from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    except ImportError:
        import pytest

        pytest.skip("pythonocc not available")

    from ada import Shape

    occ_body = BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Shape()
    shape = Shape("from-step", geom=occ_body)
    # Sanity: the OCC body should be in the transient slot, not _geom.
    assert shape._occ_cache is occ_body
    assert shape._geom is None
    # The actual invariant: pickling succeeds.
    _assert_picklable(shape)
