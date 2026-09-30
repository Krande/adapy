// What a bake of chosen steps left out, as one line for the result panel.
//
// A bake may hold some result cases only (``baked_steps`` in the manifest), or
// none at all (``baked_steps: []``: the model and its property fields). The
// step picker lists the steps a field HAS, so a case that was not baked simply
// is not there, and a bake of the model only offers the property fields and
// nothing else. Without a line saying so, both read as a deck without results.

import type { FeaManifest } from "../../../services/api/fea";

function caseList(values: number[], limit = 6): string {
  const shown = values.slice(0, limit).join(", ");
  return values.length > limit ? `${shown} and ${values.length - limit} more` : shown;
}

/** The sentence the result panel shows about unbaked cases, or null when every
 *  case the source offers is baked (or the bake is of every step). */
export function unbakedStepsNote(
  manifest: Pick<FeaManifest, "baked_steps" | "baked_steps_hint" | "result_cases"> | null | undefined,
): string | null {
  const baked = manifest?.baked_steps;
  if (!Array.isArray(baked)) return null;
  const hint = manifest?.baked_steps_hint?.trim();
  const tail = hint ? ` To bake a case: ${hint}` : "";
  if (baked.length === 0) {
    return `Result fields are not baked for any case: only the model is shown.${tail}`;
  }
  const have = new Set(baked.map(Number));
  const offered = (manifest?.result_cases ?? [])
    .map((c) => c?.n)
    .filter((n): n is number => typeof n === "number");
  const missing = offered.filter((n) => !have.has(n));
  if (offered.length > 0 && missing.length === 0) return null;
  const sorted = [...have].sort((a, b) => a - b);
  const of = offered.length ? ` of the ${offered.length} offered` : "";
  return `Result fields are baked for case${sorted.length === 1 ? "" : "s"} ${caseList(sorted)} only${of}; other cases are not baked.${tail}`;
}
