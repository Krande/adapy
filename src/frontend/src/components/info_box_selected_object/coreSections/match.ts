// Core's own `PropertyProvider.match` functions -- pure, no JSX, no React -- one per section id
// (`index.ts`'s CORE_PROPERTY_PROVIDERS), plus the few shared predicates more than one of them
// reuses. Split out so the rule a section applies under is readable, and testable
// (coreSectionMatch.test.ts), without pulling in every section's render code.

import type {SelectionSnapshot} from "@/plugins/propertyProviders";

/** Not a builder pick (a cell or a plugin's ref). */
export const notCell = (sel: SelectionSnapshot): boolean => sel.kind !== "cell" && sel.kind !== "ref";

/** A scene (GLB) pick's own sections: not a builder pick, and not shown in a "builder"-only panel.
 *  These read their OWN stores (the FEM/clash/tree-node pick, never cleared on a mode switch), so
 *  `kind` alone is not enough -- a stale GLB pick from another mode would still show here through
 *  a "builder" panel that currently has nothing selected (`kind: "none"`). */
export const sceneConceptPick = (sel: SelectionSnapshot): boolean => notCell(sel) && sel.selectionSource !== "builder";

/** A builder pick (a cell, or a plugin's ref) with a procedural model open. */
export const builderPick = (sel: SelectionSnapshot): boolean => sel.cellBuilderActive && (sel.kind === "cell" || sel.kind === "ref");

/** A mesh pick's own element id, e.g. an FEA element's `E123`. */
const isFeaElement = (sel: SelectionSnapshot): boolean => sel.kind === "mesh" && /^E\d+$/.test(sel.meshName ?? "");

/** Core's `match` functions, keyed by provider id (`index.ts`'s CORE_PROPERTY_PROVIDERS, in the
 *  same order). Kept as a lookup (rather than only inline on each provider) so a table-driven test
 *  can run every rule against a snapshot without rendering anything. */
export const MATCH: Readonly<Record<string, (sel: SelectionSnapshot) => boolean>> = {
  // The name and the actions. Also with nothing selected while the scene holds anything:
  // Unhide all / Fit all must stay reachable after the last selection was hidden.
  "selection-summary": (sel) => sel.kind !== "none" || sel.hasEntities,
  // The clash contact last pointed at in the 3D view (its marker or patch clicked): what the
  // clash result holds of it, and a link to it in Connections > Review.
  // `sceneConceptPick`, not "always": its pick store is its own and never cleared on a mode
  // switch, so without this gate a stale contact from another mode (e.g. a scene pick made in
  // Output) would show here while a builder item is now the real pick -- or, with
  // selectionSource="builder" and nothing picked there, over the "none" fallback that belongs to
  // the active structure instead. In a single mode with no selectionSource override this changes
  // nothing real: `kind` is only ever "none" when the pick that would set it is, so the stores
  // these sections read are already empty too.
  "clash-contact": sceneConceptPick,
  // Selected FEM concepts (masses, supports, loads, equipment): drawn on the FEM overlay, not tree
  // rows, so they have their own section. Same `sceneConceptPick` gate and the same reason: its
  // own pick store is never cleared on a mode switch either.
  "fem-concepts": sceneConceptPick,
  // Where an element of an exported file came from, and a jump to that node in the Sources tree
  // (switching scope when it lives in another). Any loaded model whose file has a provenance
  // record; nothing for others. Above Properties, which starts folded.
  "source-jump": notCell,
  // Renders for any picked element -- even without a server-side jsonData payload -- because it
  // also reads metadata from the lineage store (GLB extension's ``object_metadata`` field) AND
  // hosts the clicked-coordinate row.
  "object-metadata": (sel) => sel.meshName != null,
  // A picked FEA element's own data (type, nodes, section, thickness, material) in the flat
  // layout, with an ELEMENT badge; the classic panel has the same in its folded Properties rows.
  "fea-element": isFeaElement,
  // A picked element of the loaded FEA result: its property values and the element sets naming
  // it, each isolatable. Nothing for a pick no set or property names.
  "fea-sets": isFeaElement,
  // The selected row's attributes from the attribute index; nothing when it has none.
  "indexed-attributes": notCell,
  // Its system, run and related elements from the relations index; nothing without.
  related: notCell,
  // What has been found, built and simulated under the selection, from the element links: its
  // contacts, connections, welds, subjects and simulation runs (each run's status, open / replay
  // through its provider's own UI), stale and orphaned links flagged. Nothing without links.
  "element-links": (sel) => notCell(sel) && !!sel.nodes?.length,
  // Structural facts about the selected hierarchy node. Renders for any tree selection, and is the
  // ONLY section a spatial container can populate -- everything above is keyed on geometry the
  // container does not have. `sceneConceptPick`, same reason as clash-contact above: its own
  // store (selectedNodeId) is never cleared on a mode switch.
  "tree-node": sceneConceptPick,
  // Procedural cellbuilder selection detail: the picked item as one layout -- where it is (its
  // space and structure), what it is, its box and its fields, or a plugin's ref.
  "cellbuilder-item": builderPick,
  // A structure on its own (the active one, with nothing else picked).
  "cellbuilder-structure": (sel) => sel.cellBuilderActive && sel.kind === "none" && !!sel.structure,
};
