// Default names for newly placed cells: "Space_01", "Equipment_01", "OPENING_01" -- the first
// free NN (zero-padded to 2 digits) among the names already in use, so a name freed by deleting
// the last one of its kind is reused rather than the count simply growing without bound (which
// `renameCell` already rejects as a duplicate -- see cellsSlice.ts).

/** The subset of `BuilderCell` this only needs: something with a `.name`. */
export interface NamedCell {
  name: string;
}

/** `${stem}_NN`, the first NN >= 1 not already a name among `cells`. */
export function nextFreeName(
  stem: string,
  cells: Record<string, NamedCell> | readonly NamedCell[],
): string {
  const taken = new Set(
    Array.isArray(cells)
      ? cells.map((c) => c.name)
      : Object.values(cells).map((c) => c.name),
  );
  let n = 1;
  let name = `${stem}_${String(n).padStart(2, "0")}`;
  while (taken.has(name)) {
    n += 1;
    name = `${stem}_${String(n).padStart(2, "0")}`;
  }
  return name;
}

/** The default-name stem for a new equipment of catalogue type `eqType` (the picker's selected
 *  type slug, undefined when none is picked): "Equipment" for no type or the generic "box"
 *  archetype, else the type itself, uppercased, as today (e.g. "PUMP"). */
export function equipmentNameStem(eqType: string | undefined): string {
  if (!eqType || eqType.toLowerCase() === "box") return "Equipment";
  return eqType.toUpperCase();
}
