// A clash check that never leaves the browser, and never boots a Python runtime.
//
// ONE COMPILED PASS, TWO RUNTIMES. `adacpp` reads the IFC and finds its beam-to-beam joints in
// C++ (`clash_joints.h`), and adapy calls that SAME pass through nanobind on a worker. So this is
// not a second implementation of the rules -- it is the same machine code, reached through embind
// instead of nanobind. That matters beyond tidiness: a joint's id is a hash of its member names
// and the pass that found it, and a `clash_detail` hand-off re-derives joints from those ids, so
// a browser that computed them differently would show a user joints no worker could detail.
//
// WHAT THE BROWSER DOES NOT GET. The two PLATE passes need surface distances from a CAD kernel,
// which the wasm module does not carry, so a browser check reports beam-to-beam joints and says
// the plate passes did not run -- a different answer from "there are no plate joints", and one the
// result document has always distinguished. Nor does it get `applicable` specs: which generator
// could detail a joint is a server-side registry, so detailing still starts with a server run.
//
// WHAT IT COSTS AND SAVES. It saves the upload and the job -- a check on a model already open is
// a few hundred milliseconds of C++ with no round trip. It costs nothing to boot: the wasm module
// is the one the viewer already loads for native IFC conversion.

import { type ClashCheckOptions } from "@/services/api/clashCheck";
import { parseClashResult, type ClashResult } from "@/state/clashCheckStore";
import type { NativeClashResult } from "@/utils/nativeConvert/cadGlbConverter";

/** Only IFC. The reader behind this is an IFC reader; a STEP states solids, not members. */
export function browserClashCheckSupports(sourceKey: string): boolean {
  const lower = sourceKey.toLowerCase();
  return lower.endsWith(".ifc") || lower.endsWith(".ifcxml");
}

export type BrowserClashProgress = (stage: "checking", detail?: string) => void;

export interface BrowserClashCheckResult {
  result: ClashResult;
  /** Beams the reader found. Zero is a real answer -- "this IFC states no beams" -- and not a
   *  failure, so it is reported rather than turned into an error here. */
  beams: number;
  totalMs: number;
}

/** The half that is a wasm module, injected so the wiring is testable where wasm cannot load. */
export interface BrowserClashDeps {
  clashJoints(
    bytes: ArrayBuffer,
    opts: { outOfPlaneTol: number; pointTol: number },
  ): Promise<NativeClashResult>;
}

export const realBrowserClashDeps: BrowserClashDeps = {
  // Imported ON USE: the module pulls in a bundled wasm worker that is not wanted unless someone
  // actually asks for a browser check, and cannot load at all under node.
  async clashJoints(bytes, opts) {
    const { nativeIfcClashJoints } = await import(
      "@/utils/nativeConvert/cadGlbConverter"
    );
    return nativeIfcClashJoints(bytes, opts);
  },
};

/** One member of a joint, as `adacpp.clash_joints/1` states it. */
interface WireNativeMember {
  name: string;
  guid: string;
  kind: string;
  section: string;
  member_type: string;
}

interface WireNativeJoint {
  origin: string;
  centre: [number, number, number];
  angle_deg: number | null;
  type_key: string;
  members: WireNativeMember[];
}

/** Turn the compiled pass's joints into the `ada.clash/result@1` document the panel reads.
 *
 *  ASSEMBLY, NOT CLASSIFICATION. Every fact here -- which members meet, where, and the type key --
 *  comes from the C++. What this adds is the document's shape: the id (the same hash of sorted
 *  member names and origin that `run_clash_check` takes) and the grouping by type key. Nothing
 *  here decides anything about geometry.
 */
async function toClashResultDoc(
  native: { beams: number; joints: WireNativeJoint[] },
  sourceKey: string,
  options: ClashCheckOptions,
): Promise<Record<string, unknown>> {
  const joints = [] as Record<string, unknown>[];
  for (const j of native.joints) {
    joints.push({
      id: await jointId(j),
      centre: j.centre,
      members: j.members.map((m) => ({
        name: m.name,
        kind: m.kind,
        guid: m.guid || null,
        section: m.section || null,
        member_type: m.member_type || null,
      })),
      type_key: j.type_key,
      type_label: labelFor(j),
      applicable: [],
    });
  }

  const byKey = new Map<string, Record<string, unknown>[]>();
  for (const j of joints) {
    const key = j.type_key as string;
    const list = byKey.get(key);
    if (list) list.push(j);
    else byKey.set(key, [j]);
  }

  return {
    schema: "ada.clash/result@1",
    source_key: sourceKey,
    options,
    counts: {
      members: native.beams,
      beams: native.beams,
      plates: 0,
      joints: joints.length,
    },
    joints,
    groups: [...byKey.entries()].map(([key, list]) => ({
      type_key: key,
      type_label: list[0].type_label,
      count: list.length,
      joint_ids: list.map((j) => j.id),
      applicable: [],
    })),
    provenance: { reader: "adacpp-wasm", pass: "beam-beam" },
    warnings: [
      "plate joints were not looked for: the browser check carries no CAD kernel, so the " +
        "plate-to-beam and plate-to-plate passes did not run. Run the check on the server " +
        "to include them.",
      "no generators are offered: which spec could detail a joint is resolved server-side.",
    ],
  };
}

/** `sha256("<origin>|<sorted member names>")[:12]` -- byte for byte what `run_clash_check` takes,
 *  so a joint picked here is the joint a server-side detail run re-derives. */
async function jointId(j: WireNativeJoint): Promise<string> {
  const names = j.members.map((m) => m.name).sort();
  const data = new TextEncoder().encode([j.origin, ...names].join("|"));
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 12);
}

/** The same sentence `type_label_for` builds, from the same parts. */
function labelFor(j: WireNativeJoint): string {
  const kinds = [...new Set(j.members.map((m) => m.kind))].sort();
  const sections = [
    ...new Set(j.members.map((m) => m.section).filter(Boolean)),
  ].sort();
  const types = j.members
    .map((m) => m.member_type)
    .filter(Boolean)
    .sort();
  const bits = [`${j.members.length} × ${kinds.join("/")}`];
  if (sections.length) bits.push(sections.join("/"));
  if (types.length) bits.push(types.join("→"));
  const bucket = j.type_key.split("|").pop();
  if (bucket && bucket !== "unknown") bits.push(bucket);
  return bits.join(" · ");
}

/** Run the whole check in the browser. */
export async function runBrowserClashCheck(
  sourceKey: string,
  bytes: ArrayBuffer,
  options: ClashCheckOptions = {},
  onProgress?: BrowserClashProgress,
  deps: BrowserClashDeps = realBrowserClashDeps,
): Promise<BrowserClashCheckResult> {
  const t0 = performance.now();
  onProgress?.("checking");

  const native = await deps.clashJoints(bytes, {
    outOfPlaneTol: options.out_of_plane_tol ?? 0.1,
    pointTol: options.point_tol ?? 1e-5,
  });
  const parsed = JSON.parse(native.json) as {
    beams: number;
    joints: WireNativeJoint[];
  };
  const doc = await toClashResultDoc(parsed, sourceKey, options);

  return {
    result: parseClashResult(doc),
    beams: parsed.beams,
    totalMs: performance.now() - t0,
  };
}
