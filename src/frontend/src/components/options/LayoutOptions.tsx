import React, {useSyncExternalStore} from "react";

import {setCustomisablePanels, useCustomisablePanels, useCustomisablePanelsRequired} from "@/layout/customisable";
import {setMarkingMenuOn, useServedMarkingMenu} from "@/layout/markingMenuState";
import {activeUiShellId, activeUiShellVersion, getUiShell, subscribeActiveUiShell} from "@/plugins/uiShells";

// The "Customisable panels" setting: the user's per UI, and not theirs at all in a
// UI that requires it (a composed shell such as the Desktop UI), where it is shown on and disabled.
const LayoutOptions: React.FC<{className?: string}> = ({className}) => {
    useSyncExternalStore(subscribeActiveUiShell, activeUiShellVersion, activeUiShellVersion);
    const on = useCustomisablePanels();
    const required = useCustomisablePanelsRequired();
    const label = getUiShell(activeUiShellId())?.label ?? "this";
    const marking = useServedMarkingMenu();
    return (
        <div className={`space-y-3 ${className ?? ""}`}>
            <label className={`flex items-start space-x-2 ${required ? "opacity-60" : ""}`} title={required ? `Always on in the ${label} UI` : undefined}>
                <input
                    type="checkbox"
                    className="mt-1"
                    checked={on}
                    disabled={required}
                    aria-describedby={required ? "customisable-panels-required" : undefined}
                    data-testid="customisable-panels"
                    onChange={() => setCustomisablePanels(!on)}
                />
                <span className="leading-tight">
                    Customisable panels
                    <span className="block text-xs text-gray-400">
                        Float panels in windows, dock and stack them beside the view, minimise them to the top bar.
                    </span>
                    {required && (
                        <span id="customisable-panels-required" className="block text-xs text-gray-400">
                            {`Always on in the ${label} UI: its docked layout is built on them.`}
                        </span>
                    )}
                </span>
            </label>
            {marking.available && (
                <label className="flex items-start space-x-2">
                    <input
                        type="checkbox"
                        className="mt-1"
                        checked={marking.on}
                        data-testid="marking-menu"
                        onChange={() => setMarkingMenuOn(!marking.on)}
                    />
                    <span className="leading-tight">
                        Marking menu
                        <span className="block text-xs text-gray-400">
                            {"Hold the right mouse button over the 3D view (or press Shift+E) for a wheel of commands: flick towards one and let go. A quick right-click still opens the object's menu, and a right-drag still pans."}
                        </span>
                    </span>
                </label>
            )}
        </div>
    );
};

export default LayoutOptions;
