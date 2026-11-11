// The joint `ada dev connections` previews, annotated in 3D (Scene > Joints > Overlays):
//
//   * member axes -- each beam's axis, coloured by its role (the Clashes legend's incoming amber and
//     landing teal), labelled with its role and section; a plate gets its label at its centre;
//   * weld labels -- each weld's type and throat where it meets its members, in red when it does not
//     touch a member it says it joins.
//
// SPACE. The positions are the preview GLB's model coordinates; the loader rotates and recentres that
// GLB under its `__sourceSpaceRoot` node, so the overlay copies that node's world matrix -- the way the
// clash joint markers place themselves (`clashJointMarkers`) -- rather than being parented into the
// model's subtree, which the tree build and the picker scan.
//
// Both are drawn through the model (an axis runs inside its member; a fillet hides in the corner it
// fills) and take no clicks.

import * as THREE from "three";

import type { DevBuildResult } from "@/services/api/devConnections";
import { loadedSourceGroups } from "@/state/modelState";
import { requestRender } from "@/state/perfStore";
import type { ConnectionOverlays } from "@/state/connectionBuilderStore";
import { getViewerRuntime } from "@/state/viewerRuntime";
import { sourceSpaceRoot } from "@/utils/scene/meshLookup";
import { weldTypeName } from "@/utils/weldTypeNames";

export type ConnectionOverlayToggles = Pick<ConnectionOverlays, "axes" | "welds" | "weldSymbols" | "weldDetails">;

/** Role colours, as the Clashes legend draws them (`clashContactPlan`). */
const ROLE_COLOUR: Record<string, string> = { incoming: "#f59e0b", landing: "#14b8a6" };
const OTHER_COLOUR = "#a3a3a3";
const WELD_COLOUR = "#e5e7eb";
const WELD_BAD_COLOUR = "#f87171";
const LABEL_HEIGHT_PX = 16;
/** Where along a member's axis its label sits (0 = n1, 1 = n2). */
const LABEL_ALONG_AXIS = 0.8;

let overlay: THREE.Group | null = null;

export function clearConnectionOverlay(): void {
  if (!overlay) return;
  overlay.removeFromParent();
  overlay.traverse((o) => {
    const m = o as THREE.Mesh;
    m.geometry?.dispose();
    const mats = Array.isArray(m.material) ? m.material : m.material ? [m.material] : [];
    for (const mat of mats) {
      (mat as THREE.SpriteMaterial).map?.dispose();
      mat.dispose();
    }
  });
  overlay = null;
  requestRender();
}

/** Draw `result`'s annotations over the preview loaded as `file`. Returns how many marks were drawn
 *  (0, and nothing drawn, when the scene or that source is not there or both toggles are off). */
export function renderConnectionOverlay(
  file: string | null,
  result: DevBuildResult | null,
  toggles: ConnectionOverlayToggles,
): number {
  clearConnectionOverlay();
  const scene = getViewerRuntime().scene.current;
  const group = file ? loadedSourceGroups.get(file) : undefined;
  if (!scene || !group || !result || (!toggles.axes && !toggles.welds)) return 0;

  const root = sourceSpaceRoot(group);
  root.updateWorldMatrix(true, false);
  const out = new THREE.Group();
  out.name = "ada-connection-overlay";
  out.matrixAutoUpdate = false;
  out.matrix.copy(root.matrixWorld);
  out.renderOrder = 20;

  let marks = 0;
  if (toggles.axes) {
    for (const m of result.properties.members) {
      const colour = ROLE_COLOUR[m.role ?? ""] ?? OTHER_COLOUR;
      const what = m.section?.name ?? (typeof m.thickness === "number" ? `t=${mm(m.thickness)}` : m.type);
      const text = `${m.role ?? m.name} · ${what}`;
      if (m.axis) {
        out.add(axisLine(m.axis[0], m.axis[1], colour));
        // Out along the member, clear of the joint the weld labels crowd (a landing beam's axis
        // centre IS the joint; an incoming one starts there).
        out.add(label(text, along(m.axis[0], m.axis[1], LABEL_ALONG_AXIS), colour));
        marks++;
      } else if (m.centre) {
        out.add(label(text, m.centre, colour));
        marks++;
      }
    }
  }
  if (toggles.welds) {
    for (const w of result.properties.welds) {
      if (!w.at) continue;
      const bad = w.joins_its_members === false;
      const type = weldTypeName(w.weld_type);
      const text = `${toggles.weldDetails ? `${w.name} · ${type} a=${mm(w.throat)}` : type}${bad ? " · not joined" : ""}`;
      out.add(label(text, w.at, bad ? WELD_BAD_COLOUR : WELD_COLOUR, toggles.weldSymbols ? w.symbol_svg : null));
      marks++;
    }
  }
  if (marks === 0) return 0;
  scene.add(out);
  overlay = out;
  requestRender();
  return marks;
}

function mm(m: number | null | undefined): string {
  return typeof m === "number" ? `${Math.round(m * 10000) / 10} mm` : "–";
}

/** The point a fraction `t` of the way from `a` to `b`. */
function along(a: readonly number[], b: readonly number[], t: number): number[] {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

function axisLine(a: readonly number[], b: readonly number[], colour: string): THREE.LineSegments {
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.Float32BufferAttribute([...a, ...b], 3));
  const line = new THREE.LineSegments(
    geom,
    new THREE.LineDashedMaterial({ color: colour, dashSize: 0.05, gapSize: 0.03, depthTest: false, transparent: true }),
  );
  line.computeLineDistances();
  line.renderOrder = 20;
  line.raycast = () => undefined;
  return line;
}

/** A text sprite of a constant on-screen height, drawn over the model; `svg`, an icon stroked in
 *  `currentColor` (a weld symbol), is drawn before the text in the label's colour. */
function label(text: string, at: readonly number[], colour: string, svg?: string | null): THREE.Sprite {
  const scale = 2; // canvas pixels per CSS pixel, for crisp text
  const font = `${LABEL_HEIGHT_PX * scale}px sans-serif`;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d")!;
  ctx.font = font;
  const pad = 4 * scale;
  const icon = svg ? LABEL_HEIGHT_PX * scale : 0;
  const textX = pad + (icon ? icon + pad : 0);
  canvas.width = Math.ceil(ctx.measureText(text).width) + textX + pad;
  canvas.height = LABEL_HEIGHT_PX * scale + 2 * pad;
  ctx.font = font;
  ctx.fillStyle = "rgba(17, 24, 39, 0.8)";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = colour;
  ctx.textBaseline = "middle";
  ctx.fillText(text, textX, canvas.height / 2);

  const texture = new THREE.CanvasTexture(canvas);
  if (svg) {
    // An SVG decodes asynchronously: draw it into the space left for it once it has.
    const img = new Image();
    img.onload = () => {
      ctx.drawImage(img, pad, pad, icon, icon);
      texture.needsUpdate = true;
      requestRender();
    };
    img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg.replace(/currentColor/g, colour))}`;
  }
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, sizeAttenuation: false, depthTest: false, transparent: true }),
  );
  sprite.position.set(at[0], at[1], at[2]);
  sprite.center.set(0, 0); // anchored at its lower-left corner, beside the point it names
  sprite.renderOrder = 21;
  sprite.raycast = () => undefined;
  const aspect = canvas.width / canvas.height;
  const heightPx = canvas.height / scale;
  // Constant on-screen size: without size attenuation a sprite's scale is a fraction of the view height.
  sprite.onBeforeRender = (renderer, _scene, camera) => {
    const cssHeight = Math.max(1, renderer.domElement.clientHeight);
    const s = (2 * heightPx) / (cssHeight * camera.projectionMatrix.elements[5]);
    sprite.scale.set(s * aspect, s, 1);
  };
  return sprite;
}
