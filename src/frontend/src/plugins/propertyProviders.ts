// The sections of the Selected Object panel, as an ordered list of providers: each says when it
// applies to the selection and what it renders. Core's own sections are providers too (orders
// 0-70, in steps of ten), so a plugin's section slots between them by its `order`.
//
// Dependency-free (React only as a type), so it sits on the root `@/viewer-core` entry.

import type React from "react";

/** What is selected, for `match`. A builder-cell selection wins over a mesh pick behind it. */
export interface SelectionSnapshot {
  kind: "none" | "mesh" | "cell";
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
}

const providers = new Map<string, { spec: PropertyProvider; seq: number }>();
const listeners = new Set<() => void>();
let seq = 0;
let cache: PropertyProvider[] | null = null;

function changed(): void {
  cache = null;
  for (const l of [...listeners]) l();
}

/** Registers `spec`; returns a function that removes it. */
export function registerPropertyProvider(spec: PropertyProvider): () => void {
  if (!spec.id) {
    console.warn("[properties] provider registered without an id -- ignored");
    return () => {};
  }
  const entry = { spec, seq: seq++ };
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
    try {
      if (p.match(sel)) out.push(p);
    } catch (err) {
      console.warn(`[properties] provider "${p.id}" match() threw -- skipped`, err);
    }
  }
  return out;
}
