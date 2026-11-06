// The Scene panel's tabs and how the store's `mode` maps onto them. Kept apart
// from the component so the mapping can be tested.

import type {SceneInfoMode} from "@/state/sceneInfoStore";

export type SceneTab = "model" | "overlays" | "tools" | "clip" | "mesh" | "fem" | "clashes" | "joints";

export interface SceneTabDef {
    id: SceneTab;
    label: string;
    /** Shown only while its content exists. */
    ctx?: boolean;
}

// "Clashes" sits immediately before "Joints", on purpose: identifying joints and reviewing them
// are two ends of one workflow (`notes_core_asset_browser.md` §Decision 10's naming paragraph),
// never a second joints table under a different tab. "Clashes" is NOT contextual -- unlike
// Joints it needs no baked concepts to be worth showing: it runs against whatever source is
// loaded and says plainly when that source has no members to check.
export const TAB_META: SceneTabDef[] = [
    {id: "model", label: "Model"},
    {id: "overlays", label: "Overlays"},
    {id: "tools", label: "Tools"},
    {id: "clip", label: "Clip"},
    {id: "mesh", label: "Mesh"},
    {id: "clashes", label: "Clashes"},
    {id: "joints", label: "Joints", ctx: true},
];

/** The FE model's concepts, a tab only where a host asks for it (`SceneBody`'s `femTab`). */
export const FEM_TAB: SceneTabDef = {id: "fem", label: "FEM", ctx: true};

export const MODE_TO_TAB: Record<SceneInfoMode, SceneTab> = {
    info: "model",
    source: "model",
    utilities: "tools",
    section: "clip",
    mesh: "mesh",
    fem: "model",
    clashes: "clashes",
    joints: "joints",
    overlays: "overlays",
};

export const TAB_TO_MODE: Record<SceneTab, SceneInfoMode> = {
    model: "info",
    overlays: "overlays",
    tools: "utilities",
    clip: "section",
    mesh: "mesh",
    fem: "fem",
    clashes: "clashes",
    joints: "joints",
};

export interface SceneTabOptions {
    /** Which tabs, in this order. Absent: all of them, in core's order. */
    tabs?: readonly SceneTab[];
    /** Tabs left out. */
    hiddenTabs?: readonly SceneTab[];
    /** Offer the FEM tab (after Mesh). */
    femTab?: boolean;
}

/** The tabs offered, given the options and which contextual tabs have content. */
export function sceneTabs(opts: SceneTabOptions, ctxAvailable: Partial<Record<SceneTab, boolean>>): SceneTabDef[] {
    let all = TAB_META;
    if (opts.femTab) {
        const at = all.findIndex((t) => t.id === "mesh") + 1;
        all = [...all.slice(0, at), FEM_TAB, ...all.slice(at)];
    }
    const picked = opts.tabs
        ? opts.tabs.map((id) => all.find((t) => t.id === id)).filter((t): t is SceneTabDef => !!t)
        : all;
    return picked.filter((t) => !opts.hiddenTabs?.includes(t.id) && (!t.ctx || ctxAvailable[t.id]));
}

/** The tab the store's mode lands on, or the first offered when that one is not. */
export function activeSceneTab(mode: SceneInfoMode, tabs: SceneTabDef[], femTab = false): SceneTab {
    const tab = femTab && mode === "fem" ? "fem" : MODE_TO_TAB[mode];
    if (tabs.some((t) => t.id === tab)) return tab;
    return tabs.some((t) => t.id === "model") ? "model" : (tabs[0]?.id ?? "model");
}

// Stacking: a column of collapsible headers instead of the strip, once the body is tall enough
// for every header plus one open section. Leaving takes less height than entering, so a resize
// across the line does not flip it back and forth.
export const STACK_HEADER_PX = 30;
export const STACK_ENTER_BODY_PX = 240;
export const STACK_LEAVE_BODY_PX = 190;

/** Whether `arrangement: "auto"` stacks `tabCount` tabs in a body `heightPx` tall. */
export function shouldStackTabs(tabCount: number, heightPx: number, wasStacked: boolean): boolean {
    // One or two tabs read better as a strip.
    if (tabCount < 3) return false;
    const body = wasStacked ? STACK_LEAVE_BODY_PX : STACK_ENTER_BODY_PX;
    return heightPx >= tabCount * STACK_HEADER_PX + body;
}
