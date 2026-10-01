// Geometry groups for a batched mesh: one per run of adjacent draw ranges that share a material.
//
// three.js issues ONE DRAW CALL PER GROUP. `CustomBatchedMesh` already coalesced runs of the
// default material, but gave every selected or hidden range a group of its own -- so selecting a
// site, or isolating a joint (everything else hidden), turned a merged mesh's single draw into one
// per selected part, every frame, for as long as the selection stood. The render audit caught it as
// renderer `setup` / `needsUpdate` -- the per-draw cost -- taking most of the main thread in the
// frames after a large selection. Adjacent ranges of ANY one material now share a group.

export interface Group {
  start: number;
  count: number;
  materialIndex: number;
}

/** Groups covering `[0, idxCount)`: the ranges (sorted by start, non-overlapping) with their
 *  material from `materialOf`, the gaps between and around them in `gapMaterial`, every run of
 *  neighbours with the same material merged into one group. */
export function coalescedGroups(
  starts: ArrayLike<number>,
  counts: ArrayLike<number>,
  materialOf: (i: number) => number,
  idxCount: number,
  gapMaterial: number,
): Group[] {
  const out: Group[] = [];
  let runStart = 0;
  let runMat = -1;
  let cur = 0;
  const piece = (start: number, end: number, mat: number) => {
    if (end <= start) return;
    if (mat === runMat && start === cur) {
      cur = end;
      return;
    }
    if (runMat !== -1 && cur > runStart) out.push({ start: runStart, count: cur - runStart, materialIndex: runMat });
    runStart = start;
    runMat = mat;
    cur = end;
  };
  for (let i = 0; i < starts.length; i++) {
    const s = starts[i];
    const c = counts[i];
    if (s > cur) piece(cur, s, gapMaterial);
    piece(s, s + c, materialOf(i));
  }
  if (cur < idxCount) piece(cur, idxCount, gapMaterial);
  if (runMat !== -1 && cur > runStart) out.push({ start: runStart, count: cur - runStart, materialIndex: runMat });
  return out;
}
