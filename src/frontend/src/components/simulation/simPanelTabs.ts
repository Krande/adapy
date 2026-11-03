// Which tab of the Simulation panel shows. Kept free of React and three.js so the
// rules can be tested.
//
// Built-in tabs: the result controls and the FE model (masses, supports,
// constraints, load scenarios, take-off). Plugin tabs and the phone legend tab
// sit beside them under their own ids.

export const RESULTS_TAB = "animation";
export const MODEL_TAB = "model";
export const LEGEND_TAB = "legend";

export const RESULTS_TAB_LABEL = "Results";
export const MODEL_TAB_LABEL = "Model";

/** The tab the user last picked, and the loaded model it was picked for. */
export interface RememberedSimTab {
    tab: string;
    key: string;
}

/** Is an FE model loaded: a mesh through the FEA path (with or without results),
 *  or a model carrying FE concepts. */
export function feModelLoaded(s: {manifest: unknown; femHasConcepts: boolean}): boolean {
    return s.manifest != null || s.femHasConcepts;
}

/** Does the FE-concepts store hold anything to show? */
export function hasFemConcepts(s: {
    masses: readonly unknown[];
    bcs: readonly unknown[];
    constraints: readonly unknown[];
    scenarios: readonly unknown[];
    equipment: readonly unknown[];
}): boolean {
    return (
        s.masses.length > 0 ||
        s.bcs.length > 0 ||
        s.constraints.length > 0 ||
        s.scenarios.length > 0 ||
        s.equipment.length > 0
    );
}

/** Identifies what is loaded, as far as the tab choice goes: the source, and
 *  whether it has results. A pick is remembered for the model it was made on. */
export function simTabKey(sourceName: string | null, hasResults: boolean): string {
    return `${sourceName ?? ""}|${hasResults ? "results" : "model"}`;
}

export function defaultSimTab(): string {
    return RESULTS_TAB;
}

/** The tab to show: the remembered pick while the same model is loaded and the
 *  tab is still there, else the default. */
export function resolveSimTab(
    remembered: RememberedSimTab | null,
    key: string,
    available: (tab: string) => boolean,
): string {
    if (remembered && remembered.key === key && available(remembered.tab)) return remembered.tab;
    return defaultSimTab();
}
