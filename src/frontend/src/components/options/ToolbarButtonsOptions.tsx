import React from "react";

import {CORE_PANEL_OWNER, corePanelEnv} from "@/layout/corePanels";
import {toolbarPresence} from "@/layout/presets/classicRules";
import {isPanelAvailable, listToolbarPanels} from "@/layout/registry";
import type {RegisteredPanel} from "@/layout/panel";
import {
    isToolbarButtonShown,
    toolbarButtonChoices,
    useToolbarButtonsStore,
} from "@/state/toolbarButtonsStore";

// "Top bar buttons": one switch per button of the classic top bar, in the bar's own order. Turning
// one off only takes the button out of the row; its panel stays open or closed as it was, and its
// shortcut and Ctrl+K command still reach it. The Options button is not offered: it is the way to
// this setting (`PINNED_TOOLBAR_BUTTONS`).

/** Would the classic top bar draw this panel's button here (core's rules; a plugin's while it is enabled). */
function inTopBar(panel: RegisteredPanel): boolean {
    const env = corePanelEnv();
    const available = isPanelAvailable(panel, env);
    if (panel.owner !== CORE_PANEL_OWNER) return available;
    return toolbarPresence(panel, {open: false, available}, env) !== "omit";
}

const ToolbarButtonsOptions: React.FC<{className?: string}> = ({className}) => {
    const hidden = useToolbarButtonsStore((s) => s.hidden);
    const setButtonShown = useToolbarButtonsStore((s) => s.setButtonShown);
    const showAll = useToolbarButtonsStore((s) => s.showAll);

    const panels = listToolbarPanels();
    const byId = new Map(panels.map((p) => [p.id, p]));
    const choices = toolbarButtonChoices(panels, (id) => inTopBar(byId.get(id)!));
    const anyHidden = choices.some((c) => !isToolbarButtonShown(hidden, c.id));

    return (
        <fieldset className={`space-y-2 text-xs ${className ?? ""}`} data-testid="toolbar-buttons" data-option="toolbar-buttons" tabIndex={-1}>
            <legend className="flex w-full items-center justify-between gap-2 mb-1">
                <span>
                    Top bar buttons
                    <span className="block text-[10px] text-gray-400">
                        The classic layout&apos;s top row. A hidden button&apos;s panel stays as it is; the Options button always shows.
                    </span>
                </span>
                {anyHidden && (
                    <button
                        type="button"
                        onClick={showAll}
                        className="text-blue-400 hover:text-blue-300 cursor-pointer whitespace-nowrap"
                    >
                        Show all
                    </button>
                )}
            </legend>
            {choices.length === 0 && <div className="text-gray-400">No panel buttons to choose from yet.</div>}
            {choices.map((c) => (
                <label key={c.id} className="flex items-center justify-between gap-2 cursor-pointer" title={c.hint}>
                    <span>{c.label}</span>
                    <input
                        type="checkbox"
                        checked={isToolbarButtonShown(hidden, c.id)}
                        onChange={(e) => setButtonShown(c.id, e.target.checked)}
                        data-toolbar-button={c.id}
                        className="h-4 w-4 cursor-pointer accent-blue-500"
                    />
                </label>
            ))}
        </fieldset>
    );
};

export default ToolbarButtonsOptions;
