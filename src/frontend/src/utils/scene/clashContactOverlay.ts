// A convex-contact run's contacts drawn in the 3D scene: every patch the panel shows, in its
// members' ROLE colours, with the contact normal as an arrow -- what the run measured, where it is.
//
// WHAT IS DRAWN is decided in `clashContactPlan.contactOverlayPlan` (pure, tested); this file only
// turns that plan into plain buffers -- two filled meshes (the emphasised contacts and the rest, at
// two opacities), one set of outlines and arrow shafts, a cone per arrow head -- and hands them to
// the viewport on screen. Rebuilt whole on every change of what it shows: a run is a few hundred
// contacts, a rebuild is a few thousand vertices.
//
// DRAWN THROUGH THE VIEWPORT ON SCREEN (viewport/api): the buffers go into an overlay layer, so the
// three.js viewport and the three.js-free one both draw them. They were a three.js group added to the
// three.js scene ("clash-contact-overlay", its parts "clash-contact-fills", "clash-contact-fills-dimmed",
// "clash-contact-lines" and "clash-contact-arrow-heads" -- names the committed viewer bundle still
// carries), which the three.js-free viewport never draws.
//
// SPACE. The plan is in the run's model frame (Z up, untranslated). Every viewport places a loaded
// model by one rule, world = model + its source's origin (`ViewportApi.sourceOrigin`); a saved
// group's members come from several sources, so each contact is moved by the origin of the model its
// members are in (`originOf`, which `clashJointMarkers` works out per part).
//
// PICKING. The layers take no clicks: the patch triangles are kept, in the world, for the click test
// every viewport asks first (`clashOverlayPicks`). A triangle names its contact. No three.js here.

import { NORMAL_COLOUR, type OverlayContact } from "@/utils/scene/clashContactPlan";
import { linearColor } from "@/utils/scene/sceneColor";
import type { Point3 } from "@/viewport/api/billboards";
import type { OverlayLayer } from "@/viewport/api/overlayLayer";

const FILL_OPACITY = { emphasised: 0.72, dimmed: 0.2 } as const;
const DIMMED = 0.35;
/** Cone segments of an arrow head. */
const HEAD_SIDES = 10;

/** Where a contact's model-frame points are in the world: its source's origin; null leaves it out. */
export type ContactOrigin = (contactId: string) => Point3 | null;

const AT_ORIGIN: ContactOrigin = () => [0, 0, 0];

/** The flat buffers of one fill mesh: triangle-fanned polygons, a colour (linear RGB) per vertex, an
 *  id per triangle, in the world. Exported for the test: what is drawn and what a pick resolves to are
 *  one array. */
export function fillBuffers(
  contacts: readonly OverlayContact[],
  originOf: ContactOrigin = AT_ORIGIN,
): { positions: Float32Array; colors: Float32Array; ids: string[] } {
  const pos: number[] = [];
  const col: number[] = [];
  const ids: string[] = [];
  for (const contact of contacts) {
    const o = originOf(contact.id);
    if (!o) continue;
    for (const poly of contact.polygons) {
      const c = linearColor(poly.color);
      for (let k = 1; k + 1 < poly.points.length; k++) {
        for (const p of [poly.points[0], poly.points[k], poly.points[k + 1]]) {
          pos.push(p[0] + o[0], p[1] + o[1], p[2] + o[2]);
          col.push(c[0], c[1], c[2]);
        }
        ids.push(contact.id);
      }
    }
  }
  return { positions: new Float32Array(pos), colors: new Float32Array(col), ids };
}

/** The outlines and arrow shafts: six numbers per segment, a colour per segment. */
export function lineBuffers(contacts: readonly OverlayContact[], originOf: ContactOrigin = AT_ORIGIN): { segments: Float32Array; colors: Float32Array } {
  const pos: number[] = [];
  const col: number[] = [];
  for (const contact of contacts) {
    const o = originOf(contact.id);
    if (!o) continue;
    const dim = contact.emphasised ? 1 : DIMMED;
    const push = (a: readonly number[], b: readonly number[], c: readonly number[]) => {
      pos.push(a[0] + o[0], a[1] + o[1], a[2] + o[2], b[0] + o[0], b[1] + o[1], b[2] + o[2]);
      col.push(c[0] * dim, c[1] * dim, c[2] * dim);
    };
    for (const poly of contact.polygons) {
      const c = linearColor(poly.color);
      poly.points.forEach((p, k) => push(p, poly.points[(k + 1) % poly.points.length], c));
    }
    if (contact.arrow) push(contact.arrow.from, contact.arrow.to, linearColor(NORMAL_COLOUR));
  }
  return { segments: new Float32Array(pos), colors: new Float32Array(col) };
}

/** A cone per arrow, its tip at the arrow's end: a quarter of the arrow long, its base 0.35 of that
 *  across (as the three.js cone it replaced). Vertices, triangles and a colour per vertex. */
export function arrowHeadBuffers(
  contacts: readonly OverlayContact[],
  originOf: ContactOrigin = AT_ORIGIN,
): { positions: Float32Array; indices: Uint32Array; colors: Float32Array } {
  const pos: number[] = [];
  const idx: number[] = [];
  const col: number[] = [];
  const normal = linearColor(NORMAL_COLOUR);
  for (const contact of contacts) {
    const o = originOf(contact.id);
    if (!contact.arrow || !o) continue;
    const from = contact.arrow.from, to = contact.arrow.to;
    let dx = to[0] - from[0], dy = to[1] - from[1], dz = to[2] - from[2];
    const length = Math.hypot(dx, dy, dz);
    if (!(length > 0)) continue;
    dx /= length, dy /= length, dz /= length;
    const head = length * 0.25;
    const radius = head * 0.35;
    // Two directions across the arrow.
    const [ux, uy, uz] = Math.abs(dz) < 0.9 ? cross(dx, dy, dz, 0, 0, 1) : cross(dx, dy, dz, 1, 0, 0);
    const un = Math.hypot(ux, uy, uz);
    const a = [ux / un, uy / un, uz / un];
    const b = cross(dx, dy, dz, a[0], a[1], a[2]);
    const tip = [to[0] + o[0], to[1] + o[1], to[2] + o[2]];
    const base = [tip[0] - dx * head, tip[1] - dy * head, tip[2] - dz * head];
    const dim = contact.emphasised ? 1 : DIMMED;
    const first = pos.length / 3;
    pos.push(tip[0], tip[1], tip[2], base[0], base[1], base[2]);
    for (let k = 0; k < HEAD_SIDES; k++) {
      const t = (k / HEAD_SIDES) * Math.PI * 2;
      const c = Math.cos(t) * radius, s = Math.sin(t) * radius;
      pos.push(base[0] + a[0] * c + b[0] * s, base[1] + a[1] * c + b[1] * s, base[2] + a[2] * c + b[2] * s);
    }
    for (let k = 0; k < HEAD_SIDES + 2; k++) col.push(normal[0] * dim, normal[1] * dim, normal[2] * dim);
    for (let k = 0; k < HEAD_SIDES; k++) {
      const r0 = first + 2 + k, r1 = first + 2 + ((k + 1) % HEAD_SIDES);
      idx.push(first, r0, r1, first + 1, r1, r0);
    }
  }
  return { positions: new Float32Array(pos), indices: new Uint32Array(idx), colors: new Float32Array(col) };
}

function cross(ax: number, ay: number, az: number, bx: number, by: number, bz: number): [number, number, number] {
  return [ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx];
}

/** `count` vertices as triangles of their own, in order. */
function sequence(count: number): Uint32Array {
  const out = new Uint32Array(count);
  for (let i = 0; i < count; i++) out[i] = i;
  return out;
}

/** Draw `contacts` into `layer` (cleared first), each placed by `originOf`. Returns what was drawn,
 *  in the world, for the click test: the patch triangles and the contact each is. */
export function drawContactOverlay(
  layer: OverlayLayer,
  contacts: readonly OverlayContact[],
  originOf: ContactOrigin,
): { drawn: number; triangles: Float32Array; triangleIds: string[] } {
  layer.clear();
  const tris: Float32Array[] = [];
  const triIds: string[] = [];
  let drawn = 0;
  const look = { onTop: true, fitted: false } as const;
  for (const [subset, opacity] of [
    [contacts.filter((c) => c.emphasised), FILL_OPACITY.emphasised],
    [contacts.filter((c) => !c.emphasised), FILL_OPACITY.dimmed],
  ] as const) {
    const { positions, colors, ids } = fillBuffers(subset, originOf);
    if (ids.length === 0) continue;
    layer.addMesh({ positions, indices: sequence(positions.length / 3), colors, opacity, ...look });
    tris.push(positions);
    triIds.push(...ids);
  }
  const { segments, colors } = lineBuffers(contacts, originOf);
  if (segments.length) layer.addLines({ segments, colors, widthPx: 1.5, opacity: 0.95, ...look });
  const heads = arrowHeadBuffers(contacts, originOf);
  if (heads.indices.length) layer.addMesh({ ...heads, ...look });
  for (const c of contacts) if (originOf(c.id)) drawn++;
  const triangles = new Float32Array(tris.reduce((n, t) => n + t.length, 0));
  let at = 0;
  for (const t of tris) {
    triangles.set(t, at);
    at += t.length;
  }
  return { drawn, triangles, triangleIds: triIds };
}
