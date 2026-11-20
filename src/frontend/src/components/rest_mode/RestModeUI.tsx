import React, {useEffect} from "react";
import ConversionProgress, {type ToastAnchor} from "../conversion/ConversionProgress";
import UploadContextMenu from "../upload/UploadContextMenu";
import {useRestoreInflightJobs} from "@/hooks/useRestoreInflightJobs";
import {useCompressionSweepPoll} from "@/hooks/useCompressionSweepPoll";
import {useExperimentalStore} from "@/state/experimentalStore";
import {prewarmPyodide} from "@/utils/pyodide/pyodide_converter";

// Aggregator for all REST-mode-only floating UI. Lazy-loaded by
// app.tsx so the embedded desktop bundle never pulls in the
// conversion / upload code paths.
const RestModeUI: React.FC<{toastAnchor?: ToastAnchor}> = ({toastAnchor}) => {
    // Re-attach to any in-flight conversions the current user kicked
    // off in this scope so the bottom-right toast survives page
    // reloads and cross-device logins. Side-effect-only hook; the
    // component itself doesn't render anything from it.
    useRestoreInflightJobs();
    // Same idea for admin-triggered compression sweeps — state lives
    // server-side in NATS KV so the toast survives a reload or a
    // switch to a different browser session, and reflects sweeps
    // another admin session may have started.
    useCompressionSweepPoll();
    // This tab as a viewer an agent can drive over the REST API (`/api/viewer/...`, the caller's own
    // sessions only): it registers, waits for commands, and reports what it shows.
    useEffect(() => {
        let stop: (() => void) | null = null;
        let cancelled = false;
        void Promise.all([
            import("@/services/viewerControl/session"),
            import("@/services/viewerControl/realDeps"),
            import("@/services/api/viewerControl"),
        ]).then(([{startViewerControl}, {realViewerControlDeps, watchViewerState}, {viewerControlApi}]) => {
            if (cancelled) return;
            stop = startViewerControl(viewerControlApi, realViewerControlDeps, {
                watch: watchViewerState,
                client: {title: document.title, path: window.location.pathname},
            });
        });
        const onUnload = () => stop?.();
        window.addEventListener("pagehide", onUnload);
        return () => {
            cancelled = true;
            window.removeEventListener("pagehide", onUnload);
            stop?.();
        };
    }, []);
    // Pre-warm the in-browser (WASM) engine in the background whenever it's
    // enabled — both when the user flips it on and on a reload where the
    // persisted toggle is already on — so opening a file converts immediately
    // instead of cold-loading pyodide + the CAD kernel on first click.
    const wasmEngineOn = useExperimentalStore((s) => s.pyodideConverter);
    useEffect(() => {
        if (wasmEngineOn) prewarmPyodide();
    }, [wasmEngineOn]);
    return (
        <>
            <ConversionProgress anchor={toastAnchor}/>
            <UploadContextMenu/>
        </>
    );
};

export default RestModeUI;
