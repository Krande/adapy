// "Request from source" on a selection box: ask a provider for the geometry of what the box holds
// that it has not published yet.
//
// WHAT IS "IN THE BOX". The box is in scene coordinates, and the only per-element bounds the viewer
// has are those of what is DRAWN (`rangeBounds`). So the box takes elements exactly as its selection
// does (`rangesInBox`, partially or fully within), those are folded up to whole levels as a
// selection is (`captureRanges`), and the members are matched BY NAME into the chosen provider's
// published tree (`POST .../clash-check/geometry-plan`, the clash check's own resolution). No unit or
// offset translation is involved: positions never leave the scene, names do.
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
import type { DeclaredProvider, RequestProviderChoice } from "@/utils/export/missingGeometry";
import type { GroupMember } from "@/utils/groups/savedGroups";

export type { RequestProviderChoice };

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
  const own = memberProviders(members);
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

/** The question before a request, or the answer when there is nothing to ask for. */
export function boxRequestSentence(s: BoxRequestSummary, providerLabel: string, elements: number): string {
  const what = `${plural(elements, "element")} in the box (${plural(s.missing + s.present + s.files, "member")})`;
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
