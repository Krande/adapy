// Shell profiles — one AppShell, five shapes.
//
// Replaces the if-chain in app.tsx that branched into five disjoint top-level apps
// (/convert, /admin, ?simfollow=, NODE_EDITOR_ONLY, and the viewer). Those pages were
// separate because they must NOT spin up the 3D scene, the websocket or the tree; that
// constraint survives here as `canvas: false` rather than as five copies of the layout.

export const PROFILE_IDS = ["viewer", "page", "window", "graph", "embed"] as const;
export type ProfileId = (typeof PROFILE_IDS)[number];

export interface ShellProfile {
    id: ProfileId;
    /**
     * Mount the 3D viewport (and with it the websocket, the model stores and the
     * headless controllers).
     *
     * HARD REQUIREMENT for `page`: /convert and /admin must never import ThreeCanvas.
     * That is precisely why they are separate routes today, and the chunk-split hosted
     * build relies on it to keep three.js out of their entry path.
     */
    canvas: boolean;
    /** Show the mode switcher. A single-purpose window has nothing to switch between. */
    modeSwitcher: boolean;
    /** Show the left tool rail. */
    toolRail: boolean;
    /** Show the docked regions. */
    docks: boolean;
    /** Show the status bar. */
    statusBar: boolean;
}

export const SHELL_PROFILES: Record<ProfileId, ShellProfile> = {
    // The full application.
    viewer: {id: "viewer", canvas: true, modeSwitcher: true, toolRail: true, docks: true, statusBar: true},
    // A canvas-less full-window workspace: /convert, /admin. Keeps the title bar so
    // there is a way back to the viewer — the current standalone pages are a dead end.
    page: {id: "page", canvas: false, modeSwitcher: false, toolRail: false, docks: true, statusBar: true},
    // A popped-out single panel (?simfollow=). Title bar + one dock, nothing else.
    window: {id: "window", canvas: false, modeSwitcher: false, toolRail: false, docks: true, statusBar: false},
    // NODE_EDITOR_ONLY: the viewport region hosts ReactFlow instead of three.js.
    graph: {id: "graph", canvas: false, modeSwitcher: false, toolRail: true, docks: true, statusBar: true},
    // Jupyter / paradoc. Canvas plus optional panels, no chrome of our own — the host
    // page owns the surrounding layout.
    embed: {id: "embed", canvas: true, modeSwitcher: false, toolRail: false, docks: false, statusBar: false},
};

export const profileDef = (id: ProfileId): ShellProfile => SHELL_PROFILES[id] ?? SHELL_PROFILES.viewer;
