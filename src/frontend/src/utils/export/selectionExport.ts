// "Download as STEP / IFC" for the selected object: which selection it applies to, whether the
// loaded model can be exported at all, and the request/poll/download flow.
//
// WHY A SELECTION CAN BE EXPORTED AT ALL. The scene tree is built from the GLB's `id_hierarchy`
// -- a name and a parent per node -- and the GLB is triangles. The server therefore re-reads the
// model (the source file, or a provider's own objects) and finds the selection again BY NAME,
// which is why the request carries the row's name and the names of the rows above it: names
// repeat, and the path is what tells two same-named plates in two decks apart.
//
// WHAT CANNOT BE EXPORTED, and is said so rather than attempted -- the button is disabled with
// the reason, never left to fail two polls later:
//
//   - no server (a notebook, a file opened from disk): there is nothing to re-read the model;
//   - a GLB, or a source whose tree is not built from objects (a FEM deck): nothing behind it;
//   - a provider's node delivered as a MESH, read from that provider: triangles only, so no STEP;
//     and an IFC would be a tessellated shell of what the user thinks they are downloading. Read
//     from another provider that publishes objects for the same elements, it can be
//     (`planGroupExport`'s "Geometry from").
//
// SEVERAL ELEMENTS go to the route as a `group` and come back as ONE file, each element under the
// levels it sits in at its source.
//
// React-free and store-free (types only), so it runs under `node --test` without stubs; the
// panel assembles the real dependencies, as `assets/delivery.ts`'s `loadNode` callers do.

import type { NodeRef } from "@/assets/delivery";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import type { WireGeometryProvider } from "@/services/api/clashCheck";
import type { GroupMember } from "@/utils/groups/savedGroups";
import type { TreeIndices } from "@/utils/tree_view/treeGraph";

export type SelectionExportFormat = "step" | "ifc";

/** The two neutral formats adapy writes from its own objects, in the order the menu offers them. */
export const SELECTION_EXPORT_FORMATS: readonly { readonly format: SelectionExportFormat; readonly label: string }[] = [
  { format: "step", label: "STEP (.step)" },
  { format: "ifc", label: "IFC (.ifc)" },
];

/** Sources whose tree a re-read can find a selection in -- mirrors `SELECTION_SOURCE_EXTS` in
 *  `ada/comms/rest/selection_export.py`, which is authoritative (the route refuses the rest). */
export const SELECTION_SOURCE_EXTS: ReadonlySet<string> = new Set([".ifc", ".step", ".stp", ".xml", ".gnx", ".sat", ".acis"]);

/** WHAT is exported: a file core reads, or a published node its provider reads. */
export type SelectionExportTarget =
  | { readonly kind: "file"; readonly sourceKey: string }
  | {
      readonly kind: "node";
      readonly provider: string;
      readonly collection: string;
      readonly subject: string;
      readonly revision: string;
      /** The node the viewer loaded -- a covered row's own id, else the subject. */
      readonly node: string;
    };

export interface SelectionExportRequest {
  readonly target: SelectionExportTarget;
  /** The selected row's name; `null` for a model's root row, which means the whole model. */
  readonly element: string | null;
  /** Row names from below the model's root row down to `element`, inclusive. */
  readonly path: readonly string[];
  /** The model as the tree names it -- names the download when the whole model is exported. */
  readonly label: string;
}

export type SelectionExportPlan =
  | { readonly ok: true; readonly request: SelectionExportRequest }
  | { readonly ok: false; readonly reason: string };

/** What the asset browser recorded about a model it loaded (`LoadedAsset`, narrowed). */
export interface LoadedAssetFacts {
  readonly sourceName: string;
  readonly ref: NodeRef;
  /** Present for a `build` load only -- a `mesh` load has no derived GLB. */
  readonly glbKey?: string;
}

const ASSET_SOURCE_RE = /^assets:([^/]+)\/([^/]+)\/([^/@#]+)@([^#]+)(?:#(.+))?$/;

/** The node behind an asset source name (`assetSourceName`: `assets:<provider>/<collection>/
 *  <subject>@<revision>[#<node>]`). Safe to split on `/`, `@` and `#`: none of them is in the
 *  asset key alphabet (`@/assets/keys`). Null for any other source name. */
export function parseAssetSourceName(sourceName: string): NodeRef | null {
  const m = ASSET_SOURCE_RE.exec(sourceName);
  if (!m) return null;
  const [, provider, collection, subject, revision, node] = m;
  return { provider, collection, subject, revision, ...(node ? { node } : {}) };
}

/** The loaded model's ROOT row that `node` sits under: the ancestor whose parent is the synthetic
 *  container (`cacheAndBuildTree`'s `__roots__`). Null when `node` is not in this tree. */
export function modelRootOf(node: TreeNodeData, idx: TreeIndices): TreeNodeData | null {
  let cur: TreeNodeData | undefined = idx.byId.get(node.id);
  if (!cur || cur === idx.root) return null;
  for (let parent = idx.parent.get(cur.id); parent && parent !== idx.root; parent = idx.parent.get(cur.id)) {
    cur = parent;
  }
  return cur ?? null;
}

/** Row names from just below `root` down to `node`, inclusive. The root row is left out on
 *  purpose: the viewer relabels it (a site label, a file name, the Names / IDs toggle), so its
 *  name says nothing about the model's own top. */
export function elementPath(node: TreeNodeData, root: TreeNodeData, idx: TreeIndices): string[] {
  const names: string[] = [];
  for (let cur: TreeNodeData | undefined = node; cur && cur.id !== root.id; cur = idx.parent.get(cur.id)) {
    names.push(cur.name);
  }
  return names.reverse();
}

/** Which ONE tree row the panel's selection stands for, or null when it is not one row.
 *
 *  `selectedNodeId` is set by a tree click and NOT cleared by a click in the 3D view, so it can
 *  be stale. It is trusted only while it still explains the selection: its name is the one the
 *  panel shows and every selected range lies under it (a tree click on a level selects exactly
 *  its geometry). Otherwise a single picked range is resolved to its row by its unique
 *  `(model key, range id)` -- never by name, which repeats. Several picked ranges with no row
 *  that holds them all are not one selection, and the export says so. */
export function resolveSelectedRow(args: {
  readonly indices: TreeIndices | null;
  readonly selectedNodeId: string | null;
  readonly name: string | null;
  readonly ranges: readonly (readonly [modelKey: string, rangeId: string])[];
  readonly findByRange: (modelKey: string, rangeId: string) => TreeNodeData | null;
}): TreeNodeData | null {
  const { indices, selectedNodeId, name, ranges, findByRange } = args;
  if (!indices || !name) return null;
  const fromTree = selectedNodeId ? indices.byId.get(selectedNodeId) : undefined;
  if (fromTree && fromTree.name === name && ranges.every(([k, r]) => isUnder(findByRange(k, r), fromTree, indices))) {
    return fromTree;
  }
  if (ranges.length === 1) {
    const row = findByRange(ranges[0][0], ranges[0][1]);
    return row && row.name === name ? (indices.byId.get(row.id) ?? row) : null;
  }
  return null;
}

function isUnder(row: TreeNodeData | null, ancestor: TreeNodeData, idx: TreeIndices): boolean {
  for (let cur: TreeNodeData | undefined = row ?? undefined; cur; cur = idx.parent.get(cur.id)) {
    if (cur.id === ancestor.id) return true;
  }
  return false;
}

function extOf(key: string): string {
  const base = key.split("/").pop() ?? key;
  const dot = base.lastIndexOf(".");
  return dot > 0 ? base.slice(dot).toLowerCase() : "";
}

/** Whether the selection can be downloaded, and the request if it can. */
export function planSelectionExport(args: {
  /** The viewer talks to a REST server (`runtime.isRestMode()`). */
  readonly restMode: boolean;
  readonly row: TreeNodeData | null;
  readonly indices: TreeIndices | null;
  readonly loadedAssets: readonly LoadedAssetFacts[];
}): SelectionExportPlan {
  const { restMode, row, indices, loadedAssets } = args;
  if (!restMode) {
    return { ok: false, reason: "Downloads are written by the server, and this viewer is not connected to one." };
  }
  if (!row || !indices) {
    return { ok: false, reason: "Select one object, or one level in the tree, to download it." };
  }
  const root = modelRootOf(row, indices);
  const sourceName = root?.source_name ?? null;
  if (!root || !sourceName) {
    return { ok: false, reason: "This model was not loaded from storage, so there is no source to export from." };
  }
  const element = row.id === root.id ? null : row.name;
  const path = element === null ? [] : elementPath(row, root, indices);
  const label = root.top_name || root.name;

  if (sourceName.startsWith("assets:")) {
    const loaded = loadedAssets.find((a) => a.sourceName === sourceName);
    const ref = loaded?.ref ?? parseAssetSourceName(sourceName);
    if (!ref) {
      return { ok: false, reason: "This model's source name does not say which published node it came from." };
    }
    if (loaded && !loaded.glbKey) {
      return {
        ok: false,
        reason:
          `${ref.provider} delivered this model as a mesh: there are no objects behind it to write a STEP ` +
          `from, and an IFC would only be its triangles.`,
      };
    }
    return {
      ok: true,
      request: {
        target: {
          kind: "node",
          provider: ref.provider,
          collection: ref.collection,
          subject: ref.subject,
          revision: ref.revision,
          node: ref.node ?? ref.subject,
        },
        element,
        path,
        label,
      },
    };
  }

  const ext = extOf(sourceName);
  if (ext === ".glb" || ext === ".gltf") {
    return { ok: false, reason: "This model is a GLB -- triangles with no source model behind them to export." };
  }
  if (!SELECTION_SOURCE_EXTS.has(ext)) {
    return {
      ok: false,
      reason: `A ${ext || "extensionless"} source cannot be exported by selection: its tree is not built from objects a STEP or IFC can hold.`,
    };
  }
  return { ok: true, request: { target: { kind: "file", sourceKey: sourceName }, element, path, label } };
}

/** The route's body for `request` in `format` -- exactly one way of naming the model. */
export function selectionExportBody(request: SelectionExportRequest, format: SelectionExportFormat): Record<string, unknown> {
  const { target } = request;
  const named =
    target.kind === "file"
      ? { source_key: target.sourceKey }
      : {
          provider: target.provider,
          collection: target.collection,
          subject: target.subject,
          revision: target.revision,
          node: target.node,
        };
  return {
    ...named,
    format,
    element: request.element,
    path: request.path,
    label: request.label,
  };
}

// --- several elements, and where their geometry is read from --------------------------------

/** One choice of the "Geometry from" select: a provider id, or `null` for each model's own. */
export interface GeometrySourceChoice {
  readonly id: string | null;
  readonly label: string;
  readonly disabled: boolean;
  readonly title?: string;
}

export interface GroupExportPlan {
  readonly members: readonly GroupMember[];
  /** Providers among the members that cannot be written from: delivered as a mesh, or not readable
   *  into objects by the server. */
  readonly meshProviders: readonly string[];
  /** Whether any member is a published node, so a geometry provider applies at all. */
  readonly hasNodes: boolean;
  readonly choices: readonly GeometrySourceChoice[];
  /** What the select starts on: a readable provider when a member is mesh-only, else each model's own. */
  readonly defaultProvider: string | null;
}

function sameNode(a: NodeRef, t: Extract<GroupMember["target"], { kind: "node" }>): boolean {
  return a.provider === t.provider && a.collection === t.collection && a.subject === t.subject && (a.revision || null) === (t.revision || null);
}

/** Which providers the members' models were delivered by as a MESH: a load the asset browser
 *  recorded without a derived GLB key. */
export function meshDeliveredProviders(members: readonly GroupMember[], loadedAssets: readonly LoadedAssetFacts[]): string[] {
  const out = new Set<string>();
  for (const m of members) {
    if (m.target.kind !== "node") continue;
    const t = m.target;
    const loaded = loadedAssets.find((a) => sameNode(a.ref, t));
    if (loaded && !loaded.glbKey) out.add(t.provider);
  }
  return [...out].sort();
}

/** The "Geometry from" choices and the default for `members`. `providers` is the server's list of
 *  providers a live pool can read into objects; `null` when it is not known. */
export function planGroupExport(args: {
  readonly members: readonly GroupMember[];
  readonly loadedAssets: readonly LoadedAssetFacts[];
  readonly providers: readonly WireGeometryProvider[] | null;
  readonly providerName?: (id: string) => string;
}): GroupExportPlan {
  const { members, loadedAssets, providers } = args;
  const name = args.providerName ?? ((id: string) => id);
  // Members that cannot be written from their own provider: delivered as a mesh, or from a provider
  // the server says it cannot read into objects (or does not list at all, once the list is known).
  const readable = new Set((providers ?? []).filter((p) => p.readable).map((p) => p.id));
  const unreadable = new Set(meshDeliveredProviders(members, loadedAssets));
  if (providers !== null) {
    for (const m of members) if (m.target.kind === "node" && !readable.has(m.target.provider)) unreadable.add(m.target.provider);
  }
  const meshProviders = [...unreadable].sort();
  const hasNodes = members.some((m) => m.target.kind === "node");
  const own: GeometrySourceChoice = meshProviders.length
    ? {
        id: null,
        label: "Each model's own provider",
        disabled: true,
        title: `${meshProviders.map(name).join(", ")} cannot be read into objects here: nothing to write a STEP or IFC from`,
      }
    : { id: null, label: "Each model's own provider", disabled: false };
  const listed: GeometrySourceChoice[] = (providers ?? [])
    .filter((p) => !meshProviders.includes(p.id))
    .map((p) => ({
      id: p.id,
      label: p.label || name(p.id),
      disabled: !p.readable,
      title: p.readable ? undefined : p.unavailable_reason || "can't be read into objects right now",
    }))
    .sort((a, b) => Number(a.disabled) - Number(b.disabled) || a.label.localeCompare(b.label));
  const firstReadable = listed.find((c) => !c.disabled)?.id ?? null;
  return {
    members,
    meshProviders,
    hasNodes,
    choices: hasNodes ? [own, ...listed] : [own],
    defaultProvider: meshProviders.length ? firstReadable : null,
  };
}

/** Why the export cannot run with `provider`, or null when it can. */
export function groupExportBlocker(plan: GroupExportPlan, provider: string | null): string | null {
  if (plan.members.length === 0) return "Nothing selected belongs to a model loaded from storage.";
  if (provider === null && plan.meshProviders.length) {
    const readable = plan.choices.some((c) => c.id !== null && !c.disabled);
    return readable
      ? `${plan.meshProviders.join(", ")} cannot be read into objects: choose a provider to read the selection from.`
      : `${plan.meshProviders.join(", ")} cannot be read into objects, and no provider that publishes objects ` +
          "can be read right now.";
  }
  return null;
}

/** The route's body for several elements written as one file. */
export function groupExportBody(
  members: readonly GroupMember[],
  label: string,
  format: SelectionExportFormat,
  provider: string | null,
): Record<string, unknown> {
  return {
    group: { name: label, members },
    format,
    label,
    ...(provider ? { geometry_provider: provider } : {}),
  };
}

/** What a several-element export's `.log.json` says (`{geometry_provider, members, warnings}`), and
 *  -- when the export FAILED in a way the server describes as data -- `error` (`{code, message, …}`,
 *  read by `noProviderGeometryOf`). An older server writes no log for a failure. */
export interface GroupExportLog {
  readonly geometry_provider: string | null;
  readonly members: number;
  readonly warnings: readonly string[];
  readonly error?: unknown;
}

// --- the flow ------------------------------------------------------------------------------

export interface SelectionExportDeps {
  readonly api: {
    exportSelection(
      scope: string,
      body: Record<string, unknown>,
    ): Promise<{ job_id: string | null; derived_key: string; cached: boolean; filename: string }>;
    jobStatus(jobId: string): Promise<{ status: string; error: string | null }>;
  };
  readonly download: (scope: string, key: string, filename: string) => Promise<void>;
  readonly trackJob?: (opts: { jobId: string; label: string; derivedKey?: string }) => void;
  /** Poll backoff. Defaults to a real 1.5 s timer; tests inject an instant no-op. */
  readonly wait?: (ms: number) => Promise<void>;
  readonly now?: () => number;
}

export class SelectionExportError extends Error {
  /** The failed job and where its file would have been, when a job ran: a group export's
   *  `.log.json` there may say more than the message (`noProviderGeometryOf`). */
  readonly jobId?: string;
  readonly derivedKey?: string;

  constructor(message: string, job?: { jobId: string; derivedKey: string }) {
    super(message);
    this.name = "SelectionExportError";
    if (job) {
      this.jobId = job.jobId;
      this.derivedKey = job.derivedKey;
    }
  }
}

const POLL_INTERVAL_MS = 1500;
/** Longer than a clash check's ten minutes: an export reads the whole source to find one deck and
 *  then writes B-rep, which on a large model is the slow half. Giving up does not cancel the job --
 *  asking again picks up its file once it is there. */
const POLL_TIMEOUT_MS = 30 * 60 * 1000;

/** Ask for the export, wait for it if it is a job, then download it. */
export async function runSelectionExport(
  deps: SelectionExportDeps,
  scope: string,
  request: SelectionExportRequest,
  format: SelectionExportFormat,
): Promise<{ derivedKey: string; filename: string; cached: boolean }> {
  return runExportBody(deps, scope, selectionExportBody(request, format));
}

/** `runSelectionExport` for any body the route takes (one selection, or a `group`). */
export async function runExportBody(
  deps: SelectionExportDeps,
  scope: string,
  body: Record<string, unknown>,
): Promise<{ derivedKey: string; filename: string; cached: boolean }> {
  const resp = await deps.api.exportSelection(scope, body);
  if (!resp.cached) {
    if (!resp.job_id) throw new SelectionExportError("the export reported neither a stored file nor a job id");
    deps.trackJob?.({ jobId: resp.job_id, label: `Export: ${resp.filename}`, derivedKey: resp.derived_key });
    const wait = deps.wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
    const now = deps.now ?? (() => Date.now());
    const startedAt = now();
    for (;;) {
      const status = await deps.api.jobStatus(resp.job_id);
      if (status.status === "done") break;
      if (status.status === "error" || status.status === "cancelled") {
        throw new SelectionExportError(status.error || `the export ${status.status}`, {
          jobId: resp.job_id,
          derivedKey: resp.derived_key,
        });
      }
      if (now() - startedAt >= POLL_TIMEOUT_MS) {
        throw new SelectionExportError(
          `the export is still ${status.status} after ${Math.round(POLL_TIMEOUT_MS / 60000)} min. It was not ` +
            `cancelled -- asking again will download it once it is written.`,
        );
      }
      await wait(POLL_INTERVAL_MS);
    }
  }
  await deps.download(scope, resp.derived_key, resp.filename);
  return { derivedKey: resp.derived_key, filename: resp.filename, cached: resp.cached };
}

/** What a failed request says, for the panel: the route's `detail` when it sent one. */
export function describeExportError(e: unknown): string {
  const detail = (e as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail) {
    try {
      const parsed = JSON.parse(detail) as { detail?: unknown };
      if (typeof parsed.detail === "string") return parsed.detail;
    } catch {
      // Not JSON: the body itself is the best explanation there is.
    }
    return detail;
  }
  return e instanceof Error ? e.message : String(e);
}
