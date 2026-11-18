// The Scene panel's tabs and how the store's `mode` maps onto them. Kept apart
// from the component so the mapping can be tested.

import type {SceneInfoMode} from "@/state/sceneInfoStore";

export type SceneTab =
    | "model"
    | "overlays"
    | "tools"
    | "clip"
    | "mesh"
    | "fem"
    // The Joints group's tabs, which moved to the Connections panel (`core:component-controls`):
    // still names a shell may give in `tabs` / `hiddenTabs`, so its options keep compiling, but
    // the Scene panel offers none of them (`MOVED_SCENE_TABS`).
    | "connection"
    | "clashes"
    | "joints"
    | "jointOverlays";

/** The tabs that were the Scene panel's Joints group: the Connections panel has them now. */
export const MOVED_SCENE_TABS: readonly SceneTab[] = ["connection", "clashes", "joints", "jointOverlays"];

export interface SceneTabDef {
    id: SceneTab;
    label: string;
    /** Shown only while its content exists. */
    ctx?: boolean;
}

// In the order of their groups (`SCENE_GROUPS`): what is loaded, what the scene shows, then tools.
//
// The Joints group (Connection, Clashes, Review, Joint overlays) is not here any more: everything
// about a joint -- the builder, the clash check that identifies joints, what they draw in 3D and
// the review of the found or detailed ones -- is one panel's now, Connections, beside the plugins'
// tabs about connections (`ConnectionBuilderPanel`). The tab labels it had were "Connection",
// "Joints" and "Joint overlays" (in its strip "Review" and "Overlays").
export const TAB_META: SceneTabDef[] = [
    {id: "model", label: "Model"},
    {id: "overlays", label: "Overlays"},
    {id: "clip", label: "Clip"},
    {id: "mesh", label: "Mesh"},
    {id: "tools", label: "Tools"},
];

/** The FE model's concepts, a tab only where a host asks for it (`SceneBody`'s `femTab`). */
export const FEM_TAB: SceneTabDef = {id: "fem", label: "FEM", ctx: true};

// ── groups: the strip shows these, and a group of several tabs a second strip of its tabs ────────
//
// Related tabs share a group: how the scene is SHOWN (overlays, clipping, mesh distortion). The
// tabs stay what the store's mode, `tabs` / `hiddenTabs` and every opener name; a group is only how
// the strip lays them out. (The JOINT workflow was a group here too, "Joints"; it is the
// Connections panel's now.)

export type SceneGroup = "model" | "view" | "fem" | "tools";

export interface SceneGroupDef {
    id: SceneGroup;
    label: string;
    tabs: readonly SceneTab[];
}

export const SCENE_GROUPS: readonly SceneGroupDef[] = [
    {id: "model", label: "Model", tabs: ["model"]},
    {id: "view", label: "View", tabs: ["overlays", "clip", "mesh"]},
    {id: "fem", label: "FEM", tabs: ["fem"]},
    {id: "tools", label: "Tools", tabs: ["tools"]},
];

export interface OfferedGroup {
    id: SceneGroup;
    label: string;
    tabs: SceneTabDef[];
}

/** The groups of the offered tabs, in the order their first tab is offered; a group with no tab
 *  offered is left out. */
export function sceneGroups(tabs: readonly SceneTabDef[]): OfferedGroup[] {
    const out: OfferedGroup[] = [];
    for (const t of tabs) {
        const def = SCENE_GROUPS.find((g) => g.tabs.includes(t.id));
        const id = def?.id ?? (t.id as SceneGroup);
        let group = out.find((g) => g.id === id);
        if (!group) out.push((group = {id, label: def?.label ?? t.label, tabs: []}));
        group.tabs.push(t);
    }
    return out;
}

/** The group a tab is in. */
export function groupOf(tab: SceneTab): SceneGroup {
    return SCENE_GROUPS.find((g) => g.tabs.includes(tab))?.id ?? (tab as SceneGroup);
}

export const MODE_TO_TAB: Record<SceneInfoMode, SceneTab> = {
    info: "model",
    source: "model",
    utilities: "tools",
    section: "clip",
    mesh: "mesh",
    fem: "model",
    overlays: "overlays",
    // The modes of the tabs that moved to the Connections panel: an opener that still sets one
    // is sent there (`layout/connectionsTabActions`' `redirectMovedSceneModes`); here they land
    // on Model, the tab a mode with no tab of its own falls back to.
    clashes: "model",
    joints: "model",
    connection: "model",
    jointOverlays: "model",
};

export const TAB_TO_MODE: Record<SceneTab, SceneInfoMode> = {
    model: "info",
    overlays: "overlays",
    tools: "utilities",
    clip: "section",
    mesh: "mesh",
    fem: "fem",
    connection: "connection",
    clashes: "clashes",
    joints: "joints",
    jointOverlays: "jointOverlays",
};

export interface SceneTabOptions {
    /** Which tabs, in this order. Absent: all of them, in core's order. A tab that moved to the
     *  Connections panel (`MOVED_SCENE_TABS`) is ignored. */
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
        // After Mesh: its own group, between View and Joints.
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
