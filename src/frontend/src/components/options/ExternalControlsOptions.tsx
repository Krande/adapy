import React from "react";

import {useExternalControlsStore} from "@/state/externalControlsStore";

// The "External controls" switch (state/externalControlsStore): whether an agent may drive this tab --
// focus a joint, select, move the camera, load a detail model -- through the REST API. Only offered on
// a server that allows it (ADA_VIEWER_CONTROL), and only ever for the caller's own tabs.
const ExternalControlsOptions: React.FC = () => {
    const enabled = useExternalControlsStore((s) => s.enabled);
    const setEnabled = useExternalControlsStore((s) => s.setEnabled);
    return (
        <div className="space-y-1">
            <label className="flex items-start space-x-2">
                <input
                    type="checkbox"
                    className="mt-1"
                    aria-label="External controls"
                    checked={enabled}
                    onChange={() => setEnabled(!enabled)}
                />
                <span className="leading-tight">
                    Let an agent drive this tab
                    <span className="block text-xs text-gray-400">
                        Scripts and assistants -- signed in as you on a hosted viewer, or on this machine with the desktop one -- can then select objects, focus clash joints, move
                        the camera and load detail models here, and read what this tab shows. This tab only, until
                        it is closed; off by default.
                    </span>
                </span>
            </label>
        </div>
    );
};

export default ExternalControlsOptions;
