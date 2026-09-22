// The ONLY module that turns a delivery claim into scene content.
//
// Everything upstream of here -- `rowFacts`, the tree, the badges -- only ever
// says WHAT a node claims (a `DeliveryKind`) and WHERE that claim is rooted (a
// badge's `at` subject). Nothing upstream fetches a claim or loads a model.
// This module does both, and is the one place core reads a provider-authored
// `mesh`/`build` document and decides to trust it.
//
// React-free and side-effect-free by construction: `loadNode` takes its API
// and scene-loading calls as injected dependencies (`LoadNodeDeps`), so it is
// driven under `node --test` against fakes -- no network, no three.js. The
// real dependencies (REST calls, `SceneHandle.loadModelFromUrl`, the global
// job toast) are assembled by the UI layer that calls this, the same way
// `ExternalModelsPanel` assembles a standalone plugin context rather than
// this module importing the plugin runtime itself.
//
// TWO THINGS THIS MODULE REFUSES TO DO SILENTLY, both because a wrong load is
// worse than a slow one:
//
//   1. Build summary validation mirrors `validate_build_summary` in
//      `ada/assets/build.py` field for field, and names the field that
//      disagrees -- a builder that wrote another request's summary (a cache
//      key bug) and a stale blob under a reused key are told apart by which
//      field is wrong, and a generic "invalid summary" would erase that.
//   2. A source name that is already loaded is answered from the scene, not
//      re-fetched -- `assetSourceName` IS the scene identity (the note this
//      module implements, `notes_core_asset_browser.md` §2c), so "is this
//      loaded" is a Set lookup, never a network round trip.

import type { BuildDelivery, DeliveryClaim, MeshDelivery, WireBuildAssetResponse, WireDeliveryClaim } from "./types";

/** One node, resolved to the subject that actually carries its delivery claim.
 *
 * `subject` is the MANIFEST-OWNING node -- for a `solid` badge that IS the
 * clicked row; for a `ghost`/`below` badge (see `rowFacts`) it is the
 * COVERING ANCESTOR's subject, because core's routes read one manifest by
 * its own key and never walk ancestors themselves (`_manifest_for_node` in
 * `routes/assets.py` is a direct key read). The delivery route is asked
 * about `subject` (it only ever reads a manifest, never scopes a build).
 *
 * `node`, when present and different from `subject`, is the row the user
 * actually clicked. It is NOT just a display label: Decision 3 makes the
 * scoped node part of every build's fingerprint and derived key, so a build
 * sends `node` (the clicked row) and `subject` (the covering ancestor)
 * separately -- each covered row is its own build, with its own geometry,
 * under its own source name, rather than two rows sharing one ancestor-wide
 * GLB. */
export interface NodeRef {
  readonly provider: string;
  readonly collection: string;
  readonly subject: string;
  readonly revision: string;
  readonly node?: string;
}

/** `assets:<provider>/<collection>/<subject>@<revision>[#<node>]` -- stable,
 *  unique and revision-distinct. This is the scene identity: a node loaded
 *  through here is registered under this name (`registerLoadedSource`), so it
 *  shows up in the Files tab as an ordinary loaded root and the unload path
 *  (`unloadModel`) takes the same name back out. */
export function assetSourceName(ref: NodeRef): string {
  const suffix = ref.node && ref.node !== ref.subject ? `#${ref.node}` : "";
  return `assets:${ref.provider}/${ref.collection}/${ref.subject}@${ref.revision}${suffix}`;
}

export interface LoadedAsset {
  readonly sourceName: string;
  readonly ref: NodeRef;
  readonly revision: string;
  readonly provider: string;
  /** Present only for a `build` load -- a `mesh` load has no derived blob. */
  readonly glbKey?: string;
  readonly counts?: Readonly<Record<string, number>>;
  readonly warnings?: readonly string[];
}

/** A refusal this module makes on purpose -- an unreadable/disagreeing build
 *  summary, or a job that ended in `error`/`cancelled`. Named separately from
 *  a plain network `Error` so a caller can show "refused" rather than
 *  "failed" when it matters, though both carry their reason in `.message`. */
export class DeliveryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DeliveryError";
  }
}

// --- wire -> the browser's model ---------------------------------------------------

export function parseDeliveryClaim(wire: WireDeliveryClaim): DeliveryClaim {
  if (wire.kind === "mesh") {
    const mesh: MeshDelivery = {
      kind: "mesh",
      url: wire.url,
      headers: wire.headers,
      sourceUpAxis: wire.source_up_axis,
      revision: wire.revision,
      provider: wire.provider,
    };
    return mesh;
  }
  const build: BuildDelivery = {
    kind: "build",
    capability: wire.capability,
    options: wire.options,
    fingerprintInputs: wire.fingerprint_inputs,
    revision: wire.revision,
    provider: wire.provider,
  };
  return build;
}

// --- the build summary: parse + validate, mirroring ada/assets/build.py ------------

const BUILD_SCHEMA = "ada.assets/build@1";

export interface BuildProvenance {
  readonly provider: string;
  readonly collection: string;
  readonly subject: string;
  readonly revision: string;
  readonly node: string | null;
  readonly fingerprint: string;
  readonly builtAt: string;
  readonly adapyVersion?: string;
  readonly providerVersion?: string;
  readonly hierarchySource?: string;
}

export interface ParsedBuildSummary {
  readonly ok: boolean;
  readonly glbKey: string;
  readonly glbSize?: number;
  readonly provenance: BuildProvenance;
  readonly counts: Readonly<Record<string, number>>;
  readonly warnings: readonly string[];
  readonly error: string | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Read a build summary blob. An unknown schema is REFUSED, never partially
 *  read -- the same rule `parse_hierarchy`/`parse_manifest` hold elsewhere in
 *  this store: a document core cannot fully understand is not "mostly fine". */
export function parseBuildSummary(doc: unknown): ParsedBuildSummary {
  if (!isRecord(doc)) {
    throw new DeliveryError(`build summary must be a JSON object, got ${doc === null ? "null" : typeof doc}`);
  }
  if (doc.schema !== BUILD_SCHEMA) {
    throw new DeliveryError(
      `unknown build summary schema ${JSON.stringify(doc.schema)}: this core reads ${JSON.stringify(BUILD_SCHEMA)} only. ` +
        `Refusing rather than reading the fields it recognises.`,
    );
  }
  const provRaw = doc.provenance;
  if (!isRecord(provRaw)) {
    throw new DeliveryError("build summary has no 'provenance' object -- a build must name what it built");
  }
  const required = ["provider", "collection", "subject", "revision", "fingerprint"] as const;
  const missing = required.filter((k) => !(k in provRaw));
  if (missing.length) {
    throw new DeliveryError(`provenance missing required field(s): ${missing.join(", ")}`);
  }
  const provenance: BuildProvenance = {
    provider: String(provRaw.provider),
    collection: String(provRaw.collection),
    subject: String(provRaw.subject),
    revision: String(provRaw.revision),
    node: provRaw.node == null ? null : String(provRaw.node),
    fingerprint: String(provRaw.fingerprint),
    builtAt: provRaw.built_at != null ? String(provRaw.built_at) : "",
    adapyVersion: provRaw.adapy_version != null ? String(provRaw.adapy_version) : undefined,
    providerVersion: provRaw.provider_version != null ? String(provRaw.provider_version) : undefined,
    hierarchySource: provRaw.hierarchy_source != null ? String(provRaw.hierarchy_source) : undefined,
  };
  const countsRaw = doc.counts ?? {};
  if (!isRecord(countsRaw)) {
    throw new DeliveryError(`'counts' must be an object, got ${typeof countsRaw}`);
  }
  const counts: Record<string, number> = {};
  for (const [k, v] of Object.entries(countsRaw)) counts[k] = Number(v);
  return {
    ok: Boolean(doc.ok ?? false),
    glbKey: doc.glb_key != null ? String(doc.glb_key) : "",
    glbSize: typeof doc.glb_size === "number" ? doc.glb_size : undefined,
    provenance,
    counts,
    warnings: Array.isArray(doc.warnings) ? doc.warnings.map(String) : [],
    error: doc.error != null ? String(doc.error) : null,
  };
}

/** Refuse a summary that does not answer THIS request, naming the field that
 *  disagrees. Ported from `validate_build_summary` (`ada/assets/build.py`):
 *  core validates identity -- the fields the derived key was composed from --
 *  and that `glb_key` sits under the prefix core composed, and nothing else;
 *  everything past that in the document is the provider's word about itself. */
export function validateBuildSummary(
  summary: ParsedBuildSummary,
  request: {
    readonly provider: string;
    readonly collection: string;
    readonly subject: string;
    readonly revision: string;
    readonly node: string;
    readonly fingerprint: string;
    readonly derivedPrefix: string;
  },
): void {
  if (!summary.ok) {
    throw new DeliveryError(summary.error || "build reported ok=false without an error");
  }
  const p = summary.provenance;
  const checks: readonly [string, string, string | null][] = [
    ["provider", request.provider, p.provider],
    ["collection", request.collection, p.collection],
    ["subject", request.subject, p.subject],
    ["revision", request.revision, p.revision],
    ["node", request.node, p.node],
    ["fingerprint", request.fingerprint, p.fingerprint],
  ];
  for (const [field, want, got] of checks) {
    if (want !== got) {
      throw new DeliveryError(
        `build summary answers a different request: provenance.${field} is ${JSON.stringify(got)}, ` +
          `this request is ${JSON.stringify(want)}`,
      );
    }
  }
  if (!summary.glbKey) {
    throw new DeliveryError("build summary has no 'glb_key'");
  }
  if (!summary.glbKey.startsWith(`${request.derivedPrefix}/`)) {
    throw new DeliveryError(
      `build summary's glb_key ${JSON.stringify(summary.glbKey)} is outside the prefix core composed ` +
        `(${request.derivedPrefix}/) -- a build may only write under its own derived prefix`,
    );
  }
}

// --- loadNode ------------------------------------------------------------------

/** REST + build-summary calls `loadNode` makes. Kept narrow (three methods,
 *  wire shapes in/out) so a test double is three one-line functions. */
export interface DeliveryApi {
  /** `subject` names the manifest-owning node when it differs from `node` --
   *  a covered (`ghost`/`below`) build. Omitted, the route defaults it to
   *  `node`. The derived key and fingerprint are always scoped by `node`, so
   *  two covered nodes under the same ancestor are two builds. */
  buildAssetNode(
    scope: string,
    body: { provider: string; collection: string; node: string; subject?: string; revision?: string; force?: boolean },
  ): Promise<WireBuildAssetResponse>;
  /** The parsed build summary at `key` (the blob route auto-decompresses
   *  gzip-at-rest JSON, so the caller hands back a plain parsed document). */
  getBuildSummary(scope: string, key: string): Promise<unknown>;
  /** One poll tick of a job's status -- mirrors the subset of `ConvertResponse`
   *  every job (conversion or plugin) reports through `GET /convert/{job_id}`. */
  jobStatus(jobId: string): Promise<{ status: string; error: string | null }>;
}

/** Everything `loadNode` needs beyond the network: put a model in the scene,
 *  ask whether a source name is already there, resolve a storage key to a
 *  fetchable URL, and (optionally) hand a build job to the global toast. */
export interface LoadNodeDeps {
  api: DeliveryApi;
  loadModelFromUrl: (
    owner: string,
    url: string,
    opts?: { sourceName?: string; headers?: Record<string, string>; sourceUpAxis?: "z" | "y" },
  ) => Promise<void>;
  isLoaded: (sourceName: string) => boolean;
  blobUrl: (scope: string, key: string) => string;
  trackJob?: (opts: { jobId: string; label: string; derivedKey?: string }) => void;
  /** Poll backoff between job-status checks. Defaults to a real 1.5s timer;
   *  tests inject an instant no-op so a job/poll test runs with no fake timers
   *  and no real delay. */
  wait?: (ms: number) => Promise<void>;
  /** Clock the poll timeout is measured against. Injected for the same reason
   *  as `wait`: a test proves the give-up path without waiting ten minutes. */
  now?: () => number;
}

const OWNER = "assets";
const POLL_INTERVAL_MS = 1500;
/** How long a build may sit before the tab stops waiting on it.
 *
 * BOUNDED BECAUSE THIS DESIGN MAKES "NOBODY WILL RUN IT" REACHABLE. A build
 * claim routes to the pool advertising its capability, and a capability no
 * pool advertises is an ordinary deployment state (an image without the
 * builder, a pool scaled to zero) -- the job is then perfectly healthy and
 * simply never picked up. An unbounded poll renders that as a spinner that
 * never resolves, which is the one reading that is certainly wrong. Giving up
 * does NOT cancel the job: it may still be picked up later, and loading the
 * node again will find its summary already at the derived key. */
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

async function pollToTerminal(deps: LoadNodeDeps, jobId: string, capability: string): Promise<void> {
  const wait = deps.wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const now = deps.now ?? (() => Date.now());
  const startedAt = now();
  for (;;) {
    const status = await deps.api.jobStatus(jobId);
    if (status.status === "done") return;
    if (status.status === "error" || status.status === "cancelled") {
      throw new DeliveryError(`asset build ${status.status}${status.error ? `: ${status.error}` : ""}`);
    }
    if (now() - startedAt >= POLL_TIMEOUT_MS) {
      throw new DeliveryError(
        `asset build ${jobId} is still ${status.status} after ${Math.round(POLL_TIMEOUT_MS / 60000)} min. ` +
          `It is queued for the pool advertising '${capability}'; if no worker serves that capability ` +
          `the job waits indefinitely. The build was not cancelled -- loading this node again will pick ` +
          `up its result if it does finish.`,
      );
    }
    await wait(POLL_INTERVAL_MS);
  }
}

/** Turn one claim into scene content and hand back what got loaded.
 *
 *  `mesh`  -> resolve the URL (a storage key for `published`, already
 *             absolute for a live provider) and load it directly. No job, no
 *             derived blob, no provenance beyond the claim's own `revision`.
 *  `build` -> `POST /assets/build`; `cached: true` reads the summary straight
 *             from `derived_key` (no job was enqueued -- a REPEAT is not a
 *             job, per the route's own doc). Otherwise the job is handed to
 *             the toast (best-effort) and polled to `done` here, then the
 *             summary is read. Either way the summary is validated against
 *             the request BEFORE its `glb_key` is ever loaded.
 *
 *  A source name already in the scene short-circuits before any of the
 *  above: `assetSourceName` is scene identity, so a second load of the same
 *  node is a lookup, never a fetch. */
export async function loadNode(
  deps: LoadNodeDeps,
  scope: string,
  ref: NodeRef,
  claim: DeliveryClaim,
): Promise<LoadedAsset> {
  const sourceName = assetSourceName(ref);

  if (deps.isLoaded(sourceName)) {
    return { sourceName, ref, revision: ref.revision, provider: ref.provider };
  }

  if (claim.kind === "mesh") {
    // The built-in `published` provider mints `url` as a storage KEY, not an
    // absolute URL (`routes/assets.py`'s mesh branch: `"url": mesh_key`) --
    // told apart from a live provider's (possibly presigned) absolute URL by
    // SHAPE, never by provider id, so no provider-conditional code lands here.
    const url = /^[a-z][a-z0-9+.-]*:\/\//i.test(claim.url) ? claim.url : deps.blobUrl(scope, claim.url);
    await deps.loadModelFromUrl(OWNER, url, {
      sourceName,
      headers: claim.headers,
      sourceUpAxis: claim.sourceUpAxis,
    });
    return { sourceName, ref, revision: claim.revision, provider: claim.provider };
  }

  // `node` is the row actually built -- the clicked row for a covered load,
  // the subject itself otherwise. `subject` names the manifest-owning
  // ancestor whenever it differs, so the route reads the right claim while
  // still scoping the derived key/fingerprint to `node` (Decision 3): two
  // rows covered by the same ancestor are two builds, not one shared blob.
  const node = ref.node ?? ref.subject;
  const built = await deps.api.buildAssetNode(scope, {
    provider: ref.provider,
    collection: ref.collection,
    node,
    subject: ref.subject,
    revision: ref.revision,
  });

  if (!built.cached) {
    if (!built.job_id) {
      throw new DeliveryError("asset build reported neither a cached summary nor a job id");
    }
    deps.trackJob?.({
      jobId: built.job_id,
      label: `${ref.collection}/${node}`,
      derivedKey: built.derived_key,
    });
    await pollToTerminal(deps, built.job_id, built.capability);
  }

  const doc = await deps.api.getBuildSummary(scope, built.derived_key);
  const summary = parseBuildSummary(doc);
  const derivedPrefix = built.derived_key.slice(0, built.derived_key.lastIndexOf("/"));
  validateBuildSummary(summary, {
    provider: built.provider,
    collection: ref.collection,
    subject: built.subject,
    revision: built.revision,
    node: built.node,
    fingerprint: built.fingerprint,
    derivedPrefix,
  });

  const glbUrl = deps.blobUrl(scope, summary.glbKey);
  await deps.loadModelFromUrl(OWNER, glbUrl, { sourceName });
  return {
    sourceName,
    ref,
    revision: built.revision,
    provider: built.provider,
    glbKey: summary.glbKey,
    counts: summary.counts,
    warnings: summary.warnings.length ? summary.warnings : undefined,
  };
}
