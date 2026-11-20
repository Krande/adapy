// The clash-check store's state and actions (`../clashCheckStore`).

import type { ClashCheckOptions, ClashCheckTarget, WireClashChecker, WireClashPassSpec, WireGeometryProvider } from "@/services/api/clashCheck";
import type { ContactRunSummary } from "@/services/clash/contactRuns";
import type { SpecProviderPreference } from "@/services/clashSpecProviders";
import type { GroupMember } from "@/utils/groups/savedGroups";
import type { ClashFilters } from "./derive";
import type { ClashResult } from "./result";
import type { GeometryPromptAnswer, GeometryRequestPrompt } from "./geometry";
import type { ProducedJoints } from "./detail";

// ---------------------------------------------------------------------------------------------
// The store. Thin: state plus actions that assemble the REAL deps (the REST client, the global
// job toast) and call the pure flows above. `ClashesPanel.tsx` reads state and derives its view
// with the pure functions above in a `useMemo`, never by adding new store fields for a badge.
// ---------------------------------------------------------------------------------------------

export interface ClashCheckState {
  /** The scene source name the check was run against, for display only. */
  sourceName: string | null;
  /** Set when the thing to check was chosen EXPLICITLY -- a published node (the Sources tab's
   *  "Check for joints") or a saved group -- rather than followed from the loaded file. Mutually
   *  exclusive with `sourceKey` by construction -- the setters clear each other -- because the
   *  route refuses both at once and a store holding both would have to guess which it meant. */
  assetTarget: ClashCheckTarget | null;
  /** The model's SOURCE key -- what the check actually reads (never the GLB). */
  sourceKey: string | null;
  options: ClashCheckOptions;
  filters: ClashFilters;
  selectedGroup: string | null;
  selectedJoints: readonly string[];
  /** The ONE joint under the cursor -- set by clicking its marker in the 3D view, by clicking its
   *  row, and by arrowing through the list. Its group is opened with it, because a cursor on a
   *  joint inside a collapsed group would be a selection with nothing on screen to show it. */
  selectedJoint: string | null;
  /** What happens to the members that are NOT part of what the cursor is on: nothing (`off`),
   *  faded to a translucent ghost, or hidden outright. A joint is a few members inside thousands
   *  and is usually behind something, so this is how it gets looked at.
   *
   *  Defaults to `ghost`, not `off`: a joint picked out of a frame is nearly always occluded, and
   *  the fade is the reading that keeps the rest of the model as context rather than removing it.
   *  It costs nothing when nothing is selected -- with no cursor there is nothing to isolate. */
  isolate: "off" | "ghost" | "hidden";
  /** How faint the ghost is, 0-1. Adjustable because a fixed value is wrong at both ends:
   *  unusable on a dense deck, pointless on a bare frame. */
  isolateOpacity: number;
  /** Whether the joint markers are drawn in the 3D scene. Held here rather than in the panel so
   *  the overlay survives a tab switch -- the markers are a view of the RESULT, not of the panel
   *  being mounted. */
  showMarkers: boolean;
  /** Whether a convex-contact run's contacts are drawn in the 3D scene -- each patch, its normal and
   *  its members' roles (`utils/scene/clashContactOverlay`). Off by default: a run has hundreds. */
  showContacts: boolean;
  /** Joint-marker size, as a multiple of the default (a fraction of the model's diagonal). */
  markerScale: number;

  jobId: string | null;
  derivedKey: string | null;
  /** The stored convex-contact RUN the result came from (``_clash/...``), when it came from one --
   *  the thing a detail is made from. Mutually exclusive with `derivedKey` (a `result@` document). */
  runKey: string | null;
  /** The current target's convex-contact runs, newest first -- they outlive the page. */
  contactRuns: readonly ContactRunSummary[];
  /** The scope's newest convex-contact runs over every target, each with its target and label. */
  recentContactRuns: readonly ContactRunSummary[];
  cached: boolean;
  result: ClashResult | null;
  busy: boolean;
  error: string | null;
  /** Run the check IN THE BROWSER instead of enqueuing a worker job: convex contact through adacpp's
   *  wasm clash module, core's connectivity check through the wasm IFC member scan
   *  (`services/clash/browserClashCheck.ts`). `null` = the engine's own default (`runsInBrowser`):
   *  convex contact in the browser where its module loads, core's on the server -- the route that
   *  works for every source and runs the plate passes. */
  inBrowser: boolean | null;
  /** Whether this viewer's convex-contact wasm module loads. `null` until probed (on the panel's
   *  `loadCapabilities`, or by the first run). */
  contactWasm: boolean | null;
  /** Which passes the NEXT run should use, by name. `null` = core's default set, which is what
   *  omitting `passes` from the request means -- not the same as an empty selection, which is a
   *  user asking for nothing. */
  selectedPasses: readonly string[] | null;
  /** Which passes this DEPLOYMENT could run -- core's, plus whatever a live pool advertises.
   *  Fetched once when the panel opens, so a contributed pass is offerable BEFORE the first run
   *  that uses it. `null` until asked. */
  availablePasses: readonly WireClashPassSpec[] | null;
  /** The live `connection_specs` capability union behind `isSpecAvailable`. `null` = not asked
   *  yet, which that function deliberately reads as "offer everything" rather than as "nothing
   *  is available". */
  liveCapabilities: ReadonlySet<string> | null;
  /** Which clash-check ENGINES this deployment could run -- core's plus every checker a live pool
   *  advertises. `null` until asked; the panel then offers core's alone. */
  availableCheckers: readonly WireClashChecker[] | null;
  /** The engine the NEXT run uses. Convex contact by default, where it can run here (its wasm module
   *  loads, or a worker lists it); else core's connectivity check, said so in `checkerNote`
   *  (`clashCheck/engine.ts`). Not persisted: a new session starts from the default again. */
  checker: string;
  /** Whether the user picked `checker` themselves. Only then is it kept over the default: a session
   *  that never chose follows what can run here. */
  checkerChosen: boolean;
  /** Why the next run does not use the engine it would have (convex contact unavailable here), for
   *  the panel. `null` when nothing was substituted. */
  checkerNote: string | null;
  /** The selected checker's own settings, keyed by its advertised option keys. Kept per checker,
   *  so switching engines and back does not lose what was typed. */
  checkerOptions: Readonly<Record<string, Readonly<Record<string, unknown>>>>;
  /** Every spec provider (capability, or `builtin`) a live pool could serve a spec from. */
  specProviders: readonly string[];
  /** The scope's admin default spec-provider preference (`public.clash.spec_providers`). `null` is
   *  unrestricted -- also what an unconfigured scope or an unreadable setting answers. */
  adminSpecProviders: SpecProviderPreference;
  /** This session's override of that default, or `undefined` to follow it. Distinct from `null`,
   *  which is an override to "every provider". */
  specProvidersOverride: SpecProviderPreference | undefined;
  /** Producers whose joints are hidden in the result view. A joint found by a hidden producer is
   *  filtered out of the rows, the markers and the counts alike, so the view never shows a number
   *  that does not match what is listed. */
  hiddenOrigins: readonly string[];
  /** Which provider the NEXT run reads members' geometry from. `null` = each member's own -- what
   *  every run did before this existed, and what the wire says by omitting the field. Ignored for
   *  a plain file, which has no provider. */
  geometryProvider: string | null;
  /** The providers a live pool can read into members. `null` until asked. */
  availableGeometryProviders: readonly WireGeometryProvider[] | null;
  /** The question a run through a geometry provider is waiting on: some members are not at the
   *  provider -- request them first? `null` when nothing is being asked. */
  geometryPrompt: GeometryRequestPrompt | null;
  /** Where the missing-geometry step has got to (planning, requesting, re-planning). */
  geometryStage: string | null;
  /** What the last run's missing-geometry step did, for the panel. */
  geometryNotes: readonly string[];
  answerGeometryPrompt: (answer: GeometryPromptAnswer) => void;
  /** What the browser run is doing, while it does it -- a pyodide boot is slow enough that a bare
   *  spinner is not an honest answer. `null` when no browser run is in flight. */
  browserStage: string | null;

  detailBusy: boolean;
  detailError: string | null;
  detailSpec: string | null;
  /** Set once a hand-off finishes -- the RESULT document's key (the produced-joints take-off). */
  detailDerivedKey: string | null;
  /** The produced GLB's key, which is what the scene overlays. Separate from `detailDerivedKey`
   *  because they are different documents: loading the take-off json as a model answers 415. */
  detailGlbKey: string | null;
  /** What this session's detail runs have PRODUCED, accumulated across specs -- the Review tab's
   *  produced section reads this beside the loaded model's own take-off. */
  producedJoints: ProducedJoints | null;
  /** Joints a builder refused during those runs, reported rather than hidden. */
  producedSkipped: readonly string[];
  /** Where a batched "generate detail model" run has got to, for the button's label. `null` when
   *  no batched run is in flight. */
  detailProgress: { done: number; total: number } | null;
  /** The running detail job's own stage (`"detail 120/392"`: the worker reports it per joint).
   *  `null` when no detail job is running. */
  detailStage: string | null;

  setSource: (sourceName: string | null, sourceKey: string | null) => void;
  /** The panel following the LOADED model: `setSource`, unless a node or group was picked
   *  explicitly -- loading or mounting must not silently replace a target the user chose. */
  followLoadedSource: (sourceName: string | null) => void;
  /** Point the check at a published node. `label` is what the panel calls it. */
  setAssetTarget: (label: string | null, target: ClashCheckTarget | null) => void;
  /** Point the check at a saved group: its members, across models, checked as one model. */
  setGroupTarget: (group: { id?: string; name: string; members: readonly GroupMember[] }) => void;
  setOptions: (patch: Partial<ClashCheckOptions>) => void;
  setFilters: (patch: Partial<ClashFilters>) => void;
  selectGroup: (typeKey: string | null) => void;
  selectJoints: (ids: readonly string[]) => void;
  focusJoint: (id: string | null) => void;
  setIsolate: (mode: "off" | "ghost" | "hidden") => void;
  setIsolateOpacity: (v: number) => void;
  setShowMarkers: (v: boolean) => void;
  setShowContacts: (v: boolean) => void;
  setMarkerScale: (v: number) => void;
  setInBrowser: (v: boolean | null) => void;
  setSelectedPasses: (names: readonly string[] | null) => void;
  /** Pick the engine -- an explicit choice, kept over the default from then on. Clears the pass
   *  selection: passes belong to a checker, and a selection made for one engine names passes the
   *  other does not have. */
  setChecker: (name: string) => void;
  setCheckerOption: (key: string, value: unknown) => void;
  setGeometryProvider: (id: string | null) => void;
  /** Override the scope's spec-provider preference for this session; `undefined` follows it again. */
  setSpecProvidersOverride: (pref: SpecProviderPreference | undefined) => void;
  /** Fetch what this deployment can run. Best effort: a deployment whose routes predate these
   *  answers 404, and the panel then falls back to offering core's passes and every capability,
   *  which is exactly what it did before they existed. */
  loadCapabilities: (scope: string) => Promise<void>;
  toggleOriginHidden: (origin: string) => void;
  runCheck: (scope: string) => Promise<void>;
  /** List the current target's convex-contact runs. Best effort: none is an answer. */
  loadContactRuns: (scope: string) => Promise<void>;
  /** Reopen a stored convex-contact run as the result. */
  openContactRun: (scope: string, key: string) => Promise<void>;
  /** List the scope's newest runs over every target. Best effort: none is an answer. */
  loadRecentContactRuns: (scope: string) => Promise<void>;
  /** Reopen a run made on any target: the panel moves to that target, then shows the run. */
  openRecentContactRun: (scope: string, key: string) => Promise<void>;
  /** Resolve the open run's specs and member roles again -- through the geometry provider
   *  (`contactGeometryProvider`) when its members' own cannot be read, after the missing-geometry
   *  step; else through each member's own. */
  annotateContactRun: (scope: string) => Promise<void>;
  runDetail: (scope: string, jointIds: readonly string[], spec: string) => Promise<void>;
  /** Detail every joint that has a matching generator, one job per spec. */
  runDetailAll: (scope: string, only?: readonly string[] | null) => Promise<void>;
  /** The scene was cleared: drop what was found ON its models -- the result and its markers, the
   *  selection in it, the detail model and its produced joints. What the user set up stays: the
   *  target, the options, the filters, the runs listed for the target. A check still running is
   *  left to finish. */
  forgetResult: () => void;
  reset: () => void;
}
