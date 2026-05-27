import {useEffect, useRef} from "react";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {sceneRef} from "@/state/refs";
import {runtime} from "@/runtime/config";

// Consume ``?scope=...&file=...`` query params on viewer mount.
//
// Counterpart to the /convert page's "View in 3D" deep-link. The
// /convert UI hands off the converted source name through the URL;
// this hook waits for both the scope list (populated by AuthGate's
// /api/me call) and the three.js scene (mounted by ThreeCanvas) to
// be ready, then switches scope if requested and dispatches the
// regular `overlay_file_in_scene` load.
//
// Params consumed:
//   ?file   — source key under the active scope (e.g. ``cantilever.step``)
//   ?scope  — optional scopeUrlPart token (``user:me`` / ``project:abc``)
//
// The query string is stripped from window.location after consumption
// so a manual reload doesn't re-trigger the load (and doesn't leak
// the scope id into bookmarks / shares more than the user intended).
//
// REST-only; in WS / desktop builds we never get here.

const SCENE_WAIT_INTERVAL_MS = 100;
const SCENE_WAIT_MAX_MS = 15000;

export function useUrlParamLoad(): void {
    const consumed = useRef(false);
    const available = useScopeStore((s) => s.available);
    const setCurrent = useScopeStore((s) => s.setCurrent);

    useEffect(() => {
        if (consumed.current) return;
        if (!runtime.isRestMode()) return;

        const params = new URLSearchParams(window.location.search);
        const fileParam = params.get("file");
        const scopeParam = params.get("scope");
        if (!fileParam) return;

        // Need the scope list before we can resolve ``?scope=user:me``.
        // AuthGate populates ``available`` from /api/me; until then the
        // hook re-runs each store update without doing anything.
        if (available.length === 0) return;

        consumed.current = true;

        if (scopeParam) {
            const target = available.find((s) => scopeUrlPart(s) === scopeParam);
            if (target) setCurrent(target);
            // Unknown scope token: leave the current selection alone
            // and try the load anyway. Worst case the file doesn't
            // exist under the active scope and the user sees a clear
            // 404 in the conversion toast.
        }

        // Strip the params from the URL right away so a reload doesn't
        // re-fire. We keep the pathname (so /scopes/foo deep links
        // would still work in the future) and just drop the search
        // string.
        window.history.replaceState({}, "", window.location.pathname);

        // Wait for the three.js scene to be mounted before dispatching.
        // ThreeCanvas sets ``sceneRef.current`` inside its
        // ``onCreated`` callback; for a cold load (URL with params
        // straight from a /convert deep-link) that takes a few render
        // ticks. Polling stops at ~15 s so a misconfigured deployment
        // doesn't hang here forever.
        const deadline = Date.now() + SCENE_WAIT_MAX_MS;
        const pump = async () => {
            while (sceneRef.current == null) {
                if (Date.now() > deadline) {
                    // eslint-disable-next-line no-console
                    console.warn("[useUrlParamLoad] scene never mounted; giving up on", fileParam);
                    return;
                }
                await new Promise((r) => setTimeout(r, SCENE_WAIT_INTERVAL_MS));
            }
            try {
                const {overlay_file_in_scene} = await import(
                    "@/utils/scene/handlers/overlay_file_in_scene"
                );
                await overlay_file_in_scene(fileParam);
            } catch (err) {
                // eslint-disable-next-line no-console
                console.warn("[useUrlParamLoad] load failed for", fileParam, err);
            }
        };
        void pump();
    }, [available, setCurrent]);
}
