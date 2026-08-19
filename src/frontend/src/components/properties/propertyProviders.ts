import type React from "react";

// The Properties registry — one panel that follows selection, in every mode.
//
// Replaces the "N bespoke info boxes" problem: today a selection's detail is split
// across ObjectInfoBoxComponent (name + actions), ObjectMetadataPanel (server/lineage
// metadata) and CellBuilderSelectionInfo (1017 lines of procedural-cell detail), each
// deciding for itself whether it should appear. Adding a new selectable kind meant
// adding another box and another visibility flag.
//
// Now a provider declares WHEN it applies and WHAT it renders, and the panel composes
// whatever matches. That is also the real replacement for the plugin framework's
// `scene-info` region, which was declared in Phase 1 and never wired: a plugin can
// register a provider and have its detail appear inline with core's, in selection
// order, without core knowing the plugin exists.
//
// Providers render components that read their own stores. That is deliberate — it means
// the existing panels move in VERBATIM rather than being re-plumbed through a props
// interface, which is what keeps a 1000-line procedural inspector from being rewritten
// as a side effect of a layout change.

/**
 * What is selected right now.
 *
 * A summary for `match` predicates, not a replacement for the stores. `cell` wins over
 * `mesh` because a click that lands on a builder cell should read as that cell, not as
 * whatever result geometry sits behind it — the same precedence ObjectInfoBox already
 * applies.
 */
export interface SelectionSnapshot {
    kind: "none" | "mesh" | "cell";
    /** Primary display name, or null when nothing is selected. */
    name: string | null;
    /** How many things are selected — draw ranges, or cells. */
    count: number;
    /** Whether the scene holds anything at all (a loaded model or builder cells).
     *  Scene-wide recovery actions stay available with nothing selected, so hiding
     *  your last selection does not strand what you hid. */
    hasEntities: boolean;
    /** A cellbuilder model is open, regardless of what is selected. */
    cellBuilderActive: boolean;
}

export interface PropertyProvider {
    /** Unique; namespaced as `${pluginId}:${id}` when contributed by a plugin. */
    id: string;
    /** Ascending. Core uses 0/10/20 so a plugin can slot between without renumbering. */
    order?: number;
    /**
     * Does this provider apply to the current selection?
     *
     * Keep it cheap — it runs on every selection change. A provider may ALSO gate
     * internally (several core panels already `return null` when irrelevant); matching
     * here is what lets the panel show an honest empty state when nothing applies.
     */
    match: (sel: SelectionSnapshot) => boolean;
    /** Rendered inside the panel body. Components read their own stores. */
    render: () => React.ReactNode;
    /** Set for plugin-contributed providers; used for error attribution. */
    owner?: string;
}

const providers = new Map<string, PropertyProvider>();

/**
 * Register a provider. Re-registering the same id replaces it, so hot-reload during
 * development does not accumulate duplicates.
 */
export function registerPropertyProvider(p: PropertyProvider): void {
    if (!p.id) {
        console.warn("[properties] provider registered without an id — ignored");
        return;
    }
    providers.set(p.id, p);
}

export function unregisterPropertyProvider(id: string): void {
    providers.delete(id);
}

/** Every registered provider, in render order. */
export function allProviders(): PropertyProvider[] {
    return [...providers.values()].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}

/**
 * Providers that apply to a selection, in render order.
 *
 * A throwing `match` disables that provider for this pass rather than blanking the
 * panel — the same failure-isolation discipline the plugin registry uses.
 */
export function providersFor(sel: SelectionSnapshot): PropertyProvider[] {
    const out: PropertyProvider[] = [];
    for (const p of allProviders()) {
        try {
            if (p.match(sel)) out.push(p);
        } catch (err) {
            console.warn(`[properties] provider "${p.id}" match() threw — skipped`, err);
        }
    }
    return out;
}

/** Test seam. Not for production use. */
export function _resetPropertyProviders(): void {
    providers.clear();
}
