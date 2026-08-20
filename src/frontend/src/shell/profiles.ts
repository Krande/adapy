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
    /**
     * Show the application menu bar and the command palette.
     *
     * Off for the single-purpose pages. Nearly every command acts on the 3D scene, the
     * selection or the layout — none of which a /convert or /admin page has — so a full
     * menu there would be six titles of greyed-out entries. Worse, those pages mount
     * outside AdaViewerProvider on purpose, so a command that reached for viewer state
     * would not merely no-op.
     */
    menus: boolean;
}

export const SHELL_PROFILES: Record<ProfileId, ShellProfile> = {
    // The full application.
    viewer: {id: "viewer", canvas: true, modeSwitcher: true, toolRail: true, docks: true, statusBar: true, menus: true},
    // A canvas-less full-window workspace: /convert, /admin. Keeps a reduced title bar,
    // which is the entire point — these routes were a dead end with no way back.
    //
    // docks: false. The dock hosts render whatever the persisted layout says the current
    // mode has open, which on these routes would be viewer panels (Outliner, Properties)
    // reaching for a scene that was deliberately never mounted. The page fills the
    // viewport track via viewportOverride instead.
    page: {id: "page", canvas: false, modeSwitcher: false, toolRail: false, docks: false, statusBar: true, menus: false},
    // A popped-out single panel (?simfollow=). Title bar + one dock, nothing else.
    window: {id: "window", canvas: false, modeSwitcher: false, toolRail: false, docks: true, statusBar: false, menus: false},
    // NODE_EDITOR_ONLY: the viewport region hosts ReactFlow instead of three.js.
    graph: {id: "graph", canvas: false, modeSwitcher: false, toolRail: true, docks: true, statusBar: true, menus: true},
    // Jupyter / paradoc. Canvas plus optional panels, no chrome of our own — the host
    // page owns the surrounding layout.
    embed: {id: "embed", canvas: true, modeSwitcher: false, toolRail: false, docks: false, statusBar: false, menus: false},
};

export const profileDef = (id: ProfileId): ShellProfile => SHELL_PROFILES[id] ?? SHELL_PROFILES.viewer;
