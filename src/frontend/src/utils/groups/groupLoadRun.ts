// The ORDER of a saved group's "Load into scene", with everything it touches handed in
// (`GroupLoadEnv`): store-free and network-free, so it runs under `node --test` against fakes.
// `./groupLoad` assembles the live environment.
//
// ONLY THE MEMBERS END UP IN THE SCENE. A member that is one element of a large published document
// is loaded alone (its own row, built by itself) wherever the source delivers per node; a source
// that can only be delivered whole is loaded once and everything but its members is hidden
// (`./groupSources`, `planGroupLoads`). One member failing never stops the others.
//
// THE CAMERA. The group's loads never move it themselves (`holdCamera`: no per-model fit). Into an
// EMPTY scene the members are framed once, together, when the load is over; into a scene that
// already holds something the camera stays where the user has it. A cancelled load, or one that
// put nothing in the scene, frames nothing.

import { LoadCancelled, OVERRIDE_CONCURRENCY } from "@/assets/delivery";

import type { GroupSelectionOutcome } from "./groupScene";
import { planGroupLoads, sourcesToLoad, type MemberLoad } from "./groupSources";
import type { GroupMember, GroupMemberTarget, SavedGroup } from "./savedGroups";

type NodeTarget = Extract<GroupMemberTarget, { kind: "node" }>;

/** A member's load, started: its network half (the claim, the build) is under way. */
export interface StartedLoad {
  /** Settles -- never rejects -- when the network half is over, either way. */
  readonly ready: Promise<void>;
  /** Put it in the scene. Resolves the source name it is loaded under (`null`: the build found
   *  nothing to draw, which is an answer); rejects with why it could not be loaded. */
  finish(): Promise<string | null>;
}

export interface GroupLoadEnv {
  /** Select the group's members in what is loaded now, and say which are not loaded. */
  select(group: SavedGroup): GroupSelectionOutcome;
  /** Whether `target`'s source is delivered per node (a build scoped to the node asked for)
   *  rather than as one document. */
  deliversPerNode(target: NodeTarget): Promise<boolean>;
  /** `memberKey` -> the element's row id in the published tree, for the members it can place. */
  resolveRows(members: readonly GroupMember[]): Promise<ReadonlyMap<string, string>>;
  /** Start loading one member alone. */
  startMember(load: MemberLoad): StartedLoad;
  /** Load a source as the document it is; resolves its source name (`null`: nothing to draw). */
  loadWhole(target: GroupMemberTarget): Promise<string | null>;
  /** Hide everything in the model loaded under `sourceName` that is not one of `members`.
   *  Resolves how many objects it hid; rejects when it could not (the model is then shown whole). */
  showOnly(sourceName: string, members: readonly GroupMember[]): Promise<number>;
  /** Resolves once the models loaded under `sourceNames` are in the scene tree (or it gave up). */
  settle(sourceNames: readonly string[]): Promise<void>;
  /** Whether nothing is loaded in the scene. Asked once, before the first load. */
  sceneEmpty(): boolean;
  /** Keep the models this load brings in from moving the camera as each arrives. Resolves the
   *  function that ends it. */
  holdCamera(): Promise<() => void>;
  /** Frame what is selected -- the group's members, selected after the loads. */
  frameSelection(): void;
  onStage?(stage: string): void;
  /** How a failure reads to a person. */
  reasonFor?(e: unknown): string;
}

export interface GroupLoadOutcome {
  /** Models put in the scene by this call: one per member loaded alone, one per source loaded whole. */
  readonly loaded: number;
  /** Sources already in the scene, left as they were. */
  readonly already: number;
  /** Objects hidden in the sources that had to be loaded whole, so only the members show. */
  readonly hidden: number;
  /** What could not be loaded, each with its members and why. */
  readonly failed: readonly { readonly source: string; readonly members: readonly GroupMember[]; readonly reason: string }[];
  /** The selection made afterwards. */
  readonly selection: GroupSelectionOutcome;
  /** The camera was moved to frame the members (the scene was empty before the load). */
  readonly framed: boolean;
}

const describe = (m: GroupMember): string =>
  m.target.kind === "file" ? m.target.source_key : `${m.target.collection}/${m.element ?? m.target.node ?? m.target.subject}`;

export async function runGroupLoad(group: SavedGroup, env: GroupLoadEnv): Promise<GroupLoadOutcome> {
  const reasonFor = env.reasonFor ?? ((e: unknown) => (e instanceof Error ? e.message : String(e)));
  // What is in the scene already answers itself: the selection's own resolver says which members
  // are missing, by the same matching "Select" uses, so the two cannot disagree.
  const before = env.select(group);
  const sources = sourcesToLoad(before.notLoaded);

  // Which sources can hand out one node by itself. A source whose claim cannot be read is left to
  // its whole load, which reports why.
  const perNode = new Set<string>();
  await Promise.all(
    [...sources].map(async ([key, members]) => {
      const t = members[0].target;
      if (t.kind !== "node" || members.some((m) => m.element === null)) return;
      try {
        if (await env.deliversPerNode(t)) perNode.add(key);
      } catch {
        // Reported by the load below.
      }
    }),
  );
  const toPlace = [...sources].flatMap(([key, members]) => (perNode.has(key) ? members : []));
  let rows: ReadonlyMap<string, string> = new Map();
  if (toPlace.length) {
    env.onStage?.(`finding ${toPlace.length} member${toPlace.length === 1 ? "" : "s"} in the published tree`);
    try {
      rows = await env.resolveRows(toPlace);
    } catch {
      // Nothing placed: those sources load whole and show only their members.
    }
  }
  const plan = planGroupLoads(before.notLoaded, rows, (key) => perNode.has(key));
  const total = plan.alone.length + plan.whole.length;
  if (total === 0) return { loaded: 0, already: sourcesToLoad(group.members).size - sources.size, hidden: 0, failed: [], selection: before, framed: false };
  const wasEmpty = env.sceneEmpty();
  const releaseCamera = await env.holdCamera();

  const failed: { source: string; members: readonly GroupMember[]; reason: string }[] = [];
  const names: string[] = [];
  let loaded = 0;
  let hidden = 0;
  let cancelled = false;
  let n = 0;
  try {

  // Members alone: their builds run a few at a time, ahead of the scene; the scene takes one model
  // at a time, in group order.
  const started: StartedLoad[] = [];
  const startNext = () => {
    if (cancelled || started.length >= plan.alone.length) return;
    const load = env.startMember(plan.alone[started.length]);
    started.push(load);
    void load.ready.then(startNext);
  };
  for (let i = 0; i < OVERRIDE_CONCURRENCY; i++) startNext();
  let taken = 0;
  for (let i = 0; i < plan.alone.length && !cancelled; i++) {
    const item = plan.alone[i];
    taken = i + 1;
    n += 1;
    env.onStage?.(`loading ${n} of ${total}: ${describe(item.members[0])}`);
    while (started.length <= i) startNext();
    try {
      const name = await started[i].finish();
      if (name) names.push(name);
      loaded += 1;
    } catch (e) {
      if (e instanceof LoadCancelled) cancelled = true;
      else failed.push({ source: item.key, members: item.members, reason: reasonFor(e) });
    }
  }
  // Cancelled: the loads started ahead are finished off too, so each clears its own busy state.
  for (const load of started.slice(taken)) void load.finish().catch(() => undefined);

  for (const item of plan.whole) {
    if (cancelled) break;
    n += 1;
    env.onStage?.(`loading ${n} of ${total}: ${describe({ ...item.members[0], element: null })}`);
    let name: string | null;
    try {
      name = await env.loadWhole(item.target);
      loaded += 1;
    } catch (e) {
      if (e instanceof LoadCancelled) cancelled = true;
      else failed.push({ source: item.key, members: item.members, reason: reasonFor(e) });
      continue;
    }
    if (!name) continue;
    names.push(name);
    if (!item.only) continue;
    try {
      hidden += await env.showOnly(name, item.only);
    } catch (e) {
      failed.push({ source: item.key, members: item.only, reason: `loaded, but shown whole: ${reasonFor(e)}` });
    }
  }

  // Selected again AFTER the loads, once their trees are there, so the members that just arrived
  // are in it.
  if (names.length) await env.settle(names);
  } finally {
    releaseCamera();
  }
  const selection = loaded ? env.select(group) : before;
  // One frame, on the members (selected just now -- in a document loaded whole, the rest is hidden
  // and not selected), and only when the load started with an empty scene.
  const framed = wasEmpty && !cancelled && names.length > 0 && selection.ranges > 0;
  if (framed) env.frameSelection();
  const already = sourcesToLoad(group.members).size - sources.size;
  return { loaded, already, hidden, failed, selection, framed };
}
