import React, {useState} from "react";
import {useSpaceMouseStore} from "@/state/spaceMouseStore";
import {isWebHidSupported, requestSpaceMouse} from "@/utils/spacemouse/spaceMouseDevice";

// One-click reconnect over the viewport. The browser's device chooser needs a
// click, and it may forget a device without a serial number between page
// loads, so with the 3D mouse option on and nothing connected this offers the
// click where the hand already is. Dismissed for the rest of the page load.

const SpaceMouseConnectChip: React.FC = () => {
    const enabled = useSpaceMouseStore((s) => s.enabled);
    const connection = useSpaceMouseStore((s) => s.connection);
    const error = useSpaceMouseStore((s) => s.error);
    const [dismissed, setDismissed] = useState(false);

    if (!enabled || dismissed || !isWebHidSupported()) return null;
    if (connection !== "disconnected" && connection !== "error") return null;

    return (
        <div
            className="pointer-events-auto absolute left-1/2 top-2 z-20 flex -translate-x-1/2 items-center gap-1 rounded-md border border-gray-600 bg-gray-900/85 px-2 py-1 text-xs text-gray-100 shadow-lg backdrop-blur-sm"
            title={error ?? undefined}
        >
            <button
                type="button"
                className="rounded-sm bg-blue-700 px-2 py-0.5 font-semibold text-white hover:bg-blue-600"
                onClick={() => void requestSpaceMouse()}
            >
                Connect SpaceMouse…
            </button>
            {connection === "error" && <span className="text-red-300">Could not connect</span>}
            <button
                type="button"
                className="px-1 text-gray-400 hover:text-gray-100"
                aria-label="Dismiss"
                title="Hide until the page is reloaded"
                onClick={() => setDismissed(true)}
            >
                ×
            </button>
        </div>
    );
};

export default SpaceMouseConnectChip;
