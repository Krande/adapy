// What a selection export is saved as. The server names the file after the selection (its cache key
// carries that name); the name the browser saves it under is composed here at download time, from
// the export options' prefixes -- so changing a prefix never makes the server write the file again.
//
// No spaces, nothing a file system could read as a path: letters, digits, `.`, `_` and `-`, with any
// run of other characters folded to one `_`.

/** `s` as a file-name part: unsafe runs become `_`, no leading or trailing separators. */
export function safeFileNamePart(s: string): string {
    return s
        .trim()
        .replace(/[^A-Za-z0-9._-]+/g, "_")
        .replace(/_{2,}/g, "_")
        .replace(/^[._-]+|[._-]+$/g, "");
}

export interface ExportNameParts {
    /** The name the server gave the file, extension included. */
    readonly serverName: string;
    /** The project the selection is from, when one is known and its prefix is on. */
    readonly project?: string | null;
    /** A prefix the user typed; empty for none. */
    readonly customPrefix?: string;
    /** Keep the selection's own name (default); without it the prefixes alone name the file. */
    readonly includeName?: boolean;
}

/** `[project]_[custom prefix]_[name].ext`, each part made safe, empty parts left out. With nothing
 *  left -- no name and no prefix -- the file is called `export`. */
export function exportFileName({serverName, project, customPrefix, includeName = true}: ExportNameParts): string {
    const dot = serverName.lastIndexOf(".");
    const stem = dot > 0 ? serverName.slice(0, dot) : serverName;
    const ext = dot > 0 ? serverName.slice(dot) : "";
    const parts = [project ?? "", customPrefix ?? "", includeName ? stem : ""].map(safeFileNamePart).filter(Boolean);
    return `${parts.join("_") || (includeName ? "selection" : "export")}${ext}`;
}

/** A group's name: its one member's, or the first member's with how many more. */
export function groupExportLabel(names: readonly string[]): string {
    const first = names.find((n) => n.trim()) ?? "selection";
    return names.length > 1 ? `${first}_and_${names.length - 1}_more` : first;
}
