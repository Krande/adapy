// Scene panel "Clashes" tab: identify the joints in the loaded model's SOURCE, group them by
// type, and hand a chosen group off to a generator. This is the IDENTIFICATION half of one
// workflow; the REVIEW half is the existing `Joints` tab, which lists this run's joints beside
// whatever a detailing engine already produced (`JointsOverviewPanel.tsx`) -- so this panel never
// renders a second joints table of its own. `Clashes -> Joints` is the strip, on purpose.
//
// Rows are GROUPS, not joints (Decision 10: "a frame has hundreds of joints and a handful of
// KINDS of joint, and the kinds are what a person decides about"). Every count, badge and filter
// below is read through the pure functions in `state/clashCheckStore.ts` against the store's one
// `result` object -- nothing here recomputes a fact a sibling row already computed.

import React, { useEffect, useMemo, useState } from "react";

import { browserClashCheckSupports } from "@/services/clash/browserClashCheck";
import { CONTACT_ORIGIN } from "@/services/clash/contactRuns";
import { requestRender } from "@/state/perfStore";
import { useModelState } from "@/state/modelState";
import { useProviderName } from "@/state/providerNamesStore";
import { providerIdTitle } from "@/assets/providerNames";
import ClashRootPicker from "@/components/info_box_scene/ClashRootPicker";
import IsolationControls from "@/components/info_box_scene/joints/IsolationControls";
import {
  CheckerOptionsForm,
  CheckerSelector,
  OriginFilter,
  PassSelector,
  SpecProviderSelector,
} from "@/components/info_box_scene/joints/PassControls";
import { describeClashTarget, type ClashCheckTarget } from "@/services/api/clashCheck";
import { rankSpecs } from "@/services/clashSpecProviders";
import { startClashMarkerSync } from "@/utils/scene/clashJointMarkers";
import { ROLE_COLOURS } from "@/utils/scene/clashContactPlan";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { selectionGroupId } from "@/utils/groups/savedGroups";
import { focusJoint as focusJointEverywhere, selectClashMembers } from "@/utils/scene/clashJointFocus";
import {
  BUILTIN_CHECKER,
  contactRunOf,
  detailBatches,
  checkedSourceName,
  contactGeometryProvider,
  effectiveSpecProviders,
  facetOptions,
  filteredGroups,
  geometryPromptSentence,
  geometryProviderChoices,
  geometrySourceOf,
  groupColor,
  groupFacets,
  isSpecAvailable,
  jointsForGroup,
  memberNamesForGroup,
  noMembersSentence,
  useClashCheckStore,
  visibleResult,
  type ClashApplicableSpec,
  type ClashFilters,
  type ClashGroup,
  type ClashJoint,
  type ClashResult,
} from "@/state/clashCheckStore";

const Banner: React.FC<{ tone: "info" | "warn" | "error"; children: React.ReactNode }> = ({ tone, children }) => (
  <div
    className={`mx-0 mt-1 rounded-sm px-2 py-1 text-xs ${
      tone === "error" ? "bg-red-900/60 text-red-100" : tone === "warn" ? "bg-amber-900/50 text-amber-100" : "bg-gray-700 text-gray-200"
    }`}
  >
    {children}
  </div>
);

/** "Show contacts in 3D" for a convex-contact run, with the role legend the overlay is coloured by
 *  (`ROLE_COLOURS`, read by both, so the swatches cannot disagree with the scene). */
const ContactsToggle: React.FC<{ result: ClashResult }> = ({ result }) => {
  const showContacts = useClashCheckStore((s) => s.showContacts);
  const setShowContacts = useClashCheckStore((s) => s.setShowContacts);
  const selectedJoint = useClashCheckStore((s) => s.selectedJoint);
  const run = contactRunOf(useClashCheckStore((s) => s.result));
  if (!run) return null;
  const withRoles = run.contacts.filter((c) => c.roles).length;
  const swatch = (color: string, label: string) => (
    <span className="flex items-center gap-1">
      <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-gray-400">
      <label
        className="flex items-center gap-1 shrink-0"
        title="Draw every contact the list shows: each patch on both members, in their roles' colours, and the contact normal (member i to member j) as an arrow. Click a patch to focus its contact. Off, only the contact selected in the list is drawn."
      >
        <input type="checkbox" checked={showContacts} onChange={(e) => setShowContacts(e.target.checked)} />
        show contacts in 3D
      </label>
      {(showContacts || selectedJoint) && (
        <>
          {swatch(ROLE_COLOURS.incoming, "incoming")}
          {swatch(ROLE_COLOURS.landing, "landing")}
          {swatch(ROLE_COLOURS.unresolved, "role unresolved")}
          <span title="How many of this run's contacts have their incoming / landing members resolved">
            {withRoles}/{result.joints.length} with roles
          </span>
        </>
      )}
    </div>
  );
};

const numberInput = (
  label: string,
  value: number | undefined,
  onChange: (v: number) => void,
  opts?: { step?: number; title?: string },
) => (
  <label className="flex items-center gap-1 text-[11px] text-gray-300" title={opts?.title}>
    {label}
    <input
      type="number"
      step={opts?.step ?? 0.01}
      className="w-20 bg-gray-600 text-white rounded-sm px-1 py-0.5"
      value={value ?? 0}
      onChange={(e) => onChange(Number(e.target.value))}
    />
  </label>
);

/** The spec-provider preference in force, read the same way by every badge and button. */
function useSpecProviders() {
  const adminSpecProviders = useClashCheckStore((s) => s.adminSpecProviders);
  const specProvidersOverride = useClashCheckStore((s) => s.specProvidersOverride);
  return effectiveSpecProviders({ adminSpecProviders, specProvidersOverride });
}

/** WHAT the next run checks: the loaded file, a published node, or a saved group's members. An
 *  explicit node or group stays the target while other models load -- the panel no longer
 *  follows the scene over it -- until the user switches back to the loaded model here. */
const CheckTargetLine: React.FC<{
  target: ClashCheckTarget | null;
  label: string | null;
  sourceKey: string | null;
  loadedSourceName: string | null;
  onUseLoaded: () => void;
}> = ({ target, label, sourceKey, loadedSourceName, onUseLoaded }) => {
  const what = target
    ? target.kind === "group"
      ? `group "${target.name}" (${target.members.length} member${target.members.length === 1 ? "" : "s"}, checked as one model)`
      : target.kind === "node"
        ? `node ${label ?? describeClashTarget(target)}`
        : `file ${target.sourceKey.split("/").pop()}`
    : sourceKey
      ? `file ${sourceKey.split("/").pop()}`
      : "nothing";
  const title = target ? describeClashTarget(target) : (sourceKey ?? undefined);
  return (
    <div className="flex items-center gap-2 text-[11px] text-gray-300">
      <span className="flex-1 min-w-0 truncate" title={title}>
        Checking: <span className="text-gray-100">{what}</span>
      </span>
      {target && loadedSourceName && (
        <button
          type="button"
          className="shrink-0 rounded-sm px-1.5 py-0.5 bg-gray-700 hover:bg-gray-600 text-gray-200"
          title={`Check the loaded model (${loadedSourceName}) instead`}
          onClick={onUseLoaded}
        >
          use loaded model
        </button>
      )}
    </div>
  );
};

/** "Geometry from": which provider the members are READ from. Several providers can publish one
 *  collection, and the one the members were picked in may publish meshes only -- nothing a check
 *  can read. Picking a provider that publishes members re-addresses each member to that
 *  provider's node of the same name on the worker. Providers of this target that no live pool can
 *  read are listed disabled, with the reason, so their absence is explained rather than puzzling.
 *  Not shown for a plain file, which has one reader and no provider. */
const GeometryProviderSelector: React.FC<{ target: ClashCheckTarget | null }> = ({ target }) => {
  const available = useClashCheckStore((s) => s.availableGeometryProviders);
  const value = useClashCheckStore((s) => s.geometryProvider);
  const setGeometryProvider = useClashCheckStore((s) => s.setGeometryProvider);
  const choices = useMemo(() => geometryProviderChoices(target, available), [target, available]);
  const pn = useProviderName();
  if (!target || target.kind === "file") return null;
  return (
    <label
      className="flex items-center gap-1 text-[11px] text-gray-300"
      title="Which provider's published members the check reads. Members picked in another provider's tree are matched to this one's by name."
    >
      Geometry from
      <select
        className="bg-gray-600 text-white rounded-sm px-1 py-0.5 text-[11px] min-w-0 flex-1"
        value={value ?? ""}
        onChange={(e) => setGeometryProvider(e.target.value || null)}
      >
        <option value="">Own provider (each member's)</option>
        {choices.map((c) => (
          <option key={c.id} value={c.id} disabled={!c.readable} title={c.reason ?? providerIdTitle(c.id)}>
            {/* The scope's display name where it has one; else the pool's own label for it. */}
            {pn(c.id) !== c.id ? pn(c.id) : c.label}
            {c.readable ? "" : ` — ${c.reason}`}
          </option>
        ))}
      </select>
    </label>
  );
};

/** Where the result's geometry came from, when it was not each member's own provider. */
const GeometrySourceLine: React.FC<{ result: ClashResult }> = ({ result }) => {
  const source = geometrySourceOf(result);
  const pn = useProviderName();
  if (!source) return null;
  return (
    <Banner tone={source.unmatched ? "warn" : "info"}>
      Geometry from <span title={providerIdTitle(source.provider)}>{pn(source.provider)}</span>
      {` · ${source.remapped} member${source.remapped === 1 ? "" : "s"} matched by name`}
      {source.unmatched > 0 && ` · ${source.unmatched} not found there (left out)`}
    </Banner>
  );
};

/** The missing-geometry step of a run through a geometry provider: the question when the provider
 *  lacks some members (request them first, check without them, or cancel), where a request has got
 *  to while it runs, and what became of the missing members once the check is made. Inline rather
 *  than a browser dialog, so the panel stays usable and the reasons can be read before answering. */
const GeometryRequestStep: React.FC = () => {
  const prompt = useClashCheckStore((s) => s.geometryPrompt);
  const stage = useClashCheckStore((s) => s.geometryStage);
  const notes = useClashCheckStore((s) => s.geometryNotes);
  const answer = useClashCheckStore((s) => s.answerGeometryPrompt);
  if (!prompt && !stage && notes.length === 0) return null;
  const btn = "rounded-sm px-1.5 py-0.5 text-[11px] text-gray-100";
  return (
    <div className="flex flex-col gap-1">
      {prompt && (
        <div
          role="alertdialog"
          aria-label="Missing geometry"
          className="rounded-sm bg-amber-900/50 px-2 py-1 text-xs text-amber-100"
        >
          <div title={prompt.reasons.join("\n")}>{geometryPromptSentence(prompt)}</div>
          <div className="mt-1 flex flex-wrap justify-end gap-1">
            {prompt.kind === "offer" && (
              <button
                type="button"
                autoFocus
                className={`${btn} bg-blue-700 hover:bg-blue-600`}
                onClick={() => answer("request")}
              >
                {prompt.purpose === "annotate" ? "Request, then annotate" : "Request, then check"}
              </button>
            )}
            <button type="button" className={`${btn} bg-gray-700 hover:bg-gray-600`} onClick={() => answer("skip")}>
              {prompt.purpose === "annotate" ? "Annotate" : "Check"} without {prompt.count === 1 ? "it" : "them"}
            </button>
            <button type="button" className={`${btn} bg-gray-700 hover:bg-gray-600`} onClick={() => answer("cancel")}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {stage && <div className="text-[11px] text-gray-300 truncate" title={stage}>{stage}…</div>}
      {!prompt && notes.length > 0 && (
        <Banner tone="info">
          {notes.map((n, i) => (
            <div key={i} className="break-words">
              {n}
            </div>
          ))}
        </Banner>
      )}
    </div>
  );
};

/** The run form: the checker, then its settings -- core's tolerances for core's checker, the
 *  contributed checker's own advertised form for one of those -- and a subtree scope. */
const RunForm: React.FC<{ scope: string; sourceKey: string | null; loadedSourceName: string | null }> = ({
  scope,
  sourceKey,
  loadedSourceName,
}) => {
  const assetTarget = useClashCheckStore((s) => s.assetTarget);
  const targetLabel = useClashCheckStore((s) => s.sourceName);
  const setSource = useClashCheckStore((s) => s.setSource);
  const setGroupTarget = useClashCheckStore((s) => s.setGroupTarget);
  const options = useClashCheckStore((s) => s.options);
  const checker = useClashCheckStore((s) => s.checker);
  const checkerSpec = useClashCheckStore((s) => s.availableCheckers?.find((c) => c.name === s.checker));
  const isCore = checker === BUILTIN_CHECKER;
  const coreOptions = isCore || Boolean(checkerSpec?.uses_core_options);
  const busy = useClashCheckStore((s) => s.busy);
  const setOptions = useClashCheckStore((s) => s.setOptions);
  const runCheck = useClashCheckStore((s) => s.runCheck);
  const inBrowser = useClashCheckStore((s) => s.inBrowser);
  const setInBrowser = useClashCheckStore((s) => s.setInBrowser);
  const browserStage = useClashCheckStore((s) => s.browserStage);
  const geometryPrompt = useClashCheckStore((s) => s.geometryPrompt);
  const geometryStage = useClashCheckStore((s) => s.geometryStage);
  const loadContactRuns = useClashCheckStore((s) => s.loadContactRuns);
  // The convex-contact CLASH check measures viewer GLBs: one model's (a file or a node) in the
  // browser or on a worker, a saved group's several in the browser only -- so no switch for a group.
  const isContact = checker === CONTACT_ORIGIN;
  const isSavedGroup = assetTarget?.kind === "group" && Boolean(assetTarget.id);
  const contactPossible = isContact && (Boolean(sourceKey) || assetTarget?.kind === "node" || isSavedGroup);
  // Core's connectivity check in the browser: only IFC -- the member scan is an IFC reader, so for
  // anything else the option would be a checkbox that silently did nothing. A node or a group is
  // read on the server whatever this says, so the option is not offered for them.
  const browserPossible =
    (contactPossible && assetTarget?.kind !== "group") || (isCore && !assetTarget && browserClashCheckSupports(sourceKey ?? ""));

  // A clash run outlives the page: list the target's runs whenever the target (or the engine) changes.
  useEffect(() => {
    if (contactPossible) void loadContactRuns(scope);
  }, [contactPossible, sourceKey, assetTarget, scope, loadContactRuns]);

  return (
    <div className="flex flex-col gap-1 pb-1 border-b border-gray-700">
      <CheckTargetLine
        target={assetTarget}
        label={targetLabel}
        sourceKey={sourceKey}
        loadedSourceName={loadedSourceName}
        onUseLoaded={() => setSource(loadedSourceName, loadedSourceName)}
      />
      <CheckerSelector />
      <GeometryProviderSelector target={assetTarget} />
      {coreOptions && (
        <div className="flex flex-wrap items-center gap-2">
          {numberInput("out-of-plane tol", options.out_of_plane_tol, (v) => setOptions({ out_of_plane_tol: v }), {
            title: "Beam-to-beam out-of-plane tolerance",
          })}
          {numberInput("point tol", options.point_tol, (v) => setOptions({ point_tol: v }), {
            step: 0.000001,
            title: "Coincidence tolerance for a shared node",
          })}
        </div>
      )}
      <CheckerOptionsForm />
      <PassSelector />
      <div className="flex flex-wrap items-center gap-2">
        <ClashRootPicker
          value={assetTarget?.kind === "group" ? null : (options.root ?? null)}
          onChange={(root) => {
            // A part names a part of the loaded model: leave a group target for the scene first.
            if (assetTarget?.kind === "group") setSource(loadedSourceName, loadedSourceName);
            setOptions({ root });
          }}
          scope={scope}
          groupId={assetTarget?.kind === "group" ? (assetTarget.id ?? null) : null}
          groupLabel={assetTarget?.kind === "group" ? assetTarget.name : null}
          onPickGroup={(group) => {
            setOptions({ root: null });
            setGroupTarget(group);
          }}
          onPickSelection={(members) => {
            setOptions({ root: null });
            setGroupTarget({
              id: selectionGroupId(members),
              name: `selection (${members.length} member${members.length === 1 ? "" : "s"})`,
              members,
            });
          }}
        />
        {coreOptions && (
          <label className="flex items-center gap-1 text-[11px] text-gray-300">
            <input
              type="checkbox"
              checked={options.include_plate_joints ?? true}
              onChange={(e) => setOptions({ include_plate_joints: e.target.checked })}
            />
            plate joints
          </label>
        )}
        {browserPossible && (
          <label
            className="flex items-center gap-1 text-[11px] text-gray-300"
            title={
              isContact
                ? "Measure the model's GLB in the browser with the same compiled clash check a worker runs — no worker job for the check itself. The run is saved beside the model; a worker then resolves which generators could detail each clash."
                : "Read the members with the wasm IFC scan and run the same compiled beam-to-beam pass in the browser — no upload, no worker job. Produces no server-side result, so detailing a joint still needs a server run."
            }
          >
            <input type="checkbox" checked={inBrowser} onChange={(e) => setInBrowser(e.target.checked)} />
            in browser
          </label>
        )}
      </div>
      <button
        type="button"
        disabled={(!sourceKey && !assetTarget) || busy}
        className="rounded-sm px-2 py-1 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-gray-100 text-xs"
        onClick={() => void runCheck(scope)}
      >
        {busy
          ? browserStage
            ? `${browserStage}…`
            : geometryPrompt
              ? "waiting for your answer…"
              : geometryStage
                ? "getting geometry…"
                : "checking…"
          : "Run clash check"}
      </button>
      <GeometryRequestStep />
      {contactPossible && <ContactRunHistory scope={scope} />}
    </div>
  );
};

/** The target's stored clash runs, newest first -- reopen one instead of measuring again. */
const ContactRunHistory: React.FC<{ scope: string }> = ({ scope }) => {
  const runs = useClashCheckStore((s) => s.contactRuns);
  const runKey = useClashCheckStore((s) => s.runKey);
  const busy = useClashCheckStore((s) => s.busy);
  const openContactRun = useClashCheckStore((s) => s.openContactRun);
  const annotateContactRun = useClashCheckStore((s) => s.annotateContactRun);
  const through = useClashCheckStore((s) =>
    contactGeometryProvider(s.assetTarget, s.geometryProvider, s.availableGeometryProviders),
  );
  if (runs.length === 0 && !runKey) return null;
  return (
    <div className="flex items-center gap-1">
      {runs.length > 0 && (
        <label className="flex flex-1 min-w-0 items-center gap-1 text-[11px] text-gray-300" title="Clash runs kept beside this model">
          previous runs
          <select
            className="flex-1 min-w-0 bg-gray-800 text-gray-200 rounded-sm px-1 py-0.5"
            value={runKey ?? ""}
            disabled={busy}
            onChange={(e) => e.target.value && void openContactRun(scope, e.target.value)}
          >
            <option value="">—</option>
            {runs.map((r) => (
              <option key={r.key} value={r.key}>
                {`${r.created_at.replace("T", " ").replace("Z", "")} · ${r.contacts} clash${r.contacts === 1 ? "" : "es"} · ${r.engine === "wasm" ? "browser" : "worker"}`}
              </option>
            ))}
          </select>
        </label>
      )}
      {runKey && (
        <button
          type="button"
          disabled={busy}
          className="rounded-sm px-1.5 py-0.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-[11px] text-gray-100"
          title={
            through
              ? `Resolve this run's specs and member roles again, reading its members through ${through} (missing members can be requested first)`
              : "Resolve this run's specs and member roles again, reading each member through its own provider"
          }
          onClick={() => void annotateContactRun(scope)}
        >
          Resolve specs
        </button>
      )}
    </div>
  );
};

const ApplicableBadge: React.FC<{ spec: ClashApplicableSpec; scope: string; jointIds: readonly string[] }> = ({
  spec,
  scope,
  jointIds,
}) => {
  // The live `connection_specs` union, fetched when the panel opened. Still `null` until that
  // answer arrives (or where the route is absent), and `isSpecAvailable` reads null as "offer
  // everything" -- there is no way to tell "no live pool" from "nobody has checked yet", and
  // hiding a spec that in fact works is the worse of the two mistakes.
  const liveCapabilities = useClashCheckStore((s) => s.liveCapabilities);
  const available = isSpecAvailable(spec, liveCapabilities);
  const detailBusy = useClashCheckStore((s) => s.detailBusy);
  const detailSpec = useClashCheckStore((s) => s.detailSpec);
  const runDetail = useClashCheckStore((s) => s.runDetail);
  const busyHere = detailBusy && detailSpec === spec.spec;
  return (
    <button
      type="button"
      disabled={!available || detailBusy}
      title={
        available
          ? spec.capability
            ? `Runs on the '${spec.capability}' pool`
            : "Runs in core's default pool"
          : `No live pool currently advertises '${spec.capability}' -- not offered`
      }
      className={`rounded-sm px-1.5 py-0.5 text-[10px] ${
        available ? "bg-emerald-800 text-emerald-100 hover:bg-emerald-700" : "bg-gray-700 text-gray-500 cursor-not-allowed"
      }`}
      onClick={() => {
        if (!available) return;
        void runDetail(scope, jointIds, spec.spec);
      }}
    >
      {busyHere ? `${spec.spec}…` : spec.spec}
    </button>
  );
};

const JointRow: React.FC<{ joint: ClashJoint; scope: string; sourceName: string | null }> = ({ joint, scope, sourceName }) => (
  <div className="flex items-center gap-2 pl-3 py-0.5 text-[11px] text-gray-300 border-t border-white/5">
    <button
      type="button"
      className="text-blue-300 hover:text-white hover:underline truncate flex-1 min-w-0 text-left"
      title={`Select ${joint.members.map((m) => m.name).join(", ")}`}
      onClick={() => {
        // The shared focus action, not a bare selection: clicking a joint here has to mean the
        // same thing as clicking its marker or arrowing onto it in the Joints tree, or the
        // cursor-driven views (the marker emphasis, isolation) would follow only two of the
        // three ways a user can point at a joint.
        void focusJointEverywhere(joint.id);
      }}
    >
      {joint.members.map((m) => m.name).join(" + ")}
    </button>
    <span className="text-gray-500 shrink-0">{joint.applicable.length ? `${joint.applicable.length} spec(s)` : "no spec"}</span>
  </div>
);

const GroupRow: React.FC<{ group: ClashGroup; result: ClashResult; scope: string; sourceName: string | null }> = ({
  group,
  result,
  scope,
  sourceName,
}) => {
  const selectedGroup = useClashCheckStore((s) => s.selectedGroup);
  const selectGroup = useClashCheckStore((s) => s.selectGroup);
  const providers = useSpecProviders();
  // Only the specs the provider preference allows, best first -- the same ranking the batch
  // button uses, so the first badge is the spec "generate detail model" would pick.
  const offered = useMemo(() => rankSpecs(group.applicable, providers), [group.applicable, providers]);
  const open = selectedGroup === group.typeKey;
  const joints = useMemo(() => (open ? jointsForGroup(result, group.typeKey) : []), [open, result, group.typeKey]);
  const facets = useMemo(() => groupFacets(result, group.typeKey), [result, group.typeKey]);

  return (
    <div className="border-t border-gray-700">
      <div className="flex items-center gap-2 px-1 py-1">
        <span
          className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
          style={{ backgroundColor: groupColor(result, group.typeKey) }}
          title="This group's colour in the 3D view"
        />
        <button
          type="button"
          className="flex-1 min-w-0 text-left text-xs text-gray-100 hover:text-white truncate"
          onClick={() => selectGroup(open ? null : group.typeKey)}
          title={group.typeKey}
        >
          {open ? "▼" : "▶"} {group.typeLabel}
        </button>
        <span className="text-xs text-gray-400 tabular-nums">{group.count}</span>
        <button
          type="button"
          className="text-[10px] text-blue-300 hover:text-white"
          title="Select every member in this group"
          onClick={() => {
            void selectClashMembers(group.jointIds, sourceName, [...memberNamesForGroup(result, group.typeKey)]);
          }}
        >
          select all
        </button>
      </div>
      {open && (
        <div className="pb-1">
          <div className="flex flex-wrap items-center gap-1 px-1 pb-1 text-[10px] text-gray-400">
            {facets.sectionFamilies.length > 0 && <span>sections: {facets.sectionFamilies.join(", ")}</span>}
            {facets.memberTypes.length > 0 && <span>· types: {facets.memberTypes.join(", ")}</span>}
          </div>
          {offered.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1 px-1 pb-1">
              {offered.map((spec) => (
                <ApplicableBadge key={spec.spec} spec={spec} scope={scope} jointIds={group.jointIds} />
              ))}
            </div>
          ) : group.applicable.length > 0 ? (
            <div className="px-1 pb-1 text-[10px] text-gray-500">
              {group.applicable.length} spec(s) match, but none from a provider enabled for detailing here.
            </div>
          ) : (
            <div className="px-1 pb-1 text-[10px] text-gray-500">No registered spec matches every joint in this group.</div>
          )}
          {joints.map((j) => (
            <JointRow key={j.id} joint={j} scope={scope} sourceName={sourceName} />
          ))}
        </div>
      )}
    </div>
  );
};

const FilterBar: React.FC<{ result: ClashResult; filters: ClashFilters; onChange: (patch: Partial<ClashFilters>) => void }> = ({
  result,
  filters,
  onChange,
}) => {
  const options = useMemo(() => facetOptions(result), [result]);
  return (
    <div className="flex flex-wrap items-center gap-1 pt-1 pb-1">
      <select
        aria-label="Section family"
        className="bg-gray-600 text-white rounded-sm text-[10px] px-1 py-0.5"
        value={filters.sectionFamily ?? ""}
        onChange={(e) => onChange({ sectionFamily: e.target.value || null })}
      >
        <option value="">section: any</option>
        {options.sectionFamilies.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <select
        aria-label="Member type"
        className="bg-gray-600 text-white rounded-sm text-[10px] px-1 py-0.5"
        value={filters.memberType ?? ""}
        onChange={(e) => onChange({ memberType: e.target.value || null })}
      >
        <option value="">type: any</option>
        {options.memberTypes.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
      <select
        aria-label="Applicable"
        className="bg-gray-600 text-white rounded-sm text-[10px] px-1 py-0.5"
        value={filters.applicable}
        onChange={(e) => onChange({ applicable: e.target.value as ClashFilters["applicable"] })}
      >
        <option value="any">generator: any</option>
        <option value="matched">has a generator</option>
        <option value="unmatched">unmatched</option>
      </select>
      {(filters.sectionFamily || filters.memberType || filters.applicable !== "any") && (
        <button
          type="button"
          className="text-[10px] text-gray-400 hover:text-white"
          onClick={() => onChange({ sectionFamily: null, memberType: null, applicable: "any" })}
        >
          clear
        </button>
      )}
    </div>
  );
};

/** "Generate detail model" for everything currently on screen that a generator binds.
 *
 *  Scoped to the FILTERED groups rather than the whole result: the filters are what the user is
 *  looking at, and a button that quietly detailed rows they had narrowed away would build joints
 *  nobody asked about. One job per spec, each joint in exactly one of them (`detailBatches`). */
const GenerateDetailButton: React.FC<{
  result: ClashResult;
  groups: readonly ClashGroup[];
  scope: string;
  busy: boolean;
  progress: { done: number; total: number } | null;
  onRun: (jointIds: readonly string[]) => void;
}> = ({ result, groups, busy, progress, onRun }) => {
  const providers = useSpecProviders();
  const jointIds = useMemo(() => groups.flatMap((g) => [...g.jointIds]), [groups]);
  const batches = useMemo(() => detailBatches(result, jointIds, providers), [result, jointIds, providers]);
  const count = batches.reduce((n, b) => n + b.jointIds.length, 0);
  if (count === 0) return null;
  return (
    <button
      type="button"
      disabled={busy}
      title={`Detail ${count} joint${count === 1 ? "" : "s"} with ${batches.length} generator${
        batches.length === 1 ? "" : "s"
      } (${batches.map((b) => b.spec.spec).join(", ")})`}
      className={`self-start rounded-sm px-2 py-0.5 text-[11px] ${
        busy ? "bg-gray-700 text-gray-500 cursor-not-allowed" : "bg-emerald-800 text-emerald-100 hover:bg-emerald-700"
      }`}
      onClick={() => onRun(jointIds)}
    >
      {busy && progress ? `generating ${progress.done}/${progress.total}…` : `generate detail model (${count})`}
    </button>
  );
};

const ClashesPanel: React.FC = () => {
  const loadedSourceName = useModelState((s) => s.loadedSourceName);
  const scope = scopeUrlPart(useScopeStore((s) => s.current));
  const loadCapabilities = useClashCheckStore((s) => s.loadCapabilities);

  const sourceKey = useClashCheckStore((s) => s.sourceKey);
  const checkedSource = useClashCheckStore((s) => s.sourceName);
  const assetTarget = useClashCheckStore((s) => s.assetTarget);
  const followLoadedSource = useClashCheckStore((s) => s.followLoadedSource);
  // The FILTERED result -- see `visibleResult`. Every view reads the same derivation, so a
  // hidden producer disappears from the rows, the markers and the counts together.
  const rawResult = useClashCheckStore((s) => s.result);
  const hiddenOrigins = useClashCheckStore((s) => s.hiddenOrigins);
  const result = React.useMemo(
    () => visibleResult({ result: rawResult, hiddenOrigins }),
    [rawResult, hiddenOrigins],
  );
  const error = useClashCheckStore((s) => s.error);
  const filters = useClashCheckStore((s) => s.filters);
  const setFilters = useClashCheckStore((s) => s.setFilters);
  const detailGlbKey = useClashCheckStore((s) => s.detailGlbKey);
  const detailError = useClashCheckStore((s) => s.detailError);
  const showMarkers = useClashCheckStore((s) => s.showMarkers);
  const setShowMarkers = useClashCheckStore((s) => s.setShowMarkers);
  const markerScale = useClashCheckStore((s) => s.markerScale);
  const setMarkerScale = useClashCheckStore((s) => s.setMarkerScale);
  const detailBusy = useClashCheckStore((s) => s.detailBusy);
  const detailProgress = useClashCheckStore((s) => s.detailProgress);
  const runDetailAll = useClashCheckStore((s) => s.runDetailAll);
  const [loadMsg, setLoadMsg] = useState<string | null>(null);

  // The 3D markers follow the STORE, not this component: mounting starts the subscription once
  // (it is idempotent) and leaving the tab does not take the spheres down with the panel.
  useEffect(() => {
    startClashMarkerSync();
  }, []);

  // What this deployment can run, asked once when the panel opens -- which is what lets a
  // contributed pass be TICKED before the first run that uses it, and what turns the generator
  // badges from "offer everything" into the live answer. Best effort by construction: a
  // deployment whose routes predate these leaves both null and the panel behaves as it did.
  useEffect(() => {
    void loadCapabilities(scope);
  }, [scope, loadCapabilities]);

  // The check runs against the loaded SOURCE, never the GLB (see the module doc). A different
  // model invalidates whatever result was showing -- see `setSource`'s own doc for why.
  //
  // With ONE exception: loading this run's own produced joints as an overlay makes them the
  // loaded source, and throwing the result away then would clear the rows the user just acted
  // from -- "generate detail model" would empty the panel that started it.
  //
  // And a node or group chosen EXPLICITLY elsewhere ("Check for joints" in Sources, a saved
  // group's "Clash check") is not followed over: this effect runs when the tab mounts, which is
  // exactly when those callers switch to it, and following then wiped the target they had just
  // set. `followLoadedSource` leaves an explicit target alone; the run form's "use loaded model"
  // goes back to following.
  useEffect(() => {
    if (loadedSourceName && loadedSourceName === detailGlbKey) return;
    followLoadedSource(loadedSourceName);
  }, [loadedSourceName, detailGlbKey, followLoadedSource]);

  // A finished hand-off is loaded as an ordinary overlay source, the same path
  // `SourceSection`'s "Re-convert" uses -- `overlay_file_in_scene` registers it under its own
  // source name, so it appears in `Files` and its `.stats.json` joints feed the `Joints` tab.
  useEffect(() => {
    if (!detailGlbKey) return;
    let cancelled = false;
    (async () => {
      setLoadMsg("loading detailed joints…");
      try {
        const { overlay_file_in_scene } = await import("@/utils/scene/handlers/overlay_file_in_scene");
        await overlay_file_in_scene(detailGlbKey, detailGlbKey, { scope });
        if (!cancelled) {
          setLoadMsg("loaded — see the Joints tab");
          requestRender();
        }
      } catch (e) {
        if (!cancelled) setLoadMsg(`failed to load: ${e instanceof Error ? e.message : String(e)}`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [detailGlbKey, scope]);

  // Selection resolves against the model the CHECK ran against, not the produced overlay that a
  // detail run adds on top of it (`checkedSourceName`).
  const selectionSource = checkedSourceName(checkedSource, loadedSourceName);
  const groups = useMemo(() => (result ? filteredGroups(result, filters) : []), [result, filters]);
  const sentence = useMemo(() => (result ? noMembersSentence(result) : null), [result]);

  // A node or a group is read on the server, so it can be checked with nothing loaded.
  if (!loadedSourceName && !assetTarget) {
    return <div className="text-sm text-gray-400 px-1 py-2">No model loaded.</div>;
  }

  return (
    <div className="text-sm px-1 py-1 flex flex-col gap-1">
      <RunForm scope={scope} sourceKey={sourceKey} loadedSourceName={loadedSourceName} />
      {error && <Banner tone="error">{error}</Banner>}
      {result && <GeometrySourceLine result={result} />}
      {result?.warnings.map((w) => (
        <Banner key={w} tone="warn">
          {w}
        </Banner>
      ))}
      {loadMsg && <Banner tone="info">{loadMsg}</Banner>}
      {detailError && <Banner tone="error">{detailError}</Banner>}

      {result && sentence && <Banner tone="info">{sentence}</Banner>}

      {result && !sentence && (
        <>
          <OriginFilter />
          <div className="flex items-center gap-2 text-[11px] text-gray-400">
            <span className="flex-1 min-w-0 truncate">
              {result.counts.joints ?? 0} joint{(result.counts.joints ?? 0) === 1 ? "" : "s"}
              {typeof result.counts.joints_with_a_generator === "number" &&
                ` · ${result.counts.joints_with_a_generator} with a matching generator`}
            </span>
            <label className="flex items-center gap-1 shrink-0" title="Draw a coloured sphere at every joint centre">
              <input type="checkbox" checked={showMarkers} onChange={(e) => setShowMarkers(e.target.checked)} />
              show in 3D
            </label>
            {showMarkers && (
              <input
                type="range"
                // Logarithmic, the default in the middle: 0.1x at the left, 10x at the right.
                min={-100}
                max={100}
                step={5}
                value={Math.round(Math.log10(markerScale) * 100)}
                onChange={(e) => setMarkerScale(10 ** (Number(e.target.value) / 100))}
                onDoubleClick={() => setMarkerScale(1)}
                className="w-16 shrink-0 accent-blue-500"
                aria-label="Joint marker size"
                title={`Joint marker size: ${markerScale.toFixed(markerScale < 1 ? 2 : 1)}× the default (double-click to reset)`}
              />
            )}
          </div>
          <ContactsToggle result={result} />
          <IsolationControls />
          <FilterBar result={result} filters={filters} onChange={setFilters} />
          <SpecProviderSelector />
          <GenerateDetailButton
            result={result}
            groups={groups}
            scope={scope}
            busy={detailBusy}
            progress={detailProgress}
            onRun={(ids) => void runDetailAll(scope, ids)}
          />
          <div className="flex flex-col">
            {groups.length === 0 ? (
              <div className="text-[11px] text-gray-500 px-1 py-1">No group matches the current filters.</div>
            ) : (
              groups.map((g) => (
                <GroupRow key={g.typeKey} group={g} result={result} scope={scope} sourceName={selectionSource} />
              ))
            )}
          </div>
        </>
      )}

      {!result && !error && <div className="text-[11px] text-gray-500 px-1">Run a check to identify this model's joints.</div>}
    </div>
  );
};

export default ClashesPanel;
