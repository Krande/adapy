import React, {useEffect} from "react";
import ConversionProgress, {type ToastAnchor} from "../conversion/ConversionProgress";
import UploadContextMenu from "../upload/UploadContextMenu";
import {useRestoreInflightJobs} from "@/hooks/useRestoreInflightJobs";
import {useCompressionSweepPoll} from "@/hooks/useCompressionSweepPoll";
import {engineOrder} from "@/services/compute/executionPolicy";
import {useExecutionPolicyStore, useExecutionPreferenceStore} from "@/state/executionPolicyStore";
import {loadWorkerAccess} from "@/services/compute/workerAccess";
import {prewarmPyodide} from "@/utils/pyodide/pyodide_converter";
import {startPluginPrewarm} from "@/services/compute/pluginPrewarm";
import {runtime} from "@/runtime/config";

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
    // sessions only) -- only on a server that allows it, and once the user turned "External controls"
    // on for this tab (services/viewerControl/gate.ts). Nothing is loaded or opened otherwise.
    useEffect(() => {
        if (!runtime.viewerControl()) return;
        let unfollow: (() => void) | null = null;
        let cancelled = false;
        void import("@/services/viewerControl/gate").then(({followExternalControls}) => {
            if (cancelled) return;
            unfollow = followExternalControls(() => {
                let stop: (() => void) | null = null;
                let stopped = false;
                void Promise.all([
                    import("@/services/viewerControl/socket"),
                    import("@/services/viewerControl/realDeps"),
                    import("@/services/api/viewerControl"),
                ]).then(([{startViewerSocket}, {realViewerControlDeps, watchViewerState}, {browserViewerSocketIO}]) => {
                    if (stopped) return;
                    stop = startViewerSocket(realViewerControlDeps, browserViewerSocketIO, {
                        watch: watchViewerState,
                        client: {title: document.title, path: window.location.pathname},
                    });
                });
                return () => {
                    stopped = true;
                    stop?.();
                };
            });
        });
        const onUnload = () => unfollow?.();
        window.addEventListener("pagehide", onUnload);
        return () => {
            cancelled = true;
            window.removeEventListener("pagehide", onUnload);
            unfollow?.();
        };
    }, []);
    // The caller's worker policy, read once so routing can consult it
    // synchronously (see services/compute/workerAccess).
    useEffect(() => {
        void loadWorkerAccess();
    }, []);
    // Pre-warm the in-browser (Pyodide) engine in the background while opening a
    // file would use it before the worker (the default execution policy), so a
    // file converts immediately instead of cold-loading pyodide + the CAD kernel
    // on first click.
    const policy = useExecutionPolicyStore((s) => s.policy);
    const preferredRead = useExecutionPreferenceStore((s) => s.preferred.read);
    const readOrder = engineOrder("read", "*", {policy, preference: preferredRead ?? null});
    const wasmEngineOn = readOrder.indexOf("pyodide") >= 0 &&
        (readOrder.indexOf("worker") < 0 || readOrder.indexOf("pyodide") < readOrder.indexOf("worker"));
    useEffect(() => {
        if (wasmEngineOn) prewarmPyodide();
    }, [wasmEngineOn]);
    // Then, at idle, the wheels and baked dependencies of the plugins enabled in
    // this scope whose plugin jobs run in the browser first (services/compute/pluginPrewarm).
    useEffect(() => startPluginPrewarm(), []);
    return (
        <>
            <ConversionProgress anchor={toastAnchor}/>
            <UploadContextMenu/>
        </>
    );
};

export default RestModeUI;
