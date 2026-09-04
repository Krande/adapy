// The element-edge wireframe's colour, and the reference outline's.
//
// Matched to Xtract, which draws mesh lines WHITE over the contour and keeps the
// undeformed reference dark. Ours were the other way round: near-black edges that
// disappeared into a dark result field, and a light grey ghost that competed with
// them. Swapping is not cosmetic — the edges are how you read element size and
// where a stress gradient sits relative to the mesh, so they have to be the
// legible pair.

/** Element boundaries over the result. White, as Xtract draws them. */
export const FEA_EDGE_COLOR = 0xffffff;

/** The undeformed reference outline. Dark, so it reads as "behind". */
export const FEA_UNDEFORMED_COLOR = 0x000000;
