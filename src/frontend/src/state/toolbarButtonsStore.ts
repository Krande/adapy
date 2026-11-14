import {create} from "zustand";
import {persist} from "zustand/middleware";

import {CORE_PANEL_IDS} from "@/layout/corePanelIds";

// Which of the classic top bar's buttons the user has turned off (Settings > Theme > "Top bar
// buttons"). Hiding a button only takes it out of the row: its panel keeps its open state, its
// shortcut and its Ctrl+K command. Kept per browser, like the panel theme.
//
// Dependency-free apart from the panel ids, so `node --test` holds the rules without a canvas.

/**
 * Buttons that cannot be hidden. The Options button opens the drawer that holds the only
 * on-screen way to the Settings window (Options > "Settings"); Ctrl+K and Shift+Q are keyboard
 * routes a touch device does not have. Hiding it could leave no way back to turn it on again.
 */
export const PINNED_TOOLBAR_BUTTONS: ReadonlySet<string> = new Set([CORE_PANEL_IDS.options]);

/** May the user hide this panel's top-bar button. */
export function canHideToolbarButton(panelId: string): boolean {
    return !PINNED_TOOLBAR_BUTTONS.has(panelId);
}

/** Is this panel's button in the top bar, given the ids the user hid. A pinned button always is. */
export function isToolbarButtonShown(hidden: readonly string[], panelId: string): boolean {
    return !canHideToolbarButton(panelId) || !hidden.includes(panelId);
}

/** One row of the setting: a top-bar button, labelled as its panel. */
export interface ToolbarButtonChoice {
    id: string;
    label: string;
    hint?: string;
}

/**
 * The setting's rows, from the top bar's panels in their top-bar order (`listToolbarPanels()`):
 * those the bar would draw (`present`), less the pinned ones, labelled by panel title, else hint.
 */
export function toolbarButtonChoices(
    panels: readonly {id: string; title?: string; hint?: string}[],
    present: (panelId: string) => boolean = () => true,
): ToolbarButtonChoice[] {
    return panels
        .filter((p) => canHideToolbarButton(p.id) && present(p.id))
        .map((p) => {
            const label = p.title?.trim() || p.hint?.trim() || p.id;
            const hint = p.hint && p.hint !== label ? p.hint : undefined;
            return hint ? {id: p.id, label, hint} : {id: p.id, label};
        });
}

interface ToolbarButtonsState {
    /** Panel ids whose top-bar button the user turned off. */
    hidden: string[];
    setButtonShown: (panelId: string, shown: boolean) => void;
    showAll: () => void;
}

export const useToolbarButtonsStore = create<ToolbarButtonsState>()(
    persist(
        (set) => ({
            hidden: [],
            setButtonShown: (panelId, shown) =>
                set((s) => {
                    if (!canHideToolbarButton(panelId)) return s;
                    const has = s.hidden.includes(panelId);
                    if (shown === !has) return s;
                    return {hidden: shown ? s.hidden.filter((id) => id !== panelId) : [...s.hidden, panelId]};
                }),
            showAll: () => set({hidden: []}),
        }),
        {
            name: "ada-toolbar-buttons",
            partialize: (s) => ({hidden: s.hidden}),
            // A malformed stored value shows every button rather than breaking the top bar.
            merge: (persisted, current) => {
                const stored = (persisted as {hidden?: unknown} | undefined)?.hidden;
                const hidden = Array.isArray(stored) ? stored.filter((id): id is string => typeof id === "string") : [];
                return {...current, hidden};
            },
        },
    ),
);
