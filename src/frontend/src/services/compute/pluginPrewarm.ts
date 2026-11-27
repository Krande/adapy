// Install the browser wheels (and baked dependency files) of the plugins a user
// can run here, in the background, before their first job.
//
// Which plugins: those in /wheels/plugin-wheels-manifest.json that are enabled
// where the user is -- a registered plugin of that id must be on (not disabled,
// its access gate open for the open scope); an entry with its own `gate` needs
// that gate open; an entry nothing gates is on -- and whose plugin-job execution
// order runs Pyodide before (or without) the worker. Plugins they depend on come
// with them. When: at idle after the page settles, and again whenever the scope's
// plugin access or the execution policy changes; each plugin is sent once per
// worker, and a job that starts first shares the same install.

import { getPlugin } from "@/plugins/registry";
import { isGateOpen, subscribePluginAccess } from "@/plugins/pluginAccess";
import { useExecutionPolicyStore, useExecutionPreferenceStore } from "@/state/executionPolicyStore";
import { loadPluginWheelsManifest, selectPrewarmPlugins, type PluginWheelEntry, type PluginWheelsManifest } from "@/utils/pyodide/pluginWheels";
import { prewarmPyodide } from "@/utils/pyodide/pyodide_converter";

import { engineOrder } from "./executionPolicy";

/** Is a manifest plugin enabled where the user is now? */
export function isManifestPluginEnabled(id: string, entry: PluginWheelEntry): boolean {
  const registered = getPlugin(id);
  if (registered) return !registered.disabled && isGateOpen(registered.accessGate ?? null);
  if (entry.gate) return isGateOpen(entry.gate);
  return true;
}

/** The manifest plugins to pre-install now. */
export function prewarmCandidates(manifest: PluginWheelsManifest): string[] {
  return selectPrewarmPlugins(manifest, {
    isEnabled: isManifestPluginEnabled,
    order: (id) => engineOrder("plugin_job", id),
  });
}

type Idle = (cb: () => void) => void;

const defaultIdle: Idle = (cb) => {
  const ric = (globalThis as { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number })
    .requestIdleCallback;
  if (ric) ric(cb, { timeout: 15_000 });
  else setTimeout(cb, 3_000);
};

/** Start the background plugin prewarm; returns the stop. */
export function startPluginPrewarm(opts: { idle?: Idle; onLog?: (msg: string) => void } = {}): () => void {
  const idle = opts.idle ?? defaultIdle;
  let stopped = false;
  let pending = false;
  const schedule = () => {
    if (stopped || pending) return;
    pending = true;
    idle(() => {
      pending = false;
      if (stopped) return;
      void loadPluginWheelsManifest().then((manifest) => {
        if (stopped) return;
        const ids = prewarmCandidates(manifest);
        if (ids.length > 0) prewarmPyodide(opts.onLog, { plugins: ids });
      });
    });
  };
  const unsubs = [
    subscribePluginAccess(schedule),
    useExecutionPolicyStore.subscribe(schedule),
    useExecutionPreferenceStore.subscribe(schedule),
  ];
  schedule();
  return () => {
    stopped = true;
    for (const u of unsubs) u();
  };
}
