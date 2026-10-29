// "Request the geometry first?" for a selection export that failed because the geometry provider has
// published NOTHING in the members' collection(s) -- the ordinary state of a collection a tree-only
// provider published and a provider that publishes objects has not been asked about yet -- and,
// asked BEFORE exporting from the provider's plan (`missingFromPlan`), for one it has only part of.
//
// THE SERVER SAYS SO AS DATA. Such a failed group export leaves its `.log.json` beside the file it
// did not write, with `error: {code: "no_provider_geometry", message, geometry_provider, collections,
// members, unmatched}` (`ada/clash/group_model.py`'s `NoProviderGeometryError`); the job's own error
// is `error.message`. An older server writes no log for a failure, and then this finds nothing and
// the panel shows the job's error as before.
//
// THE REQUEST IS THE CLASH CHECK'S: exactly the members left out, asked of a provider's declared node
// request (`requestUnmatchedMembers` in `@/utils/groups/groupRequest`), not the whole collection.
//
// React-free and store-free, so it runs under `node --test`.

import type { WireGeometryProvider } from "@/services/api/clashCheck";
import type { AssetNodeRequest } from "@/services/assetScopeCollections";
import type { UnmatchedMember } from "@/utils/groups/groupRequest";

export const NO_PROVIDER_GEOMETRY = "no_provider_geometry";

/** A failed export's "no geometry here yet" outcome. */
export interface NoProviderGeometry {
  readonly message: string;
  /** The geometry provider the export read from. */
  readonly provider: string;
  readonly collections: readonly string[];
  /** How many members the export has (for a failed export: all of them were left out). */
  readonly members: number;
  readonly unmatched: readonly UnmatchedMember[];
  /** Members the provider DOES have, when the outcome was read from a plan before exporting
   *  (`missingFromPlan`); absent for a failed export, which had none. */
  readonly present?: number;
}

/** What a geometry plan says the provider lacks, asked BEFORE exporting: an outcome when any member
 *  would be left out, null when none would. */
export function missingFromPlan(plan: {
  readonly geometry_provider: string;
  readonly matched: readonly unknown[];
  readonly unchanged: readonly unknown[];
  readonly unmatched: readonly { readonly member: unknown; readonly reason?: string; readonly label?: string | null; readonly row?: string | null }[];
}): NoProviderGeometry | null {
  if (plan.unmatched.length === 0) return null;
  const collections = new Set<string>();
  for (const u of plan.unmatched) {
    const t = isRecord(u.member) && isRecord(u.member.target) ? u.member.target : null;
    if (t && typeof t.collection === "string" && t.collection) collections.add(t.collection);
  }
  if (collections.size === 0) return null;
  const present = plan.matched.length + plan.unchanged.length;
  const members = present + plan.unmatched.length;
  return {
    message: `${plan.unmatched.length} of ${members} member${members === 1 ? "" : "s"} have no stored geometry from ${plan.geometry_provider}`,
    provider: plan.geometry_provider,
    collections: [...collections],
    members,
    present,
    unmatched: plan.unmatched.map((u) => ({ member: u.member, reason: u.reason, label: u.label ?? null, row: u.row ?? null })),
  };
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** The outcome in a failed export's log, or null when the log does not carry one.
 *
 *  `jobError` is the failed job's own error: a log whose `error.message` differs from it is left
 *  from an EARLIER run at the same key, and says nothing about this one. */
export function noProviderGeometryOf(log: unknown, jobError?: string | null): NoProviderGeometry | null {
  if (!isRecord(log) || !isRecord(log.error)) return null;
  const e = log.error;
  if (e.code !== NO_PROVIDER_GEOMETRY) return null;
  const provider = typeof e.geometry_provider === "string" ? e.geometry_provider.trim() : "";
  const collections = Array.isArray(e.collections)
    ? e.collections.filter((c): c is string => typeof c === "string" && c.length > 0)
    : [];
  const unmatched = Array.isArray(e.unmatched) ? e.unmatched.filter(isRecord) : [];
  if (!provider || collections.length === 0 || unmatched.length === 0) return null;
  const message = typeof e.message === "string" ? e.message : "";
  if (jobError && message && jobError !== message) return null;
  return {
    message: message || jobError || "",
    provider,
    collections,
    members: typeof e.members === "number" ? e.members : unmatched.length,
    unmatched: unmatched.map((u) => ({
      member: u.member,
      reason: typeof u.reason === "string" ? u.reason : undefined,
      label: typeof u.label === "string" ? u.label : null,
      row: typeof u.row === "string" ? u.row : null,
    })),
  };
}

/** A provider the geometry can be requested from. */
export interface RequestProviderChoice {
  readonly providerId: string;
  readonly label: string;
  readonly req: AssetNodeRequest;
}

/** A provider as the live plugin specs declare it (`assetProviderCollections`, narrowed). */
export interface DeclaredProvider {
  readonly providerId: string;
  readonly collections: readonly string[];
  readonly nodeRequest: AssetNodeRequest | null;
}

/** The providers the outcome's members can be requested from, the one the export named first.
 *
 *  A provider qualifies when it declares a node request this user may run, the server can read it
 *  into objects (when that is known -- an export from it has to read what it publishes), and it
 *  offers every collection the outcome names (or advertises no list to tell). The members' OWN
 *  providers are not offered: they are what the geometry is missing from. The named provider always
 *  qualifies on the last two counts -- the export already chose it. */
export function requestProviderChoices(args: {
  readonly outcome: NoProviderGeometry;
  readonly declared: readonly DeclaredProvider[];
  readonly readable: readonly WireGeometryProvider[] | null;
  readonly isAdmin: boolean;
  readonly providerName?: (id: string) => string;
}): RequestProviderChoice[] {
  const { outcome, declared, readable, isAdmin } = args;
  const name = args.providerName ?? ((id: string) => id);
  const own = new Set<string>();
  for (const u of outcome.unmatched) {
    const t = isRecord(u.member) && isRecord(u.member.target) ? u.member.target : null;
    if (t && typeof t.provider === "string") own.add(t.provider);
  }
  const wanted = outcome.collections.map((c) => c.toLowerCase());
  const out: RequestProviderChoice[] = [];
  const seen = new Set<string>();
  for (const p of declared) {
    const req = p.nodeRequest;
    if (!req || seen.has(p.providerId)) continue;
    if (req.requiresAdmin && !isAdmin) continue;
    const named = p.providerId === outcome.provider;
    if (!named) {
      if (own.has(p.providerId)) continue;
      if (readable !== null && !readable.some((r) => r.id === p.providerId && r.readable)) continue;
      const offered = p.collections.map((c) => c.toLowerCase());
      if (offered.length > 0 && !wanted.every((c) => offered.includes(c))) continue;
    }
    seen.add(p.providerId);
    out.push({ providerId: p.providerId, label: name(p.providerId), req });
  }
  return out.sort(
    (a, b) =>
      Number(b.providerId === outcome.provider) - Number(a.providerId === outcome.provider) ||
      a.label.localeCompare(b.label),
  );
}

/** "No stored geometry for <collection> from <provider>. Request it now?" -- or, when the provider has
 *  some of the members, "<n> of <m> members have no stored geometry from <provider>. Request them
 *  before exporting?" */
export function noGeometryQuestion(outcome: NoProviderGeometry, providerLabel: string): string {
  if (outcome.present) {
    const n = outcome.unmatched.length;
    return `${n} of ${outcome.members} member${outcome.members === 1 ? "" : "s"} have no stored geometry from ${providerLabel}. Request ${n === 1 ? "it" : "them"} before exporting?`;
  }
  const c = outcome.collections;
  const where = c.length === 1 ? c[0] : `${c.slice(0, -1).join(", ")} and ${c[c.length - 1]}`;
  return `No stored geometry for ${where} from ${providerLabel}. Request it now?`;
}
