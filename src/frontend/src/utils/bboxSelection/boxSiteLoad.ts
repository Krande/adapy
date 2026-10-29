// A selection box's members into the scene AS THEIR WHOLE SITES: each site the box touches is loaded
// once, as one model with its whole tree, and everything in it outside the box is hidden. The
// scene tree then holds the whole structure, and what the box missed is one "show" away there --
// rather than one model per member, each a tree of its own with nothing around it.
//
// A site already in the scene is not loaded again, and what is hidden in it stays as the user left
// it: only the box's members are shown. A model of one node of a site loaded here is replaced by
// the site's, which draws the same geometry.
//
// MEMBERS ARE FOUND BY NAME in the site's tree: a member names its node by id, which the scene tree
// does not carry, so the caller hands each node's full name (the published bounds' `full_name`).

import type { LoadNodeDeps } from "@/assets/delivery";
import { getSingletonViewerStores } from "@/state/AdaViewerContext";
import type { GroupMember, GroupMemberTarget } from "@/utils/groups/savedGroups";

type NodeTarget = Extract<GroupMemberTarget, { kind: "node" }>;

export interface BoxSiteLoadOutcome {
  /** Sites put in the scene by this call. */
  readonly loaded: number;
  /** Sites already in the scene, whose box members were shown. */
  readonly already: number;
  /** Members shown, across the sites. */
  readonly shown: number;
  readonly failed: readonly { readonly site: string; readonly reason: string }[];
}

/** How long a loaded site's tree is waited for before its members are shown and the rest hidden. */
const TREE_WAIT_MS = 20_000;
const TREE_POLL_MS = 250;

/** The whole-site target a member's node belongs to. */
function siteOf(t: NodeTarget): NodeTarget {
  return { kind: "node", provider: t.provider, collection: t.collection, subject: t.subject, revision: t.revision, node: null };
}

const siteKey = (t: NodeTarget) => `${t.provider}\u0000${t.collection}\u0000${t.subject}`;

/** The name a member's row carries in its site's tree: its element, or its node's full name. */
function memberName(m: GroupMember, names: ReadonlyMap<string, string>): string | null {
  if (m.target.kind !== "node") return null;
  if (m.element) return m.element;
  return names.get(`${m.target.collection.toLowerCase()}\u0000${m.target.node ?? m.target.subject}`) ?? null;
}

export async function loadBoxSites(
  members: readonly GroupMember[],
  scope: string,
  deps: LoadNodeDeps,
  /** `collection \0 node id` -> full name, from the box's bounds hits. */
  names: ReadonlyMap<string, string>,
  onStage?: (stage: string) => void,
): Promise<BoxSiteLoadOutcome> {
  const [
    { loadAssetNode, nodeDisplayName },
    { namedRowsInModel },
    { geometryRowsUnder },
    { hideRows, showRows },
    { makePluginContextStandalone },
    { beginBulkLoad, endBulkLoad },
    { holdHidden, revealHeld },
  ] = await Promise.all([
    import("@/utils/groups/groupLoad"),
    import("@/utils/groups/groupScene"),
    import("@/utils/groups/savedGroups"),
    import("@/utils/tree_view/sceneTreeActions"),
    import("@/plugins/context"),
    import("@/utils/scene/loadingView"),
    import("@/utils/scene/heldHidden"),
  ]);

  const sites = new Map<string, { site: NodeTarget; names: Set<string> }>();
  for (const m of members) {
    if (m.target.kind !== "node") continue;
    const site = siteOf(m.target);
    const entry = sites.get(siteKey(site)) ?? { site, names: new Set<string>() };
    const name = memberName(m, names);
    if (name) entry.names.add(name);
    sites.set(siteKey(site), entry);
  }

  let loaded = 0;
  let already = 0;
  let shown = 0;
  const failed: { site: string; reason: string }[] = [];
  let n = 0;
  // The camera stays where the user put it: no framing of a site that is mostly about to be hidden.
  const session = beginBulkLoad(scope, `assets:box`, { hold: true, save: false });
  try {
    for (const { site, names: wanted } of sites.values()) {
      n += 1;
      const label = nodeDisplayName(site) ?? site.subject;
      onStage?.(`loading site ${n} of ${sites.size}: ${label}`);
      const before = namedRowsInModel(site, wanted);
      // A site loaded here joins the scene invisible and is shown once what is outside the box is
      // hidden -- never drawn whole in between. Revealed whatever happens below.
      if (!before) holdHidden(site.provider, site.collection, site.subject);
      try {
        try {
          if (!before) await loadAssetNode(scope, deps, site);
        } catch (e) {
          failed.push({ site: label, reason: e instanceof Error ? e.message : String(e) });
          continue;
        }
        if (before) already += 1;
        else loaded += 1;

        // The site's tree is built after its model arrives.
        let found = before;
        for (let waited = 0; !found && waited < TREE_WAIT_MS; waited += TREE_POLL_MS) {
          await new Promise((r) => setTimeout(r, TREE_POLL_MS));
          found = namedRowsInModel(site, wanted);
        }
        if (!found) {
          failed.push({ site: label, reason: "its scene tree was not built in time; nothing was hidden" });
          continue;
        }
        shown += found.rows.length;
        if (before) {
          if (found.rows.length) showRows(found.rows);
        } else if (found.rows.length) {
          // Everything of the site outside the box hidden; nothing hidden when no member was found
          // in it, rather than the whole site.
          const keep = new Set(geometryRowsUnder(found.rows).map((r) => r.id));
          hideRows(geometryRowsUnder([found.root]).filter((r) => !keep.has(r.id)));
        }
        replaceNodeModels(site, makePluginContextStandalone);
      } finally {
        if (!before) revealHeld(site.provider, site.collection, site.subject);
      }
    }
  } finally {
    endBulkLoad(session);
  }
  return { loaded, already, shown, failed };
}

/** Unload this site's one-node models: the site's own model draws the same geometry. */
function replaceNodeModels(
  site: NodeTarget,
  context: (owner: string) => { scene: { unloadModel: (name: string) => void } },
): void {
  const browser = getSingletonViewerStores().useAssetBrowserStore.getState();
  for (const a of browser.loaded) {
    const r = a.ref;
    if (r.provider !== site.provider || r.collection !== site.collection || r.subject !== site.subject) continue;
    if (!r.node || r.node === r.subject) continue;
    context("assets").scene.unloadModel(a.sourceName);
  }
}
