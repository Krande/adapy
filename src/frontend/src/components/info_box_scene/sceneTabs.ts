// The Scene panel's tabs and how the store's `mode` maps onto them. Kept apart
// from the component so the mapping can be tested.

import type {SceneInfoMode} from "@/state/sceneInfoStore";

export type SceneTab = "model" | "overlays" | "tools" | "clip" | "mesh" | "clashes" | "joints";

// "Clashes" sits immediately before "Joints", on purpose: identifying joints and reviewing them
// are two ends of one workflow (`notes_core_asset_browser.md` §Decision 10's naming paragraph),
// never a second joints table under a different tab. "Clashes" is NOT contextual -- unlike
// Joints it needs no baked concepts to be worth showing: it runs against whatever source is
// loaded and says plainly when that source has no members to check.
export const TAB_META: {id: SceneTab; label: string; ctx?: boolean}[] = [
    {id: "model", label: "Model"},
    {id: "overlays", label: "Overlays"},
    {id: "tools", label: "Tools"},
    {id: "clip", label: "Clip"},
    {id: "mesh", label: "Mesh"},
    {id: "clashes", label: "Clashes"},
    {id: "joints", label: "Joints", ctx: true},
];

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
    clashes: "clashes",
    joints: "joints",
};
