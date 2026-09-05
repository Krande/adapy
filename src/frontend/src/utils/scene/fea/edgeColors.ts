// The element-edge wireframe's colour, and the reference outline's.
//
// Matched to the reference postprocessor, which draws mesh lines WHITE over the contour and keeps the
// undeformed reference dark. Ours were the other way round: near-black edges that
// disappeared into a dark result field, and a light grey ghost that competed with
// them. Swapping is not cosmetic — the edges are how you read element size and
// where a stress gradient sits relative to the mesh, so they have to be the
// legible pair.

/** Element boundaries over the result. White, as the reference postprocessor draws them. */
export const FEA_EDGE_COLOR = 0xffffff;

/** The undeformed reference outline. Dark, so it reads as "behind". */
export const FEA_UNDEFORMED_COLOR = 0x000000;

/** Beam (line) element edges. Dimmer than the shell grid on purpose.
 *
 * A shell's element edges are a mesh you read element size off; a beam's edge is
 * a member. Drawn in the same white the members disappear into the grid — they
 * are individually far more prominent than any one shell edge, so matching
 * brightness over-weights them. Grey keeps them legible as structure without
 * competing with the surface they cross. */
export const FEA_BEAM_EDGE_COLOR = 0x9aa0a6;
