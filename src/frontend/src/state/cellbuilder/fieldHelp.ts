// What a builder doc's columns mean, for the help of the fields that edit them. The engine catalog
// carries it per engine (`ProceduralEngineSummary.field_help`, served by /procedural-engines):
// adapy's own entity descriptions under the engine's text, which wins per column. Keyed by the doc
// list and the column as the doc's rows name them ("spaces" / "GRID_X_CREATE"); "structures" and
// "supports" for the rows that are not a doc list. Engine-agnostic: core names no engine's columns.

import type { ProceduralEngineSummary } from "@/services/viewerApi";
import { useCellBuilderStore } from "@/state/cellBuilderStore";

/** `{list: {COLUMN: text}}`, as an engine summary carries it. */
export type FieldHelp = Readonly<Record<string, Readonly<Record<string, string>>>>;

/** The field help of engine `slug` among `engines` (empty when it has none, or is not listed). */
export function engineFieldHelp(engines: readonly Pick<ProceduralEngineSummary, "slug" | "field_help">[], slug: string): FieldHelp {
  return engines.find((e) => e.slug === slug)?.field_help ?? {};
}

/** The help text of `column` in doc list `list`, or undefined: plain text, newlines kept. */
export function fieldHelpText(help: FieldHelp, list: string, column: string): string | undefined {
  const text = help[list]?.[column];
  return typeof text === "string" && text.trim() ? text : undefined;
}

/** The help of `column` in `list` for the engine the builder compiles with now. */
export function fieldHelp(list: string, column: string): string | undefined {
  const st = useCellBuilderStore.getState();
  return fieldHelpText(engineFieldHelp(st.engines, st.selectedEngine), list, column);
}

/** `fieldHelp`, following the engine catalog and the engine picked. */
export function useFieldHelp(list: string, column: string): string | undefined {
  return useCellBuilderStore((st) => fieldHelpText(engineFieldHelp(st.engines, st.selectedEngine), list, column));
}
