// The joint `ada dev connections` previews, annotated in 3D (Connections > Overlays):
//
//   * member axes -- each beam's axis, coloured by its role (the Clashes legend's incoming amber and
//     landing teal), labelled with its role and section; a plate gets its label at its centre;
//   * weld labels -- each weld's type and throat where it meets its members, in red when it does not
//     touch a member it says it joins.
//
// DRAWN THROUGH THE VIEWPORT ON SCREEN (viewport/api): the axes as lines of an overlay layer, the
// labels as billboards, so either viewport draws them. They were three.js objects added to the
// three.js scene, which the three.js-free viewport never draws -- there a joint built from the
// Inspect tab showed no annotations at all, while a plugin's lines (drawn through the same layers as
// these now) showed.
//
// SPACE. The positions are the preview GLB's model coordinates. Every viewport places a loaded model
// by one rule, world = model + its source's origin (`ViewportApi.sourceOrigin`), so the lines are
// drawn with that translation and each label at its point plus it.
//
// Both are drawn through the model (an axis runs inside its member; a fillet hides in the corner it
// fills) and take no clicks. They go when their model leaves the scene (LIFETIME, below). No three.js
// in this module.

import type { DevBuildResult } from "@/services/api/devConnections";
import type { ConnectionOverlays } from "@/state/connectionBuilderStore";
import { useModelState } from "@/state/modelState";
import { linearColor } from "@/utils/scene/sceneColor";
import { weldSymbolSvg } from "@/utils/weldSymbols";
import { weldTypeName } from "@/utils/weldTypeNames";
import type { Billboard, BillboardImage, BillboardLayer, Point3 } from "@/viewport/api/billboards";
import type { OverlayLayer } from "@/viewport/api/overlayLayer";
import { activeViewport } from "@/viewport/api/viewportApi";

export type ConnectionOverlayToggles = Pick<ConnectionOverlays, "axes" | "welds" | "weldSymbols" | "weldDetails">;

/** The viewport layers (lines, billboards) the annotations are drawn in. */
export const CONNECTION_OVERLAY_LAYER = "core:connection-preview";

/** Role colours, as the Clashes legend draws them (`clashContactPlan`). */
const ROLE_COLOUR: Record<string, string> = { incoming: "#f59e0b", landing: "#14b8a6" };
const OTHER_COLOUR = "#a3a3a3";
const WELD_COLOUR = "#e5e7eb";
const WELD_BAD_COLOUR = "#f87171";
const LABEL_HEIGHT_PX = 16;
const LABEL_PAD_PX = 4;
/** Where along a member's axis its label sits (0 = n1, 1 = n2). */
const LABEL_ALONG_AXIS = 0.8;
const AXIS_DASH = { dash: 0.05, gap: 0.03 };
const LABEL_ORDER = 21;

/** What is drawn, in which layers, and over which loaded source. */
let drawn: { lines: OverlayLayer; labels: BillboardLayer; file: string } | null = null;
/** Bumped by every clear: a weld symbol that decodes after it has nothing left to update. */
let generation = 0;

export function clearConnectionOverlay(): void {
  generation++;
  if (!drawn) return;
  const { lines, labels } = drawn;
  drawn = null;
  // Each on its own: one that fails (a renderer gone) must not leave the other on screen.
  try {
    lines.dispose();
  } finally {
    labels.dispose();
  }
}

// LIFETIME. The annotations are the model's: they go when the source they were drawn over leaves the
// scene, by whichever route -- the storage browser's Clear and the Scene tab's Remove all
// (`clearScene`), unloading that one model, another model replacing the scene, a scope
// change. The viewport layers they are drawn in are not the model's, so clearing the models leaves
// them; this takes them away. Beside what draws them rather than in the builder's session: whatever
// drew them takes them away, whether a session is running or not.
useModelState.subscribe((s, prev) => {
  if (s.loadedSourceNames !== prev.loadedSourceNames && drawn && !s.loadedSourceNames.has(drawn.file)) {
    clearConnectionOverlay();
  }
});

/** Draw `result`'s annotations over the preview loaded as `file`, in the viewport on screen. Returns
 *  how many marks were drawn (0, and nothing drawn, when there is no viewport, that source is not on
 *  screen in it, or both toggles are off). */
export function renderConnectionOverlay(
  file: string | null,
  result: DevBuildResult | null,
  toggles: ConnectionOverlayToggles,
): number {
  clearConnectionOverlay();
  const viewport = activeViewport();
  const origin = viewport && file ? viewport.sourceOrigin(file) : null;
  if (!viewport || !origin || !result || (!toggles.axes && !toggles.welds)) return 0;

  const lines = viewport.overlays(CONNECTION_OVERLAY_LAYER);
  const labels = viewport.billboards(CONNECTION_OVERLAY_LAYER);
  drawn = { lines, labels, file: file! };
  const matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, origin[0], origin[1], origin[2], 1];
  const world = (p: readonly number[]): Point3 => [p[0] + origin[0], p[1] + origin[1], p[2] + origin[2]];
  const mine = generation;

  let marks = 0;
  if (toggles.axes) {
    for (const m of result.properties.members) {
      const colour = ROLE_COLOUR[m.role ?? ""] ?? OTHER_COLOUR;
      const what = m.section?.name ?? (typeof m.thickness === "number" ? `t=${mm(m.thickness)}` : m.type);
      const text = `${m.role ?? m.name} · ${what}`;
      if (m.axis) {
        lines.addLines({
          segments: Float32Array.of(...m.axis[0], ...m.axis[1]),
          color: linearColor(colour),
          widthPx: 1.5,
          dashed: AXIS_DASH,
          onTop: true,
          fitted: false,
          matrix,
        });
        // Out along the member, clear of the joint the weld labels crowd (a landing beam's axis
        // centre IS the joint; an incoming one starts there).
        addLabel(labels, text, world(along(m.axis[0], m.axis[1], LABEL_ALONG_AXIS)), colour, null, mine);
        marks++;
      } else if (m.centre) {
        addLabel(labels, text, world(m.centre), colour, null, mine);
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
      // The server's symbol where it sent one; an older server's result has none, and the viewer draws the same one.
      const svg = toggles.weldSymbols ? (w.symbol_svg ?? weldSymbolSvg(w.weld_type, { size: 20 })) : null;
      addLabel(labels, text, world(w.at), bad ? WELD_BAD_COLOUR : WELD_COLOUR, svg, mine);
      marks++;
    }
  }
  if (marks === 0) clearConnectionOverlay();
  return marks;
}

function mm(m: number | null | undefined): string {
  return typeof m === "number" ? `${Math.round(m * 10000) / 10} mm` : "–";
}

/** The point a fraction `t` of the way from `a` to `b`. */
function along(a: readonly number[], b: readonly number[], t: number): number[] {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** A text label of a constant on-screen height at `at` (world), anchored at its lower-left corner
 *  beside the point it names. `svg`, an icon stroked in `currentColor` (a weld symbol), is drawn
 *  before the text in the label's colour once it has decoded: the label shows its text at once, with
 *  the icon's room kept, and takes the icon when it is ready. */
function addLabel(
  layer: BillboardLayer,
  text: string,
  at: Point3,
  colour: string,
  svg: string | null,
  mine: number,
): void {
  const billboard = (icon: CanvasImageSource | null): Billboard => ({
    position: at,
    image: labelImage(text, colour, !!svg, icon),
    height: LABEL_HEIGHT_PX + 2 * LABEL_PAD_PX,
    sizing: "screen",
    anchor: [0, 0],
    order: LABEL_ORDER,
  });
  const handle = layer.add(billboard(null));
  if (!svg || typeof Image === "undefined") return;
  // An SVG decodes asynchronously; the label it was for may be gone by then.
  const img = new Image();
  img.onload = () => {
    if (mine === generation) layer.update(handle, billboard(img));
  };
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg.replace(/currentColor/g, colour))}`;
}

/** The label's pixels: the text on a dark plate, with room for an icon before it when `withIcon`. */
function labelImage(text: string, colour: string, withIcon: boolean, icon: CanvasImageSource | null): BillboardImage {
  return {
    key: `connection-label:${colour}:${withIcon ? (icon ? "icon" : "room") : "text"}:${text}`,
    crisp: true,
    draw: () => {
      const scale = 2; // canvas pixels per CSS pixel, for crisp text
      const font = `${LABEL_HEIGHT_PX * scale}px sans-serif`;
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d")!;
      ctx.font = font;
      const pad = LABEL_PAD_PX * scale;
      const iconSize = withIcon ? LABEL_HEIGHT_PX * scale : 0;
      const textX = pad + (iconSize ? iconSize + pad : 0);
      canvas.width = Math.ceil(ctx.measureText(text).width) + textX + pad;
      canvas.height = LABEL_HEIGHT_PX * scale + 2 * pad;
      ctx.font = font;
      ctx.fillStyle = "rgba(17, 24, 39, 0.8)";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = colour;
      ctx.textBaseline = "middle";
      ctx.fillText(text, textX, canvas.height / 2);
      if (icon) ctx.drawImage(icon, pad, pad, iconSize, iconSize);
      return canvas;
    },
  };
}
