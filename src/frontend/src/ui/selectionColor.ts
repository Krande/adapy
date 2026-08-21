// The one place the selection colour is defined.
//
// It has to be a plain hex string in a dependency-free module because two very
// different consumers need it and neither can pull in the other's dependencies:
//
//   * the 3D highlight (`utils/default_materials.ts` → THREE.MeshStandardMaterial),
//     used by CustomBatchedMesh, handleClickPoints and selectGroupMembers;
//   * the DOM chrome (`--ada-select`), so an Outliner row, a properties header and
//     the highlighted geometry are visibly the same "selected".
//
// Before this existed the 3D side hard-coded the CSS keyword 'blue' and the DOM
// side had nothing, so the two could not agree by construction.

/**
 * Selection highlight. #2563EB (Tailwind blue-600) rather than pure #0000FF: full-
 * saturation blue is near-black in luminance, so it read as a dark hole against the
 * default grey geometry instead of as a highlight, and it failed contrast against
 * the dark panel presets.
 */
export const SELECTION_COLOR = "#2563eb";

/** Same value as a THREE-friendly integer. */
export const SELECTION_COLOR_INT = 0x2563eb;
