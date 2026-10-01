// The GPU picker's per-triangle colour map, built from runs of triangles that share an id.
//
// Built IN THE PICKER WORKER rather than on the main thread. The main thread allocates the ids --
// one per draw range (and per face, with face picking), from a counter shared by every registered
// mesh -- which is a loop over RANGES, cheap. Writing each id into every triangle it covers is a
// loop over TRIANGLES: 72 M byte writes for a 24 M-triangle model, measured at ~450 ms of main-thread
// time per large model in the load audit. So the main thread sends the runs and the worker fills.

/** One run per `(startTri, triCount, id)` triple, applied IN ORDER: a later run overwrites an
 *  earlier one where they overlap, which is how per-face ids replace the per-solid id beneath them.
 *  Triangles no run covers stay id 0 (black), as an unfilled map always was. */
export function fillTriColors(nTris: number, runs: Uint32Array): Uint8Array {
  const triColor = new Uint8Array(nTris * 3);
  for (let i = 0; i + 2 < runs.length; i += 3) {
    const startTri = runs[i];
    const end = Math.min(nTris, startTri + runs[i + 1]);
    const id = runs[i + 2];
    const r = id & 0xff;
    const g = (id >> 8) & 0xff;
    const b = (id >> 16) & 0xff;
    for (let t = startTri; t < end; t++) {
      const ti = t * 3;
      triColor[ti] = r;
      triColor[ti + 1] = g;
      triColor[ti + 2] = b;
    }
  }
  return triColor;
}
