// The Builder's compile defaults a UI shell may set: the engine and the blueprint a
// model gets when its document names none (a model just made in the Builder, a legacy
// doc). A doc that names its own keeps them, and so does a choice the user makes.
//
// Core's own defaults stay `adapy-default` and the engine's first blueprint; a shell
// that ships with a particular engine (one a tool registers in a local viewer, say)
// prefers it here, by what the catalogs offer, so a server without it falls back.

import type { ProceduralBlueprintOption, ProceduralEngineSummary } from "@/services/api/proceduralCatalog";

export interface BuilderDefaults {
  /** The engine for a doc that names none, from those offered; null for core's default. */
  engine?: (engines: readonly ProceduralEngineSummary[]) => string | null;
  /** The blueprint for a doc that names none, from those `engine` offers; null for core's
   *  (the engine's first). */
  blueprint?: (engine: string, blueprints: readonly ProceduralBlueprintOption[]) => string | null;
}

let current: BuilderDefaults | null = null;

/** Set (or with null, clear) the shell's defaults. */
export function setBuilderDefaults(defaults: BuilderDefaults | null): void {
  current = defaults;
}

/** The default engine among `engines`, or null for core's. An answer not offered counts as none. */
export function defaultEngine(engines: readonly ProceduralEngineSummary[]): string | null {
  const slug = current?.engine?.(engines) ?? null;
  return slug && engines.some((e) => e.slug === slug) ? slug : null;
}

/** The default blueprint among `blueprints` for `engine`, or null for core's. */
export function defaultBlueprint(engine: string, blueprints: readonly ProceduralBlueprintOption[]): string | null {
  const slug = current?.blueprint?.(engine, blueprints) ?? null;
  return slug && blueprints.some((b) => b.slug === slug) ? slug : null;
}
