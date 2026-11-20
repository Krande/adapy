// `ctx.scene.loadSavedGroup` (plugin API 1.33.0): the Scene panel's "Load into scene" for a saved
// group, asked by a plugin -- one whose view follows a saved group, such as one that draws it. The
// same load (`loadSavedGroup` in utils/groups/groupLoad.ts) and the same words (`groupLoadNote`), handed
// back as plain data.
//
// The live environment (the scope, the saved groups, the loader) is imported on use: the loader
// pulls three.js in, and this module is on the boot path and in plain-node tests.

import type { SceneBox, SceneGroupLoad, SceneGroupLoadOptions } from "./registry";
import type { GroupLoadOutcome } from "@/utils/groups/groupLoadRun";
import { groupLoadNote } from "@/utils/groups/groupLoadNote";
import { describeMember, type SavedGroup } from "@/utils/groups/savedGroups";
import { useSceneFrameStore } from "@/state/sceneFrameStore";
import { activeViewport } from "@/viewport/api/viewportApi";

/** What a plugin's group load needs of the viewer. */
export interface PluginGroupLoadEnv {
  /** The current scope as the saved groups are stored under it; null with none. */
  scope(): string | null;
  /** The saved group `id` of `scope`, null when it has none; throws when the groups cannot be read. */
  findGroup(scope: string, id: string): Promise<SavedGroup | null>;
  load(group: SavedGroup, scope: string, onStage?: (stage: string) => void): Promise<GroupLoadOutcome>;
  /** The box round what is selected now, in the scene's own coordinates; null when nothing draws. */
  selectionBounds(): SceneBox | null;
}

/** A world-space box in the scene's own coordinates: world = scene + offset (state/sceneFrameStore). */
export function sceneBoxFromWorld(
  world: { min: readonly number[]; max: readonly number[] } | null,
  offset: readonly number[] | null,
): SceneBox | null {
  if (!world) return null;
  const o = offset ?? [0, 0, 0];
  const at = (p: readonly number[]): [number, number, number] => [p[0] - o[0], p[1] - o[1], p[2] - o[2]];
  const box = { min: at(world.min), max: at(world.max) };
  return [...box.min, ...box.max].every(Number.isFinite) ? box : null;
}

/** A load's outcome as the plugin API hands it back. */
export function summariseGroupLoad(group: SavedGroup, out: GroupLoadOutcome, bounds: SceneBox | null = null): SceneGroupLoad {
  const note = groupLoadNote(out);
  return {
    groupId: group.id,
    groupName: group.name,
    loaded: out.loaded,
    already: out.already,
    hidden: out.hidden,
    failed: out.failed.map((f) => ({ source: f.source, members: f.members.map(describeMember), reason: f.reason })),
    selected: out.selection.ranges,
    notFound: out.selection.notFound.map(describeMember),
    framed: out.framed,
    bounds,
    text: note.text,
    tone: note.tone,
  };
}

/** Load the saved group `id` of the current scope into the scene, as the Scene panel's button does. */
export async function loadSavedGroupFor(
  env: PluginGroupLoadEnv,
  id: string,
  opts?: SceneGroupLoadOptions,
): Promise<SceneGroupLoad> {
  const scope = env.scope();
  if (!scope) throw new Error("loadSavedGroup: no scope is open, so there are no saved groups to load");
  const group = await env.findGroup(scope, id);
  if (!group) throw new Error(`loadSavedGroup: no saved group '${id}' in scope '${scope}'`);
  const onStage = opts?.onStage;
  const out = await env.load(group, scope, onStage ? (stage) => {
    try {
      onStage(stage);
    } catch (err) {
      console.error("[plugins] a loadSavedGroup onStage callback failed", err);
    }
  } : undefined);
  // The load ends by selecting the group's members: the box round the selection is the group's.
  let bounds: SceneBox | null = null;
  try {
    bounds = env.selectionBounds();
  } catch (err) {
    console.error("[plugins] loadSavedGroup: the group's bounds could not be read", err);
  }
  return summariseGroupLoad(group, out, bounds);
}

/** The viewer's own environment, the scope read by `scope` (the plugin context's scope store). */
export const liveGroupLoadEnv = (scope: () => string | null): PluginGroupLoadEnv => ({
  scope,
  async findGroup(scope, id) {
    const { useSavedGroupsStore } = await import("@/state/savedGroupsStore");
    const store = useSavedGroupsStore;
    const held = store.getState();
    const known = held.scope === scope ? held.groups.find((g) => g.id === id) : undefined;
    if (known) return known;
    // Not in the list the Scene panel holds (not read yet, or made since): read it again.
    await store.getState().load(scope);
    const now = store.getState();
    if (now.scope !== scope) return null;
    const found = now.groups.find((g) => g.id === id) ?? null;
    if (!found && now.error) throw new Error(`loadSavedGroup: ${now.error}`);
    return found;
  },
  async load(group, scope, onStage) {
    const [{ loadSavedGroup }, { realDeliveryDeps }, { getSingletonViewerStores }] = await Promise.all([
      import("@/utils/groups/groupLoad"),
      import("@/components/asset_browser/AssetsTab"),
      import("@/state/AdaViewerContext"),
    ]);
    const deps = realDeliveryDeps((name) => getSingletonViewerStores().useModelState.getState().loadedSourceNames.has(name));
    return loadSavedGroup(group, scope, deps, onStage);
  },
  selectionBounds() {
    return sceneBoxFromWorld(activeViewport()?.selectionBounds() ?? null, useSceneFrameStore.getState().offset);
  },
});
