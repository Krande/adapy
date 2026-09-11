"""Route groups extracted from ``create_app`` — the pattern for splitting it.

``ada.comms.rest.app.create_app`` grew into one closure holding every route
and every helper as local names. That is why nothing in it can be imported,
tested or read on its own. The way out, set here with the plugin/catalog
listing group and meant to be repeated group by group:

1. **One module per cohesive route group**, built on ``fastapi.APIRouter``
   with NO prefix and NO dependencies of its own (``router = APIRouter()``).
   ``create_app`` does ``api.include_router(<group>.router)`` at the point where
   the routes used to be defined — the ``/api`` prefix and the
   ``current_user`` dependency come from ``api``, and *position matters*: a
   fixed-segment path (``procedural-models/cell-types``) must register before
   a parameterised sibling (``procedural-models/{model_id}``) that would
   otherwise capture it.

2. **Closure state is reached through an explicit dependency, never the
   closure.** ``create_app`` stores its per-app services on
   ``app.state.rest`` as a :class:`~ada.comms.rest.routes.deps.RestContext`
   (settings, storage, queue); a route asks for it with
   ``ctx: RestContext = Depends(rest_context)``. Add a field when a group
   genuinely needs another service; do not reach for ``request.app.state``
   ad hoc.

3. **Shared helpers are lifted to module level first.** A closure helper a
   group needs (``_scope_from_path``, ``_live_worker_specs``, ...) moves to
   :mod:`~ada.comms.rest.routes.deps` as a plain function taking what it
   captured as a parameter (``live_worker_specs(queue, ...)``); ``create_app``
   keeps the old underscore name bound to it (an alias or a one-line wrapper)
   so the routes still inside the closure are untouched. Group-specific
   helpers (the plugin admin gate) move with their group and are imported
   back into ``create_app`` the same way.

4. **Behaviour and response shapes stay identical.** Route bodies move
   verbatim apart from the renames above; tests keep driving them through
   ``create_app`` + ``TestClient`` — an extracted module should need no new
   fixtures to be exercised.

Every remaining group in ``create_app`` (procedural models, equipment/system
catalog CRUD, engines, storage, jobs, admin, ...) can follow the same four
steps; the ``routes/deps.py`` aliases at the top of the closure are the list
of what has been lifted so far.
"""
