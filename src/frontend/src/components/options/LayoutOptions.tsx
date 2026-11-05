import React from "react";
import {useStore} from "zustand";

import {layoutEngineEnabled} from "@/layout/flag";
import {layoutStore} from "@/layout/layoutStore";

// The "Customisable panels" setting. Shown only while the classic UI renders
// through the layout host (the layout-engine flag), the one path that honours it.
const LayoutOptions: React.FC<{className?: string}> = ({className}) => {
    const on = useStore(layoutStore, (s) => s.prefs.customisablePanels === true);
    const [engine] = React.useState(() => layoutEngineEnabled());
    if (!engine) return null;
    return (
        <label className={`flex items-start space-x-2 ${className ?? ""}`}>
            <input
                type="checkbox"
                className="mt-1"
                checked={on}
                onChange={() => layoutStore.getState().setCustomisablePanels(!on)}
            />
            <span className="leading-tight">
                Customisable panels
                <span className="block text-xs text-gray-400">
                    Float panels out of the column, minimise them to the top bar, and dock them back.
                </span>
            </span>
        </label>
    );
};

export default LayoutOptions;
