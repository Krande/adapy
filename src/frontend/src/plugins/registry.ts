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
import { registerUiShell, type UiShellSpec } from "./uiShells";

// Bumped on a breaking change to any slot interface below. A plugin declares the
// core range it was built against via `coreApiRange`; an out-of-range plugin is
// skipped with a visible log rather than loaded half-way (Decision 5 / lifecycle).
//
// Also bumped on an ADDITIVE capability a plugin can DEPEND on — which 1.1.0 and
// 1.2.0 both are. Without a version to name in `coreApiRange`, a plugin built
// against the newer core fails on an older one at an undefined import, from
// inside the plugin, with nothing naming the mismatch. A minor bump turns that
// into the registry's own "built against x, this core is y — skipped" line.
//
//   1.1.0  placement (`dock` / `modes`) — see the section below
//   1.2.0  browser-side external-model providers
//          (`registerExternalModelClient`, @/services/externalModels)
export const PLUGIN_API_VERSION = "1.2.0";

// The named mount regions core exposes in Phase 1. Deliberately small
// (`fem-sidebar` covers the FEM simulation panel, `top-panel` the menu bar,
// `admin` the /admin page's tab strip); more are added on demand rather than
// up-front.
export type PluginRegion =
  | "fem-sidebar"
  | "top-panel"
  | "scene-info"
  | "storage-detail"
  | "admin";

// ---------------------------------------------------------------------------
// Placement (1.1.0)
//
// A region names a HOST. It says which of core's containers mounts the panel,
// which is all a panel needed while the UI was one fixed layout. A UI shell with
// docks and modes needs the other half of the question — WHERE in its chrome, and
// WHEN — and there was no way for a plugin to say it. A shell had to guess, which
// in practice meant plugin panels piled into whichever single host the shell
// happened to mount.
//
// These two fields are that answer, and they are deliberately thin: core does not
// own docks or modes, it only carries the words between a plugin and the shell
// that has them. A shell with no docks ignores `dock` and mounts by region as
// before; core itself does exactly that.
// ---------------------------------------------------------------------------

/** Named regions of a shell's chrome. A shell that has no such thing ignores it.
 *
 * `right-aux` is a SECOND right-hand column, beside the first rather than tabbed
 * into it. A panel you read WHILE reading another one — a detail view for the row
 * selected in a table — belongs there: sharing one dock means the two take turns,
 * and stacking them means scrolling between them. `right-aux2` is a third such
 * column, for the case where reading one panel means comparing it against two
 * others at once. A shell with fewer right columns than a plugin asks for can
 * treat the extras as `right`. */
export type PluginDockId =
  | "left"
  | "right"
  | "right-aux"
  | "right-aux2"
  | "bottom"
  | "float"
  | "overlay";

/**
 * A mode id. Core's own UI has no modes, so this is an open string: the four a
 * shell ships (`inspect`/`results`/`build`/`convert` in the docked UI) and any a
 * plugin contributes through `modes` are the same kind of thing, and core has no
 * business ranking them.
 */
export type PluginModeId = string;

/**
 * A whole activity a plugin contributes — a mode, in a shell that has modes.
 *
 * The slot exists because a mode is the one contribution no other field can carry.
 * A panel can say `modes: ["capacity"]`, but nothing tells the shell what
 * "capacity" IS: its name, its glyph, where it sits in the switcher. Without that
 * the shell would have to invent a label from an id, which is how you end up with
 * a mode button reading "my-plugin:review".
 *
 * Contributing one does not make it exist: a shell with no modes ignores this
 * list, and the plugin's panels fall back to their region as they always did.
 */
export interface PluginModeSpec {
  /** Globally unique, kebab-case. What a panel's `modes` names. */
  id: PluginModeId;
  /** Short name for the mode switcher. */
  label: string;
  /** One line: what you DO here. Tooltip / palette text. */
  hint?: string;
  /** Sort key among the shell's own modes; ties broken by id. */
  order?: number;
  /** The mode's glyph. A plugin ships the component — it cannot name one from a
   * shell's icon registry, which it has never heard of. */
  icon?: React.FC;
  /**
   * The mode's toolbar: the controls that belong to this activity and no other.
   *
   * A mode without one is a switcher entry that changes which panels are offered.
   * With one, it is a place to work — which is the difference between a feature
   * living in a panel and a feature having a home.
   */
  toolbar?: (ctx: AdaPluginContext) => React.ReactNode;
}

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
  // Draw-range ids (per-element selection) currently highlighted on the active
  // FEA mesh, or an empty array when nothing is selected / no FEA model is
  // loaded. Same identity core's own selection highlight uses, so a plugin
  // drawing an opaque overlay on top of the mesh can mirror the selection
  // colour instead of hiding it. Plain string ids — no three import, names no
  // plugin.
  getSelectedFeaRangeIds: () => string[];
  // Drive core's per-element selection on the active FEA mesh from a set of
  // draw-range ids — writes the SAME selection store a scene click writes, so the
  // highlight uses the exact selection colour + CustomBatchedMesh path as
  // click-select. A plugin listing results should call this (instead of painting
  // its own overlay) so list-selection and click-selection look identical.
  // ``additive`` false (default) replaces the selection; true unions. No-op when
  // no FEA model is loaded. Plain string ids — no three import, names no plugin.
  setSelectedFeaRanges: (rangeIds: string[], additive?: boolean) => void;
  // Load a GLB/glTF from an arbitrary URL and overlay it in the scene, the same
  // way core's own storage-browser load does — a plugin sourcing geometry from
  // somewhere core knows nothing about (an external catalog, a signed URL) has no
  // other way in: `add` takes an already-built Object3D, and core's loaders all
  // resolve a key under a viewer scope. Resolves once the model is in the scene.
  //
  // `sourceName` is the identity the model is registered under (defaults to the
  // URL's filename) and the handle `unloadModel` takes. `headers` are passed to
  // the loader for a URL that needs auth; omit for an already-signed one.
  // `translate` (default true) reuses the first-loaded model's recentering frame
  // so an overlay lands aligned rather than re-derived from its own bbox.
  //
  // `sourceUpAxis` names the up axis of the file's OWN coordinates. It defaults
  // to "z", meaning the content already matches this viewer's Z-up world — what
  // adapy's own exports carry. Pass "y" for a glTF that follows the glTF 2.0
  // spec's Y-up convention (most third-party producers), and core rotates it
  // upright on load instead of laying it on its side. The rotation is applied
  // before the recentering frame is measured, so it composes correctly with
  // `translate` and leaves a "y" model sharing one frame with a "z" one.
  loadModelFromUrl: (
    owner: string,
    url: string,
    opts?: {
      sourceName?: string;
      headers?: Record<string, string>;
      translate?: boolean;
      sourceUpAxis?: "z" | "y";
    },
  ) => Promise<void>;
  // Remove a model previously loaded through `loadModelFromUrl` (or any source
  // registered under that name), disposing its GPU resources. No-op when the
  // name is not loaded.
  unloadModel: (sourceName: string) => void;
}

/** Namespaced REST client helper — plugin routes live under `/api/plugins/{id}`. */
export interface PluginApiClient {
  base: string;
  /** Absolute base for this (or another) plugin's routes: `${base}/plugins/{id}`. */
  plugin: (id?: string) => string;
}

/** Resolved colour tokens core hands plugins so their panels paint with core's
 * active theme instead of hardcoding grays — the same values core mirrors onto
 * the `--ada-*` CSS custom properties (so a panel can consume either the object
 * here or `var(--ada-…)` directly). `bg`/`surface`/`border`/`text`/`textMuted`
 * track the user's panel-chrome theme; `accent`/`pass`/`warn`/`fail` are the
 * theme-neutral semantic colours (interactive / OK / caution / failure). Any
 * CSS colour string. Named generically — core carries no plugin knowledge. */
export interface PluginTheme {
  bg: string;
  surface: string;
  border: string;
  text: string;
  textMuted: string;
  accent: string;
  pass: string;
  warn: string;
  fail: string;
}

/** The single context object every slot callback receives. */
export interface AdaPluginContext {
  pluginId: string;
  api: PluginApiClient;
  stores: AdaViewerStores;
  scene: SceneHandle;
  scope: () => string;
  /** Active theme tokens (mirrors of the `--ada-*` CSS vars) so plugin panels
   * match core's chrome in light + dark without hardcoding colours. */
  theme: PluginTheme;
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
  /**
   * Which of the shell's docks this panel wants (1.1.0). Ignored by a shell with
   * no docks — core's own UI mounts by `region` regardless.
   */
  dock?: PluginDockId;
  /**
   * Which modes offer this panel (1.1.0). Omitted means every mode. Names a
   * shell's own mode or one this plugin contributed through `modes`.
   */
  modes?: readonly PluginModeId[];
  order?: number;
  activationPredicate?: ActivationPredicate;
  render: (ctx: AdaPluginContext) => React.ReactNode;
  topBarButton?: TopBarButtonSpec;
  // When set on a `fem-sidebar` panel, core promotes the panel to a tab in the
  // Simulation panel (alongside the built-in "animation" tab) rather than
  // stacking it inline. Buttonless / non-`asTab` fem-sidebar panels keep
  // rendering inline via `PluginPanelRegion` — this is purely additive.
  //
  // `admin` panels are ALWAYS tabs (the admin page is a tab strip), so there
  // `asTab` is optional and only supplies the label; without it the namespaced
  // panel id is shown.
  asTab?: {
    label: string;
    order?: number;
    // Show a small dot on the tab button when the panel's activation predicate
    // holds (mirrors SceneInfoBox's contextual-tab dot).
    contextual?: boolean;
    // Optional count/label rendered as a badge on the tab button. Wrapped in a
    // try/catch by core; a throw disables the plugin rather than the panel.
    badge?: (ctx: AdaPluginContext) => number | string | null;
  };
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
  // Whole activities (1.1.0). Consumed by a shell that has modes; ignored by one
  // that does not, including core's own UI.
  modes?: PluginModeSpec[];
  // Whole-UI contributions: a plugin may ship an entire alternative viewer UI
  // that core mounts INSTEAD of its own shell (see `./uiShells`). Orthogonal to
  // the slots above — a plugin can do either, both, or neither.
  uiShells?: UiShellSpec[];
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
  modes: PluginModeSpec[];
  // Ids of the UI shells this plugin contributed (the shells themselves live in
  // the shell registry); kept for introspection / audit.
  uiShellIds: string[];
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
  // UI shells go to their own registry (core mounts exactly one), but are
  // registered here so a plugin still has ONE entry point: registerPlugin().
  const uiShellIds: string[] = [];
  for (const shell of spec.uiShells ?? []) {
    registerUiShell(shell, id);
    if (shell?.id) uiShellIds.push(shell.id);
  }

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
    // NOT namespaced, unlike panels and colour fields. A mode id is user-facing —
    // a panel writes `modes: ["capacity"]` and a shell may accept `?mode=capacity`
    // — so it has to be the word the author chose. Collisions are the same
    // first-writer-wins as everywhere else in this registry.
    modes: spec.modes ?? [],
    uiShellIds,
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
/**
 * Every mode contributed by an enabled plugin, in switcher order.
 *
 * Context-free: a shell needs this to build its mode switcher, which exists before
 * any model is loaded and long before a plugin context is worth constructing. So
 * `activationPredicate` is NOT consulted — a mode is a place you can go, not a
 * thing that comes and goes. A mode whose panels are all inactive is an empty
 * mode, which is a smaller failure than a switcher button that appears and
 * disappears under the pointer.
 */
export function getPluginModes(): Array<{ pluginId: string; mode: PluginModeSpec }> {
  const out: Array<{ pluginId: string; mode: PluginModeSpec; order: number }> = [];
  for (const p of _registry.values()) {
    if (p.disabled) continue;
    for (const mode of p.modes) {
      if (!mode?.id) continue;
      out.push({ pluginId: p.id, mode, order: mode.order ?? 0 });
    }
  }
  out.sort((a, b) => a.order - b.order || a.mode.id.localeCompare(b.mode.id));
  return out.map(({ pluginId, mode }) => ({ pluginId, mode }));
}

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

/** One plugin-contributed Simulation tab. */
export interface SimulationTabEntry {
  pluginId: string;
  panel: PanelSlot;
  asTab: NonNullable<PanelSlot["asTab"]>;
}

/** `fem-sidebar` panels that opt into a Simulation tab (carry `asTab`), filtered
 * by activation exactly like `getPanelsForRegion` and ordered by
 * `(asTab.order ?? 0, id)`. Core appends these after the built-in "animation"
 * tab; each carries its owning `pluginId` so core can build a per-plugin ctx and
 * ErrorBoundary-wrap the mount. Empty when no plugin advertises a tab (the panel
 * then looks byte-identical to the pre-plugin Simulation panel). */
export function getSimulationTabs(ctx: AdaPluginContext): SimulationTabEntry[] {
  const entries = getPanelsForRegion("fem-sidebar", ctx)
    .filter(({ panel }) => !!panel.asTab)
    .map(({ pluginId, panel }) => ({ pluginId, panel, asTab: panel.asTab! }));
  entries.sort(
    (a, b) =>
      (a.asTab.order ?? 0) - (b.asTab.order ?? 0) ||
      a.panel.id.localeCompare(b.panel.id),
  );
  return entries;
}

/** Resolve a single Simulation tab by panel id, IGNORING activation predicates.
 * The forced-tab hosts (the "open in new window" follower and the maximized
 * window) mount one specific plugin panel by id; those hosts are canvas-less and
 * load only result sidecars, so the plugin's activation predicate (which may key
 * off a loaded mesh) can be false even though the panel's own data is present.
 * Returns null only when no plugin registers a `fem-sidebar` `asTab` panel with
 * that id at all. */
export function findSimulationTabById(panelId: string): SimulationTabEntry | null {
  for (const p of _registry.values()) {
    for (const panel of p.panels) {
      if (panel.region === "fem-sidebar" && panel.asTab && panel.id === panelId) {
        return { pluginId: p.id, panel, asTab: panel.asTab };
      }
    }
  }
  return null;
}

/** One plugin-contributed admin tab. */
export interface AdminTabEntry {
  pluginId: string;
  panel: PanelSlot;
  label: string;
}

/** `admin` panels, filtered by activation exactly like `getPanelsForRegion` and
 * ordered by `(order ?? 0, id)`. Core appends these after the built-in admin
 * tabs; each carries its owning `pluginId` so core can build a per-plugin ctx and
 * ErrorBoundary-wrap the mount. Empty when no plugin advertises an admin tab (the
 * page then looks byte-identical to the pre-plugin admin page). */
export function getAdminTabs(ctx: AdaPluginContext): AdminTabEntry[] {
  return getPanelsForRegion("admin", ctx).map(({ pluginId, panel }) => ({
    pluginId,
    panel,
    label: panel.asTab?.label ?? panel.id,
  }));
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
