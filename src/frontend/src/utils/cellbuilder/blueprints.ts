/**
 * Pure blueprint-selection reconciliation for the cellbuilder — no zustand, no
 * three.js, node-testable. Kept out of cellBuilderStore.ts so tests don't pull
 * the store's whole dependency graph.
 */

export interface BlueprintChoice {
    slug: string;
}

/**
 * Reconcile the selected blueprint against the list a (possibly newly-selected)
 * engine offers. Keeps the current selection when the engine still offers it;
 * otherwise falls back to the engine's default — the FIRST entry (the endpoint
 * returns them default-first) — or null when the engine advertises none. Used
 * on model open and whenever the compile engine changes.
 */
export function resolveSelectedBlueprint(
    blueprints: BlueprintChoice[],
    current: string | null,
): string | null {
    if (current && blueprints.some((b) => b.slug === current)) return current;
    return blueprints[0]?.slug ?? null;
}
