import {useEffect, useRef} from "react";

import {pollUntilTerminal} from "@/hooks/useJobPoll";
import {runtime} from "@/runtime/config";
import {capabilities} from "@/services/capabilities";
import {useViewerRefs} from "@/state/AdaViewerContext";
import {useModelState} from "@/state/modelState";
import {useScopeStore} from "@/state/scopeStore";
import {restoreScene, startSceneRecording} from "@/utils/scene/sceneRestore";

// Start recording what is loaded, and restore the previous page's models, once the scope list
// (and so the API) and the scene are ready. REST only.
//
// A `?file=` deep link is loaded by useUrlParamLoad, which strips it from the URL in its effect;
// it is read here during render, before that, so the restore can wait for that load and skip it.

const SCENE_WAIT_MAX_MS = 15000;
const DEEP_LINK_WAIT_MAX_MS = 30000;

function deepLinkedFile(): string {
    try {
        return new URLSearchParams(window.location.search).get("file") ?? "";
    } catch {
        return "";
    }
}

export function useSceneRestore(): void {
    const {scene: sceneRef} = useViewerRefs();
    const available = useScopeStore((s) => s.available);
    const deepLink = useRef<string | null>(null);
    if (deepLink.current === null) deepLink.current = deepLinkedFile();
    const started = useRef(false);

    useEffect(() => {
        if (started.current) return;
        if (!runtime.isRestMode() || !capabilities.files.supports("blobUrl")) return;
        if (available.length === 0) return;
        started.current = true;
        startSceneRecording();

        const file = deepLink.current ?? "";
        void (async () => {
            const scene = await pollUntilTerminal({
                fetch: async () => sceneRef.current,
                terminal: (s) => s != null,
                fetchFirst: true,
                intervalMs: 100,
                deadlineMs: SCENE_WAIT_MAX_MS,
            });
            if (scene.outcome !== "terminal") return;
            if (file) {
                // Loads share the loader; let the deep-linked one land (or give up) first.
                await pollUntilTerminal({
                    fetch: async () => useModelState.getState().loadedSourceNames.has(file),
                    terminal: (loaded) => loaded,
                    fetchFirst: true,
                    intervalMs: 250,
                    deadlineMs: DEEP_LINK_WAIT_MAX_MS,
                });
            }
            await restoreScene(file ? new Set([file]) : undefined);
        })();
    }, [available, sceneRef]);
}
