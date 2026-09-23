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

import { requestRender } from "@/state/perfStore";
import { useModelState } from "@/state/modelState";
import ClashRootPicker from "@/components/info_box_scene/ClashRootPicker";
import { startClashMarkerSync } from "@/utils/scene/clashJointMarkers";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { selectInOtherModel } from "@/utils/scene/crossModelSelect";
import {
  detailBatches,
  checkedSourceName,
  facetOptions,
  filteredGroups,
  groupColor,
  groupFacets,
  isSpecAvailable,
  jointsForGroup,
  memberNamesForGroup,
  noMembersSentence,
  useClashCheckStore,
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

/** The run form: tolerances + a subtree scope. Core's own options only -- no provider option
 *  ever rides in this document (`ClashCheckOptions`'s doc). */
const RunForm: React.FC<{ scope: string; sourceKey: string | null }> = ({ scope, sourceKey }) => {
  const options = useClashCheckStore((s) => s.options);
  const busy = useClashCheckStore((s) => s.busy);
  const setOptions = useClashCheckStore((s) => s.setOptions);
  const runCheck = useClashCheckStore((s) => s.runCheck);

  return (
    <div className="flex flex-col gap-1 pb-1 border-b border-gray-700">
      <div className="flex flex-wrap items-center gap-2">
        {numberInput("out-of-plane tol", options.out_of_plane_tol, (v) => setOptions({ out_of_plane_tol: v }), {
          title: "Beam-to-beam out-of-plane tolerance",
        })}
        {numberInput("point tol", options.point_tol, (v) => setOptions({ point_tol: v }), {
          step: 0.000001,
          title: "Coincidence tolerance for a shared node",
        })}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <ClashRootPicker value={options.root ?? null} onChange={(root) => setOptions({ root })} />
        <label className="flex items-center gap-1 text-[11px] text-gray-300">
          <input
            type="checkbox"
            checked={options.include_plate_joints ?? true}
            onChange={(e) => setOptions({ include_plate_joints: e.target.checked })}
          />
          plate joints
        </label>
      </div>
      <button
        type="button"
        disabled={!sourceKey || busy}
        className="rounded-sm px-2 py-1 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-gray-100 text-xs"
        onClick={() => void runCheck(scope)}
      >
        {busy ? "checking…" : "Run clash check"}
      </button>
    </div>
  );
};

const ApplicableBadge: React.FC<{ spec: ClashApplicableSpec; scope: string; jointIds: readonly string[] }> = ({
  spec,
  scope,
  jointIds,
}) => {
  // `liveCapabilities: null` -- see `isSpecAvailable`'s doc and the TODO in
  // `services/api/clashCheck.ts`: the live `connection_specs` union route does not exist yet, so
  // this fails OPEN (every capability-bearing spec is offered) rather than hiding one that may in
  // fact work. Once that route lands, swap this `null` for the fetched set.
  const available = isSpecAvailable(spec, null);
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
        if (!sourceName) return;
        void selectInOtherModel({ file: sourceName, nodeNames: joint.members.map((m) => m.name) });
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
            if (!sourceName) return;
            void selectInOtherModel({ file: sourceName, nodeNames: [...memberNamesForGroup(result, group.typeKey)] });
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
          {group.applicable.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1 px-1 pb-1">
              {group.applicable.map((spec) => (
                <ApplicableBadge key={spec.spec} spec={spec} scope={scope} jointIds={group.jointIds} />
              ))}
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
  const jointIds = useMemo(() => groups.flatMap((g) => [...g.jointIds]), [groups]);
  const batches = useMemo(() => detailBatches(result, jointIds), [result, jointIds]);
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

  const sourceKey = useClashCheckStore((s) => s.sourceKey);
  const checkedSource = useClashCheckStore((s) => s.sourceName);
  const setSource = useClashCheckStore((s) => s.setSource);
  const result = useClashCheckStore((s) => s.result);
  const error = useClashCheckStore((s) => s.error);
  const filters = useClashCheckStore((s) => s.filters);
  const setFilters = useClashCheckStore((s) => s.setFilters);
  const detailGlbKey = useClashCheckStore((s) => s.detailGlbKey);
  const detailError = useClashCheckStore((s) => s.detailError);
  const showMarkers = useClashCheckStore((s) => s.showMarkers);
  const setShowMarkers = useClashCheckStore((s) => s.setShowMarkers);
  const detailBusy = useClashCheckStore((s) => s.detailBusy);
  const detailProgress = useClashCheckStore((s) => s.detailProgress);
  const runDetailAll = useClashCheckStore((s) => s.runDetailAll);
  const [loadMsg, setLoadMsg] = useState<string | null>(null);

  // The 3D markers follow the STORE, not this component: mounting starts the subscription once
  // (it is idempotent) and leaving the tab does not take the spheres down with the panel.
  useEffect(() => {
    startClashMarkerSync();
  }, []);

  // The check runs against the loaded SOURCE, never the GLB (see the module doc). A different
  // model invalidates whatever result was showing -- see `setSource`'s own doc for why.
  //
  // With ONE exception: loading this run's own produced joints as an overlay makes them the
  // loaded source, and throwing the result away then would clear the rows the user just acted
  // from -- "generate detail model" would empty the panel that started it.
  useEffect(() => {
    if (loadedSourceName && loadedSourceName === detailGlbKey) return;
    setSource(loadedSourceName, loadedSourceName);
  }, [loadedSourceName, detailGlbKey, setSource]);

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

  if (!loadedSourceName) {
    return <div className="text-sm text-gray-400 px-1 py-2">No model loaded.</div>;
  }

  return (
    <div className="text-sm px-1 py-1 flex flex-col gap-1">
      <RunForm scope={scope} sourceKey={sourceKey} />
      {error && <Banner tone="error">{error}</Banner>}
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
          </div>
          <FilterBar result={result} filters={filters} onChange={setFilters} />
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
