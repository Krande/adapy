import React from "react";
import {useStore} from "zustand";

import {LAYOUT_PRESET_CHOICES, resolvePreset, switchLayoutPreset} from "@/layout/presetChoice";
import {layoutEngineEnabled} from "@/layout/flag";
import {customisablePanelsOn} from "@/layout/layout";
import {layoutStore} from "@/layout/layoutStore";

// The layout mode (classic or docked) and the "Customisable panels" setting.
// Shown only while the viewer renders through the layout host (the layout-engine
// flag), the one path that honours them.
const LayoutOptions: React.FC<{className?: string}> = ({className}) => {
    const on = useStore(layoutStore, (s) => customisablePanelsOn(s.prefs));
    const active = useStore(layoutStore, (s) => resolvePreset(s.prefs.activePreset));
    const [engine] = React.useState(() => layoutEngineEnabled());
    if (!engine) return null;
    return (
        <div className={`space-y-3 ${className ?? ""}`}>
            <fieldset>
                <legend className="leading-tight">
                    Layout
                    <span className="block text-xs text-gray-400">
                        Classic stacks panels over the viewer; docked puts them in docks around it. Switching reloads the viewer.
                    </span>
                </legend>
                <div className="mt-1 flex gap-3">
                    {LAYOUT_PRESET_CHOICES.map((c) => (
                        <label key={c.id} className="flex items-center space-x-1">
                            <input
                                type="radio"
                                name="ada-layout-preset"
                                checked={active === c.id}
                                onChange={() => switchLayoutPreset(c.id)}
                            />
                            <span>{c.label}</span>
                        </label>
                    ))}
                </div>
            </fieldset>
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
                        Float panels out of the column, minimise them to the top bar, and dock them back.
                    </span>
                </span>
            </label>
        </div>
    );
};

export default LayoutOptions;
