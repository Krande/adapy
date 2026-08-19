// Loads the committed dev fixture model when the page is opened with ``?demo=1``.
//
// Why this exists: ``npm run dev`` serves the SPA with no backend, so the WS connect
// fails (gracefully) and the viewport is empty. Every UI review would otherwise start
// against a blank canvas. ``?demo=1`` puts a real model in the scene with no server,
// no auth and no conversion round-trip.
//
// Deliberately NOT routed through useUrlParamLoad: that hook is REST-only (it waits on
// the scope list from /api/me) and the fixture must work in the default WS dev mode.
// It goes straight to the same store entry point that the embedded/base64 path uses,
// so the fixture travels the ordinary model-load code path.
//
// Dev-only by construction: guarded on ``import.meta.env.DEV`` so the call is dropped
// from every production build (vite statically replaces it, rollup removes the branch),
// and public/dev/ is not referenced by any shipped bundle.

import { SceneOperations } from "@/flatbuffers/scene/scene-operations";
import { useModelState } from "@/state/modelState";

/** Path under public/ — regenerate with scripts/make-dev-fixture.py. */
const FIXTURE_URL = "/dev/demo.glb";

/**
 * Consume ``?demo=1`` and load the fixture. No-op in production builds, when the param
 * is absent, or when the page already has an embedded model to show.
 *
 * Returns true when a fixture load was dispatched (used by tests).
 */
export function loadDevFixtureIfRequested(search: string = window.location.search): boolean {
    if (!import.meta.env.DEV) return false;

    const value = new URLSearchParams(search).get("demo");
    if (!value || value === "0" || value === "false") return false;

    // ``?demo=<name>`` picks a different file under public/dev/ so extra fixtures (e.g. a
    // FEA result deck) can be dropped in without touching this file. ``?demo=1`` is the default.
    const url = value === "1" || value === "true" ? FIXTURE_URL : `/dev/${value}.glb`;

    console.info(`[devFixture] loading ${url} (?demo=${value})`);
    useModelState.getState().setModelUrl(url, SceneOperations.REPLACE);
    return true;
}
