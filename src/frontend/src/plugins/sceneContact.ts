// Where two objects of the loaded models touch, for a plugin (`SceneHandle.contactBetween`).
//
// The answer is the convex-contact check's (the clash module, utils/clashContacts): the two objects'
// convex hulls, the nearest points, the contact normal, the signed distance and the contact patches
// -- the polygons where the hulls meet. It reads the models' stored GLBs, not what a viewport holds,
// so it answers the same on either viewport, in the scene's own coordinates (those of the loaded
// model's file, z-up: the frame `ScenePick.point` and `addLineLayer` use).
//
// Which GLB a model is: the stored build an asset load came from, else the GLB of the scope's file
// the model was loaded from; a caller that loaded a model itself from a stored GLB names it in the
// object's `glb`. A model none of these finds (dropped in from disk, a provider's external mesh) is
// answered `unavailable`, with the reason.
//
// No three.js in this module or in anything it imports at load time.

export type ContactVec3 = [number, number, number];

/** One object of a loaded model, as `ScenePick` names it. */
export interface SceneObjectRef {
  /** The model's source name in the scene. */
  sourceName: string;
  /** The object's name in that model. */
  name: string;
  /** Where the model's GLB is stored, for a model the caller loaded itself (`loadModelFromUrl` from
   *  bytes it read). `scope` defaults to the open one. */
  glb?: { key: string; scope?: string };
}

export interface SceneContactOptions {
  /** Hulls closer than this (metres) count as touching. Default 0.01. */
  securityMargin?: number;
  /** A contact with a smaller patch area (m^2) is no contact. Default 1e-6. */
  minContactArea?: number;
  /** The width given to an edge-on-face contact (metres), so it has an area. Default 1e-3. */
  lineContactThickness?: number;
}

/** One contact patch: a convex polygon where the two hulls meet. */
export interface SceneContactPatch {
  /** The patch plane's normal, from `a` towards `b`. */
  normal: ContactVec3;
  /** The polygon's vertices on `a`'s surface, xyz after xyz, in order around `normal`. */
  pointsA: number[];
  /** The same vertices on `b`'s surface (moved by the distance along `normal`). */
  pointsB: number[];
}

export interface SceneContact {
  /** Negative: how deep the hulls overlap; positive: the gap, within the security margin. */
  distance: number;
  /** The contact normal, from `a` towards `b`. */
  normal: ContactVec3;
  nearA: ContactVec3;
  nearB: ContactVec3;
  /** The patches' area, m^2. */
  area: number;
  /** Usually one. */
  patches: SceneContactPatch[];
}

/** `contact` null: the two do not touch. `unavailable`: they could not be measured, and why. */
export type SceneContactAnswer = { contact: SceneContact | null } | { unavailable: string };

/** The columns the clash module's `glbMembers` returns. */
export interface ContactGlbMembers {
  ids: readonly string[];
  names: readonly string[];
  vertices: ArrayLike<number>;
  vertexOffsets: ArrayLike<number>;
  triangles: ArrayLike<number>;
  triangleOffsets: ArrayLike<number>;
}

/** The columns the clash module's `findConvexContacts` returns. */
export interface ContactColumns {
  pairs: ArrayLike<number>;
  near1: ArrayLike<number>;
  near2: ArrayLike<number>;
  normal: ArrayLike<number>;
  penetrationDepth: ArrayLike<number>;
  contactArea: ArrayLike<number>;
  patchOffsets: ArrayLike<number>;
  patchNormals: ArrayLike<number>;
  patchPointOffsets: ArrayLike<number>;
  patchPoints1: ArrayLike<number>;
  patchPoints2: ArrayLike<number>;
}

export interface ContactSoup {
  vertices: Float64Array;
  vertexOffsets: Uint32Array;
  triangles: Uint32Array;
  triangleOffsets: Uint32Array;
}

/** What the verb needs of the viewer, handed in so it is tested without one. */
export interface SceneContactDeps {
  /** The stored GLB a loaded model was drawn from; null when the viewer does not know one. */
  locate(sourceName: string): { scope: string; key: string } | null;
  /** The open scope, for a `glb` given without one. */
  scope(): string;
  getBytes(scope: string, key: string): Promise<ArrayBuffer>;
  glbMembers(glb: ArrayBuffer): Promise<ContactGlbMembers>;
  convexContacts(soup: ContactSoup, options: Required<SceneContactOptions>): Promise<ContactColumns>;
}

export const CONTACT_DEFAULTS: Required<SceneContactOptions> = {
  securityMargin: 0.01,
  minContactArea: 1e-6,
  lineContactThickness: 1e-3,
};

/** The two meshes as the soup the clash module measures: `a` is mesh 0, `b` mesh 1. */
export function pairSoup(a: { m: ContactGlbMembers; k: number }, b: { m: ContactGlbMembers; k: number }): ContactSoup {
  const span = (x: { m: ContactGlbMembers; k: number }) => ({
    v0: x.m.vertexOffsets[x.k], v1: x.m.vertexOffsets[x.k + 1], t0: x.m.triangleOffsets[x.k], t1: x.m.triangleOffsets[x.k + 1],
  });
  const sa = span(a);
  const sb = span(b);
  const nva = sa.v1 - sa.v0, nvb = sb.v1 - sb.v0, nta = sa.t1 - sa.t0, ntb = sb.t1 - sb.t0;
  const vertices = new Float64Array(3 * (nva + nvb));
  const triangles = new Uint32Array(3 * (nta + ntb));
  for (let i = 0; i < 3 * nva; i++) vertices[i] = a.m.vertices[3 * sa.v0 + i];
  for (let i = 0; i < 3 * nvb; i++) vertices[3 * nva + i] = b.m.vertices[3 * sb.v0 + i];
  // Triangle indices are local to their mesh already.
  for (let i = 0; i < 3 * nta; i++) triangles[i] = a.m.triangles[3 * sa.t0 + i];
  for (let i = 0; i < 3 * ntb; i++) triangles[3 * nta + i] = b.m.triangles[3 * sb.t0 + i];
  return {
    vertices,
    vertexOffsets: Uint32Array.from([0, nva, nva + nvb]),
    triangles,
    triangleOffsets: Uint32Array.from([0, nta, nta + ntb]),
  };
}

const vec3 = (a: ArrayLike<number>, i: number): ContactVec3 => [a[3 * i], a[3 * i + 1], a[3 * i + 2]];
const neg = (v: ContactVec3): ContactVec3 => [-v[0], -v[1], -v[2]];

/** The contact of the pair (mesh 0, mesh 1) in the columns, from 0 towards 1; null when there is none. */
export function contactOfPair(raw: ContactColumns): SceneContact | null {
  const n = raw.penetrationDepth.length;
  for (let q = 0; q < n; q++) {
    const i = raw.pairs[2 * q];
    const j = raw.pairs[2 * q + 1];
    if (!((i === 0 && j === 1) || (i === 1 && j === 0))) continue;
    // The module reports i < j; written for either order all the same.
    const flip = i === 1;
    const patches: SceneContactPatch[] = [];
    for (let p = raw.patchOffsets[q]; p < raw.patchOffsets[q + 1]; p++) {
      const on1: number[] = [];
      const on2: number[] = [];
      for (let v = raw.patchPointOffsets[p]; v < raw.patchPointOffsets[p + 1]; v++) {
        on1.push(...vec3(raw.patchPoints1, v));
        on2.push(...vec3(raw.patchPoints2, v));
      }
      const normal = vec3(raw.patchNormals, p);
      patches.push(flip ? { normal: neg(normal), pointsA: on2, pointsB: on1 } : { normal, pointsA: on1, pointsB: on2 });
    }
    const normal = vec3(raw.normal, q);
    return {
      distance: raw.penetrationDepth[q],
      normal: flip ? neg(normal) : normal,
      nearA: vec3(flip ? raw.near2 : raw.near1, q),
      nearB: vec3(flip ? raw.near1 : raw.near2, q),
      area: raw.contactArea[q],
      patches,
    };
  }
  return null;
}

const errText = (err: unknown): string => (err instanceof Error ? err.message : String(err));

/** `contactBetween` over `deps`. A model's members are read once per GLB and kept for the next ask. */
export function makeContactBetween(deps: SceneContactDeps, keep = 8) {
  const read = new Map<string, Promise<ContactGlbMembers>>();
  const membersOf = (scope: string, key: string): Promise<ContactGlbMembers> => {
    const id = `${scope}\n${key}`;
    let have = read.get(id);
    if (!have) {
      have = deps.getBytes(scope, key).then((bytes) => deps.glbMembers(bytes));
      // A failed read is asked again next time, not remembered.
      have.catch(() => read.delete(id));
      read.set(id, have);
      while (read.size > keep) read.delete(read.keys().next().value as string);
    }
    return have;
  };

  const meshOf = async (ref: SceneObjectRef): Promise<{ m: ContactGlbMembers; k: number } | string> => {
    const where = ref.glb ? { scope: ref.glb.scope ?? deps.scope(), key: ref.glb.key } : deps.locate(ref.sourceName);
    if (!where) return `the geometry of ${ref.sourceName} is not a GLB this viewer can read again`;
    let m: ContactGlbMembers;
    try {
      m = await membersOf(where.scope, where.key);
    } catch (err) {
      return `the geometry of ${ref.sourceName} could not be read: ${errText(err)}`;
    }
    const k = m.names.findIndex((name) => String(name) === ref.name);
    if (k < 0) return `${ref.name} is not an object of ${ref.sourceName}'s geometry`;
    return { m, k };
  };

  return async (a: SceneObjectRef, b: SceneObjectRef, options: SceneContactOptions = {}): Promise<SceneContactAnswer> => {
    const [ma, mb] = await Promise.all([meshOf(a), meshOf(b)]);
    if (typeof ma === "string") return { unavailable: ma };
    if (typeof mb === "string") return { unavailable: mb };
    if (ma.m === mb.m && ma.k === mb.k) return { unavailable: `${a.name} and ${b.name} are one object` };
    try {
      const raw = await deps.convexContacts(pairSoup(ma, mb), { ...CONTACT_DEFAULTS, ...options });
      return { contact: contactOfPair(raw) };
    } catch (err) {
      return { unavailable: `the contact could not be measured: ${errText(err)}` };
    }
  };
}

let viewerContact: ReturnType<typeof makeContactBetween> | null = null;

/** `SceneHandle.contactBetween`: on the viewer's own stores, scope and clash module. */
export async function contactBetween(a: SceneObjectRef, b: SceneObjectRef, options?: SceneContactOptions): Promise<SceneContactAnswer> {
  if (!viewerContact) {
    // Imported on first use: the clash module's worker, the stores and the REST client are not
    // on the plugin context's boot path.
    const [{ useAssetBrowserStore }, { scopeUrlPart, useScopeStore }, { authedFetch }, { filesApi }, { glbKeyForSource }] = await Promise.all([
      import("@/state/assetBrowserStore"),
      import("@/state/scopeStore"),
      import("@/services/api/client"),
      import("@/services/api/files"),
      import("@/services/clash/contactRuns"),
    ]);
    const scope = () => scopeUrlPart(useScopeStore.getState().current);
    const clash = () => import("@/utils/clashContacts/clashContacts");
    viewerContact ??= makeContactBetween({
      scope,
      locate(sourceName) {
        const asset = useAssetBrowserStore.getState().loaded.find((l) => l.sourceName === sourceName);
        if (asset) return asset.glbKey ? { scope: scope(), key: asset.glbKey } : null;
        // A provider's asset that is not a stored build is not a file of the scope either.
        if (sourceName.startsWith("assets:")) return null;
        return { scope: scope(), key: glbKeyForSource(sourceName) };
      },
      async getBytes(scopeUrl, key) {
        const r = await authedFetch(filesApi.blobUrl(scopeUrl as Parameters<typeof filesApi.blobUrl>[0], key));
        if (!r.ok) throw new Error(`${key}: ${r.status}`);
        return r.arrayBuffer();
      },
      glbMembers: async (glb) => (await clash()).glbMembers(glb, "z"),
      convexContacts: async (soup, o) => (await clash()).findConvexContacts(soup, o),
    });
  }
  return viewerContact(a, b, options);
}
