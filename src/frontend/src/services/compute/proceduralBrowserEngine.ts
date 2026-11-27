// How the browser (Pyodide) compiles a procedural model for the selected engine,
// decided before anything runs -- pure, so the routing can be tested without Pyodide.
//
// The engines the builder lists come from the server: the built-in ones, engines
// registered in the database (resolved through /procedural-engines/{id}/resolve:
// a wheel the browser installs, or server-only), and engines a worker advertises
// (`origin: "worker"`, id `worker:<slug>`). A worker-advertised engine used to be
// server-only by definition. It is browser-capable when the viewer bakes a plugin
// wheel under the engine's slug in /wheels/plugin-wheels-manifest.json: installing
// that plugin and running its register hook announces the engine in the browser's
// own adapy, so the compile dispatches by slug there exactly as on the worker.

import type {PluginWheelsManifest} from "@/utils/pyodide/pluginWheels";

export type ProceduralLod = "sim" | "detail";

/** The engine as the builder lists it (a subset of ProceduralEngineSummary). */
export interface ListedEngine {
  id: string;
  slug: string;
  origin?: "builtin" | "db" | "local" | "worker";
}

export type BrowserEngineRoute =
  /** The built-in default engine (ada.topo_model.wasm_compile, engine null). */
  | {kind: "default"}
  /** Another built-in engine, dispatched by slug in the browser's adapy. */
  | {kind: "builtin"; engine: string}
  /** An engine a baked plugin wheel registers: install that plugin, then dispatch by slug. */
  | {kind: "baked"; engine: string}
  /** A database-registered engine: the server says whether its wheel runs in the browser. */
  | {kind: "resolve"; engineId: string}
  /** No browser path: the build goes to the server worker. */
  | {kind: "server-only"; reason: string};

export interface BrowserEngineInput {
  /** The builder's selected engine slug ("" / "adapy-default" = the default engine). */
  slug: string | null | undefined;
  /** The engines the builder lists. */
  engines: readonly ListedEngine[];
  /** /wheels/plugin-wheels-manifest.json ({plugins: {}} when nothing is baked). */
  manifest: PluginWheelsManifest;
  lod: ProceduralLod;
  /** The selected detailing engine ("none" / "" = none). */
  detailing?: string | null;
}

const isDefault = (slug: string | null | undefined): boolean => !slug || slug === "adapy-default";

/** Whether a baked plugin wheel registers the engine `slug`: its manifest entry is
 * keyed by the slug and has a register hook (the only way a wheel announces an
 * engine; an entry with just a job entrypoint is a plugin job, not an engine). */
export function bakedEngineWheel(manifest: PluginWheelsManifest, slug: string | null | undefined): boolean {
  if (isDefault(slug)) return false;
  const plugins = manifest?.plugins ?? {};
  if (!Object.prototype.hasOwnProperty.call(plugins, slug as string)) return false;
  return Boolean(plugins[slug as string]?.register);
}

/** Where the browser compiles this engine, or why it cannot. */
export function browserEngineRoute(input: BrowserEngineInput): BrowserEngineRoute {
  const {slug, engines, manifest, lod} = input;
  const detailing = (input.detailing ?? "").trim();
  const listed = isDefault(slug) ? undefined : engines.find((e) => e.slug === slug);
  const baked = !isDefault(slug) && listed?.origin !== "builtin" && bakedEngineWheel(manifest, slug);

  if (lod === "detail") {
    // The detail LOD builds on the server's detailing stack, except for an engine a
    // baked wheel registers with no detailing engine selected: that engine's own
    // compile is the whole detail build, so it is the same call in the browser.
    if (detailing && detailing !== "none") return {kind: "server-only", reason: "a detailing engine is selected"};
    if (!baked) return {kind: "server-only", reason: "the detail level of detail runs on the server"};
    return {kind: "baked", engine: slug as string};
  }

  if (isDefault(slug)) return {kind: "default"};
  if (baked) return {kind: "baked", engine: slug as string};
  if (!listed || listed.origin === "builtin") return {kind: "builtin", engine: slug as string};
  if (listed.origin === "worker") {
    return {kind: "server-only", reason: "a worker-advertised engine with no baked browser wheel"};
  }
  return {kind: "resolve", engineId: listed.id};
}
