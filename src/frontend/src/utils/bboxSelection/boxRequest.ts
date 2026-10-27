// "Request from source" on a selection box: ask a geometry provider for the design geometry of what
// the box holds that it has not published yet.
//
// WHAT IS "IN THE BOX". The box is in scene coordinates, and the only per-element bounds the viewer
// has are those of what is DRAWN (`rangeBounds`): a mesh delivery of the model -- the whole plant as
// faceted meshes, say -- is what locates the elements. So the box takes elements exactly as its
// selection does (`rangesInBox`, partially or fully within), those are folded up to whole levels as
// a selection is (`captureRanges`), and the members are matched BY NAME into the chosen provider's
// published tree (`POST .../clash-check/geometry-plan`, the clash check's own resolution). No unit or
// offset translation is involved: positions never leave the scene, names do.
//
// WHAT IS REQUESTED. Exactly the members the plan says the provider has no geometry for -- the same
// request the clash check and the selection export make (`requestUnmatchedMembers`): one provider
// job per collection batch on the job queue, then core's publish. Members the provider already
// publishes, or that are its own models, are not asked for again; members from a file name no
// published node and cannot be requested.
//
// WHICH PROVIDER. Only one that reads into objects (a design-geometry provider, from the live
// `geometry-providers` list) and declares a node request this user may run. A mesh-only provider is
// never offered: the box asks for design geometry, not more meshes.
//
// React-free and store-free, so it runs under `node --test`.

import type { WireGeometryPlan, WireGeometryProvider } from "@/services/api/clashCheck";
import type { DeclaredProvider, RequestProviderChoice } from "@/utils/export/missingGeometry";
import type { GroupMember } from "@/utils/groups/savedGroups";

export type { RequestProviderChoice };

/** The providers a box's members can be requested from, by label.
 *
 *  A provider qualifies when it declares a node request this user may run, a live pool reads it into
 *  objects (`readable`; when that list could not be read nothing qualifies, rather than offering a
 *  mesh-only provider by mistake), and it offers every collection of the box's node members (or
 *  advertises no list to tell). `preferred` -- the last provider asked -- sorts first. */
export function boxRequestChoices(args: {
  readonly members: readonly GroupMember[];
  readonly declared: readonly DeclaredProvider[];
  readonly readable: readonly WireGeometryProvider[] | null;
  readonly isAdmin: boolean;
  readonly providerName?: (id: string) => string;
  readonly preferred?: string | null;
}): RequestProviderChoice[] {
  const { members, declared, readable, isAdmin } = args;
  if (readable === null) return [];
  const name = args.providerName ?? ((id: string) => id);
  const wanted = [
    ...new Set(members.flatMap((m) => (m.target.kind === "node" ? [m.target.collection.toLowerCase()] : []))),
  ];
  const out: RequestProviderChoice[] = [];
  const seen = new Set<string>();
  for (const p of declared) {
    const req = p.nodeRequest;
    if (!req || seen.has(p.providerId)) continue;
    if (req.requiresAdmin && !isAdmin) continue;
    if (!readable.some((r) => r.id === p.providerId && r.readable)) continue;
    const offered = p.collections.map((c) => c.toLowerCase());
    if (offered.length > 0 && !wanted.every((c) => offered.includes(c))) continue;
    seen.add(p.providerId);
    out.push({ providerId: p.providerId, label: name(p.providerId), req });
  }
  const preferred = args.preferred ?? null;
  return out.sort(
    (a, b) =>
      Number(b.providerId === preferred) - Number(a.providerId === preferred) || a.label.localeCompare(b.label),
  );
}

/** Why no provider can be asked for `members`, for the menu (when `boxRequestChoices` is empty). */
export function noChoiceReason(members: readonly GroupMember[], readable: readonly WireGeometryProvider[] | null): string {
  if (members.length > 0 && members.every((m) => m.target.kind === "file")) {
    return "Everything in the box is from a file, which no source can be asked for.";
  }
  if (readable === null) return "Could not list the design-geometry sources.";
  return "No design-geometry source that takes requests is online for these collections (or only an administrator may ask it).";
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
