// "Request from source" on a selection box: ask a provider for the geometry of what the box holds
// that it has not published yet -- loaded in the scene or not.
//
// WHAT IS "IN THE BOX". First the PUBLISHED node bounds (`POST .../assets/bounds/search`): a
// provider that publishes a box per node on every level (`ada.assets/node-bounds@1`) lets the
// server answer every node of the collection in the box, loaded or not, folded to whole levels
// ("partially within": every leaf the box meets; "fully within": every node inside it). The box is
// in scene coordinates and the bounds in the provider's model frame (Z up, its geometry's units),
// so the box is moved by the scene's recentring offset first (`sceneBoxToSource`) -- the offset of
// a loaded model of the bounds' collection, which is where the loader put that frame.
//
// When no provider published bounds for the collections in the scene, the box falls back to what
// is DRAWN, and says so: it takes elements exactly as its selection does (`rangesInBox`), folded up
// to whole levels as a selection is (`captureRanges`).
//
// Either way the members are matched BY NAME into the chosen provider's published tree
// (`POST .../clash-check/geometry-plan`, the clash check's own resolution).
//
// WHAT IS REQUESTED. Exactly the members the plan says the provider has no geometry for -- the same
// request the clash check and the selection export make (`requestUnmatchedMembers`): one provider
// job per collection batch on the job queue, then core's publish. Members the provider already
// publishes, or that are its own models, are not asked for again; members from a file name no
// published node and cannot be requested.
//
// WHICH PROVIDER. Any provider that declares a node request this user may run and that covers the
// box's collections -- mesh or object providers alike (`boxRequestChoices`). The default is the
// first available one (`defaultBoxProvider`): the user's last explicit pick while it still
// qualifies, else the provider the box's members were loaded from, else the first in the Sources
// tab's provider order (by provider id). The "From" picker opts into any other.
//
// React-free and store-free, so it runs under `node --test`.

import type { WireGeometryPlan } from "@/services/api/clashCheck";
import type { BoundsHit, BoundsSet } from "@/services/api/nodeBounds";
import type { DeclaredProvider, RequestProviderChoice } from "@/utils/export/missingGeometry";
import type { GroupMember } from "@/utils/groups/savedGroups";

import type { Aabb, BoxSelectMode } from "./boxMath";

export type { RequestProviderChoice };

// --- the published bounds -----------------------------------------------------------------------

/** The scene box in a provider's model frame: less the scene's recentring `offset` (where the loader
 *  moved that frame's origin; null when nothing was moved). */
export function sceneBoxToSource(box: Aabb, offset: readonly [number, number, number] | null): {
  min: [number, number, number];
  max: [number, number, number];
} {
  const o = offset ?? [0, 0, 0];
  return {
    min: [box.min[0] - o[0], box.min[1] - o[1], box.min[2] - o[2]],
    max: [box.max[0] - o[0], box.max[1] - o[1], box.max[2] - o[2]],
  };
}

/** The bounds query's mode for a box mode. */
export function boundsMode(mode: BoxSelectMode): "partial" | "within" {
  return mode === "full" ? "within" : "partial";
}

/** Per collection, the ONE bounds set to read: the one published by a provider the scene's models of
 *  that collection were loaded from (most loaded first), else the first by provider id. Two
 *  providers' bounds of one collection name the same elements under different ids. */
export function pickBoundsSets(sets: readonly BoundsSet[], loadedProviders: readonly string[]): Map<string, BoundsSet> {
  const rank = (p: string) => {
    const i = loadedProviders.indexOf(p);
    return i < 0 ? loadedProviders.length : i;
  };
  const out = new Map<string, BoundsSet>();
  const sorted = [...sets].sort(
    (a, b) => rank(a.provider) - rank(b.provider) || (a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0),
  );
  for (const s of sorted) if (!out.has(s.collection)) out.set(s.collection, s);
  return out;
}

/** The box's hits as group members: each a whole published node, addressed through the subject a
 *  request or load of it goes through. Hits of a set not picked for their collection are dropped,
 *  and so is a node listed twice. */
export function boundsMembers(hits: readonly BoundsHit[], picked: ReadonlyMap<string, BoundsSet>): GroupMember[] {
  const out: GroupMember[] = [];
  const seen = new Set<string>();
  for (const h of hits) {
    const set = picked.get(h.collection);
    if (!set || set.provider !== h.provider || set.scope !== h.scope || !h.subject) continue;
    const key = `${h.collection}\u0000${h.node_id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      target: { kind: "node", provider: h.provider, collection: h.collection, subject: h.subject, revision: null, node: h.node_id },
      element: null,
      path: [],
    });
  }
  return out;
}

/** Where a box's members came from, for the sentence: the published bounds, or what is drawn. */
export type BoxCandidates =
  | { readonly kind: "bounds"; readonly providers: readonly string[]; readonly truncated: string | null }
  | { readonly kind: "drawn"; readonly elements: number; readonly why: string | null };

/** The members of a plan its provider ALREADY has (matched to its node, or its own): what a load,
 *  not a request, puts in the scene. Files are left out. */
export function presentMembers(plan: WireGeometryPlan): GroupMember[] {
  const out: GroupMember[] = [];
  for (const m of plan.matched) out.push(m.to);
  for (const m of plan.unchanged) if (m.target.kind === "node") out.push(m);
  return out;
}

/** The providers of the box's node members, most members first (ties by provider id). */
export function memberProviders(members: readonly GroupMember[]): string[] {
  const counts = new Map<string, number>();
  for (const m of members) {
    if (m.target.kind === "node") counts.set(m.target.provider, (counts.get(m.target.provider) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0)).map(([p]) => p);
}

/** The providers a box's members can be requested from, in a stable order: the providers the
 *  members were loaded from first (most members first), then the rest in `declared` order -- the
 *  order the Sources tab lists providers in (`assetProviderCollections`, by provider id).
 *
 *  A provider qualifies when a live plugin declares a node request for it (`declared`), this user
 *  may run it (admin-only respected), and it offers every collection of the box's node members (or
 *  advertises no list to tell). Mesh and object providers alike. */
export function boxRequestChoices(args: {
  readonly members: readonly GroupMember[];
  readonly declared: readonly DeclaredProvider[];
  readonly isAdmin: boolean;
  readonly providerName?: (id: string) => string;
  /** The providers to rank first, in order, instead of the members' own: the providers of the
   *  scene's models, when the members came from another provider's published bounds. */
  readonly rankFirst?: readonly string[];
}): RequestProviderChoice[] {
  const { members, declared, isAdmin } = args;
  const name = args.providerName ?? ((id: string) => id);
  const wanted = [
    ...new Set(members.flatMap((m) => (m.target.kind === "node" ? [m.target.collection.toLowerCase()] : []))),
  ];
  if (wanted.length === 0) return [];
  const out: RequestProviderChoice[] = [];
  const seen = new Set<string>();
  for (const p of declared) {
    const req = p.nodeRequest;
    if (!req || seen.has(p.providerId)) continue;
    if (req.requiresAdmin && !isAdmin) continue;
    const offered = p.collections.map((c) => c.toLowerCase());
    if (offered.length > 0 && !wanted.every((c) => offered.includes(c))) continue;
    seen.add(p.providerId);
    out.push({ providerId: p.providerId, label: name(p.providerId), req });
  }
  const own = args.rankFirst?.length ? [...args.rankFirst] : memberProviders(members);
  const rank = (id: string) => {
    const i = own.indexOf(id);
    return i < 0 ? own.length : i;
  };
  // Stable sort: equal ranks keep `declared` order.
  return out.map((c, i) => [c, i] as const).sort((a, b) => rank(a[0].providerId) - rank(b[0].providerId) || a[1] - b[1]).map(([c]) => c);
}

/** The provider a box's request defaults to: `lastChosen` (the user's last explicit pick) while it
 *  still qualifies, else the first of `choices` -- which `boxRequestChoices` orders with the
 *  members' own provider first. Null when nothing qualifies. */
export function defaultBoxProvider(choices: readonly RequestProviderChoice[], lastChosen: string | null): string | null {
  if (lastChosen && choices.some((c) => c.providerId === lastChosen)) return lastChosen;
  return choices[0]?.providerId ?? null;
}

/** Why no provider can be asked for `members`, for the menu (when `boxRequestChoices` is empty). */
export function noChoiceReason(members: readonly GroupMember[]): string {
  if (members.length > 0 && members.every((m) => m.target.kind === "file")) {
    return "Everything in the box is from a file, which no source can be asked for.";
  }
  return "No source that takes requests is online for these collections (or only an administrator may ask it).";
}

/** What a geometry plan says about a box's members. */
export interface BoxRequestSummary {
  readonly provider: string;
  /** Members the provider has no geometry for: what a request asks for. */
  readonly missing: number;
  /** Members it already publishes (matched by name, or its own models). */
  readonly present: number;
  /** Members from a file, which name no published node. */
  readonly files: number;
}

export function summariseBoxPlan(plan: WireGeometryPlan): BoxRequestSummary {
  const files = plan.unchanged.filter((m) => m.target.kind === "file").length;
  return {
    provider: plan.geometry_provider,
    missing: plan.unmatched.length,
    present: plan.matched.length + plan.unchanged.length - files,
    files,
  };
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Where the box's members came from, as the sentence's opening. */
export function candidatesPhrase(from: BoxCandidates, members: number): string {
  if (from.kind === "bounds") {
    const by = from.providers.length ? ` (by ${from.providers.join(", ")}'s published bounds, loaded or not)` : "";
    const cut = from.truncated ? ` -- incomplete: ${from.truncated}` : "";
    return `${plural(members, "node")} in the box${by}${cut}`;
  }
  const why = from.why ? ` -- ${from.why}, so only what is drawn was looked at` : "";
  return `${plural(from.elements, "drawn element")} in the box (${plural(members, "member")})${why}`;
}

/** The question before a request, or the answer when there is nothing to ask for. */
export function boxRequestSentence(s: BoxRequestSummary, providerLabel: string, from: BoxCandidates): string {
  const what = candidatesPhrase(from, s.missing + s.present + s.files);
  const parts: string[] = [];
  if (s.present) parts.push(`${s.present} already at ${providerLabel}`);
  if (s.files) parts.push(`${s.files} from a file, which cannot be requested`);
  const known = parts.length ? `; ${parts.join(", ")}` : "";
  if (s.missing === 0) return `${what}${known}. Nothing to request.`;
  return `${what}${known}. Request ${plural(s.missing, "member")} from ${providerLabel}?`;
}

/** The box's members as the plan route's group target. */
export function boxPlanTarget(label: string, members: readonly GroupMember[]) {
  return { kind: "group" as const, name: label, members };
}
