// The simulations panel (`core:simulations`, titled "Robotic Simulations"): what can be simulated
// here, and the runs of the open scope.
//
// Three parts, top to bottom:
//
//   * the providers the server lists, grouped by domain (the kind of simulation), with a filter
//     by domain; one that no live worker serves is shown as offline and cannot be started;
//   * the start form of the chosen provider -- a plugin's parameter section when one is
//     registered for the provider or its domain (`simulationProviders` in the plugin registry),
//     else inputs generated from the provider's JSON Schema;
//   * the scope's runs, newest first, a line each: its status, what it is called, when it was
//     started, a cancel button while it is going, and a plugin's "open" handler when there is
//     one. A line opens to the rest (who started it, how long it took, its outcome, its
//     artefacts as downloads), one at a time. The list scrolls within the panel.
//
// It knows no engine. Everything specific to one is the server's description of the provider or
// a plugin's registration.

import React, { useEffect, useMemo, useState } from "react";

import ErrorBoundary from "@/components/common/ErrorBoundary";
import { PanelHeaderActions, usePanelHeaderActions } from "@/components/ui/PanelFrame";
import { makePluginContext } from "@/plugins/context";
import { openResultButtonLabel, resolveSimulationProviderUi, type AdaPluginContext } from "@/plugins/registry";
import { filesApi } from "@/services/api/files";
import {
  isActiveRun,
  type SimulationArtefact,
  type SimulationProviderInfo,
  type SimulationRun,
} from "@/services/api/simulations";
import { durationLabel, runLabel, runOutcome, startedLabel } from "@/services/simulations/runRow";
import { defaultParameters, missingRequired } from "@/services/simulations/schemaForm";
import { useViewerStores } from "@/state/AdaViewerContext";
import { useRunConsoleStore } from "@/state/runConsoleStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { providersByDomain, useSimulationStore } from "@/state/simulationStore";
import { usePluginTheme } from "@/state/themeStore";

import { SchemaForm } from "./SchemaForm";

/** The panel's name, wherever it is shown. */
export const SIMULATIONS_PANEL_TITLE = "Robotic Simulations";

const BUTTON = "rounded-sm bg-gray-700 px-2 py-0.5 text-white hover:bg-gray-600 disabled:opacity-40 disabled:hover:bg-gray-700";
const STATUS_DOT: Record<string, string> = {
  queued: "bg-gray-400",
  running: "bg-blue-400",
  done: "bg-green-400",
  error: "bg-red-400",
  cancelled: "bg-yellow-400",
};
const STATUS_COLOR: Record<string, string> = {
  queued: "text-gray-300",
  running: "text-blue-300",
  done: "text-green-300",
  error: "text-red-300",
  cancelled: "text-yellow-300",
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
            className="text-blue-300 hover:underline truncate text-left"
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
          <span className="text-gray-400 shrink-0">
            {a.role}
            {a.partial ? " · partial" : ""}
            {a.size != null ? ` · ${sizeLabel(a.size)}` : ""}
          </span>
        </li>
      ))}
      {error && <li className="text-red-300 break-words">{error}</li>}
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
  const contextFor = usePluginContextFor();
  const [openError, setOpenError] = useState<string | null>(null);
  const baseCtx = contextFor("");
  const opener = resolveSimulationProviderUi(baseCtx, run.provider).openResult;
  const active = isActiveRun(run);
  const percent = Math.round(Math.max(0, Math.min(1, run.progress)) * 100);
  const status = run.cancel_requested && active ? "cancelling" : run.status;
  const label = runLabel(run, provider);
  const outcome = runOutcome(run);
  const took = durationLabel(run.started_at ?? run.created_at, run.finished_at);

  const open = async () => {
    if (!opener?.ui.openResult) return;
    setOpenError(null);
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
    <li className={`flex flex-col rounded-sm ${expanded ? "bg-gray-800/70" : "bg-gray-800/40"}`} data-run-id={run.run_id}>
      <div className="flex items-center gap-1.5 px-1 py-0.5 min-w-0">
        {/* The line itself opens and closes the rest; the buttons beside it are their own. */}
        <button
          type="button"
          className="flex items-center gap-1.5 min-w-0 flex-1 text-left hover:text-white"
          aria-expanded={expanded}
          title={`${label}: ${status}`}
          onClick={onToggle}
        >
          <span className={`h-2 w-2 rounded-full shrink-0 ${STATUS_DOT[run.status] ?? "bg-gray-400"}`} aria-hidden />
          <span className="font-medium truncate">{label}</span>
          <span className="text-gray-400 shrink-0">{active ? run.stage || status : startedLabel(run.created_at)}</span>
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
        <div className="h-0.5 bg-gray-700 overflow-hidden" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full bg-blue-400" style={{ width: `${percent}%` }} />
        </div>
      )}
      {/* What went wrong is said on the line's own card, open or not. */}
      {openError && <div className="px-1 pb-0.5 text-red-300 break-words">{openError}</div>}
      {!expanded && run.status === "error" && run.error && (
        <div className="px-1 pb-0.5 text-red-300 truncate" title={run.error}>
          {run.error}
        </div>
      )}
      {expanded && (
        <div className="flex flex-col gap-1 px-1 pb-1 pt-0.5 border-t border-gray-700/60" data-run-details>
          <div className="text-gray-300">
            <span className={STATUS_COLOR[run.status] ?? "text-gray-300"}>{status}</span>
            {outcome && <span> · {outcome}</span>}
          </div>
          <div className="text-gray-400">
            {provider?.title ?? run.provider.id} · {run.provider.engine}
            {run.provider.version ? ` ${run.provider.version}` : ""}
          </div>
          <div className="text-gray-400">
            {new Date(run.created_at).toLocaleString()}
            {took && ` · ${took}`}
            {run.started_by?.display && ` · ${run.started_by.display}`}
          </div>
          {run.error && <div className="text-red-300 break-words">{run.error}</div>}
          <ArtefactList scope={scope} artefacts={run.artefacts} />
        </div>
      )}
    </li>
  );
};

const StartForm: React.FC<{ scope: string; provider: SimulationProviderInfo }> = ({ scope, provider }) => {
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

  return (
    <div className="flex flex-col gap-1.5 p-1 bg-gray-800/40 rounded-sm">
      <div className="text-gray-300">
        {provider.title} <span className="text-gray-500">· {provider.engine}{provider.version ? ` ${provider.version}` : ""}</span>
      </div>
      {provider.description && <div className="text-gray-400">{provider.description}</div>}
      {provider.kinds.length > 1 && (
        <label className="flex items-center gap-2">
          <span className="text-gray-300 w-28 shrink-0">Kind of run</span>
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
          })}
        </ErrorBoundary>
      ) : (
        <SchemaForm schema={provider.parameters_schema} value={parameters} onChange={setParameters} disabled={starting} />
      )}
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={BUTTON}
          disabled={starting || !provider.online || missing.length > 0}
          title={
            !provider.online
              ? "No worker serving this provider is online"
              : missing.length > 0
                ? `Fill in: ${missing.join(", ")}`
                : undefined
          }
          onClick={() =>
            void startRun(scope, { provider: provider.id, parameters, inputs, ...(kind ? { kind } : {}) })
          }
        >
          {starting ? "Starting…" : "Start"}
        </button>
        {!provider.online && (
          <span className="text-yellow-300">offline{provider.unavailable_reason ? `: ${provider.unavailable_reason}` : ""}</span>
        )}
      </div>
      {startError && <div className="text-red-300 break-words">{startError}</div>}
    </div>
  );
};

/** The panel's content, without a frame: providers by domain, the start form, the scope's runs. */
export const SimulationsBody: React.FC = () => {
  const domains = useSimulationStore((s) => s.domains);
  const providers = useSimulationStore((s) => s.providers);
  const providersLoaded = useSimulationStore((s) => s.providersLoaded);
  const providersError = useSimulationStore((s) => s.providersError);
  const runs = useSimulationStore((s) => s.runs);
  const runsError = useSimulationStore((s) => s.runsError);
  const loadProviders = useSimulationStore((s) => s.loadProviders);
  const loadRuns = useSimulationStore((s) => s.loadRuns);
  const scope = scopeUrlPart(useScopeStore((s) => s.current));

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
  const refresh = async () => {
    setRefreshing(true);
    try {
      // Either failing says so in its own place (providersError, runsError); neither throws.
      await Promise.all([loadProviders(), loadRuns(scope)]);
    } finally {
      setRefreshing(false);
    }
  };

  const groups = useMemo(() => providersByDomain(domains, providers), [domains, providers]);
  const shownGroups = domainFilter ? groups.filter((g) => g.domain.id === domainFilter) : groups;
  const shownRuns = domainFilter ? runs.filter((r) => r.provider.domain === domainFilter) : runs;
  const selected = providers.find((p) => p.id === selectedId) ?? null;

  return (
    <div className="flex flex-col gap-2 text-xs text-white min-w-0">
      {groups.length > 1 && (
        <label className="flex items-center gap-2">
          <span className="text-gray-300">Kind</span>
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

      {providersError && <div className="text-red-300 break-words">{providersError}</div>}
      {providersLoaded && groups.length === 0 && !providersError && (
        <div className="text-gray-400">No simulation providers are registered on this server.</div>
      )}
      {shownGroups.map((group) => (
        <div key={group.domain.id} className="flex flex-col gap-0.5">
          <div className="text-gray-400 uppercase tracking-wide" title={group.domain.description}>
            {group.domain.title}
          </div>
          {group.providers.map((p) => (
            <button
              key={p.id}
              type="button"
              aria-pressed={p.id === selectedId}
              className={`flex items-center gap-2 px-1 py-0.5 rounded-sm text-left ${
                p.id === selectedId ? "bg-gray-600" : "hover:bg-gray-700"
              }`}
              onClick={() => setSelectedId(p.id === selectedId ? null : p.id)}
            >
              <span className={`h-2 w-2 rounded-full shrink-0 ${p.online ? "bg-green-400" : "bg-gray-500"}`} />
              <span className="truncate">{p.title}</span>
              <span className="text-gray-400 truncate">{p.engine}</span>
              <span className="flex-1" />
              {!p.online && <span className="text-yellow-300 shrink-0">offline</span>}
            </button>
          ))}
        </div>
      ))}

      {selected && <StartForm scope={scope} provider={selected} />}

      <div className="flex items-center gap-2">
        <div className="text-gray-400 uppercase tracking-wide flex-1">Runs</div>
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
      {runsError && <div className="text-red-300 break-words">{runsError}</div>}
      {shownRuns.length === 0 ? (
        <div className="text-gray-400">No runs in this scope yet.</div>
      ) : (
        // The list scrolls within itself: many runs do not push the panel off the screen.
        <ul className="flex flex-col gap-0.5 max-h-[45vh] overflow-y-auto overscroll-contain pr-0.5" data-runs-list>
          {shownRuns.map((run) => (
            <RunRow
              key={run.run_id}
              scope={scope}
              run={run}
              provider={providers.find((p) => p.id === run.provider.id) ?? null}
              expanded={run.run_id === expandedRun}
              onToggle={() => setExpandedRun(run.run_id === expandedRun ? null : run.run_id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
};

/** The panel in the classic UI's card: shown while its flag is set. */
const SimulationsPanel: React.FC = () => {
  const open = useSimulationStore((s) => s.panelOpen);
  const hosted = usePanelHeaderActions().length > 0;
  if (!open) return null;
  return (
    <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/70 rounded-md min-w-[300px] max-w-[420px]">
      <div className="flex items-center gap-2">
        <span className="font-bold">{SIMULATIONS_PANEL_TITLE}</span>
        {hosted && <PanelHeaderActions />}
      </div>
      <SimulationsBody />
    </div>
  );
};

export default SimulationsPanel;
