import type {SelectionSnapshot} from "./propertyProviders";

// WHEN each core provider applies, separated from WHAT it renders.
//
// Split out because the render side transitively imports the whole viewer — the
// cellbuilder panel reaches cellBuilderStore, which reaches a vite `?worker&inline`
// module that only a bundler can resolve. Keeping the rules here means the composition
// logic is testable under plain `node --test`, which is the half most likely to be got
// wrong: whether an empty selection still offers scene-wide recovery is a real decision,
// while "does it render a div" is not.
//
// Orders leave gaps of ten so a plugin can slot between core entries without renumbering.

export interface CoreProviderRule {
    id: string;
    order: number;
    match: (sel: SelectionSnapshot) => boolean;
}

export const CORE_PROVIDER_RULES: CoreProviderRule[] = [
    {
        id: "selection-summary",
        order: 0,
        // Also matches an EMPTY selection while the scene holds entities: scene-wide
        // recovery (Unhide all / Fit all) must stay reachable after you hide your last
        // selection, or you have hidden something with no way to get it back.
        match: (sel) => sel.kind !== "none" || sel.hasEntities,
    },
    {
        id: "object-metadata",
        order: 10,
        // Any named mesh selection, even with no server payload — it also reads the
        // lineage store's GLB `object_metadata` and hosts the clicked-coordinate row.
        match: (sel) => sel.kind === "mesh" && sel.name != null,
    },
    {
        id: "cellbuilder-cell",
        order: 20,
        // Mirrors the component's own internal gate (`!active || !selection` → null), so
        // the registry knows when it applies rather than mounting it to render nothing.
        match: (sel) => sel.kind === "cell",
    },
];

export const ruleFor = (id: string): CoreProviderRule | undefined =>
    CORE_PROVIDER_RULES.find((r) => r.id === id);
