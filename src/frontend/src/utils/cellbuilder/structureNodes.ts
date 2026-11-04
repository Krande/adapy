/**
 * Where a cellbuilder GROUP's compiled geometry is in the Scene tree.
 *
 * A grouping engine (pm-engine) builds one structure per group and places each
 * as a part named after the group directly under the assembly root, and the
 * GLB's `id_hierarchy` keeps that level -- so in the Scene tree a compiled
 * result's root row has one child row per structure, named exactly as the group.
 * Finding a group's geometry is therefore a name lookup under the root row the
 * result was loaded as (`source_name` = the result's scene source).
 *
 * Pure (no store, no scene), so it is node-testable; the scene side lives in
 * `groupView.ts`.
 */

export interface StructureTreeNode {
  id: string;
  name: string;
  children: StructureTreeNode[];
  source_name?: string | null;
}

/** The root rows loaded under `sourceName`, from the Scene tree's container (or a
 * single root). A reload can briefly leave two; the newest is last. */
export function rootsForSource<T extends StructureTreeNode>(
  tree: T | null | undefined,
  sourceName: string,
): T[] {
  if (!tree) return [];
  const roots = (tree.id === "__roots__" ? tree.children : [tree]) as T[];
  return roots.filter((r) => r.source_name === sourceName);
}

/** The row for structure `name` under `root`: the shallowest row of that name
 * below the root (breadth-first), so a structure is found at its own level even
 * when something deeper inside another structure happens to share its name.
 * Null when the result has no such structure (e.g. the group was excluded). */
export function findStructureNode<T extends StructureTreeNode>(root: T, name: string): T | null {
  let level = [...root.children] as T[];
  while (level.length) {
    const hit = level.find((n) => n.name === name);
    if (hit) return hit;
    level = level.flatMap((n) => n.children as T[]);
  }
  return null;
}
