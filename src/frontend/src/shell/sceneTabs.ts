// Which tabs the Scene panel offers, and when.
//
// Pure and separate from SceneBody for the usual reason: the component imports panel
// sections that reach stores that reach a vite `?worker&inline` module only a bundler can
// resolve, so a test importing it cannot run. The rule is the part worth asserting.

export type SceneTab = "model" | "tools" | "clip" | "mesh" | "fem" | "joints";

export interface SceneTabDef {
    id: SceneTab;
    label: string;
    /** Needs its content to exist — FEM concepts, detailing joints. */
    ctx?: boolean;
    /** Shell modes that offer it. Absent means every mode. */
    modes?: string[];
}

export const TAB_META: SceneTabDef[] = [
    {id: "model", label: "Model"},
    {id: "tools", label: "Tools"},
    {id: "clip", label: "Clip"},
    // Mesh quality asks whether a discretisation is good enough to trust the analysis
    // built on it. That is Inspect and Results work. In Build you are authoring the
    // geometry the mesh will later be made FROM, so there is nothing to assess yet, and
    // Convert has no 3D view at all.
    {id: "mesh", label: "Mesh", modes: ["inspect", "results"]},
    {id: "fem", label: "FEM", ctx: true},
    {id: "joints", label: "Joints", ctx: true},
];

/** Tabs this mode offers, given what the loaded model actually carries. */
export function tabsForMode(
    all: SceneTabDef[],
    mode: string,
    ctxAvailable: Partial<Record<SceneTab, boolean>>,
): SceneTabDef[] {
    return all.filter((t) => {
        if (t.ctx && !ctxAvailable[t.id]) return false;
        if (t.modes && !t.modes.includes(mode)) return false;
        return true;
    });
}
