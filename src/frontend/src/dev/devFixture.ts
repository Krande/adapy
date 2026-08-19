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

    // ``?demo=<name>`` picks a different file under public/dev/ so extra fixtures can be
    // dropped in without touching this file. ``?demo=1`` is the default.
    const url = value === "1" || value === "true" ? FIXTURE_URL : `/dev/${value}.glb`;

    console.info(`[devFixture] loading ${url} (?demo=${value})`);
    useModelState.getState().setModelUrl(url, SceneOperations.REPLACE);
    return true;
}

/** Source key the FEA fixture is baked under; must match vite.plugin-dev-rest.mjs. */
const FEA_SOURCE = "dev-cantilever";

/**
 * Consume ``?fea=1`` and load the baked FEA fixture (`npm run dev:rest` only).
 *
 * Goes through the REAL streaming loader — `load_fea_streaming`, the same call
 * SimulationControls makes — rather than a shortcut, so the review exercises the
 * production path: manifest → range-fetched field step → morph target + vertex colours.
 * The dev server stands in for the blob endpoints (see vite.plugin-dev-rest.mjs); the
 * only thing skipped is the server-side bake, because the fixture ships pre-baked.
 *
 * Deliberately NOT routed through `load_fea_with_defaults`: that helper starts a bake
 * job and polls for a manifest that already exists here.
 */
export async function loadDevFeaFixtureIfRequested(
    search: string = window.location.search,
): Promise<boolean> {
    if (!import.meta.env.DEV) return false;

    const value = new URLSearchParams(search).get("fea");
    if (!value || value === "0" || value === "false") return false;

    const {runtime} = await import("@/runtime/config");
    if (!runtime.isRestMode()) {
        console.warn("[devFixture] ?fea=1 needs REST mode — start the dev server with `npm run dev:rest`");
        return false;
    }

    // Wait for the scene. This runs at module-eval, before ThreeCanvas has mounted and
    // set sceneRef — without the wait, load_fea_streaming throws "scene not ready" and
    // the fixture silently never appears. Same polling shape useUrlParamLoad uses for
    // the ?file= deep link, and for the same reason.
    const {sceneRef} = await import("@/state/refs");
    const deadline = Date.now() + 15000;
    while (sceneRef.current == null) {
        if (Date.now() > deadline) {
            console.warn("[devFixture] scene never mounted; giving up on the FEA fixture");
            return false;
        }
        await new Promise((r) => setTimeout(r, 100));
    }

    try {
        const manifest = await (await fetch("/dev/fea/fea.manifest.json")).json();
        const field = manifest.fields?.[0];
        if (!field) {
            console.warn("[devFixture] fixture manifest carries no fields");
            return false;
        }

        const {load_fea_streaming} = await import("@/utils/scene/handlers/load_fea_streaming");
        console.info(
            `[devFixture] loading FEA fixture "${FEA_SOURCE}" — ` +
                `${field.name_canonical}, ${field.n_steps} steps`,
        );
        await load_fea_streaming({
            sourceName: FEA_SOURCE,
            manifest,
            fieldName: field.name_canonical,
            stepIndex: 0,
            // Magnitude rather than a single component: it is the view that reads as a
            // mode shape without the user picking an axis first.
            reduction: null,
        });
        return true;
    } catch (err) {
        console.warn("[devFixture] FEA fixture load failed", err);
        return false;
    }
}
