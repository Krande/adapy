// Geometry providers for a clash check: which provider supplies each member's geometry, and
// asking a provider for the members it has not published before a check or an annotation.

import type { ClashCheckOptions, ClashCheckTarget, WireClashChecker, WireGeometryPlan, WireGeometryProvider } from "@/services/api/clashCheck";
import type { ScopeUrl } from "@/services/api/client";
import { annotateRunFlow, type ContactFlowDeps, type ContactRunDoc } from "@/services/clash/contactRuns";
import type { CollectionRequestDeps } from "@/assets/collectionRequest";
import type { AssetNodeRequest } from "@/services/assetScopeCollections";
import type { SpecProviderPreference } from "@/services/clashSpecProviders";
import { requestUnmatchedMembers } from "@/utils/groups/groupRequest";
import type { GroupMember } from "@/utils/groups/savedGroups";
import { runClashCheckFlow, type ClashFlowDeps } from "./flows";
import { BUILTIN_CHECKER, type ClashResult } from "./result";

// ---------------------------------------------------------------------------------------------
// Missing geometry, asked for BEFORE a check. A check through a geometry provider leaves out every
// member that provider has no node for; the user who picked the provider usually wants those
// members IN, and the provider can often be asked to publish them. So a group or node check through
// a provider first asks the server which members it would leave out (`geometry-plan`), offers to
// request them, requests exactly those, re-plans to confirm, and only then checks. The check is a
// cache miss after a new publish without `force`: its derived key carries the collection's listing
// token whenever a geometry provider is in play (`routes/clash_check.py`).
// ---------------------------------------------------------------------------------------------

/** What the panel asks before a check through a geometry provider that lacks some members.
 *
 *  `offer` -- the provider takes node requests and this user may run them: request, skip, or cancel.
 *  `admin-only` -- it takes them, but only an administrator may: skip or cancel.
 *  `no-request` -- it takes none, so nothing can be asked for: skip or cancel. */
export interface GeometryRequestPrompt {
  readonly kind: "offer" | "admin-only" | "no-request";
  readonly provider: string;
  /** How many members the provider has no node for. */
  readonly count: number;
  /** The request is quick (`on_demand`), not a long export. Only meaningful for `offer`. */
  readonly onDemand: boolean;
  /** The plugin that would run the request, when there is one. */
  readonly pluginId: string | null;
  /** Why each member is missing, as the plan says -- shown on hover. */
  readonly reasons: readonly string[];
  /** What waits on the answer: a classic `check`, or the `annotate` of a stored convex-contact run
   *  (its specs and member roles, read through the provider). Absent means `check`. */
  readonly purpose?: "check" | "annotate";
}

export type GeometryPromptAnswer = "request" | "skip" | "cancel";

/** The prompt for a plan's unmatched members, given how (and whether) the provider takes node
 *  requests and whether this user is an administrator. `null` when nothing is missing. */
export function geometryRequestPrompt(
  plan: WireGeometryPlan,
  req: AssetNodeRequest | null,
  isAdmin: boolean,
  purpose: "check" | "annotate" = "check",
): GeometryRequestPrompt | null {
  if (plan.unmatched.length === 0) return null;
  const kind = !req ? "no-request" : req.requiresAdmin && !isAdmin ? "admin-only" : "offer";
  return {
    kind,
    provider: plan.geometry_provider,
    count: plan.unmatched.length,
    onDemand: Boolean(req?.onDemand),
    pluginId: req?.pluginId ?? null,
    reasons: plan.unmatched.map((u) => u.reason),
    purpose,
  };
}

/** The question, in words: count, provider, how long a request takes, and who may run it. */
export function geometryPromptSentence(p: GeometryRequestPrompt): string {
  const missing = `${p.count} member${p.count === 1 ? "" : "s"} ${p.count === 1 ? "has" : "have"} no geometry at ${p.provider}`;
  const them = p.count === 1 ? "it" : "them";
  const without =
    p.purpose === "annotate" ? `the run would be annotated without ${them}` : `the check would leave ${them} out`;
  if (p.kind === "no-request") {
    return `${missing}, and ${p.provider} takes no node requests, so it cannot be asked for ${them}. ${without.charAt(0).toUpperCase()}${without.slice(1)}.`;
  }
  if (p.kind === "admin-only") {
    return `${missing}. Only an administrator can request ${them}${p.pluginId ? ` (${p.pluginId})` : ""}; ${without}.`;
  }
  const how = p.onDemand
    ? "The request is quick."
    : "The request runs an export at the provider and may take several minutes.";
  const before = p.purpose === "annotate" ? "before annotating the run" : "before the check";
  return `${missing}. Request ${them} from ${p.provider} ${before}? ${how}`;
}

/** What the missing-geometry step needs, whatever waits on it (a check, or a run's annotate). */
export interface GeometryStepDeps {
  /** `POST …/clash-check/geometry-plan`. */
  geometryPlan(scope: string, target: ClashCheckTarget, provider: string): Promise<WireGeometryPlan>;
  /** How `provider` takes node requests, or null when it takes none. */
  nodeRequestFor(provider: string): Promise<AssetNodeRequest | null>;
  isAdmin: boolean;
  /** Ask the user; resolves with their answer. */
  prompt(question: GeometryRequestPrompt): Promise<GeometryPromptAnswer>;
  /** Element members -> the published tree's row ids (`resolveElementRows`). */
  resolveRows(scope: string, members: readonly GroupMember[]): Promise<ReadonlyMap<string, string>>;
  /** The request jobs' own deps -- the same ones "Request geometry…" uses. */
  request: CollectionRequestDeps;
  /** Where the flow has got to, for the panel; `null` when it moves on to the check itself. */
  onStage?: (stage: string | null) => void;
  /** After a request published something: let the asset browser re-read what is published. */
  afterRequest?: () => Promise<void> | void;
}

export interface GeometryCheckFlowDeps extends ClashFlowDeps, GeometryStepDeps {}

export interface GeometryCheckOutcome {
  readonly result: ClashResult;
  readonly derivedKey: string;
  readonly cached: boolean;
  /** What happened to the missing members on the way, for the panel. */
  readonly notes: readonly string[];
}

/** `runClashCheckFlow`, preceded -- for a group or node checked through a geometry provider -- by
 *  the missing-geometry step above. Resolves `null` when the user cancels. */
export async function runGeometryAwareCheckFlow(
  deps: GeometryCheckFlowDeps,
  scope: string,
  target: ClashCheckTarget,
  options: ClashCheckOptions,
): Promise<GeometryCheckOutcome | null> {
  const provider = options.geometry_provider;
  let notes: string[] = [];
  if (provider && target.kind !== "file") {
    const step = await missingGeometryStep(deps, scope, target, provider, "check");
    if (step === null) return null;
    notes = step;
  }
  deps.onStage?.(null);
  const out = await runClashCheckFlow(deps, scope, target, options);
  return { ...out, notes };
}

/** Plan which members `provider` has; when some are missing, ask -- request them (then plan again),
 *  go on without them, or cancel. Resolves the notes for the panel, or `null` on cancel. */
async function missingGeometryStep(
  deps: GeometryStepDeps,
  scope: string,
  target: ClashCheckTarget,
  provider: string,
  purpose: "check" | "annotate",
): Promise<string[] | null> {
  const notes: string[] = [];
  const stage = (s: string | null) => deps.onStage?.(s);
  stage(`asking ${provider} which members it has`);
  const plan = await deps.geometryPlan(scope, target, provider);
  if (plan.unmatched.length === 0) return notes;
  const req = await deps.nodeRequestFor(provider);
  const question = geometryRequestPrompt(plan, req, deps.isAdmin, purpose) as GeometryRequestPrompt;
  stage(null);
  const answer = await deps.prompt(question);
  if (answer === "cancel") return null;
  if (answer === "request" && question.kind === "offer" && req) {
    notes.push(...(await requestMissing(deps, scope, provider, req, plan, stage)));
    stage(`asking ${provider} again`);
    const again = await deps.geometryPlan(scope, target, provider);
    if (again.unmatched.length > 0) {
      notes.push(
        `${again.unmatched.length} member${again.unmatched.length === 1 ? " is" : "s are"} still not at ${provider} and ` +
          `${again.unmatched.length === 1 ? "is" : "are"} left out: ${again.unmatched[0].reason}` +
          (again.unmatched.length > 1 ? " (and others)" : ""),
      );
    } else {
      notes.push(`Every member is now at ${provider}.`);
    }
  } else {
    notes.push(`${plan.unmatched.length} member${plan.unmatched.length === 1 ? "" : "s"} not at ${provider} left out.`);
  }
  return notes;
}

// The same step for a convex-contact RUN. The run is measured from the viewer GLBs -- any provider's
// meshes -- but its specs and member-bound roles need the members READ into objects, which a
// mesh-only provider cannot do. So once the run is stored, the members are read through a provider
// that can (the "Geometry from" choice, or the readable one there is when nobody reads the members'
// own): plan, ask about the missing ones, request them, and only then annotate the stored run
// (`POST …/clash-contacts/annotate`). Mesh-derived roles stay the fallback for what does not bind.

/** The members' own providers, as the target names them: a node's own (not its collection's other
 *  publishers -- those are what a geometry provider is), a group's members'. */
function ownProviders(target: ClashCheckTarget): readonly string[] {
  if (target.kind === "file") return [];
  if (target.kind === "node") return target.provider ? [target.provider] : [];
  return targetProviders(target);
}

/** The provider a convex-contact run is annotated through, or `null` for each member's own.
 *
 *  The "Geometry from" choice when one is made (and is not simply every member's own provider).
 *  Without one: when the providers are known and no live pool reads any of the members' own, the
 *  readable provider there is (the first, by id) -- so a run over a mesh-only provider's members
 *  still binds without the user knowing to pick one. A file has no provider. */
export function contactGeometryProvider(
  target: ClashCheckTarget | null,
  chosen: string | null,
  available: readonly WireGeometryProvider[] | null,
): string | null {
  if (!target || target.kind === "file") return null;
  const own = ownProviders(target);
  if (chosen) return own.length > 0 && own.every((p) => p === chosen) ? null : chosen;
  if (!available || own.length === 0) return null;
  const readable = available.filter((p) => p.readable).map((p) => p.id).sort();
  if (own.some((p) => readable.includes(p))) return null;
  return readable[0] ?? null;
}

export interface GeometryAnnotateFlowDeps extends GeometryStepDeps {
  /** The run's REST deps (`annotate`, `getRun`, the job poll). */
  contact: ContactFlowDeps;
}

export interface GeometryAnnotateOutcome {
  readonly run: ContactRunDoc;
  readonly runKey: string;
  readonly notes: readonly string[];
}

/** Annotate a stored convex-contact run through `provider`, preceded by the missing-geometry step.
 *  Resolves `null` when the user cancels (the run stays stored as it is). */
export async function runGeometryAwareAnnotateFlow(
  deps: GeometryAnnotateFlowDeps,
  scope: string,
  target: ClashCheckTarget,
  runKey: string,
  provider: string,
): Promise<GeometryAnnotateOutcome | null> {
  const through = target.kind === "file" ? null : provider;
  const notes = through ? await missingGeometryStep(deps, scope, target, through, "annotate") : [];
  if (notes === null) return null;
  const out = await annotateRunFlow(deps.contact, scope as ScopeUrl, runKey, through, (s) => deps.onStage?.(s));
  deps.onStage?.(null);
  const applicability = out.run.provenance?.applicability as { error?: unknown } | undefined;
  if (out.error) notes.push(`The specs were not resolved: ${out.error}`);
  else if (typeof applicability?.error === "string") notes.push(`The specs were not resolved: ${applicability.error}`);
  return { run: out.run, runKey: out.runKey, notes };
}

/** Request exactly the plan's unmatched members from `provider`, through the shared group request:
 *  element members resolved to the published tree's rows, whole nodes by their own id, each with the
 *  label the plan looked it up under (a provider may need it to find a node another provider
 *  published), in the provider's batches. Returns notes for the panel. */
async function requestMissing(
  deps: GeometryStepDeps,
  scope: string,
  provider: string,
  req: AssetNodeRequest,
  plan: WireGeometryPlan,
  stage: (s: string | null) => void,
): Promise<string[]> {
  // One implementation with the selection export's "request it now?" (`requestUnmatchedMembers`).
  return (await requestUnmatchedMembers(deps, scope, provider, req, plan.unmatched, stage)).notes;
}

/** The options a run sends, from what the panel holds.
 *
 *  Core's checker is sent as it always was -- no `checker` field -- so a default run keeps the
 *  options document, and with it the derived key, of every result cached before checkers existed.
 *  A contributed checker sends its name and ITS settings, filled from its advertised defaults; core's
 *  tolerances are dropped for one that says it does not use them, so editing a field the engine
 *  ignores cannot turn a cache hit into a rerun.
 *
 *  `selectedPasses === null` means "the checker's default set", which the wire expresses by
 *  OMITTING the field -- sending an empty array would ask for no passes at all. */
export function checkRequestOptions(state: {
  options: ClashCheckOptions;
  selectedPasses: readonly string[] | null;
  checker: string;
  checkerOptions: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  availableCheckers: readonly WireClashChecker[] | null;
  /** `null`/absent = each member read by its own provider, which the wire expresses by OMITTING
   *  `geometry_provider` -- so a default run keeps the cache key it always had. */
  geometryProvider?: string | null;
}): ClashCheckOptions {
  const geometry: Partial<ClashCheckOptions> = state.geometryProvider
    ? { geometry_provider: state.geometryProvider }
    : {};
  const withPasses: ClashCheckOptions = {
    ...(state.selectedPasses === null ? state.options : { ...state.options, passes: state.selectedPasses }),
    ...geometry,
  };
  if (state.checker === BUILTIN_CHECKER) return withPasses;
  const spec = state.availableCheckers?.find((c) => c.name === state.checker);
  const typed = state.checkerOptions[state.checker] ?? {};
  const checkerOptions: Record<string, unknown> = {};
  for (const opt of spec?.options ?? []) {
    const value = opt.key in typed ? typed[opt.key] : opt.default;
    if (value !== undefined && value !== null && value !== "") checkerOptions[opt.key] = value;
  }
  const base: ClashCheckOptions = spec?.uses_core_options ? withPasses : { root: state.options.root ?? null };
  return {
    ...base,
    ...geometry,
    ...(state.selectedPasses !== null ? { passes: state.selectedPasses } : {}),
    checker: state.checker,
    ...(Object.keys(checkerOptions).length ? { checker_options: checkerOptions } : {}),
  };
}

/** The spec-provider preference in force: the session's override when set, else the scope's. */
export function effectiveSpecProviders(state: {
  adminSpecProviders: SpecProviderPreference;
  specProvidersOverride: SpecProviderPreference | undefined;
}): SpecProviderPreference {
  return state.specProvidersOverride === undefined ? state.adminSpecProviders : state.specProvidersOverride;
}

/** One entry of the panel's "Geometry from" choice. */
export interface GeometryProviderChoice {
  readonly id: string;
  readonly label: string;
  /** Whether a live pool can read it into members -- only then can it be picked. */
  readonly readable: boolean;
  /** Why it cannot be picked, when it cannot. */
  readonly reason: string | null;
}

export const UNREADABLE_GEOMETRY_REASON = "can't be read into members -- choose a provider that publishes members";

/** The providers a target's members were published by, as far as the target says. A file has none. */
export function targetProviders(target: ClashCheckTarget | null): readonly string[] {
  if (!target || target.kind === "file") return [];
  const out = new Set<string>();
  if (target.kind === "node") {
    if (target.provider) out.add(target.provider);
    for (const p of target.collectionProviders ?? []) out.add(p);
  } else {
    for (const m of target.members) if (m.target.kind === "node" && m.target.provider) out.add(m.target.provider);
  }
  return [...out].sort();
}

/** What the "Geometry from" select offers: every provider a live pool can read, then -- disabled,
 *  with the reason -- the target's own providers that nobody can read, so a user looking for the
 *  provider they picked the members in sees WHY it is not a choice rather than its absence.
 *
 *  `available === null` (never asked, or the route predates this) offers nothing at all: an
 *  unknown list is not evidence that the target's providers are unreadable. */
export function geometryProviderChoices(
  target: ClashCheckTarget | null,
  available: readonly WireGeometryProvider[] | null,
): readonly GeometryProviderChoice[] {
  const out: GeometryProviderChoice[] = [];
  const seen = new Set<string>();
  for (const p of available ?? []) {
    seen.add(p.id);
    out.push({
      id: p.id,
      label: p.label || p.id,
      readable: p.readable,
      reason: p.readable ? null : p.unavailable_reason || UNREADABLE_GEOMETRY_REASON,
    });
  }
  if (available !== null) {
    for (const id of targetProviders(target)) {
      if (seen.has(id)) continue;
      out.push({ id, label: id, readable: false, reason: UNREADABLE_GEOMETRY_REASON });
    }
  }
  return out.sort((a, b) => Number(b.readable) - Number(a.readable) || a.id.localeCompare(b.id));
}

/** Where a result's geometry came from, read off its provenance: the provider asked for, and how
 *  many members were re-addressed to it or left out. `null` when the check read each member from
 *  its own provider, which is what every result written before the choice existed did. */
export function geometrySourceOf(
  result: ClashResult | null,
): { provider: string; remapped: number; unmatched: number } | null {
  const prov = result?.provenance ?? {};
  const provider = prov.geometry_provider;
  if (typeof provider !== "string" || !provider) return null;
  const count = (v: unknown) => (Array.isArray(v) ? v.length : 0);
  return { provider, remapped: count(prov.geometry_remap), unmatched: count(prov.geometry_unmatched) };
}
