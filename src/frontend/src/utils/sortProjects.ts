// The one order for every UI list of projects or collections a user picks from or scans:
// alphabetical, case-insensitive, locale-aware, with digit runs compared as numbers ("P2" < "P10").
// Sort at the point of display, never in an API client, so what goes back to the server keeps its
// own order. Lists whose order means something (run history by time, results by relevance) are
// not project lists in this sense and keep their order.

// A fixed locale, not the browser's: codes are not words, and a locale's tailoring would move them
// (Norwegian and Danish sort "AA" as "Å", after "Z", so a project "AAB" would land last there).
const collator = new Intl.Collator("en", {sensitivity: "base", numeric: true});

/** Comparator for project / collection codes or names. Ties fall back to code-unit order so the
 *  result is stable and total ("aaa" vs "AAA" always comes out the same way). */
export function compareProjects(a: string, b: string): number {
    return collator.compare(a, b) || (a < b ? -1 : a > b ? 1 : 0);
}

/** A new array of `names`, sorted with {@link compareProjects}. The input is not touched. */
export function sortProjects<T extends string>(names: readonly T[]): T[] {
    return [...names].sort(compareProjects);
}

/** A new array of `items`, sorted by the project / collection name `key` picks out. */
export function sortProjectsBy<T>(items: readonly T[], key: (item: T) => string): T[] {
    return [...items].sort((a, b) => compareProjects(key(a), key(b)));
}

/** A new array where the items `isProject` picks are sorted by `key` among the positions they
 *  already hold, and every other item stays exactly where it is. For lists that put fixed entries
 *  first by design ("Personal", "Shared", a default) and the projects after them. */
export function sortProjectsAmong<T>(
    items: readonly T[],
    isProject: (item: T) => boolean,
    key: (item: T) => string,
): T[] {
    const sorted = sortProjectsBy(items.filter(isProject), key);
    let next = 0;
    return items.map((item) => (isProject(item) ? sorted[next++] : item));
}
