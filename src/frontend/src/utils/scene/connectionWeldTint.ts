// The welds of the joint `ada dev connections` previews (Scene > Joints > Overlays): drawn in one
// colour ("Weld colour") -- a weld shares its members' steel grey and is hard to tell from them -- or
// hidden ("Show welds"), to see the members' end preparation the welds fill.
//
// The welds are draw ranges of the preview's batched meshes, painted through the mesh's range colours
// (`CustomBatchedMesh.setRangeColors`). A vertex colour MULTIPLIES the material's colour, so a painted
// mesh's material goes white and every range it does not paint keeps the material's colour as its
// vertex colour: the members look as they did, the welds take the colour asked for.

import * as THREE from "three";

import { requestRender } from "@/state/perfStore";
import type { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { drawRangesOfNames } from "@/utils/scene/crossModelSelect";

type Coloured = THREE.Material & { color: THREE.Color };

export interface WeldLook {
  /** A CSS colour to paint the welds in; null leaves their own. */
  colour: string | null;
  hidden: boolean;
}

/** Each painted mesh, with its materials' own colours, so `clearWeldTint` puts them back. */
const painted = new Map<CustomBatchedMesh, Map<Coloured, THREE.Color>>();
/** The ranges hidden, per mesh, so `clearWeldTint` shows them again. */
const hidden = new Map<CustomBatchedMesh, string[]>();
let generation = 0;

function materialsOf(mesh: CustomBatchedMesh): Coloured[] {
  const first = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
  return [...new Set([first, mesh.originalMaterial])].filter(
    (m): m is Coloured => !!m && (m as Coloured).color instanceof THREE.Color,
  );
}

export function clearWeldTint(): void {
  generation++;
  for (const [mesh, colours] of painted) {
    mesh.disableVertexColorsAndResetMaterial();
    for (const [mat, colour] of colours) mat.color.copy(colour);
  }
  painted.clear();
  for (const [mesh, ids] of hidden) mesh.unhideBatchDrawRange(ids);
  hidden.clear();
  requestRender();
}

/** Draw the welds named `weldNames` of the loaded source `file` as `look` says; a plain look puts
 *  back whatever an earlier one changed. */
export async function tintWelds(file: string | null, weldNames: string[], look: WeldLook): Promise<void> {
  clearWeldTint();
  if (!file || (!look.colour && !look.hidden) || weldNames.length === 0) return;
  const mine = generation;
  const ranges = await drawRangesOfNames(file, weldNames);
  // A newer paint (or a clear) was asked for while the ranges resolved.
  if (mine !== generation) return;
  const byMesh = new Map<CustomBatchedMesh, string[]>();
  for (const [mesh, id] of ranges) byMesh.set(mesh, [...(byMesh.get(mesh) ?? []), id]);
  for (const [mesh, ids] of byMesh) {
    if (look.hidden) {
      mesh.hideBatchDrawRange(ids);
      hidden.set(mesh, ids);
      continue;
    }
    const tint = new THREE.Color(look.colour!);
    const mats = materialsOf(mesh);
    const base = mats[0]?.color.clone() ?? new THREE.Color(1, 1, 1);
    painted.set(mesh, new Map(mats.map((m) => [m, m.color.clone()])));
    for (const m of mats) m.color.setRGB(1, 1, 1);
    mesh.setRangeColors(new Map(ids.map((id) => [id, tint])), base);
  }
  requestRender();
}
