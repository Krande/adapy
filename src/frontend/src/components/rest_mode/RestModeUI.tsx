import React from "react";
import ConversionProgress from "../conversion/ConversionProgress";
import UploadContextMenu from "../upload/UploadContextMenu";
import {useRestoreInflightJobs} from "@/hooks/useRestoreInflightJobs";
import {useCompressionSweepPoll} from "@/hooks/useCompressionSweepPoll";

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
    return (
        <>
            <ConversionProgress/>
            <UploadContextMenu/>
        </>
    );
};

export default RestModeUI;
