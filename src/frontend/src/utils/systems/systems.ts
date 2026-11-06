// Systems and related elements, from the relations index -- core's, whatever provider published
// them: the systems a loaded collection holds, what one element is related to, and selecting or
// loading a system (or one run of it) by its members.
//
// A system is named by the elements that `serves_system` it; a run by the `run` its members carry.
// Selecting acts on what is loaded, by full name. Loading puts each member's whole site in the scene
// with everything else hidden -- the selection box's way (`boxSiteLoad`), through the provider the
// collection is already loaded from when it publishes the member.

import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { nodeRelationsApi, type NodeRelation, type SystemMember } from "@/services/api/nodeRelations";
import { useAssetBrowserStore } from "@/state/assetBrowserStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { isMirroredWindow } from "@/layout/sceneActions";
import { registerMirrorCommand, sendMirrorCommand } from "@/layout/windowMirror";
import { windowBus } from "@/layout/windows";
import { useTreeViewStore } from "@/state/treeViewStore";
import { refOfSourceName, type GroupMember } from "@/utils/groups/savedGroups";
import { buildTreeIndices } from "@/utils/tree_view/treeGraph";

const KIND_LABELS: Record<string, string> = { duct: "HVAC", piping: "Piping", cable: "Cable", electrical: "Electrical" };

/** "HVAC" for a duct system, and so on; the kind itself for one core has no word for. */
export function systemKindLabel(kind: string | null | undefined): string {
  if (!kind) return "System";
  return KIND_LABELS[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1);
}

export function currentScope(): string {
  return scopeUrlPart(useScopeStore.getState().current);
}

/** The collections of the models loaded from published sources, each with the providers it was
 *  loaded from. */
export function loadedCollections(): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>();
  const root = useTreeViewStore.getState().treeData;
  const loaded = useAssetBrowserStore.getState().loaded;
  for (const model of root?.children ?? []) {
    const source = model.source_name;
    if (!source || !source.startsWith("assets:")) continue;
    const ref = refOfSourceName(source, loaded);
    if (!ref) continue;
    const key = ref.collection.toLowerCase();
    out.set(key, (out.get(key) ?? new Set()).add(ref.provider));
  }
  return out;
}

/** The collection a scene row's model was loaded from, or null for a row of anything else. */
export function collectionOfRow(row: TreeNodeData): string | null {
  const root = useTreeViewStore.getState().treeData;
  if (!root) return null;
  const idx = buildTreeIndices(root);
  let top: TreeNodeData | undefined = idx.byId.get(row.id);
  if (!top || top === idx.root) return null;
  for (let p = idx.parent.get(top.id); p && p !== idx.root; p = idx.parent.get(top.id)) top = p;
  const source = top.source_name;
  if (!source || !source.startsWith("assets:")) return null;
  return refOfSourceName(source, useAssetBrowserStore.getState().loaded)?.collection.toLowerCase() ?? null;
}

export interface Related {
  readonly collection: string;
  readonly system: { readonly name: string; readonly kind: string | null } | null;
  readonly run: string | null;
  /** Elements this one names directly: the next in its run, what it connects to, its opening and
   *  the plate it is in. */
  readonly elements: readonly string[];
  readonly relations: readonly NodeRelation[];
}

/** What `name` is related to in `collection`; null when the collection has no relations. */
export async function relatedOf(scope: string, collection: string, name: string): Promise<Related | null> {
  const answer = await nodeRelationsApi.relationsFrom(scope, collection, [name]);
  if (!answer.indexed) return null;
  const mine = answer.relations.filter((r) => r.from_full_name === name);
  const served = mine.find((r) => r.relation === "serves_system");
  const run = mine.map((r) => r.props.run).find((v): v is string => typeof v === "string") ?? null;
  const elements = new Set<string>();
  for (const r of mine) {
    if (r.relation === "serves_system") continue;
    if (r.to_full_name) elements.add(r.to_full_name);
    const host = r.props.host_full_name;
    if (typeof host === "string") elements.add(host);
  }
  return {
    collection,
    system: served?.to_full_name
      ? { name: served.to_full_name, kind: typeof served.props.system_kind === "string" ? served.props.system_kind : null }
      : null,
    run,
    elements: [...elements],
    relations: mine,
  };
}

/** The members of `system`, or of one `run` of it. */
export async function membersOf(scope: string, collection: string, system: string, run?: string | null): Promise<SystemMember[]> {
  const { members } = await nodeRelationsApi.systemMembers(scope, collection, system);
  return run ? members.filter((m) => m.run === run) : members;
}

/** Select what is loaded of `names` -- with `add`, on top of the current selection. Returns how
 *  many draw ranges were selected. */
export async function selectNames(names: Iterable<string>, label: string, add = false): Promise<number> {
  const set = new Set(names);
  // A pop-out window has no scene: the main window selects. How many it found is not known here.
  if (isMirroredWindow()) {
    sendMirrorCommand(windowBus(), SELECT_NAMES_COMMAND, { names: [...set].slice(0, MAX_NAMES), label, add });
    return set.size;
  }
  const { selectNamedElements } = await import("@/utils/groups/groupScene");
  return selectNamedElements(set, label, add);
}

export const SELECT_NAMES_COMMAND = "core:select-names";
export const LOAD_MEMBERS_COMMAND = "core:load-members";
/** Most names or members one request from a pop-out window carries. */
const MAX_NAMES = 200_000;

/** The runs the elements `names` belong to, each with its system: `[{system, run}]`, once each. */
export async function runsOf(scope: string, collection: string, names: readonly string[]): Promise<{ system: string; run: string }[]> {
  if (!names.length) return [];
  const answer = await nodeRelationsApi.relationsFrom(scope, collection, names);
  const out = new Map<string, { system: string; run: string }>();
  for (const r of answer.relations) {
    const run = r.props.run;
    if (r.relation !== "serves_system" || !r.to_full_name || typeof run !== "string") continue;
    out.set(`${r.to_full_name}\u0000${run}`, { system: r.to_full_name, run });
  }
  return [...out.values()];
}

/** The load members for `members`: each through the provider `collection` is loaded from when it
 *  publishes the member, else the first that does. Members no provider placed are counted. */
export function loadPlan(
  collection: string,
  members: readonly SystemMember[],
  preferred: ReadonlySet<string>,
): { members: GroupMember[]; names: Map<string, string>; unplaced: number } {
  const out: GroupMember[] = [];
  const names = new Map<string, string>();
  let unplaced = 0;
  for (const m of members) {
    const site = m.sites.find((s) => preferred.has(s.provider)) ?? m.sites[0];
    if (!site) {
      unplaced++;
      continue;
    }
    out.push({
      target: { kind: "node", provider: site.provider, collection, subject: site.subject, revision: null, node: site.node_id },
      element: null,
      path: [],
    });
    names.set(`${collection.toLowerCase()}\u0000${site.node_id}`, m.full_name);
  }
  return { members: out, names, unplaced };
}

/** Load `members` into the scene, each as its whole site with the rest hidden. Resolves a sentence
 *  saying what happened. */
export async function loadMembers(collection: string, members: readonly SystemMember[], label: string): Promise<string> {
  if (isMirroredWindow()) {
    sendMirrorCommand(windowBus(), LOAD_MEMBERS_COMMAND, { collection, members: members.slice(0, MAX_NAMES), label });
    return `Loading ${label} in the main window.`;
  }
  return loadMembersHere(collection, members, label);
}

/** Let pop-out windows select and load by name here (once per page). */
let commandsRegistered = false;
export function registerSystemsCommands(): void {
  if (commandsRegistered) return;
  commandsRegistered = true;
  registerMirrorCommand<{ names?: unknown; label?: unknown; add?: unknown }>({
    id: SELECT_NAMES_COMMAND,
    run: (args) => {
      if (!Array.isArray(args?.names) || typeof args.label !== "string") return;
      void selectNames(args.names.filter((n): n is string => typeof n === "string"), args.label.slice(0, 500), args.add === true);
    },
  });
  registerMirrorCommand<{ collection?: unknown; members?: unknown; label?: unknown }>({
    id: LOAD_MEMBERS_COMMAND,
    run: (args) => {
      if (typeof args?.collection !== "string" || !Array.isArray(args.members) || typeof args.label !== "string") return;
      const members = args.members.filter((m): m is SystemMember => !!m && typeof (m as SystemMember).full_name === "string");
      void loadMembersHere(args.collection, members, args.label.slice(0, 500)).then((s) => console.info(s));
    },
  });
}

async function loadMembersHere(collection: string, members: readonly SystemMember[], label: string): Promise<string> {
  const plan = loadPlan(collection, members, loadedCollections().get(collection.toLowerCase()) ?? new Set());
  if (plan.members.length === 0) {
    return `None of the ${members.length} element${members.length === 1 ? "" : "s"} of ${label} has a published place to load it from.`;
  }
  const [{ beginOperation }, { loadBoxSites }, { realDeliveryDeps }, { getSingletonViewerStores }] = await Promise.all([
    import("@/services/operationTracking"),
    import("@/utils/bboxSelection/boxSiteLoad"),
    import("@/components/asset_browser/AssetsTab"),
    import("@/state/AdaViewerContext"),
  ]);
  const op = beginOperation(`Loading ${label}`);
  const release = op.hold();
  try {
    const deps = realDeliveryDeps(
      (name) => getSingletonViewerStores().useModelState.getState().loadedSourceNames.has(name),
      op.trackJob,
    );
    const out = await loadBoxSites(plan.members, currentScope(), deps, plan.names, (s) => op.stage(s));
    for (const f of out.failed) op.fail(`${f.site}: ${f.reason}`);
    const sites = (n: number) => `${n} site${n === 1 ? "" : "s"}`;
    const parts = [
      out.loaded ? `loaded ${sites(out.loaded)}` : "",
      out.already ? `${sites(out.already)} already in the scene` : "",
      `${out.shown} element${out.shown === 1 ? "" : "s"} shown`,
      plan.unplaced ? `${plan.unplaced} with no published place to load from` : "",
      out.failed.length ? `${out.failed.length} site${out.failed.length === 1 ? "" : "s"} failed` : "",
    ].filter(Boolean);
    return `${label}: ${parts.join(", ")}.`;
  } finally {
    release();
  }
}
