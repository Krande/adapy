// Which loaded models a mode's LISTS show.
//
// The scene is untouched: every model stays loaded, visible and selectable in the 3D view
// in every mode. This filters the Outliner's roots only.
//
// That distinction is the whole design. Filtering the scene would mean a model silently
// vanishing when you switch mode, with the reason off-screen — "my model disappeared and
// I do not know why" is a worse problem than the one being solved. It would also break
// the non-modality contract in modeStore.ts, which says switching mode changes what is
// OFFERED, never what is loaded or visible.
//
// Filtering the list keeps that contract and still gets the benefit: in Results you see
// the result sets, not the eight geometry files you happen to have open.
//
// Pure and separate so the classification is testable — it reaches no store, and the
// component supplies the context.

export type ModelKind = "procedural" | "result" | "geometry";

export interface ClassifyContext {
    /** Name of the open procedural model, if any. */
    proceduralName: string | null;
    /** Does this name look like an FEA result? Injected so the rule stays pure. */
    isResult: (name: string) => boolean;
}

/**
 * What kind of thing a top-level Outliner root is.
 *
 * Procedural wins over result: a compiled procedural model can carry results, and while
 * you are building it the fact that it is YOUR model matters more than that it has been
 * analysed.
 */
export function classifyRoot(name: string, ctx: ClassifyContext): ModelKind {
    const bare = name.replace(/^\/+/, "");
    if (ctx.proceduralName && bare.includes(ctx.proceduralName)) return "procedural";
    if (ctx.isResult(bare)) return "result";
    return "geometry";
}

/**
 * Which kinds a mode lists.
 *
 * Inspect lists everything on purpose: it is the base state, and "the model on its own"
 * means all of it. Convert has no 3D view at all, so its outliner is moot — it lists
 * everything rather than inventing a rule for a list nobody sees.
 */
export function kindsForMode(mode: string): ModelKind[] | "all" {
    switch (mode) {
        case "build":
            return ["procedural"];
        case "results":
            return ["result"];
        default:
            return "all";
    }
}

export interface FilterResult<T> {
    shown: T[];
    /** How many roots the mode filtered out. Surfaced in the UI — a list that quietly
     *  drops rows is indistinguishable from a list that failed to load. */
    hidden: number;
}

export function filterRoots<T>(
    roots: T[],
    nameOf: (root: T) => string,
    mode: string,
    ctx: ClassifyContext,
    showAll = false,
): FilterResult<T> {
    const kinds = kindsForMode(mode);
    if (showAll || kinds === "all") return {shown: roots, hidden: 0};

    const shown = roots.filter((r) => kinds.includes(classifyRoot(nameOf(r), ctx)));

    // Never filter down to nothing. An empty Outliner in Results, when models ARE loaded,
    // reads as "the tree is broken" — and the mode filter is a convenience, not a rule
    // worth enforcing against the only thing you have open.
    if (shown.length === 0) return {shown: roots, hidden: 0};

    return {shown, hidden: roots.length - shown.length};
}
