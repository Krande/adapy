import {useEffect, useRef} from "react";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useViewerRefs} from "@/state/AdaViewerContext";
import {runtime} from "@/runtime/config";
import {dispatchPluginUrlParams} from "@/plugins/urlParams";
import {isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {pollUntilTerminal} from "@/hooks/useJobPoll";
import {carryDeepLinkOnShellSwitch} from "@/hooks/deepLinkCarry";
import {useModelState} from "@/state/modelState";
import {PROCEDURAL_PARAM, openProceduralFromUrl} from "@/hooks/proceduralDeepLink";
import {withoutDeepLinkParams} from "@/hooks/deepLinkParams";
import {applyViewParams} from "@/hooks/viewParams";

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
//   ?procedural — a procedural model id, opened in the cell builder (see
//                 proceduralDeepLink.ts; ``?mode=build`` puts the shell there). With
//                 ``?file=`` as well, both open: the file in the scene, the model in
//                 the builder - they are separate states, and dropping either would
//                 lose a link's half without a word.
//   ?grid ?snap — the builder's grid step and snap distance (viewParams.ts; kept in the URL)
//
// The params this hook consumes are stripped from window.location after
// consumption so a manual reload doesn't re-trigger the load (and doesn't
// leak the scope id into bookmarks / shares more than the user intended).
// ONLY those: the rest belong to someone else and may not have been read yet.
// ``?mode=`` is the shell's, read when the shell installs - which can be AFTER
// the scope list arrives here, so stripping the whole query string made a
// ``?file=...&mode=capacity`` link open in whichever mode won that race.
// A UI-shell switch that reloads is the exception: it puts the params back
// into its reload URL while the file is still loaded (see deepLinkCarry.ts).
// A switch in place keeps the scene and needs nothing.
//
// REST-only; in WS / desktop builds we never get here.

function stripDeepLinkParams(): void {
    const {pathname, search, hash} = window.location;
    window.history.replaceState({}, "", `${pathname}${withoutDeepLinkParams(search)}${hash}`);
}

const SCENE_WAIT_INTERVAL_MS = 100;
const SCENE_WAIT_MAX_MS = 15000;

export function useUrlParamLoad(): void {
    // The scene of the provider this hook is mounted under — the one whose
    // canvas the deep-linked file is about to be loaded into.
    const {scene: sceneRef} = useViewerRefs();
    const consumed = useRef(false);
    const pluginParamsOffered = useRef(false);
    const available = useScopeStore((s) => s.available);
    const setCurrent = useScopeStore((s) => s.setCurrent);

    useEffect(() => {
        if (consumed.current) return;
        if (!runtime.isRestMode()) return;

        // Offer the deep-link query params to registered plugin url-param
        // handlers once on mount (independent of core's ?file= handling below,
        // and before it — a plugin may claim params even with no file load).
        // Fully isolated; core routing is unchanged.
        if (!pluginParamsOffered.current) {
            pluginParamsOffered.current = true;
            void dispatchPluginUrlParams(window.location.search);
            // ?grid= / ?snap=: the builder's grid for the model the link opens (viewParams.ts).
            applyViewParams(window.location.search);
        }

        const params = new URLSearchParams(window.location.search);
        const fileParam = params.get("file");
        const scopeParam = params.get("scope");
        const derivedParam = params.get("derived");
        const proceduralParam = params.get(PROCEDURAL_PARAM);
        // A link with only ?scope= (a local viewer's, say) still picks the scope: else the
        // tab keeps the one it had and Storage lists another scope than the link names.
        if (!fileParam && !proceduralParam && !scopeParam) return;

        // Need the scope list before we can resolve ``?scope=user:me``.
        // AuthGate populates ``available`` from /api/me; until then the
        // hook re-runs each store update without doing anything.
        if (available.length === 0) return;

        consumed.current = true;

        let scopeKey = scopeUrlPart(useScopeStore.getState().current);
        if (scopeParam) {
            const target = available.find((s) => scopeUrlPart(s) === scopeParam);
            if (target) {
                setCurrent(target);
                scopeKey = scopeUrlPart(target);
            }
            // Unknown scope token: leave the current selection alone
            // and try the load anyway. Worst case the file doesn't
            // exist under the active scope and the user sees a clear
            // 404 in the conversion toast.
        }
        if (!fileParam && !proceduralParam) {
            stripDeepLinkParams();
            return;
        }

        if (proceduralParam) {
            // A model: no scene to wait for (the cell builder draws once it has the
            // document) and nothing to carry across a shell switch.
            void openProceduralFromUrl(scopeKey, proceduralParam);
        }
        if (!fileParam) {
            stripDeepLinkParams();
            return;
        }

        // A UI-shell switch may reload the page as well, and must bring the
        // file along: remember the params before they go (deepLinkCarry).
        const carry = carryDeepLinkOnShellSwitch(window.location.search, () =>
            useModelState.getState().loadedSourceNames.has(fileParam),
        );

        // Strip the params from the URL right away so a reload doesn't
        // re-fire. We keep the pathname (so /scopes/foo deep links
        // would still work in the future) and the params that are not
        // ours (see DEEP_LINK_PARAMS).
        stripDeepLinkParams();

        // Wait for the three.js scene to be mounted before dispatching.
        // ThreeCanvas sets ``sceneRef.current`` inside its
        // ``onCreated`` callback; for a cold load (URL with params
        // straight from a /convert deep-link) that takes a few render
        // ticks. Polling stops at ~15 s so a misconfigured deployment
        // doesn't hang here forever.
        const pump = async () => {
            // Read the ref first (a warm mount costs no wait), sleep between
            // reads, give up quietly at the deadline.
            const waited = await pollUntilTerminal({
                fetch: async () => sceneRef.current,
                terminal: (scene) => scene != null,
                fetchFirst: true,
                intervalMs: SCENE_WAIT_INTERVAL_MS,
                deadlineMs: SCENE_WAIT_MAX_MS,
            });
            if (waited.outcome !== "terminal") {
                // eslint-disable-next-line no-console
                console.warn("[useUrlParamLoad] scene never mounted; giving up on", fileParam);
                return;
            }
            try {
                // Streaming-FEA sources (.sin/.sif/.rmed/...) have no legacy GLB
                // target — they load from their baked `_derived/<src>.fea/`
                // artefacts. Routing them through overlay_file_in_scene made the
                // deep link a no-op ("non-GLB source but conversion disabled")
                // on any deployment without a conversion queue, even though the
                // bake was already on storage. Go through the same load queue the
                // storage browser uses, which picks the streaming loader for
                // those and the overlay for everything else.
                if (!derivedParam && isStreamingFEAResult(fileParam)) {
                    const {load_fea_with_defaults} = await import(
                        "@/utils/scene/handlers/load_fea_streaming"
                    );
                    await load_fea_with_defaults(fileParam);
                } else {
                    const {overlay_file_in_scene} = await import(
                        "@/utils/scene/handlers/overlay_file_in_scene"
                    );
                    // ``derivedParam`` (e.g. ``_derived/foo.step.glb``) is
                    // the explicit derived-blob hand-off from /convert's
                    // "View in 3D" button. Pass it through so the loader
                    // can fetch the blob directly without a second
                    // ``/convert`` round-trip to re-resolve the path.
                    await overlay_file_in_scene(fileParam, derivedParam ?? undefined);
                }
            } catch (err) {
                // eslint-disable-next-line no-console
                console.warn("[useUrlParamLoad] load failed for", fileParam, err);
            }
        };
        void pump().finally(() => carry.settle());
    }, [available, setCurrent]);
}
