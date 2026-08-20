import {create} from "zustand";
import {persist} from "zustand/middleware";
import {DOCK_LIMITS, type DockedId, type DockId} from "./regions";
import {MODE_IDS, type ModeId} from "./modeStore";
import type {PanelId} from "./panelRegistry";

// Layout geometry, per mode.
//
// This is the source of truth for "which panel is open where". The ~25 scattered
// visibility booleans (isOptionsVisible, show_info_box, show_scene_info_box,
// showServerInfoBox, isControlsVisible, panelVisible, isPanelOpen, …) stay on their own
// stores for now — deleting them would mean touching business logic — and thin adapters
// keep them in sync so external openers such as `tableNavStore.togglePanel` keep
// working. The adapters come out at cutover.
//
// Per mode, because that is what makes modes worth having: Results wants a wide bottom
// dock for the data table, Build wants a tall right dock for the cellbuilder, and
// neither should disturb the other.

export const LAYOUT_VERSION = 2;

export interface DockState {
    /** px along the dock's variable axis (width for left/right, height for bottom). */
    size: number;
    collapsed: boolean;
    /** Tab order within the dock. */
    tabs: PanelId[];
    /** Which tab is showing. null when the dock is empty. */
    active: PanelId | null;
}

export interface FloatState {
    x: number;
    y: number;
    w: number;
    h: number;
}

export interface ModeLayout {
    docks: Record<DockedId, DockState>;
    floats: Partial<Record<PanelId, FloatState>>;
    /** Canvas-anchored HUDs that are on by default in this mode. */
    overlays: Partial<Record<PanelId, boolean>>;
    /** Pinned panels survive a mode switch (Blender's persistent areas). */
    pinned: PanelId[];
}

const emptyDock = (id: DockedId): DockState => ({
    size: DOCK_LIMITS[id].default,
    collapsed: false,
    tabs: [],
    active: null,
});

const clampSize = (dock: DockedId, n: number) =>
    Math.min(DOCK_LIMITS[dock].max, Math.max(DOCK_LIMITS[dock].min, Math.round(n)));

/**
 * Default layout for a mode.
 *
 * Deliberately sparse: a mode opens with the few panels its persona always needs, not
 * with everything it could show. "Too much on screen at once" is the problem being
 * solved; a default that opens six docks reproduces it with extra steps.
 */
function defaultLayout(mode: ModeId): ModeLayout {
    const base: ModeLayout = {
        docks: {left: emptyDock("left"), right: emptyDock("right"), bottom: emptyDock("bottom")},
        floats: {},
        overlays: {},
        pinned: [],
    };

    // `size` lets a mode widen a dock for a panel that genuinely needs the room. The
    // Builder is the case in point: it is a dense authoring surface with tabs, numeric
    // fields and a compile control, and at the 300px default its content truncates —
    // which reads as a broken panel rather than a narrow one.
    const put = (dock: DockedId, tabs: PanelId[], collapsed = false, size?: number) => {
        base.docks[dock] = {
            ...emptyDock(dock),
            ...(size != null ? {size: clampSize(dock, size)} : {}),
            tabs,
            active: tabs[0] ?? null,
            collapsed,
        };
    };

    switch (mode) {
        case "inspect":
            put("left", ["outliner"]);
            put("right", ["properties", "scene"]);
            put("bottom", [], true);
            break;
        case "results":
            put("left", ["outliner"], true);
            put("right", ["simulation", "properties"]);
            // The direct fix for "panels cover the 3D". Present but COLLAPSED: the table
            // is the thing you open when you want numbers, and defaulting it open would
            // spend 220px of viewport on an empty grid for every user who only wants to
            // look at a mode shape.
            put("bottom", ["fea-table"], true);
            break;
        case "build":
            put("left", ["outliner"], true);
            // Builder first: authoring is what this mode is for, and Properties reads
            // the selected cell beside it.
            put("right", ["cellbuilder", "properties"], false, 400);
            // The procedure graph is present but collapsed — it is a second authoring
            // surface, not something you want eating viewport height by default.
            put("bottom", ["node-editor"], true);
            break;
        case "data":
            put("left", ["storage"], false, 320);
            break;
        case "convert":
            // The converter is the mode, so it gets the room: a wide right dock rather
            // than the 300px default, because it is a form with a drop zone, a target
            // matrix and a job list stacked vertically.
            //
            // Files on the left too, read-only in practice — you convert a file you can
            // see, and having to switch modes to remember its name would be the same
            // dead end /convert had as a standalone page.
            put("left", ["storage"], false, 300);
            put("right", ["convert"], false, 520);
            put("bottom", [], true);
            break;
    }
    return base;
}

const defaultPerMode = (): Record<ModeId, ModeLayout> =>
    Object.fromEntries(MODE_IDS.map((m) => [m, defaultLayout(m)])) as Record<ModeId, ModeLayout>;

interface LayoutState {
    v: number;
    perMode: Record<ModeId, ModeLayout>;
    /** Named saved arrangements (Maya workspaces). */
    workspaces: Record<string, Record<ModeId, ModeLayout>>;

    setDockSize: (mode: ModeId, dock: DockedId, size: number) => void;
    toggleDock: (mode: ModeId, dock: DockedId, collapsed?: boolean) => void;
    /** Open a panel in a mode, in its preferred dock. No-op if already open there. */
    openPanel: (mode: ModeId, panel: PanelId, dock: DockId) => void;
    closePanel: (mode: ModeId, panel: PanelId) => void;
    togglePanel: (mode: ModeId, panel: PanelId, dock: DockId) => void;
    activateTab: (mode: ModeId, dock: DockedId, panel: PanelId) => void;
    setFloat: (mode: ModeId, panel: PanelId, rect: FloatState) => void;
    /** Move a docked panel out to the float layer, or back. */
    floatPanel: (mode: ModeId, panel: PanelId, rect: FloatState) => void;
    dockPanel: (mode: ModeId, panel: PanelId, dock: DockedId) => void;
    togglePin: (mode: ModeId, panel: PanelId) => void;
    setOverlay: (mode: ModeId, panel: PanelId, on: boolean) => void;
    resetMode: (mode: ModeId) => void;
    resetAll: () => void;
    saveWorkspace: (name: string) => void;
    loadWorkspace: (name: string) => void;
    deleteWorkspace: (name: string) => void;
}

/** Immutably edit one mode's layout. */
const editMode = (
    s: LayoutState,
    mode: ModeId,
    fn: (l: ModeLayout) => ModeLayout,
): Partial<LayoutState> => ({
    perMode: {...s.perMode, [mode]: fn(s.perMode[mode] ?? defaultLayout(mode))},
});

/** Remove a panel from every dock, the float layer and the overlay set. */
function removeEverywhere(l: ModeLayout, panel: PanelId): ModeLayout {
    const docks = {...l.docks};
    for (const id of Object.keys(docks) as DockedId[]) {
        const d = docks[id];
        if (!d.tabs.includes(panel)) continue;
        const tabs = d.tabs.filter((t) => t !== panel);
        docks[id] = {...d, tabs, active: d.active === panel ? (tabs[0] ?? null) : d.active};
    }
    const floats = {...l.floats};
    delete floats[panel];
    const overlays = {...l.overlays};
    delete overlays[panel];
    return {...l, docks, floats, overlays};
}

export const useLayoutStore = create<LayoutState>()(
    persist(
        (set) => ({
            v: LAYOUT_VERSION,
            perMode: defaultPerMode(),
            workspaces: {},

            setDockSize: (mode, dock, size) =>
                set((s) =>
                    editMode(s, mode, (l) => ({
                        ...l,
                        docks: {...l.docks, [dock]: {...l.docks[dock], size: clampSize(dock, size)}},
                    })),
                ),

            toggleDock: (mode, dock, collapsed) =>
                set((s) =>
                    editMode(s, mode, (l) => ({
                        ...l,
                        docks: {
                            ...l.docks,
                            [dock]: {...l.docks[dock], collapsed: collapsed ?? !l.docks[dock].collapsed},
                        },
                    })),
                ),

            openPanel: (mode, panel, dock) =>
                set((s) =>
                    editMode(s, mode, (l) => {
                        if (dock === "overlay") return {...l, overlays: {...l.overlays, [panel]: true}};
                        if (dock === "float") {
                            const cleared = removeEverywhere(l, panel);
                            return {
                                ...cleared,
                                floats: {...cleared.floats, [panel]: l.floats[panel] ?? {x: 120, y: 120, w: 380, h: 460}},
                            };
                        }
                        const cleared = removeEverywhere(l, panel);
                        const d = cleared.docks[dock];
                        return {
                            ...cleared,
                            docks: {...cleared.docks, [dock]: {...d, tabs: [...d.tabs, panel], active: panel, collapsed: false}},
                        };
                    }),
                ),

            closePanel: (mode, panel) => set((s) => editMode(s, mode, (l) => removeEverywhere(l, panel))),

            togglePanel: (mode, panel, dock) =>
                set((s) => {
                    const l = s.perMode[mode] ?? defaultLayout(mode);

                    // A panel sitting in a COLLAPSED dock is not visible, so "toggle"
                    // must mean reveal it — not remove it. Treating collapsed as open
                    // made the rail button look broken: the first click silently dropped
                    // a panel the user could not see, and only the second showed it.
                    const collapsedHost = (Object.keys(l.docks) as DockedId[]).find(
                        (d) => l.docks[d].collapsed && l.docks[d].tabs.includes(panel),
                    );
                    if (collapsedHost) {
                        return editMode(s, mode, (x) => ({
                            ...x,
                            docks: {
                                ...x.docks,
                                [collapsedHost]: {...x.docks[collapsedHost], collapsed: false, active: panel},
                            },
                        }));
                    }

                    const open =
                        Object.values(l.docks).some((d) => !d.collapsed && d.tabs.includes(panel)) ||
                        panel in l.floats ||
                        l.overlays[panel] === true;
                    if (open) return editMode(s, mode, (x) => removeEverywhere(x, panel));
                    // Reuse openPanel's placement rules rather than duplicating them.
                    return editMode(s, mode, (x) => {
                        if (dock === "overlay") return {...x, overlays: {...x.overlays, [panel]: true}};
                        if (dock === "float") {
                            return {...x, floats: {...x.floats, [panel]: x.floats[panel] ?? {x: 120, y: 120, w: 380, h: 460}}};
                        }
                        const d = x.docks[dock];
                        return {
                            ...x,
                            docks: {...x.docks, [dock]: {...d, tabs: [...d.tabs, panel], active: panel, collapsed: false}},
                        };
                    });
                }),

            activateTab: (mode, dock, panel) =>
                set((s) =>
                    editMode(s, mode, (l) => ({
                        ...l,
                        docks: {...l.docks, [dock]: {...l.docks[dock], active: panel, collapsed: false}},
                    })),
                ),

            setFloat: (mode, panel, rect) =>
                set((s) => editMode(s, mode, (l) => ({...l, floats: {...l.floats, [panel]: rect}}))),

            floatPanel: (mode, panel, rect) =>
                set((s) =>
                    editMode(s, mode, (l) => {
                        const cleared = removeEverywhere(l, panel);
                        return {...cleared, floats: {...cleared.floats, [panel]: rect}};
                    }),
                ),

            dockPanel: (mode, panel, dock) =>
                set((s) =>
                    editMode(s, mode, (l) => {
                        const cleared = removeEverywhere(l, panel);
                        const d = cleared.docks[dock];
                        return {
                            ...cleared,
                            docks: {...cleared.docks, [dock]: {...d, tabs: [...d.tabs, panel], active: panel, collapsed: false}},
                        };
                    }),
                ),

            togglePin: (mode, panel) =>
                set((s) =>
                    editMode(s, mode, (l) => ({
                        ...l,
                        pinned: l.pinned.includes(panel) ? l.pinned.filter((p) => p !== panel) : [...l.pinned, panel],
                    })),
                ),

            setOverlay: (mode, panel, on) =>
                set((s) =>
                    editMode(s, mode, (l) => {
                        const overlays = {...l.overlays};
                        if (on) overlays[panel] = true;
                        else delete overlays[panel];
                        return {...l, overlays};
                    }),
                ),

            resetMode: (mode) => set((s) => editMode(s, mode, () => defaultLayout(mode))),
            resetAll: () => set({perMode: defaultPerMode()}),

            saveWorkspace: (name) => set((s) => ({workspaces: {...s.workspaces, [name]: s.perMode}})),
            loadWorkspace: (name) =>
                set((s) => (s.workspaces[name] ? {perMode: s.workspaces[name]} : s)),
            deleteWorkspace: (name) =>
                set((s) => {
                    const workspaces = {...s.workspaces};
                    delete workspaces[name];
                    return {workspaces};
                }),
        }),
        {
            // v1 is reserved so the two pre-existing keys (ada:sim-docked-width,
            // ada-panel-theme) are untouched by this store.
            name: "ada:layout:v2",
            version: LAYOUT_VERSION,
            /**
             * Never try to migrate a layout blob.
             *
             * A stale layout referencing panels that no longer exist, or docks that
             * changed meaning, produces a broken workspace that looks like a bug in the
             * new code. A hard reset costs the user one re-arrange; a half-migrated
             * layout costs an afternoon of confusion. The shell shows a toast when this
             * fires.
             */
            migrate: () => ({
                v: LAYOUT_VERSION,
                perMode: defaultPerMode(),
                workspaces: {},
            }) as never,
            /**
             * Clamp on rehydrate: DOCK_LIMITS can tighten between releases, and a
             * persisted 900px left dock would then exceed its own maximum forever.
             */
            onRehydrateStorage: () => (state) => {
                if (!state) return;
                for (const mode of MODE_IDS) {
                    const l = state.perMode[mode];
                    if (!l) {
                        state.perMode[mode] = defaultLayout(mode);
                        continue;
                    }
                    for (const dock of Object.keys(l.docks) as DockedId[]) {
                        l.docks[dock].size = clampSize(dock, l.docks[dock].size);
                    }
                }
            },
        },
    ),
);

/** Read a mode's layout without subscribing (for tests and imperative callers). */
export const layoutFor = (mode: ModeId): ModeLayout =>
    useLayoutStore.getState().perMode[mode] ?? defaultLayout(mode);

export {defaultLayout, clampSize};
