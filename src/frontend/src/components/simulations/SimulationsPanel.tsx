// The simulations panel (`core:simulations`, titled "Robotic Simulations"): what can be simulated
// here, and the runs of the open scope.
//
// Four parts, top to bottom:
//
//   * when something offered a subject (`useSimulationSubjectStore`, `ctx.simulations.offerSubject`),
//     a "Subject" picker, the newest offer chosen: a run is started ON it, in its scope, by a
//     provider that accepts its kind (those are listed first; the others say "does not take a ...");
//   * the providers the server lists, grouped by domain (the kind of simulation), with a filter
//     by domain; one that no live worker serves is shown as offline and cannot be started;
//   * the start form of the chosen provider -- a plugin's parameter section when one is
//     registered for the provider or its domain (`simulationProviders` in the plugin registry),
//     else inputs generated from the provider's JSON Schema;
//   * the scope's runs, newest first, a line each: its status, what it is called, when it was
//     started, a cancel button while it is going, and a plugin's "open" handler when there is
//     one. A line opens to the rest (who started it, how long it took, its outcome, its
//     artefacts as downloads), one at a time. The list scrolls within the panel. The runs a
//     client started together (a batch) are ONE line -- label, provider, runs by status, progress,
//     the newest start -- that opens to its runs, indented, a page at a time (`BatchRow`).
//
// It knows no engine. Everything specific to one is the server's description of the provider or
// a plugin's registration.

import React, { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import ErrorBoundary from "@/components/common/ErrorBoundary";
import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";
import { activeModeDef, activeShellMode, shellModesVersion, subscribeShellModes } from "@/layout/shellModes";
import { makePluginContext } from "@/plugins/context";
import { activateMode, modeOffered } from "@/plugins/pluginModes";
import { openResultButtonLabel, resolveSimulationProviderUi, type AdaPluginContext } from "@/plugins/registry";
import { filesApi } from "@/services/api/files";
import {
  isActiveRun,
  type SimulationArtefact,
  type SimulationBatch,
  type SimulationProviderInfo,
  type SimulationRun,
} from "@/services/api/simulations";
import {
  batchCountsLabel,
  batchPercent,
  batchTone,
  isBatchOpen,
  listedRunCount,
  runListItems,
  setBatchOpen,
} from "@/services/simulations/batchRows";
import { durationLabel, runLabel, runOutcome, runSubjectLabel, runTone, startedLabel } from "@/services/simulations/runRow";
import {
  modeHasOwnProvider,
  modeProvidersFirst,
  modeToOpenIn,
  providerShownInMode,
  simulationsFilterFor,
} from "@/services/simulations/modeScope";
import { defaultParameters, missingRequired } from "@/services/simulations/schemaForm";
import { useViewerStores } from "@/state/AdaViewerContext";
import { useRunConsoleStore } from "@/state/runConsoleStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { providersByDomain, useSimulationStore } from "@/state/simulationStore";
import {
  acceptingFirst,
  chosenSubject,
  doesNotTake,
  formSubject,
  NO_SUBJECT,
  providerAccepts,
  subjectId,
  useSimulationSubjectStore,
  type SimulationSubject,
} from "@/state/simulationSubjects";
import { usePluginTheme } from "@/state/themeStore";

import {
  clickRun,
  EMPTY_SELECTION,
  selectAllInView,
  toggleRun as togglePicked,
  type RunSelection,
} from "@/services/simulations/runSelection";

import {
  confirmAndDeleteRuns,
  DeleteBatchDialog,
  MoveToBatchDialog,
  RunSelectContext,
  SelectionBar,
  useLongPress,
  useRunSelect,
  type RunSelectApi,
} from "./RunAdmin";
import { SchemaForm } from "./SchemaForm";
import { SimulationsFrame } from "./SimulationsFrame";

/** The panel's name, wherever it is shown. */
export const SIMULATIONS_PANEL_TITLE = "Robotic Simulations";

const BUTTON = "rounded-sm bg-[var(--ada-panel-surface)] px-2 py-0.5 text-[var(--ada-panel-text)] hover:bg-[color-mix(in_srgb,var(--ada-panel-text)_20%,transparent)] disabled:opacity-40 disabled:hover:bg-[var(--ada-panel-surface)]";
const STATUS_DOT: Record<string, string> = {
  queued: "bg-gray-400",
  running: "bg-blue-400",
  done: "bg-green-400",
  attention: "bg-orange-400",
  error: "bg-red-400",
  cancelled: "bg-yellow-400",
};
const STATUS_COLOR: Record<string, string> = {
  queued: "text-[var(--ada-panel-text)]",
  running: "text-[var(--ada-accent)]",
  done: "text-[var(--ada-pass)]",
  attention: "text-[var(--ada-warn)]",
  error: "text-[var(--ada-fail)]",
  cancelled: "text-[var(--ada-warn)]",
};

/** "12.3 kB" for a download link; empty when the size is not known. */
function sizeLabel(size: number | null): string {
  if (size == null) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} kB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** The plugin context the seam's callbacks are given, for the plugin that registered them. */
function usePluginContextFor(): (pluginId: string) => AdaPluginContext {
  const stores = useViewerStores();
  const theme = usePluginTheme();
  return (pluginId) => makePluginContext(pluginId, stores, theme);
}

const ArtefactList: React.FC<{ scope: string; artefacts: readonly SimulationArtefact[] }> = ({ scope, artefacts }) => {
  const [error, setError] = useState<string | null>(null);
  if (artefacts.length === 0) return null;
  return (
    <ul className="flex flex-col gap-0.5">
      {artefacts.map((a) => (
        <li key={a.name} className="flex items-center gap-2">
          <button
            type="button"
            className="text-[var(--ada-accent)] hover:underline truncate text-left"
            title={`Download ${a.name}`}
            onClick={() => {
              setError(null);
              // Through an authorised fetch: with sign-in on, a bare link would carry no token.
              filesApi
                .downloadBlob(scope, a.key, a.name.split("/").pop() ?? a.name)
                .catch((err) => setError(err instanceof Error ? err.message : String(err)));
            }}
          >
            {a.name}
          </button>
          <span className="text-[var(--ada-panel-text-muted)] shrink-0">
            {a.role}
            {a.partial ? " · partial" : ""}
            {a.size != null ? ` · ${sizeLabel(a.size)}` : ""}
          </span>
        </li>
      ))}
      {error && <li className="text-[var(--ada-fail)] break-words">{error}</li>}
    </ul>
  );
};

const RunRow: React.FC<{
  scope: string;
  run: SimulationRun;
  provider: SimulationProviderInfo | null;
  expanded: boolean;
  onToggle: () => void;
}> = ({ scope, run, provider, expanded, onToggle }) => {
  const cancelRun = useSimulationStore((s) => s.cancelRun);
  const select = useRunSelect();
  const pressHandlers = useLongPress(() => select.longPress(run.run_id));
  const [deleting, setDeleting] = useState(false);
  const picked = select.isSelected(run.run_id);
  const contextFor = usePluginContextFor();
  const [openError, setOpenError] = useState<string | null>(null);
  const baseCtx = contextFor("");
  const opener = resolveSimulationProviderUi(baseCtx, run.provider).openResult;
  const active = isActiveRun(run);
  const percent = Math.round(Math.max(0, Math.min(1, run.progress)) * 100);
  const status = run.cancel_requested && active ? "cancelling" : run.status;
  const label = runLabel(run, provider);
  const offered = useSimulationSubjectStore((s) => s.offered);
  const subjectLabel = runSubjectLabel(run, scope, offered);
  const outcome = runOutcome(run);
  const tone = runTone(run);
  const took = durationLabel(run.started_at ?? run.created_at, run.finished_at);

  const open = async () => {
    if (!opener?.ui.openResult) return;
    setOpenError(null);
    // A provider whose runs belong to a mode (a robot cell's) opens its result there, where the UI
    // on screen offers it: the user asked to look at this run, and that is where it is looked at.
    const target = modeToOpenIn(resolveSimulationProviderUi(baseCtx, run.provider).mode, activeShellMode(), modeOffered);
    if (target) activateMode(target, { reason: `open ${label}` });
    try {
      await opener.ui.openResult(contextFor(opener.pluginId), { run, scope, provider });
      // The run on screen is this one from here on: its console follows its activity feed.
      useRunConsoleStore.getState().open(scope, run, label);
    } catch (err) {
      // Against this run only: one result that will not open says nothing about the next.
      setOpenError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <li
      className={`flex flex-col rounded-sm ${
        picked
          ? "bg-[color-mix(in_srgb,var(--ada-accent)_25%,transparent)]"
          : expanded
            ? "bg-[color-mix(in_srgb,var(--ada-panel-text)_20%,transparent)]"
            : "bg-[var(--ada-panel-surface)]"
      }`}
      data-run-id={run.run_id}
      data-selected={picked || undefined}
    >
      <div className="flex items-center gap-1.5 px-1 py-0.5 min-w-0">
        {select.selecting && (
          <input
            type="checkbox"
            className="h-4 w-4 shrink-0"
            checked={picked}
            aria-label={`Select ${label}`}
            data-run-select
            // The click, not the change: a change event carries no shift key.
            onClick={(e) => {
              e.stopPropagation();
              select.click(run.run_id, e.shiftKey);
            }}
            onChange={() => {}}
          />
        )}
        {/* The line itself opens and closes the rest (picks it, while picking); a long press starts
            picking. The buttons beside it are their own. */}
        <button
          type="button"
          className="flex items-center gap-1.5 min-w-0 flex-1 text-left hover:text-[var(--ada-panel-text)] select-none"
          aria-expanded={select.selecting ? undefined : expanded}
          aria-pressed={select.selecting ? picked : undefined}
          title={`${label}: ${status}`}
          {...pressHandlers}
          onClick={(e) => (select.selecting ? select.click(run.run_id, e.shiftKey) : onToggle())}
        >
          <span className={`h-2 w-2 rounded-full shrink-0 ${STATUS_DOT[tone] ?? "bg-gray-400"}`} aria-hidden />
          <span className="font-medium truncate">{label}</span>
          {subjectLabel && (
            <span className="text-[var(--ada-panel-text)] truncate min-w-0" title={`On ${subjectLabel}`} data-run-subject>
              on {subjectLabel}
            </span>
          )}
          <span className="text-[var(--ada-panel-text-muted)] shrink-0">{active ? run.stage || status : startedLabel(run.created_at)}</span>
        </button>
        {opener && run.artefacts.length > 0 && (
          <button type="button" className={BUTTON} onClick={() => void open()}>
            {openResultButtonLabel(opener.ui, active)}
          </button>
        )}
        {active && (
          <button
            type="button"
            className={BUTTON}
            disabled={run.cancel_requested}
            onClick={() => void cancelRun(scope, run.run_id)}
          >
            Cancel
          </button>
        )}
      </div>
      {active && (
        <div className="h-0.5 bg-[var(--ada-panel-surface)] overflow-hidden" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full bg-blue-400" style={{ width: `${percent}%` }} />
        </div>
      )}
      {/* What went wrong is said on the line's own card, open or not. */}
      {openError && <div className="px-1 pb-0.5 text-[var(--ada-fail)] break-words">{openError}</div>}
      {!expanded && run.status === "error" && run.error && (
        <div className="px-1 pb-0.5 text-[var(--ada-fail)] truncate" title={run.error}>
          {run.error}
        </div>
      )}
      {expanded && (
        <div className="flex flex-col gap-1 px-1 pb-1 pt-0.5 border-t border-[var(--ada-panel-border)]" data-run-details>
          <div className="text-[var(--ada-panel-text)]">
            <span className={STATUS_COLOR[tone] ?? "text-[var(--ada-panel-text)]"}>{status}</span>
            {outcome && <span> · {outcome}</span>}
          </div>
          <div className="text-[var(--ada-panel-text-muted)]">
            {provider?.title ?? run.provider.id} · {run.provider.engine}
            {run.provider.version ? ` ${run.provider.version}` : ""}
          </div>
          {subjectLabel && (
            <div className="text-[var(--ada-panel-text-muted)]">
              On {subjectLabel} <span className="text-[var(--ada-panel-text-muted)]">· {run.subject?.kind}</span>
            </div>
          )}
          <div className="text-[var(--ada-panel-text-muted)]">
            {new Date(run.created_at).toLocaleString()}
            {took && ` · ${took}`}
            {run.started_by?.display && ` · ${run.started_by.display}`}
          </div>
          {run.error && <div className="text-[var(--ada-fail)] break-words">{run.error}</div>}
          <ArtefactList scope={scope} artefacts={run.artefacts} />
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              className={`${BUTTON} text-[var(--ada-fail)]`}
              disabled={deleting}
              data-run-delete
              onClick={() => {
                setDeleting(true);
                void confirmAndDeleteRuns(scope, [run], select.report).finally(() => setDeleting(false));
              }}
            >
              {deleting ? "Deleting…" : "Delete run"}
            </button>
          </div>
        </div>
      )}
    </li>
  );
};

const StartForm: React.FC<{ scope: string; provider: SimulationProviderInfo; subject: SimulationSubject | null }> = ({
  scope,
  provider,
  subject,
}) => {
  const startRun = useSimulationStore((s) => s.startRun);
  const starting = useSimulationStore((s) => s.starting);
  const startError = useSimulationStore((s) => s.startError);
  const contextFor = usePluginContextFor();
  const [parameters, setParameters] = useState<Record<string, unknown>>(() => defaultParameters(provider.parameters_schema));
  const [inputs, setInputs] = useState<string[]>([]);
  const [kind, setKind] = useState<string>(provider.kinds[0]?.id ?? "");

  // A different provider starts from its own defaults.
  useEffect(() => {
    setParameters(defaultParameters(provider.parameters_schema));
    setInputs([]);
    setKind(provider.kinds[0]?.id ?? "");
  }, [provider.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const custom = resolveSimulationProviderUi(contextFor(""), provider).parameters;
  const missing = missingRequired(provider.parameters_schema, parameters);
  // A subject is chosen and this provider cannot take its kind: not startable, and says why.
  const cannotTake = subject !== null && !providerAccepts(provider, subject.kind);

  return (
    <div className="flex flex-col gap-1.5 p-1 bg-[var(--ada-panel-surface)] rounded-sm">
      <div className="text-[var(--ada-panel-text)]">
        {provider.title} <span className="text-[var(--ada-panel-text-muted)]">· {provider.engine}{provider.version ? ` ${provider.version}` : ""}</span>
      </div>
      {provider.description && <div className="text-[var(--ada-panel-text-muted)]">{provider.description}</div>}
      {provider.kinds.length > 1 && (
        <label className="flex items-center gap-2">
          <span className="text-[var(--ada-panel-text)] w-28 shrink-0">Kind of run</span>
          <select
            className="text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 flex-1 min-w-0"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            {provider.kinds.map((k) => (
              <option key={k.id} value={k.id} title={k.description}>
                {k.title}
              </option>
            ))}
          </select>
        </label>
      )}
      {custom?.ui.renderParameters ? (
        // A plugin's section, in its own boundary: a form that throws leaves the panel standing.
        <ErrorBoundary label={`Simulation form ${custom.ui.id}`}>
          {custom.ui.renderParameters(contextFor(custom.pluginId), {
            provider,
            scope,
            value: parameters,
            onChange: setParameters,
            inputs,
            onInputsChange: setInputs,
            subject: formSubject(provider, subject),
          })}
        </ErrorBoundary>
      ) : (
        <SchemaForm schema={provider.parameters_schema} value={parameters} onChange={setParameters} disabled={starting} />
      )}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={BUTTON}
          disabled={starting || !provider.online || missing.length > 0 || cannotTake}
          title={
            !provider.online
              ? "No worker serving this provider is online"
              : cannotTake
                ? `${provider.title} ${doesNotTake(subject!.kind)}`
                : missing.length > 0
                  ? `Fill in: ${missing.join(", ")}`
                  : undefined
          }
          onClick={() =>
            void startRun(scope, {
              provider: provider.id,
              parameters,
              inputs,
              ...(kind ? { kind } : {}),
              ...(subject ? { subject: { kind: subject.kind, key: subject.key } } : {}),
            })
          }
        >
          {starting ? "Starting…" : subject && !cannotTake ? `Start on ${subject.label}` : "Start"}
        </button>
        {!provider.online && (
          <span className="text-[var(--ada-warn)]">offline{provider.unavailable_reason ? `: ${provider.unavailable_reason}` : ""}</span>
        )}
        {cannotTake && <span className="text-[var(--ada-warn)]">{doesNotTake(subject!.kind)}</span>}
      </div>
      {startError && <div className="text-[var(--ada-fail)] break-words">{startError}</div>}
    </div>
  );
};

/** A batch's line: what it is, how many runs in which status, how far along; it opens to its runs,
 *  indented, each the same line as a run of no batch (Replay / open and Cancel included), a page at
 *  a time, the cancelled ones behind a toggle. Closed until opened; then as it was left, for the
 *  session. */
export const BatchRow: React.FC<{
  scope: string;
  batch: SimulationBatch;
  providers: readonly SimulationProviderInfo[];
  expandedRun: string | null;
  onToggleRun: (runId: string) => void;
  /** Called when the line is opened or closed (which runs are in view changed). */
  onOpenChange?: () => void;
}> = ({ scope, batch, providers, expandedRun, onToggleRun, onOpenChange }) => {
  const [open, setOpen] = useState(() => isBatchOpen(scope, batch.id));
  const [deleteOpen, setDeleteOpen] = useState(false);
  const page = useSimulationStore((s) => s.batchRuns[batch.id]);
  const loadBatchRuns = useSimulationStore((s) => s.loadBatchRuns);
  // Read when opened -- also when it was left open and the panel comes back -- and not before.
  useEffect(() => {
    if (open && !page) void loadBatchRuns(scope, batch.id);
  }, [open, page, scope, batch.id, loadBatchRuns]);

  const toggle = () => {
    setBatchOpen(scope, batch.id, !open);
    setOpen(!open);
    onOpenChange?.();
  };
  const provider = providers.find((p) => p.id === batch.provider.id) ?? null;
  const providerTitle = batch.providers.length > 1 ? `${batch.providers.length} providers` : provider?.title ?? batch.provider.id;
  const tone = batchTone(batch);
  const percent = batchPercent(batch);
  const cancelledShown = page?.cancelled ?? false;
  const cancelledCount = batch.counts?.cancelled ?? 0;
  const shown = page?.runs ?? [];
  const remaining = Math.max(0, (cancelledShown ? batch.count : batch.count - cancelledCount) - shown.length);
  const counts = batchCountsLabel(batch);

  return (
    <li className="flex flex-col rounded-sm bg-[var(--ada-panel-surface)]" data-batch-id={batch.id}>
      <button
        type="button"
        className="flex items-center gap-1.5 px-1 py-0.5 min-w-0 text-left hover:text-[var(--ada-panel-text)]"
        aria-expanded={open}
        title={`${batch.label}: ${batch.count} run${batch.count === 1 ? "" : "s"}${counts ? ` (${counts})` : ""}`}
        data-batch-toggle
        onClick={toggle}
      >
        <span aria-hidden="true" className="w-3 shrink-0 text-[var(--ada-panel-text-muted)]">{open ? "▾" : "▸"}</span>
        <span className={`h-2 w-2 rounded-full shrink-0 ${STATUS_DOT[tone] ?? "bg-gray-400"}`} aria-hidden />
        <span className="font-medium truncate">{batch.label}</span>
        <span className="text-[var(--ada-panel-text-muted)] shrink-0" data-batch-count>
          {batch.count} run{batch.count === 1 ? "" : "s"}
        </span>
        <span className="text-[var(--ada-panel-text-muted)] truncate min-w-0">{providerTitle}</span>
        <span className="flex-1" />
        <span className="text-[var(--ada-panel-text-muted)] shrink-0">{batch.newest_at ? startedLabel(batch.newest_at) : ""}</span>
      </button>
      <div className="flex items-center gap-1.5 px-1 pb-0.5 pl-5 min-w-0 text-[var(--ada-panel-text-muted)]" data-batch-counts>
        <span className="truncate">{counts || "no runs"}</span>
        <span className="flex-1" />
        <span className="shrink-0">{percent}%</span>
      </div>
      {batch.active > 0 && (
        <div className="h-0.5 bg-[var(--ada-panel-surface)] overflow-hidden" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full bg-blue-400" style={{ width: `${percent}%` }} />
        </div>
      )}
      {deleteOpen && <DeleteBatchDialog scope={scope} batch={batch} onClose={() => setDeleteOpen(false)} />}
      {open && (
        <div className="flex flex-col gap-0.5 ml-3 pl-1.5 pb-1 border-l border-[var(--ada-panel-border)]" data-batch-runs>
          <div className="flex items-center gap-1.5">
            <span className="flex-1" />
            <button
              type="button"
              className={`${BUTTON} text-[var(--ada-fail)]`}
              title="Delete this batch: keep its runs, or delete them too"
              data-batch-delete
              onClick={() => setDeleteOpen(true)}
            >
              Delete batch…
            </button>
          </div>
          {cancelledCount > 0 && (
            <label className="flex items-center gap-1.5 text-[var(--ada-panel-text-muted)]">
              <input
                type="checkbox"
                checked={cancelledShown}
                disabled={page?.loading}
                data-batch-show-cancelled
                onChange={() => void loadBatchRuns(scope, batch.id, { cancelled: !cancelledShown })}
              />
              Show cancelled ({cancelledCount})
            </label>
          )}
          {page?.error && <div className="text-[var(--ada-fail)] break-words">{page.error}</div>}
          {shown.length > 0 && (
            <ul className="flex flex-col gap-0.5">
              {shown.map((run) => (
                <RunRow
                  key={run.run_id}
                  scope={scope}
                  run={run}
                  provider={providers.find((p) => p.id === run.provider.id) ?? null}
                  expanded={run.run_id === expandedRun}
                  onToggle={() => onToggleRun(run.run_id)}
                />
              ))}
            </ul>
          )}
          {page?.loading ? (
            <div className="text-[var(--ada-panel-text-muted)]">Loading…</div>
          ) : page && shown.length === 0 && !page.error ? (
            <div className="text-[var(--ada-panel-text-muted)]">{cancelledCount > 0 && !cancelledShown ? "Every run of it was cancelled." : "No runs to show."}</div>
          ) : null}
          {page?.next && !page.loading && (
            <button
              type="button"
              className={`${BUTTON} self-start`}
              data-batch-more
              onClick={() => void loadBatchRuns(scope, batch.id, { more: true })}
            >
              Show more{remaining > 0 ? ` (${remaining})` : ""}
            </button>
          )}
        </div>
      )}
    </li>
  );
};

/** The panel's content, without a frame: providers by domain, the start form, the scope's runs.
 *  `initialRunsOpen` opens the runs list from the start (it is folded otherwise). */
export const SimulationsBody: React.FC<{ initialRunsOpen?: boolean }> = ({ initialRunsOpen = false }) => {
  const domains = useSimulationStore((s) => s.domains);
  const providers = useSimulationStore((s) => s.providers);
  const providersLoaded = useSimulationStore((s) => s.providersLoaded);
  const providersError = useSimulationStore((s) => s.providersError);
  const runs = useSimulationStore((s) => s.runs);
  const batches = useSimulationStore((s) => s.batches);
  const runsError = useSimulationStore((s) => s.runsError);
  const loadProviders = useSimulationStore((s) => s.loadProviders);
  const loadRuns = useSimulationStore((s) => s.loadRuns);
  const currentScope = scopeUrlPart(useScopeStore((s) => s.current));
  const offered = useSimulationSubjectStore((s) => s.offered);
  const withdraw = useSimulationSubjectStore((s) => s.withdraw);
  // The offer chosen in the picker; null until one is, which reads as the newest offer.
  const [chosenId, setChosenId] = useState<string | null>(null);
  const subject = chosenSubject(offered, chosenId);
  // A run on a subject belongs to the scope its file is in; the runs listed are that scope's.
  const scope = subject?.scope ?? currentScope;

  const [domainFilter, setDomainFilter] = useState<string>("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The run whose line is open; one at a time, so the list stays a list.
  const [expandedRun, setExpandedRun] = useState<string | null>(null);

  // Opening the panel (or changing scope) brings both lists up to date; the store then follows
  // unfinished runs on its own.
  useEffect(() => {
    void loadProviders();
    void loadRuns(scope);
  }, [scope, loadProviders, loadRuns]);

  const [refreshing, setRefreshing] = useState(false);
  // The runs list starts folded (see the heading below).
  const [runsOpen, setRunsOpen] = useState(initialRunsOpen);
  const refresh = async () => {
    setRefreshing(true);
    try {
      // Either failing says so in its own place (providersError, runsError); neither throws.
      await Promise.all([loadProviders(), loadRuns(scope)]);
    } finally {
      setRefreshing(false);
    }
  };

  // The active mode may narrow the lists to its domain (`ModeBehaviour.simulations`), with the
  // providers that declare the mode first; none is hidden for declaring no mode.
  useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  const mode = activeModeDef();
  const modeFilter = simulationsFilterFor(mode);
  const baseCtx = usePluginContextFor()("");
  const modeOf = (p: { id: string; domain: string }) => resolveSimulationProviderUi(baseCtx, p).mode;
  const modeProviders = modeFilter
    ? modeProvidersFirst(providers.filter((p) => providerShownInMode(p, mode)), mode, modeOf)
    : providers;
  const modeRuns = modeFilter ? runs.filter((r) => providerShownInMode(r.provider, mode)) : runs;
  // Nothing declares this mode yet: the mode says so (`simulations.empty`), over whatever else is listed.
  const modeEmpty = modeFilter && providersLoaded && !modeHasOwnProvider(modeProviders, mode, modeOf) ? modeFilter.empty : undefined;
  const modeBatches = modeFilter ? batches.filter((b) => providerShownInMode(b.provider, mode)) : batches;

  const groups = useMemo(() => providersByDomain(domains, modeProviders), [domains, modeProviders]);
  const shownGroups = domainFilter ? groups.filter((g) => g.domain.id === domainFilter) : groups;
  const shownRuns = domainFilter ? modeRuns.filter((r) => r.provider.domain === domainFilter) : modeRuns;
  const shownBatches = domainFilter ? modeBatches.filter((b) => b.provider.domain === domainFilter) : modeBatches;
  const items = useMemo(() => runListItems(shownRuns, shownBatches), [shownRuns, shownBatches]);
  const runCount = listedRunCount(shownRuns, shownBatches);
  const toggleRun = (runId: string) => setExpandedRun(runId === expandedRun ? null : runId);
  const selected = providers.find((p) => p.id === selectedId) ?? null;

  // ── picking many runs ──
  const batchRuns = useSimulationStore((s) => s.batchRuns);
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<RunSelection>(EMPTY_SELECTION);
  const [moveOpen, setMoveOpen] = useState(false);
  const [notice, setNotice] = useState<{ text: string; failed: boolean } | null>(null);
  // Bumped when a batch's line is opened or closed: which runs are in view changed.
  const [, setOpenTick] = useState(0);
  // The runs the list shows, in its order: those of no batch, and those of the open batches.
  const viewOrder = items.flatMap((item) =>
    item.kind === "run"
      ? [item.run.run_id]
      : isBatchOpen(scope, item.batch.id)
        ? (batchRuns[item.batch.id]?.runs ?? []).map((r) => r.run_id)
        : [],
  );
  const known = new Map<string, SimulationRun>();
  for (const r of runs) known.set(r.run_id, r);
  for (const page of Object.values(batchRuns)) for (const r of page.runs) known.set(r.run_id, r);
  const pickedRuns = [...picked.ids].flatMap((id) => (known.has(id) ? [known.get(id)!] : []));
  const allInView = viewOrder.length > 0 && viewOrder.every((id) => picked.ids.has(id));
  const stopSelecting = () => {
    setSelecting(false);
    setPicked(EMPTY_SELECTION);
  };
  // A scope of its own starts with nothing picked.
  useEffect(() => {
    setSelecting(false);
    setPicked(EMPTY_SELECTION);
    setNotice(null);
  }, [scope]);
  const selectApi: RunSelectApi = {
    selecting,
    isSelected: (id) => picked.ids.has(id),
    click: (id, shift) => setPicked((s) => clickRun(s, viewOrder, id, shift)),
    longPress: (id) => {
      setSelecting(true);
      setPicked((s) => (s.ids.has(id) ? s : togglePicked(s, id)));
    },
    report: (text, failed = false) => setNotice({ text, failed }),
  };
  // After an action nothing stays picked: what was acted on moved or is gone.
  const afterAction = () => setPicked(EMPTY_SELECTION);

  return (
    <div className="flex flex-col gap-2 text-xs text-[var(--ada-panel-text)] min-w-0">
      {groups.length > 1 && (
        <label className="flex items-center gap-2">
          <span className="text-[var(--ada-panel-text)]">Kind</span>
          <select
            className="text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5"
            value={domainFilter}
            onChange={(e) => setDomainFilter(e.target.value)}
          >
            <option value="">All</option>
            {groups.map((g) => (
              <option key={g.domain.id} value={g.domain.id}>
                {g.domain.title}
              </option>
            ))}
          </select>
        </label>
      )}

      {offered.length > 0 && (
        <div className="flex flex-col gap-0.5" data-subject-picker>
          <div className="flex items-center gap-2">
            <label className="text-[var(--ada-panel-text)] shrink-0" htmlFor="sim-subject">
              Subject
            </label>
            <select
              id="sim-subject"
              className="text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 flex-1 min-w-0"
              value={subject ? subjectId(subject) : NO_SUBJECT}
              onChange={(e) => setChosenId(e.target.value)}
            >
              {offered.map((o) => (
                <option key={subjectId(o)} value={subjectId(o)} title={o.source ? `${o.kind} · ${o.source}` : o.kind}>
                  {o.label}
                </option>
              ))}
              <option value={NO_SUBJECT}>(none)</option>
            </select>
            {subject && (
              <button
                type="button"
                className={BUTTON}
                title="Take this subject off the list (its file stays where it is)"
                aria-label="Withdraw the subject"
                onClick={() => {
                  withdraw(subject.key, subject.scope);
                  setChosenId(null);
                }}
              >
                ×
              </button>
            )}
          </div>
          {subject && (
            <div className="text-[var(--ada-panel-text-muted)]">
              {subject.kind}
              {subject.source ? ` · ${subject.source}` : ""} · providers that take it are listed first
            </div>
          )}
        </div>
      )}

      {providersError && <div className="text-[var(--ada-fail)] break-words">{providersError}</div>}
      {modeEmpty && !providersError && (
        <div className="text-[var(--ada-panel-text-muted)]" data-simulations-mode-empty>
          {modeEmpty}
        </div>
      )}
      {providersLoaded && groups.length === 0 && !providersError && !modeEmpty && (
        <div className="text-[var(--ada-panel-text-muted)]" data-simulations-empty>
          {modeFilter?.domain
            ? "No simulation provider of this kind is registered on this server."
            : "No simulation providers are registered on this server."}
        </div>
      )}
      {shownGroups.map((group) => (
        <div key={group.domain.id} className="flex flex-col gap-0.5">
          <div className="text-[var(--ada-panel-text-muted)] uppercase tracking-wide" title={group.domain.description}>
            {group.domain.title}
          </div>
          {acceptingFirst(group.providers, subject).map((p) => (
            <button
              key={p.id}
              type="button"
              aria-pressed={p.id === selectedId}
              className={`flex items-center gap-2 px-1 py-0.5 rounded-sm text-left ${
                p.id === selectedId ? "bg-[color-mix(in_srgb,var(--ada-panel-text)_20%,transparent)]" : "hover:bg-[var(--ada-panel-surface)]"
              }`}
              onClick={() => setSelectedId(p.id === selectedId ? null : p.id)}
            >
              <span className={`h-2 w-2 rounded-full shrink-0 ${p.online ? "bg-green-400" : "bg-gray-500"}`} />
              <span className="truncate">{p.title}</span>
              <span className="text-[var(--ada-panel-text-muted)] truncate">{p.engine}</span>
              <span className="flex-1" />
              {subject && !providerAccepts(p, subject.kind) && (
                <span className="text-[var(--ada-panel-text-muted)] shrink-0" data-cannot-take>
                  {doesNotTake(subject.kind)}
                </span>
              )}
              {!p.online && <span className="text-[var(--ada-warn)] shrink-0">offline</span>}
            </button>
          ))}
        </div>
      ))}

      {selected && <StartForm scope={scope} provider={selected} subject={subject} />}

      {/* The scope's runs fold under their heading, folded until opened: the start form above is what
          the panel is mostly used for, and a long list of runs pushed it out of view. The count
          says what is there while it is folded; Refresh stays in the heading. */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className="flex flex-1 items-center gap-1.5 text-left"
          aria-expanded={runsOpen}
          aria-controls="simulation-runs"
          data-runs-toggle
          onClick={() => setRunsOpen(!runsOpen)}
        >
          <span aria-hidden="true" className="w-3 text-[var(--ada-panel-text-muted)]">{runsOpen ? "\u25BE" : "\u25B8"}</span>
          <span className="text-[var(--ada-panel-text-muted)] uppercase tracking-wide flex-1">
            Runs{runCount > 0 ? ` (${runCount})` : ""}
          </span>
        </button>
        <button
          type="button"
          className={BUTTON}
          aria-pressed={selecting}
          title={selecting ? "Stop picking runs" : "Pick runs to move or delete (or press and hold a run)"}
          data-runs-select
          onClick={() => {
            if (selecting) stopSelecting();
            else {
              setSelecting(true);
              setRunsOpen(true);
            }
          }}
        >
          {selecting ? "Done" : "Select"}
        </button>
        {/* The store follows the runs it knows are unfinished; a run somebody else started since the
            panel was opened (another tab, the API, a schedule) is only found by asking again. */}
        <button
          type="button"
          className={BUTTON}
          title="Look for new runs, and for providers that have come or gone"
          aria-label="Refresh the runs"
          disabled={refreshing}
          data-runs-refresh
          onClick={() => void refresh()}
        >
          {refreshing ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {runsError && <div className="text-[var(--ada-fail)] break-words">{runsError}</div>}
      {notice && (
        <div
          className={`flex items-start gap-1.5 break-words ${notice.failed ? "text-[var(--ada-warn)]" : "text-[var(--ada-panel-text-muted)]"}`}
          role="status"
          data-runs-notice
        >
          <span className="flex-1 min-w-0">{notice.text}</span>
          <button type="button" className="shrink-0 px-1" aria-label="Dismiss" onClick={() => setNotice(null)}>
            ×
          </button>
        </div>
      )}
      <RunSelectContext.Provider value={selectApi}>
      {moveOpen && (
        <MoveToBatchDialog
          scope={scope}
          runs={pickedRuns}
          batches={batches}
          onClose={() => setMoveOpen(false)}
          onMoved={afterAction}
        />
      )}
      {!runsOpen ? null : items.length === 0 ? (
        <div className="text-[var(--ada-panel-text-muted)]">No runs in this scope yet.</div>
      ) : (
        // The list scrolls within itself: many runs do not push the panel off the screen. On a phone
        // the whole sheet scrolls instead (one scroller, not one inside another). A batch is one line
        // that opens to its runs (`BatchRow`); a run of no batch is a line of its own.
        <ul id="simulation-runs" className="flex flex-col gap-0.5 sm:max-h-[45vh] sm:overflow-y-auto overscroll-contain pr-0.5" data-runs-list>
          {items.map((item) =>
            item.kind === "batch" ? (
              <BatchRow
                key={item.key}
                scope={scope}
                batch={item.batch}
                providers={providers}
                expandedRun={expandedRun}
                onToggleRun={toggleRun}
                onOpenChange={() => setOpenTick((n) => n + 1)}
              />
            ) : (
              <RunRow
                key={item.key}
                scope={scope}
                run={item.run}
                provider={providers.find((p) => p.id === item.run.provider.id) ?? null}
                expanded={item.run.run_id === expandedRun}
                onToggle={() => toggleRun(item.run.run_id)}
              />
            ),
          )}
        </ul>
      )}
      {selecting && runsOpen && (
        <SelectionBar
          scope={scope}
          selected={pickedRuns}
          count={picked.ids.size}
          viewCount={viewOrder.length}
          allInView={allInView}
          onSelectAll={() => setPicked((s) => selectAllInView(s, viewOrder))}
          onDone={stopSelecting}
          onMove={() => setMoveOpen(true)}
          onActed={afterAction}
        />
      )}
      </RunSelectContext.Provider>
    </div>
  );
};

/** The panel in its classic frame (a bottom sheet on phones): shown while its flag is set. */
const SimulationsPanel: React.FC = () => {
  const open = useSimulationStore((s) => s.panelOpen);
  const setPanelOpen = useSimulationStore((s) => s.setPanelOpen);
  const hosted = usePanelHeaderActions().length > 0;
  if (!open) return null;
  return (
    <SimulationsFrame
      onDismiss={() => setPanelOpen(false)}
      header={
        <>
          <span className="font-bold">{SIMULATIONS_PANEL_TITLE}</span>
          {hosted && <PanelHeaderActions />}
        </>
      }
    >
      <SimulationsBody />
    </SimulationsFrame>
  );
};

export default SimulationsPanel;
