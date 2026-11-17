/**
 * The colour of a structural BLUEPRINT -- what a space (cell) is drawn in, in the
 * scene and on its Model-panel row: a cell in a structure takes its structure's
 * blueprint colour, so two structures built differently read apart at a glance.
 *
 * The blueprint slug is the engine's (an engine that reads a workbook advertises
 * its Structures sheet's BLUEPRINT strings). The named ones keep fixed colours, chosen apart from
 * the builder's other colours (equipment orange, opening red, loft teal, hover
 * yellow, selection pink) and from one another along the blue-yellow axis, which
 * every common colour-vision deficiency keeps:
 *
 *   Stiffened plate      blue    #3b82f6   (the colour every cell had)
 *   Framework            violet  #a78bfa
 *   Framework with deck  lime    #a3e635
 *
 * Any other blueprint takes a stable colour from a short extra palette by a hash
 * of its slug. A cell in no structure, or in a structure naming no blueprint,
 * keeps the plain cell blue. "Colour by group" (display only, groupViewStore)
 * wins over all of this when it is on.
 *
 * Pure: no store, no three.js. Display only -- never part of the document.
 */

import type {CellGroup} from "./groups";

/** The plain cell colour (and the Stiffened plate's). */
export const CELL_BLUE = "#3b82f6";

export const BLUEPRINT_COLOURS: Readonly<Record<string, string>> = {
  "Stiffened plate": CELL_BLUE,
  Framework: "#a78bfa",
  "Framework with deck": "#a3e635",
};

/** Colours for blueprints the table does not name, picked by a hash of the slug. */
export const EXTRA_BLUEPRINT_PALETTE: readonly string[] = ["#e879f9", "#fbbf24", "#22d3ee", "#f87171", "#34d399"];

function hashSlug(slug: string): number {
  let h = 0;
  for (let i = 0; i < slug.length; i++) h = (h * 31 + slug.charCodeAt(i)) | 0;
  return Math.abs(h);
}

/** The colour of blueprint `slug` ("#rrggbb"); the plain cell blue for none. */
export function blueprintColour(slug: string | null | undefined): string {
  const s = (slug ?? "").trim();
  if (!s) return CELL_BLUE;
  return BLUEPRINT_COLOURS[s] ?? EXTRA_BLUEPRINT_PALETTE[hashSlug(s) % EXTRA_BLUEPRINT_PALETTE.length];
}

/** The blueprint of structure `name` among `groups`, or undefined. */
export function structureBlueprint(groups: readonly CellGroup[], name: string | null | undefined): string | undefined {
  if (!name) return undefined;
  const bp = groups.find((g) => g.name === name)?.blueprint;
  return bp && bp.trim() ? bp : undefined;
}

/** The colour a space in structure `name` draws in: its blueprint's, else the
 * cell blue. */
export function structureColour(groups: readonly CellGroup[], name: string | null | undefined): string {
  return blueprintColour(structureBlueprint(groups, name));
}

/** The blueprints the model's structures use, each once, in the order the
 * structures are listed -- the legend's rows. */
export function blueprintsInUse(groups: readonly CellGroup[]): string[] {
  const out: string[] = [];
  for (const g of groups) {
    const bp = g.blueprint?.trim();
    if (bp && !out.includes(bp)) out.push(bp);
  }
  return out;
}

/** "#rrggbb" as the number three.js takes. */
export function hexToNumber(hex: string): number {
  return parseInt(hex.replace("#", ""), 16);
}
