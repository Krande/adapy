import React from "react";

import {runtime} from "@/runtime/config";
import {buildLabel} from "@/utils/buildLabel";

/** "Build: <label>" -- which viewer this is, for a bug report. */
export const BuildLine: React.FC<{className?: string}> = ({className = "text-xs text-gray-300"}) => {
    const label = buildLabel(
        runtime.adapyVersion(),
        runtime.frontendSha(),
        runtime.viewerImageTag(),
        runtime.uniqueVersionId(),
        runtime.adapyBuildRef(),
    );
    return (
        <div className={className}>
            Build: <span className="font-mono">{label}</span>
        </div>
    );
};
