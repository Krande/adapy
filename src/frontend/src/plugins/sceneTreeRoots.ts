// Top-level rows a plugin adds to the Scene tree, after the loaded models' roots.
//
// Dependency-free (types only from the tree), so it sits on the root `@/viewer-core`
// entry. A provider owns its rows: it says when they change, and may take a selection
// that includes them instead of the tree's own handler (rows with no geometry behind
// them would otherwise clear the 3D selection).

import type { TreeNodeData } from "@/components/tree_view/CustomNode";

export interface SceneTreeRootProvider {
  /** Unique; re-registering an id replaces the provider. */
  id: string;
  /** Ascending; ties keep registration order. */
  order?: number;
  /** The rows, appended after the model roots. Return the same array while nothing changed. */
  roots(): readonly TreeNodeData[];
  /** Calls `onChange` whenever `roots()` would answer differently; returns the unsubscribe. */
  subscribe(onChange: () => void): () => void;
  /** Takes the selection (row ids) when it returns true; otherwise the tree's own handler runs. */
  onSelect?(ids: readonly string[]): boolean;
}

const providers = new Map<string, { spec: SceneTreeRootProvider; unsubscribe: () => void; seq: number }>();
const listeners = new Set<() => void>();
let seq = 0;
let cache: TreeNodeData[] | null = null;

const EMPTY: TreeNodeData[] = [];

function changed(): void {
  cache = null;
  for (const l of [...listeners]) l();
}

/** Registers `spec`; returns a function that removes it. */
export function registerSceneTreeRoots(spec: SceneTreeRootProvider): () => void {
  if (!spec.id) {
    console.warn("[scene-tree] root provider registered without an id -- ignored");
    return () => {};
  }
  providers.get(spec.id)?.unsubscribe();
  const entry = { spec, unsubscribe: spec.subscribe(changed), seq: seq++ };
  providers.set(spec.id, entry);
  changed();
  return () => {
    if (providers.get(spec.id) !== entry) return;
    entry.unsubscribe();
    providers.delete(spec.id);
    changed();
  };
}

/** Every registered provider, in order. */
export function listSceneTreeRoots(): SceneTreeRootProvider[] {
  return [...providers.values()]
    .sort((a, b) => (a.spec.order ?? 0) - (b.spec.order ?? 0) || a.seq - b.seq)
    .map((e) => e.spec);
}

/** Every provider's rows, concatenated in order. The same array until something changes. */
export function sceneTreeContributedRoots(): readonly TreeNodeData[] {
  if (cache) return cache;
  const out: TreeNodeData[] = [];
  for (const p of listSceneTreeRoots()) {
    try {
      out.push(...p.roots());
    } catch (err) {
      console.warn(`[scene-tree] root provider "${p.id}" roots() threw -- skipped`, err);
    }
  }
  cache = out.length ? out : EMPTY;
  return cache;
}

/** Subscribes to any change of the contributed rows; returns the unsubscribe. */
export function subscribeSceneTreeRoots(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => void listeners.delete(onChange);
}

/** Offers a selection to the providers in order; true when one took it. */
export function claimSceneTreeSelection(ids: readonly string[]): boolean {
  for (const p of listSceneTreeRoots()) {
    try {
      if (p.onSelect?.(ids)) return true;
    } catch (err) {
      console.warn(`[scene-tree] root provider "${p.id}" onSelect() threw -- skipped`, err);
    }
  }
  return false;
}
