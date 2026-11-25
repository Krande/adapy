// The sections of the Selected Object panel, as an ordered list of providers: each says when it
// applies to the selection and what it renders. Core's own sections are providers too (orders
// 0-60, in steps of ten; the builder's three at 70, 72 and 74), so a plugin's section slots between
// them by its `order`.
//
// Dependency-free (React only as a type), so it sits on the root `@/viewer-core` entry.

import type React from "react";

import { isGateOpen, noteGatedRegistration, subscribePluginAccess } from "./pluginAccess";

/** A node element links are asked for (`queryElementLinks`): an element key (`node:...`,
 *  `file:...#p:...`), a published node, or a member of a loaded model as a saved group keeps it
 *  (`{target, element, path}`). */
export type ElementNodeRef =
  | { readonly key: string }
  | { readonly collection: string; readonly node_id: string; readonly provider?: string }
  | {
      readonly target:
        | { readonly kind: "file"; readonly source_key: string }
        | {
            readonly kind: "node";
            readonly provider?: string;
            readonly collection: string;
            readonly subject: string;
            readonly node?: string | null;
            readonly revision?: string | null;
          };
      /** The row's name; null for the model's root row (the whole model). */
      readonly element: string | null;
      /** Row names from below the model's root row down to `element`. */
      readonly path?: readonly string[];
      /** The provider's own node id, when the tree carries it. */
      readonly element_id?: string | null;
    };

/** What is selected, for `match`. A builder selection (a cell, or a plugin's ref) wins over a
 *  mesh pick behind it. */
export interface SelectionSnapshot {
  kind: "none" | "mesh" | "cell" | "ref";
  /** Under a ref selection: the provider that showed it and its own key for the row
   *  (`registerBuilderRefs`). */
  refOwner?: string;
  refKey?: string;
  /** Under a builder selection: the structure and the space it belongs to, by name, so a
   *  section can place itself. With a procedural model open and nothing selected, `structure`
   *  is the active structure. */
  structure?: string;
  space?: string;
  /** The name the panel shows: the cell's under a cell selection, else the picked element's. */
  name: string | null;
  /** The picked element's name, under a cell selection too. */
  meshName: string | null;
  /** Draw ranges, or cells, selected. */
  count: number;
  /** The scene holds a loaded model or builder cells. */
  hasEntities: boolean;
  /** A procedural builder model is open, whatever is selected. */
  cellBuilderActive: boolean;
  /** The composed shell's active mode (`activeShellMode`); absent in a shell without modes. */
  shellMode?: string;
  /** The selection as the nodes it is -- each a member of a model loaded from storage, folded up to
   *  whole levels as a saved group folds it -- for the element links (`queryElementLinks`, the
   *  `useElementLinks` hook): what has been found, built and simulated under them. Absent when
   *  nothing of a stored model is selected, and in a pop-out window. */
  nodes?: readonly ElementNodeRef[];
  /** The saved group the selection is, while it is the selection Scene > Groups made of it. */
  group_id?: string;
}

export interface PropertyProvider {
  /** Unique; re-registering an id replaces the provider. */
  id: string;
  /** Ascending; ties keep core's sections first, then registration order. */
  order?: number;
  /** Whether it applies. Runs on every selection change: keep it cheap. */
  match(sel: SelectionSnapshot): boolean;
  /** Its section. Rendered inside its own error boundary. */
  render(sel: SelectionSnapshot): React.ReactNode;
  /** Names the provider in the error boundary's message. */
  owner?: string;
  /**
   * The scope-gated plugin this section belongs to (1.41.0): a gate id, as `PluginSpec.access` names
   * it (the plugin id for `"scope"`). The section is left out wherever an admin has not turned that
   * plugin on, for the open scope or for the user, whatever `match` says.
   */
  access?: string;
}

const providers = new Map<string, { spec: PropertyProvider; seq: number }>();
const listeners = new Set<() => void>();
let seq = 0;
let cache: PropertyProvider[] | null = null;

function changed(): void {
  cache = null;
  for (const l of [...listeners]) l();
}

// A gated section comes and goes with its plugin's switch: the panel asks again when that changes.
subscribePluginAccess(changed);

/** Registers `spec`; returns a function that removes it. */
export function registerPropertyProvider(spec: PropertyProvider): () => void {
  if (!spec.id) {
    console.warn("[properties] provider registered without an id -- ignored");
    return () => {};
  }
  const entry = { spec, seq: seq++ };
  if (spec.access) noteGatedRegistration();
  providers.set(spec.id, entry);
  changed();
  return () => {
    if (providers.get(spec.id) !== entry) return;
    providers.delete(spec.id);
    changed();
  };
}

/** Every registered provider (not core's own sections), in order. The same array until one changes. */
export function listPropertyProviders(): PropertyProvider[] {
  if (cache) return cache;
  cache = [...providers.values()]
    .sort((a, b) => (a.spec.order ?? 0) - (b.spec.order ?? 0) || a.seq - b.seq)
    .map((e) => e.spec);
  return cache;
}

/** Subscribes to registrations; returns the unsubscribe. */
export function subscribePropertyProviders(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => void listeners.delete(onChange);
}

/** `core` merged with `plugins` by order (core first on a tie), keeping those that match. A
 *  throwing `match` drops its provider for this pass. */
export function providersFor(
  sel: SelectionSnapshot,
  core: readonly PropertyProvider[],
  plugins: readonly PropertyProvider[] = listPropertyProviders(),
): PropertyProvider[] {
  const all = [...core.map((p, i) => ({ p, k: 0, i })), ...plugins.map((p, i) => ({ p, k: 1, i }))];
  all.sort((a, b) => (a.p.order ?? 0) - (b.p.order ?? 0) || a.k - b.k || a.i - b.i);
  const out: PropertyProvider[] = [];
  for (const { p } of all) {
    if (p.access && !isGateOpen(p.access)) continue;
    try {
      if (p.match(sel)) out.push(p);
    } catch (err) {
      console.warn(`[properties] provider "${p.id}" match() threw -- skipped`, err);
    }
  }
  return out;
}
