import React from "react";
import {useStore} from "zustand";

import {layoutEngineEnabled} from "@/layout/flag";
import {customisablePanelsOn} from "@/layout/layout";
import {layoutStore} from "@/layout/layoutStore";

// The "Customisable panels" setting. Shown only while the viewer renders
// through the layout host (the layout-engine flag), the one path that honours it.
const LayoutOptions: React.FC<{className?: string}> = ({className}) => {
    const on = useStore(layoutStore, (s) => customisablePanelsOn(s.prefs));
    const [engine] = React.useState(() => layoutEngineEnabled());
    if (!engine) return null;
    return (
        <div className={`space-y-3 ${className ?? ""}`}>
            <label className="flex items-start space-x-2">
                <input
                    type="checkbox"
                    className="mt-1"
                    checked={on}
                    onChange={() => layoutStore.getState().setCustomisablePanels(!on)}
                />
                <span className="leading-tight">
                    Customisable panels
                    <span className="block text-xs text-gray-400">
                        Float panels in windows, dock and stack them beside the view, minimise them to the top bar.
                    </span>
                </span>
            </label>
        </div>
    );
};

export default LayoutOptions;
