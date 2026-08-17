// Core plugin registry — the frontend twin of the backend's slug-keyed
// engine catalogs (`ada/topo_model/engine_catalog.py` et al). Core adapy carries
// NO hardcoded knowledge of any feature plugin: a plugin self-describes what UI
// slots it fills via `registerPlugin({...})` at module import, core snapshots the
// registry and iterates it, and it never names a plugin by id.
//
// This module is deliberately free of heavy runtime imports (no three, no
// zustand, no React runtime) so it can be unit-tested under plain `node --test`
// + tsx. All React / store / scene types are pulled in `import type` only, so
// they are erased at transpile and add no runtime dependency. The concrete
// runtime context (stores + refs + a SceneHandle) is assembled by core UI in
// `@/plugins/context`, not here.

import type React from "react";
import type { AdaViewerStores } from "@/state/AdaViewerContext";

// Bumped on a breaking change to any slot interface below. A plugin declares the
// core range it was built against via `coreApiRange`; an out-of-range plugin is
// skipped with a visible log rather than loaded half-way (Decision 5 / lifecycle).
export const PLUGIN_API_VERSION = "1.0.0";

// The named mount regions core exposes in Phase 1. Deliberately small
// (`fem-sidebar` covers the FEM simulation panel, `top-panel` the menu bar);
// more are added on demand rather than up-front.
export type PluginRegion =
  | "fem-sidebar"
  | "top-panel"
  | "scene-info"
  | "storage-detail";

export type PluginLogLevel = "debug" | "info" | "warn" | "error";

/** Values a color-field provider returns for core to paint + legend. */
export interface SceneColorFieldResult {
  // Per-entity scalar values. A typed array (index-aligned to the mesh) or a
  // Map keyed by entity id — the same two shapes the FEA paint path accepts.
  values: Float32Array | Map<string, number>;
  range: [number, number];
  // Optional explicit colour mapper; when omitted core uses the active colormap.
  colorFor?: (v: number) => [number, number, number];
}

/** A manifest-relative byte/JSON fetcher handed to sidecar loaders. */
export interface SidecarFetcher {
  json: (relKey: string) => Promise<unknown>;
  bytes: (relKey: string, range?: { start: number; end: number }) => Promise<ArrayBuffer>;
  url: (relKey: string) => string;
}

/** Owned-object scene handle: every plugin Object3D is added through this so
 * core's helper-exclusion (zoomToAll), isolate, and model-clear can treat plugin
 * resources uniformly by owner. Kept structural (`unknown` object type) so this
 * module needs no `three` import. */
export interface SceneHandle {
  add: (owner: string, obj: unknown) => void;
  remove: (owner: string, obj?: unknown) => void;
  requestRender: () => void;
  // Route a named color field through core's paint + legend path
  // (applyField / colorLegendStore). Core owns the single active-field arbiter.
  paintField: (fieldId: string, result: SceneColorFieldResult) => void;
  // The active FEA mesh (a custom-batch mesh with per-element ``drawRanges``), or
  // null when no FEA model is loaded. Lets a result plugin drive element-level
  // scene ops (isolate / highlight / attach overlays) off the same mesh core
  // deforms. Structural ``unknown`` — no three import in this dependency-free core.
  getActiveFeaMesh: () => unknown | null;
}

/** Namespaced REST client helper — plugin routes live under `/api/plugins/{id}`. */
export interface PluginApiClient {
  base: string;
  /** Absolute base for this (or another) plugin's routes: `${base}/plugins/{id}`. */
  plugin: (id?: string) => string;
}

/** The single context object every slot callback receives. */
export interface AdaPluginContext {
  pluginId: string;
  api: PluginApiClient;
  stores: AdaViewerStores;
  scene: SceneHandle;
  scope: () => string;
  log: (level: PluginLogLevel, msg: string, ...args: unknown[]) => void;
}

export type ActivationPredicate = (ctx: AdaPluginContext) => boolean;

export interface TopBarButtonSpec {
  icon: React.FC;
  label: string;
  // If set, the button toggles this store-backed panel; core uses it purely as a
  // stable identity for aria + active state.
  ariaKeepsStore?: string;
  onClick?: (ctx: AdaPluginContext) => void;
}

export interface PanelSlot {
  // Local id; namespaced to `${pluginId}:${id}` on register.
  id: string;
  region: PluginRegion;
  order?: number;
  activationPredicate?: ActivationPredicate;
  render: (ctx: AdaPluginContext) => React.ReactNode;
  topBarButton?: TopBarButtonSpec;
}

export interface SceneColorFieldProvider {
  id: string; // namespaced on register
  label: string;
  supports: "element" | "node";
  available?: ActivationPredicate;
  resolve: (
    ctx: AdaPluginContext,
    opts: { field: string; caseId?: string },
  ) => Promise<SceneColorFieldResult>;
}

export interface ResultSidecarLoader {
  id: string; // namespaced on register
  detect: (manifest: unknown) => boolean;
  // Runs AFTER core geometry is visible; returns a disposer that core calls on
  // model-clear so the loader's scene objects / store state are torn down.
  load: (
    ctx: AdaPluginContext,
    args: { manifest: unknown; fetcher: SidecarFetcher; scope: string; sourceName?: string },
  ) => Promise<() => void>;
}

export interface UrlParamHandler {
  params: string[];
  // Return true when the plugin consumed the params (core stops offering them).
  handle: (ctx: AdaPluginContext, values: Record<string, string>) => Promise<boolean>;
}

export interface PluginSpec {
  id: string;
  version?: string;
  coreApiRange?: string;
  schemaVersion?: number;
  apiNamespace?: string;
  activationPredicate?: ActivationPredicate;
  panels?: PanelSlot[];
  topBarButtons?: PanelSlot[]; // panels whose only purpose is a top-bar button
  sceneColorFields?: SceneColorFieldProvider[];
  resultSidecarLoaders?: ResultSidecarLoader[];
  urlParamHandlers?: UrlParamHandler[];
}

export interface RegisteredPlugin {
  id: string;
  version: string;
  coreApiRange?: string;
  schemaVersion?: number;
  apiNamespace: string;
  activationPredicate?: ActivationPredicate;
  panels: PanelSlot[];
  sceneColorFields: SceneColorFieldProvider[];
  resultSidecarLoaders: ResultSidecarLoader[];
  urlParamHandlers: UrlParamHandler[];
  // Set when a slot callback threw during the session; a disabled plugin
  // contributes no slots until reload (failure isolation, Decision 4).
  disabled?: string;
}

// Slug-keyed, insertion-ordered map — the plain-Map twin of the backend's
// dict registries. Duplicate ids are ignored (first-writer-wins) with a warning.
const _registry = new Map<string, RegisteredPlugin>();

function namespaced(pluginId: string, localId: string): string {
  return localId.startsWith(`${pluginId}:`) ? localId : `${pluginId}:${localId}`;
}

// Minimal semver range check supporting the `">=1.0 <2.0"` grammar the manifest
// uses (space-joined comparator clauses; `*`/empty = any). Enough for core↔plugin
// compat gating without pulling a semver dependency into the bundle.
function versionSatisfies(version: string, range?: string): boolean {
  if (!range || range.trim() === "" || range.trim() === "*") return true;
  const parse = (v: string): number[] =>
    v
      .split(".")
      .map((p) => parseInt(p, 10))
      .map((n) => (Number.isFinite(n) ? n : 0));
  const cmp = (a: number[], b: number[]): number => {
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const d = (a[i] ?? 0) - (b[i] ?? 0);
      if (d !== 0) return d < 0 ? -1 : 1;
    }
    return 0;
  };
  const cur = parse(version);
  for (const clause of range.trim().split(/\s+/)) {
    const m = clause.match(/^(>=|<=|>|<|=)?\s*([0-9][0-9.]*)$/);
    if (!m) return false;
    const op = m[1] ?? "=";
    const c = cmp(cur, parse(m[2]));
    const ok =
      op === ">="
        ? c >= 0
        : op === "<="
          ? c <= 0
          : op === ">"
            ? c > 0
            : op === "<"
              ? c < 0
              : c === 0;
    if (!ok) return false;
  }
  return true;
}

/** Register (or reject a duplicate of) a plugin. Idempotent-by-id: a second
 * registration of the same id is ignored with a warning (mirrors the backend's
 * last/first-writer-wins registries). An out-of-`coreApiRange` plugin is skipped
 * with a visible warning and contributes no slots. */
export function registerPlugin(spec: PluginSpec): void {
  const id = spec.id?.trim();
  if (!id) {
    console.warn("[plugins] registerPlugin called without an id; ignored");
    return;
  }
  if (_registry.has(id)) {
    console.warn(`[plugins] duplicate plugin id "${id}"; ignoring the later registration`);
    return;
  }
  if (!versionSatisfies(PLUGIN_API_VERSION, spec.coreApiRange)) {
    console.warn(
      `[plugins] plugin "${id}" requires core API ${spec.coreApiRange}; ` +
        `this core is ${PLUGIN_API_VERSION} — skipped`,
    );
    return;
  }

  const panels = [...(spec.panels ?? []), ...(spec.topBarButtons ?? [])].map((p) => ({
    ...p,
    id: namespaced(id, p.id),
  }));
  const sceneColorFields = (spec.sceneColorFields ?? []).map((f) => ({
    ...f,
    id: namespaced(id, f.id),
  }));
  const resultSidecarLoaders = (spec.resultSidecarLoaders ?? []).map((l) => ({
    ...l,
    id: namespaced(id, l.id),
  }));
  const urlParamHandlers = spec.urlParamHandlers ?? [];

  _registry.set(id, {
    id,
    version: spec.version ?? "0.0.0",
    coreApiRange: spec.coreApiRange,
    schemaVersion: spec.schemaVersion,
    apiNamespace: spec.apiNamespace ?? id,
    activationPredicate: spec.activationPredicate,
    panels,
    sceneColorFields,
    resultSidecarLoaders,
    urlParamHandlers,
  });
}

/** All registered plugins in insertion order (test/introspection helper). */
export function getRegisteredPlugins(): RegisteredPlugin[] {
  return [..._registry.values()];
}

export function getPlugin(id: string): RegisteredPlugin | undefined {
  return _registry.get(id);
}

/** Clear the registry — for unit tests only. */
export function resetRegistry(): void {
  _registry.clear();
}

/** Mark a plugin disabled for the session (called by core on a slot throw). */
export function disablePlugin(id: string, reason: string): void {
  const p = _registry.get(id);
  if (p && !p.disabled) {
    p.disabled = reason;
    console.error(`[plugins] plugin "${id}" disabled for this session: ${reason}`);
  }
}

function isActive(p: RegisteredPlugin, ctx: AdaPluginContext): boolean {
  if (p.disabled) return false;
  if (!p.activationPredicate) return true;
  try {
    return !!p.activationPredicate({ ...ctx, pluginId: p.id });
  } catch (err) {
    disablePlugin(p.id, `activationPredicate threw: ${String(err)}`);
    return false;
  }
}

/** Panels for a region, deterministically ordered by `(order ?? 0, id)`, filtered
 * by whole-plugin + per-slot activation predicates against the given ctx. Each
 * entry carries its owning `pluginId` so core can build a per-plugin ctx + wrap
 * the render in an ErrorBoundary keyed on the plugin. */
export function getPanelsForRegion(
  region: PluginRegion,
  ctx: AdaPluginContext,
): Array<{ pluginId: string; panel: PanelSlot }> {
  const out: Array<{ pluginId: string; panel: PanelSlot; order: number }> = [];
  for (const p of _registry.values()) {
    const pctx: AdaPluginContext = { ...ctx, pluginId: p.id };
    if (!isActive(p, pctx)) continue;
    for (const panel of p.panels) {
      if (panel.region !== region) continue;
      if (panel.activationPredicate) {
        try {
          if (!panel.activationPredicate(pctx)) continue;
        } catch (err) {
          disablePlugin(p.id, `panel "${panel.id}" activationPredicate threw: ${String(err)}`);
          break;
        }
      }
      out.push({ pluginId: p.id, panel, order: panel.order ?? 0 });
    }
  }
  out.sort((a, b) => (a.order - b.order) || a.panel.id.localeCompare(b.panel.id));
  return out.map(({ pluginId, panel }) => ({ pluginId, panel }));
}

/** Color-field providers available under the given ctx, ordered by `id`. */
export function getSceneColorFieldProviders(
  ctx: AdaPluginContext,
): Array<{ pluginId: string; provider: SceneColorFieldProvider }> {
  const out: Array<{ pluginId: string; provider: SceneColorFieldProvider }> = [];
  for (const p of _registry.values()) {
    const pctx: AdaPluginContext = { ...ctx, pluginId: p.id };
    if (!isActive(p, pctx)) continue;
    for (const provider of p.sceneColorFields) {
      if (provider.available) {
        try {
          if (!provider.available(pctx)) continue;
        } catch (err) {
          disablePlugin(p.id, `colorField "${provider.id}" available() threw: ${String(err)}`);
          break;
        }
      }
      out.push({ pluginId: p.id, provider });
    }
  }
  out.sort((a, b) => a.provider.id.localeCompare(b.provider.id));
  return out;
}

/** All sidecar loaders across active plugins (order = insertion). */
export function getResultSidecarLoaders(
  ctx: AdaPluginContext,
): Array<{ pluginId: string; loader: ResultSidecarLoader }> {
  const out: Array<{ pluginId: string; loader: ResultSidecarLoader }> = [];
  for (const p of _registry.values()) {
    const pctx: AdaPluginContext = { ...ctx, pluginId: p.id };
    if (!isActive(p, pctx)) continue;
    for (const loader of p.resultSidecarLoaders) out.push({ pluginId: p.id, loader });
  }
  return out;
}

/** All url-param handlers across active plugins (order = insertion). */
export function getUrlParamHandlers(
  ctx: AdaPluginContext,
): Array<{ pluginId: string; handler: UrlParamHandler }> {
  const out: Array<{ pluginId: string; handler: UrlParamHandler }> = [];
  for (const p of _registry.values()) {
    const pctx: AdaPluginContext = { ...ctx, pluginId: p.id };
    if (!isActive(p, pctx)) continue;
    for (const handler of p.urlParamHandlers) out.push({ pluginId: p.id, handler });
  }
  return out;
}

// Re-export the range checker so tooling / tests can reuse the exact grammar.
export { versionSatisfies as _versionSatisfies };
