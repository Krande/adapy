import * as THREE from "three";

import { requestRender } from "@/state/perfStore";
import { sceneRef } from "@/state/refs";
import { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { systemRouteRanges } from "@/utils/viewer/pipeTrace";

// A stable, distinct colour per procedural system, derived from its name so the
// same system always gets the same colour across renders (no palette index to
// drift as systems are added/removed). The golden-ratio hue step spreads
// successive hash values far apart on the wheel; fixed saturation/lightness keep
// every system readable in both the overview swatch and the 3D highlight.
const GOLDEN_RATIO_CONJUGATE = 0.618033988749895;

function hueFromName(name: string): number {
  let h = 0;
  for (let i = 0; i < name.length; i++) {
    h = (h * 31 + name.charCodeAt(i)) >>> 0;
  }
  // Map the hash into [0,1), then nudge by the golden ratio so near-identical
  // names (System1 / System2) still land on well-separated hues.
  return ((h % 360) / 360 + GOLDEN_RATIO_CONJUGATE) % 1;
}

export function systemColor(name: string): THREE.Color {
  return new THREE.Color().setHSL(hueFromName(name), 0.65, 0.55);
}

export function systemColorHex(name: string): string {
  return "#" + systemColor(name).getHexString();
}

function allBatchedMeshes(): CustomBatchedMesh[] {
  const out: CustomBatchedMesh[] = [];
  sceneRef.current?.traverse((o) => {
    if (o instanceof CustomBatchedMesh) out.push(o);
  });
  return out;
}

/** Tint each system's routed geometry with its unique colour (everything else
 * dims to neutral grey, so the systems read clearly). Returns the number of
 * systems that actually matched geometry in the scene. Reversible via
 * :func:`revertSystemHighlight`. */
export function highlightSystems(systemNames: string[]): number {
  const colorByRangeId = new Map<string, THREE.Color>();
  let matched = 0;
  for (const name of systemNames) {
    const ranges = systemRouteRanges(name);
    if (ranges.length === 0) continue;
    matched++;
    const col = systemColor(name);
    for (const [, rangeId] of ranges) colorByRangeId.set(rangeId, col);
  }
  if (colorByRangeId.size === 0) return 0;
  for (const mesh of allBatchedMeshes()) mesh.setRangeColors(colorByRangeId);
  requestRender();
  return matched;
}

/** Restore the original materials after :func:`highlightSystems`. */
export function revertSystemHighlight(): void {
  for (const mesh of allBatchedMeshes()) {
    mesh.disableVertexColorsAndResetMaterial();
  }
  requestRender();
}
