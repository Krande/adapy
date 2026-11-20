// When the storage browser says "No files yet": nothing to list at all. The list is the scope's
// files AND its procedural models (database rows drawn as entries of the tree), so a scope with a
// model but no files is not empty -- it lists the model.

export function storageListEmpty(p: {
    /** The scope's stored files. */
    files: readonly unknown[];
    /** What the tree lists: the files that are rows of their own, and the procedural models. */
    rows: readonly unknown[];
    /** Folders made in this session that have nothing in them yet. */
    pendingFolders: readonly unknown[];
    /** A folder being named right now (null: none). */
    newFolderAt: string | null;
}): boolean {
    return p.files.length === 0 && p.rows.length === 0 && p.pendingFolders.length === 0 && p.newFolderAt === null;
}
