import React, {useEffect} from "react";
import ConversionProgress from "../conversion/ConversionProgress";
import UploadContextMenu from "../upload/UploadContextMenu";
import {useRestoreInflightJobs} from "@/hooks/useRestoreInflightJobs";
import {useCompressionSweepPoll} from "@/hooks/useCompressionSweepPoll";
import {useExperimentalStore} from "@/state/experimentalStore";
import {prewarmPyodide} from "@/utils/pyodide/pyodide_converter";

// Aggregator for all REST-mode-only floating UI. Lazy-loaded by
// app.tsx so the embedded desktop bundle never pulls in the
// conversion / upload code paths.
const RestModeUI: React.FC = () => {
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
            <ConversionProgress/>
            <UploadContextMenu/>
        </>
    );
};

export default RestModeUI;
