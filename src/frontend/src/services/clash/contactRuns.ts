// Convex-contact clash RUNS: the clash check on the viewer GLB, run in the browser or on a worker, and
// kept as a sidecar beside its model (`ada.clash.sidecar`, schema `ada.clash/contacts@1`).
//
// SAME BYTES, SAME C++. The browser measures the GLB it loaded with adacpp's wasm module; a worker
// measures the same stored GLB with the same C++ through Python. A browser run is posted to
// `/clash-contacts/runs`, which validates it, stores it and asks a worker which connection specs could
// detail each clash -- the one thing a browser has no registry for. Either way the run outlives the
// page: it is listed per target and can be reopened, and it is what a detail is made FROM (the stored
// contact points, normal, distance and patch polygons; nothing is re-measured).
//
// Everything here is plain functions over injected deps, so it runs under `node --test`.

import { runtime } from "@/runtime/config";
import { authedFetch, jsonOrThrow, type ScopeUrl } from "@/services/api/client";
import { filesApi } from "@/services/api/files";

export const CONTACT_ORIGIN = "convex-contact";
export const CONTACT_RUN_SCHEMA = "ada.clash/contacts@1";

type Vec3 = [number, number, number];

/** What a run was checked on -- the wire form the server normalises to. */
export type ContactTarget =
  | { kind: "file"; source_key: string }
  | { kind: "node"; collection: string; subject: string; revision?: string; node?: string };

export interface ContactPatchDoc {
  normal: Vec3;
  points1: Vec3[];
  points2: Vec3[];
}

export interface ContactRecordDoc {
  id: string;
  members: [number, number];
  near_points: [Vec3, Vec3];
  normal: Vec3;
  /** coal's signed distance: negative is overlap, positive a gap inside the margin. */
  distance: number;
  contact_area: number;
  patches: ContactPatchDoc[];
  applicable?: { spec: string; capability?: string | null; tags?: string[]; priority?: number }[];
}

export interface ContactRunDoc {
  schema: typeof CONTACT_RUN_SCHEMA;
  run_id: string;
  created_at: string;
  engine: "wasm" | "worker";
  checker?: string;
  target: ContactTarget;
  geometry: { key: string; sha256: string; up_axis: "z" | "y" };
  options: { security_margin: number; min_contact_area: number; line_contact_thickness: number };
  members: { id: string; name: string }[];
  contacts: ContactRecordDoc[];
  provenance: Record<string, unknown>;
}

export interface ContactRunSummary {
  run_id: string;
  key: string;
  created_at: string;
  engine: string;
  contacts: number;
  members: number;
  options?: Record<string, unknown>;
}

/** The raw columns `findGlbContacts` returns (see utils/clashContacts). */
export interface GlbContactsColumns {
  memberIds: readonly string[];
  memberNames: readonly string[];
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
  candidatePairs?: number;
  belowMinArea?: number;
  degenerate?: ArrayLike<number>;
}

// ── targets ─────────────────────────────────────────────────────────────────────────────────

/** The viewer GLB of a file source -- `derivedKeyForGlb`'s rule (a `.glb` source is its own GLB),
 *  restated here so this module does not pull the scene code in. */
export function glbKeyForSource(sourceKey: string): string {
  return sourceKey.toLowerCase().endsWith(".glb") ? sourceKey : `_derived/${sourceKey}.glb`;
}

/** The run target for what the panel is checking: a file, or a published node. A group spans
 *  several GLBs and has no single run target (null). */
export function contactTargetFor(
  sourceKey: string | null,
  assetTarget: { kind: string; collection?: string; subject?: string; revision?: string | null; node?: string | null } | null,
): ContactTarget | null {
  if (assetTarget) {
    if (assetTarget.kind !== "node" || !assetTarget.collection || !assetTarget.subject) return null;
    const t: ContactTarget = { kind: "node", collection: assetTarget.collection, subject: assetTarget.subject };
    if (assetTarget.revision) t.revision = assetTarget.revision;
    if (assetTarget.node) t.node = assetTarget.node;
    return t;
  }
  return sourceKey ? { kind: "file", source_key: sourceKey } : null;
}

// ── identity ────────────────────────────────────────────────────────────────────────────────

export async function sha256Hex(data: ArrayBuffer | Uint8Array | string): Promise<string> {
  const bytes = typeof data === "string" ? new TextEncoder().encode(data) : data instanceof Uint8Array ? data : new Uint8Array(data);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** `sha256("convex-contact|<sorted element ids>")[:12]` -- `ada.clash.sidecar.contact_id`, byte for byte.
 *  Element ids, not names: names may repeat, element ids do not. */
export async function contactId(elementIds: readonly string[]): Promise<string> {
  return (await sha256Hex([CONTACT_ORIGIN, ...[...elementIds].sort()].join("|"))).slice(0, 12);
}

function vec3(a: ArrayLike<number>, i: number): Vec3 {
  return [a[3 * i], a[3 * i + 1], a[3 * i + 2]];
}

// ── building a run from the wasm columns ────────────────────────────────────────────────────

export interface RunMeta {
  runId: string;
  createdAt: string;
  target: ContactTarget;
  glbKey: string;
  glbSha256: string;
  upAxis: "z" | "y";
  options: ContactRunDoc["options"];
  provenance?: Record<string, unknown>;
}

/** The run document for a browser run -- the shape `ada.clash.sidecar.parse_run` accepts. */
export async function runDocFromColumns(raw: GlbContactsColumns, meta: RunMeta): Promise<ContactRunDoc> {
  const members = raw.memberIds.map((id, k) => ({ id: String(id), name: String(raw.memberNames[k]) }));
  const contacts: ContactRecordDoc[] = [];
  const n = raw.penetrationDepth.length;
  for (let q = 0; q < n; q++) {
    const i = raw.pairs[2 * q];
    const j = raw.pairs[2 * q + 1];
    const patches: ContactPatchDoc[] = [];
    for (let p = raw.patchOffsets[q]; p < raw.patchOffsets[q + 1]; p++) {
      const points1: Vec3[] = [];
      const points2: Vec3[] = [];
      for (let v = raw.patchPointOffsets[p]; v < raw.patchPointOffsets[p + 1]; v++) {
        points1.push(vec3(raw.patchPoints1, v));
        points2.push(vec3(raw.patchPoints2, v));
      }
      patches.push({ normal: vec3(raw.patchNormals, p), points1, points2 });
    }
    contacts.push({
      id: await contactId([members[i].id, members[j].id]),
      members: [i, j],
      near_points: [vec3(raw.near1, q), vec3(raw.near2, q)],
      normal: vec3(raw.normal, q),
      distance: raw.penetrationDepth[q],
      contact_area: raw.contactArea[q],
      patches,
    });
  }
  return {
    schema: CONTACT_RUN_SCHEMA,
    run_id: meta.runId,
    created_at: meta.createdAt,
    engine: "wasm",
    checker: CONTACT_ORIGIN,
    target: meta.target,
    geometry: { key: meta.glbKey, sha256: meta.glbSha256, up_axis: meta.upAxis },
    options: meta.options,
    members,
    contacts,
    provenance: {
      ...(meta.provenance ?? {}),
      candidate_pairs: raw.candidatePairs ?? null,
      below_min_area: raw.belowMinArea ?? null,
      degenerate: raw.degenerate ? Array.from(raw.degenerate) : [],
    },
  };
}

// ── a run as the panel's result document ────────────────────────────────────────────────────

/** A run as an `ada.clash/result@2` document, for `parseClashResult` -- so the panel's groups,
 *  markers, isolation and spec badges work on a run unchanged. Clashes are grouped by WHICH SPECS can
 *  detail them, so a group's badge is "detail these"; a clash with no spec yet is "unassigned". The
 *  contact is written in the run's order (member i first); a detail resolves incoming / landing on
 *  the worker, from the rebuilt members. */
export function runToResultDoc(run: ContactRunDoc, runKey: string | null): Record<string, unknown> {
  const joints = run.contacts.map((c) => {
    const [i, j] = c.members;
    const [a, b] = c.near_points;
    const specs = [...(c.applicable ?? [])].map((s) => s.spec).sort();
    const typeKey = specs.length ? `2|contact|${specs.join("+")}` : "2|contact|unassigned";
    return {
      id: c.id,
      centre: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2],
      members: [
        { name: run.members[i].name, kind: "MEMBER" },
        { name: run.members[j].name, kind: "MEMBER" },
      ],
      type_key: typeKey,
      type_label: specs.length ? `contact · ${specs.join(" / ")}` : "contact · no spec resolved yet",
      origin: CONTACT_ORIGIN,
      applicable: (c.applicable ?? []).map((s) => ({
        spec: s.spec,
        capability: s.capability ?? null,
        tags: s.tags ?? [],
        priority: s.priority ?? 0,
      })),
      contact: {
        normal: c.normal,
        penetration_depth: Math.abs(c.distance),
        near_points: c.near_points,
        contact_area: c.contact_area,
        security_margin: run.options.security_margin,
        extras: { distance: c.distance, patches: c.patches, run_id: run.run_id },
      },
    };
  });
  const byKey = new Map<string, typeof joints>();
  for (const j of joints) {
    const list = byKey.get(j.type_key);
    if (list) list.push(j);
    else byKey.set(j.type_key, [j]);
  }
  return {
    schema: "ada.clash/result@2",
    source_key: run.target.kind === "file" ? run.target.source_key : `${run.target.collection}/${run.target.subject}`,
    options: { checker: CONTACT_ORIGIN, checker_options: run.options },
    counts: { members: run.members.length, joints: joints.length },
    joints,
    groups: [...byKey.entries()].map(([key, list]) => ({
      type_key: key,
      type_label: list[0].type_label,
      count: list.length,
      joint_ids: list.map((j) => j.id),
      applicable: list[0].applicable,
    })),
    provenance: {
      reader: run.engine === "wasm" ? "adacpp-wasm" : "worker",
      run_id: run.run_id,
      run_key: runKey,
      created_at: run.created_at,
      geometry: run.geometry,
      ...run.provenance,
    },
    warnings: run.contacts.some((c) => !c.applicable?.length) && run.provenance?.applicability === undefined
      ? ["which specs could detail each clash is still being resolved on a worker; reopen the run to see them."]
      : [],
    passes: [{ name: CONTACT_ORIGIN, ran: true, found: joints.length }],
    checker: CONTACT_ORIGIN,
  };
}

// ── REST ────────────────────────────────────────────────────────────────────────────────────

function base(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/clash-contacts`;
}

async function postJson<T>(url: string, body: unknown, what: string): Promise<T> {
  const r = await authedFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return jsonOrThrow<T>(r, what);
}

export const contactRunsApi = {
  runOnWorker(scope: ScopeUrl, body: { target: ContactTarget; glb_key: string; up_axis: "z" | "y"; options: Record<string, number> }) {
    return postJson<{ job_id: string; run_key: string }>(base(scope), body, "clash-contacts");
  },
  storeRun(scope: ScopeUrl, run: ContactRunDoc) {
    return postJson<{ run_key: string; summary: ContactRunSummary; job_id: string | null }>(
      `${base(scope)}/runs`,
      { run },
      "clash-contacts/runs",
    );
  },
  async listRuns(scope: ScopeUrl, target: ContactTarget): Promise<ContactRunSummary[]> {
    const r = await authedFetch(`${base(scope)}/runs?target=${encodeURIComponent(JSON.stringify(target))}`);
    return (await jsonOrThrow<{ runs?: ContactRunSummary[] }>(r, "clash-contacts runs")).runs ?? [];
  },
  async getRun(scope: ScopeUrl, key: string): Promise<ContactRunDoc> {
    const r = await authedFetch(`${base(scope)}/run?key=${encodeURIComponent(key)}`);
    return jsonOrThrow<ContactRunDoc>(r, "clash-contacts run");
  },
  detail(scope: ScopeUrl, body: { run_key: string; contact_ids: readonly string[]; spec: string; options?: Record<string, unknown> }) {
    return postJson<{ job_id: string; derived_key: string; glb_key: string }>(
      `${base(scope)}/detail`,
      { ...body, options: body.options ?? {} },
      `clash-contacts/detail(${body.spec})`,
    );
  },
  async getBytes(scope: ScopeUrl, key: string): Promise<ArrayBuffer> {
    const r = await authedFetch(filesApi.blobUrl(scope, key));
    if (!r.ok) throw new Error(`could not read ${key} (${r.status}) -- has the model been converted?`);
    return r.arrayBuffer();
  },
  async getJson(scope: ScopeUrl, key: string): Promise<unknown> {
    return jsonOrThrow<unknown>(await authedFetch(filesApi.blobUrl(scope, key)), key);
  },
};

// ── flows ───────────────────────────────────────────────────────────────────────────────────

export interface ContactFlowDeps {
  api: Pick<typeof contactRunsApi, "runOnWorker" | "storeRun" | "getRun" | "detail" | "getBytes" | "getJson">;
  /** The wasm check (utils/clashContacts). */
  measure(glb: ArrayBuffer, opts: { securityMargin: number; minContactArea: number; lineContactThickness: number; upAxis: "z" | "y" }): Promise<GlbContactsColumns>;
  jobStatus(jobId: string): Promise<{ status: string; error: string | null }>;
  trackJob?(opts: { jobId: string; label: string; derivedKey?: string }): void;
  wait?(ms: number): Promise<void>;
  now?(): number;
  newRunId?(): string;
  isoNow?(): string;
}

export interface ContactRunRequest {
  target: ContactTarget;
  glbKey: string;
  upAxis: "z" | "y";
  options: ContactRunDoc["options"];
}

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

async function pollJob(deps: ContactFlowDeps, jobId: string, label: string): Promise<void> {
  const wait = deps.wait ?? ((ms: number) => new Promise<void>((res) => setTimeout(res, ms)));
  const now = deps.now ?? (() => Date.now());
  const start = now();
  for (;;) {
    const { status, error } = await deps.jobStatus(jobId);
    if (status === "done") return;
    if (status === "error" || status === "cancelled") throw new Error(`${label} ${status}: ${error ?? "no reason given"}`);
    if (now() - start > POLL_TIMEOUT_MS) throw new Error(`${label} did not finish in ${POLL_TIMEOUT_MS / 60000} minutes`);
    await wait(POLL_INTERVAL_MS);
  }
}

function newRunId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

/** Measure the GLB in the browser, store the run, wait for its specs. Returns the stored run. */
export async function runBrowserContactFlow(
  deps: ContactFlowDeps,
  scope: ScopeUrl,
  req: ContactRunRequest,
  onStage?: (stage: string) => void,
): Promise<{ run: ContactRunDoc; runKey: string }> {
  onStage?.("reading geometry");
  const glb = await deps.api.getBytes(scope, req.glbKey);
  const glbSha256 = await sha256Hex(glb);
  onStage?.("checking");
  // `measure` may transfer the buffer away; the hash is taken first.
  const raw = await deps.measure(glb, {
    securityMargin: req.options.security_margin,
    minContactArea: req.options.min_contact_area,
    lineContactThickness: req.options.line_contact_thickness,
    upAxis: req.upAxis,
  });
  const run = await runDocFromColumns(raw, {
    runId: (deps.newRunId ?? newRunId)(),
    createdAt: (deps.isoNow ?? (() => new Date().toISOString().replace(/\.\d+Z$/, "Z")))(),
    target: req.target,
    glbKey: req.glbKey,
    glbSha256,
    upAxis: req.upAxis,
    options: req.options,
  });
  onStage?.("saving");
  const stored = await deps.api.storeRun(scope, run);
  if (stored.job_id) {
    onStage?.("resolving specs");
    deps.trackJob?.({ jobId: stored.job_id, label: "Clash specs", derivedKey: stored.run_key });
    try {
      await pollJob(deps, stored.job_id, "resolving specs");
    } catch {
      // The run is stored and good; only its specs are missing. Shown as such, not as a failure.
      return { run, runKey: stored.run_key };
    }
    return { run: await deps.api.getRun(scope, stored.run_key), runKey: stored.run_key };
  }
  return { run, runKey: stored.run_key };
}

/** Run the check on a worker over the stored GLB. Returns the stored run. */
export async function runWorkerContactFlow(
  deps: ContactFlowDeps,
  scope: ScopeUrl,
  req: ContactRunRequest,
): Promise<{ run: ContactRunDoc; runKey: string }> {
  const { job_id, run_key } = await deps.api.runOnWorker(scope, {
    target: req.target,
    glb_key: req.glbKey,
    up_axis: req.upAxis,
    options: { ...req.options },
  });
  deps.trackJob?.({ jobId: job_id, label: "Clash check", derivedKey: run_key });
  await pollJob(deps, job_id, "clash check");
  return { run: await deps.api.getRun(scope, run_key), runKey: run_key };
}

export interface ContactDetailStats {
  joints: unknown;
  skipped: string[];
}

/** Detail clashes of a stored run with a spec. */
export async function runContactDetailFlow(
  deps: ContactFlowDeps,
  scope: ScopeUrl,
  runKey: string,
  contactIds: readonly string[],
  spec: string,
): Promise<{ derivedKey: string; glbKey: string; stats: ContactDetailStats }> {
  const resp = await deps.api.detail(scope, { run_key: runKey, contact_ids: contactIds, spec });
  deps.trackJob?.({ jobId: resp.job_id, label: `Detail: ${spec}`, derivedKey: resp.derived_key });
  await pollJob(deps, resp.job_id, "detail");
  let stats: ContactDetailStats = { joints: null, skipped: [] };
  try {
    const raw = (await deps.api.getJson(scope, resp.derived_key)) as { joints?: unknown; skipped?: unknown };
    stats = { joints: raw?.joints ?? null, skipped: Array.isArray(raw?.skipped) ? raw.skipped.map(String) : [] };
  } catch {
    // The model is built; a missing take-off is not a failed detail.
  }
  return { derivedKey: resp.derived_key, glbKey: resp.glb_key, stats };
}
