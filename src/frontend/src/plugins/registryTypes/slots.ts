// What a plugin declares (`PluginSpec`) and the slots it fills: panels, modes, top-bar buttons,
// colour fields, sidecar loaders, renderable files, simulation UIs and URL handlers, and the
// context a plugin is handed. Types only: part of the plugin API `../registry` versions and
// re-exports.

import type React from "react";
import type { AdaViewerStores } from "@/state/AdaViewerContext";
import type { UiShellSpec } from "../uiShells";
import type { SimulationProviderInfo, SimulationRun } from "@/services/api/simulations";
import type { SceneColorFieldResult, SceneHandle, SidecarFetcher } from "./scene";
import type { PluginLayers, PluginPlayback, PluginSimulations } from "./playback";

// The named mount regions core exposes. Deliberately small
// (`fem-sidebar` covers the FEM simulation panel, `top-panel` the menu bar,
// `admin` the /admin page's tab strip, `connection-builder` the connection
// builder panel's); more are added on demand rather than up-front.
export type PluginRegion =
  | "fem-sidebar"
  | "top-panel"
  | "scene-info"
  | "storage-detail"
  | "admin"
  | "connection-builder";

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
  /**
   * While this mode is active, core suspends the viewer's active FEA field
   * colouring — vertex colours and legend — and restores it, exactly as it was,
   * on leaving. For a mode that paints its own colouring (a check overlay, a
   * property painter) and would otherwise show another mode's field underneath
   * it. Declarative on purpose: no lifecycle to get wrong, no ordering between
   * plugins, and a shell keeps its "modes do not mutate scene state" property —
   * core does the suspending, on the mode's stated behalf, when the shell
   * reports the transition via `notifyActiveModeSceneColor`.
   */
  ownsSceneColor?: boolean;
  /**
   * True: the FEA model is shown deformed while this mode is active. Default false
   * for a plugin's mode (opt in): core holds the model undeformed (1.13.0). A
   * shell's own modes (`UiShellMode`) default to shown.
   */
  showsDeformation?: boolean;
}

export type PluginLogLevel = "debug" | "info" | "warn" | "error";

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
  /** The viewer's playback clock (1.18.0). */
  playback: PluginPlayback;
  /** The plugin's rows of the Layers panel (1.18.0). */
  layers: PluginLayers;
  /** Offer something to simulate (1.21.0). */
  simulations: PluginSimulations;
  scope: () => string;
  /** Active theme tokens (mirrors of the `--ada-*` CSS vars) so plugin panels
   * match core's chrome in light + dark without hardcoding colours. */
  theme: PluginTheme;
  log: (level: PluginLogLevel, msg: string, ...args: unknown[]) => void;
  /** Hand a worker job to the viewer's global progress toast (1.3.0).
   *
   * WHAT IT REPLACES. A panel that enqueues a job and awaits its own poll loop is
   * the only thing watching it, so closing the panel unmounts the watcher and the
   * job disappears from the UI while running perfectly well server-side. The
   * toast is mounted at app level and outlives every panel, and core already
   * repopulates it from `my-jobs` on load — so a tracked job also survives a page
   * reload, which no panel-local loop can.
   *
   * Returns the store key for the toast entry. Fire-and-forget: the poll is
   * detached, which is the point.
   *
   * To act when the job is done (reload results, say), give it a `kind` and register a
   * finisher for that kind at module load (`registerJobFinisher` on @/viewer-core/app,
   * 1.9.0): it runs as the entry's last stage, with `data`. With `persist` the entry is
   * remembered in this browser, so the toast -- and the finisher -- resume after a reload.
   *
   * `scope` is the scope the job RAN in, when that is not the open one: a provider job runs in
   * the provider store, and `POST /plugins/{id}/jobs` answers which (`scope` in its response).
   */
  trackJob: (opts: {
    jobId: string;
    label: string;
    derivedKey?: string;
    storeKey?: string;
    scope?: string;
    kind?: string;
    data?: unknown;
    persist?: boolean;
  }) => string;
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
  /**
   * Opened and closed by code, with no top-bar button (1.26.0): a button of the plugin's own
   * elsewhere -- an action of the playback transport, say -- shows and hides it with `toggle` /
   * `reveal` / `hide` (`@/viewer-core`). It has the open state a top-bar panel has, so it is drawn
   * only while open, keeps its place in a layout while closed, and starts closed. In the panel
   * column core frames it -- its `title`, float, move and close -- as nothing else there closes it.
   */
  toggled?: boolean;
  /** The panel's name in a window's title bar, a group's tab and a menu (1.26.0). Default: a
   *  top-bar button's or tab's label, else its id. */
  title?: string;
  /** With `dock: "float"`: the size of its window the first time (1.26.0). */
  floatSize?: { w: number; h: number };
  /**
   * May open in a browser window of its own (1.13.0). Only for a panel that works
   * without the 3D scene: the new window boots canvas-less, with stores of its own.
   * Applies to a panel that stands on its own (`top-panel`, or one naming a `dock`).
   */
  popout?: boolean;
  /**
   * May go to a pop-out window although it reads the scene (1.13.0): the plugin
   * mirrors what it reads from the main window and acts through it
   * (`registerMirrorTopic`, `registerMirrorCommand`).
   */
  mirrored?: boolean;
  // When set on a `fem-sidebar` panel, core promotes the panel to a tab in the
  // Simulation panel (alongside the built-in "animation" tab) rather than
  // stacking it inline. Buttonless / non-`asTab` fem-sidebar panels keep
  // rendering inline via `PluginPanelRegion` — this is purely additive.
  //
  // `admin` panels are ALWAYS tabs (the admin page is a tab strip), so there
  // `asTab` is optional and only supplies the label; without it the namespaced
  // panel id is shown.
  //
  // `connection-builder` panels (1.26.0) are always tabs too, of the Connections
  // panel, after core's own (Builder, Clashes, Overlays, Review; 1.28.0): `asTab` supplies the label, order
  // and badge; without it the panel's `title`, else its id, is the label.
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

// ---------------------------------------------------------------------------
// Renderable file providers (1.5.0)
//
// WHAT WAS MISSING. Every slot above extends what the viewer does with a model
// core already loaded. None of them let a plugin say "I can turn a file of THIS
// kind into scene content" — so a format core has no converter for was
// unopenable no matter what a plugin could do with it, and core's own
// "can this file go in the scene?" predicate had no one to ask.
//
// WHY IT IS A REGISTRY SLOT AND NOT JUST A BUTTON IN A PANEL. A plugin panel can
// already load anything it likes through `SceneHandle.loadModelFromUrl`; that is
// how the first such plugin does it. What a panel cannot do is make core's OWN
// file affordances — the storage-browser load checkbox, the gallery walk — agree
// that the file is openable. Those read one predicate, and the predicate is in
// core. This slot is that predicate's answer.
//
// THE SPLIT: `claims` IS PURE, `open` IS NOT. `claims` takes a key and returns a
// boolean. No context, no promise, no I/O. That is not minimalism, it is the only
// shape that fits the caller: the predicate runs once per file in a listing of
// hundreds, inside a `useMemo`, on every render — the gallery builds its whole
// walk list from it. A per-file HTTP round trip there is a hundred requests to
// draw one file list. (Core answers its own half the same way, from the
// worker-advertised conversion matrix it already has; see
// `@/utils/scene/fileKinds`.) `open` is where the work goes: it is async, gets a
// full context, and runs once, for one file, when the user asks.
//
// A provider is NOT consulted for extensions core can already open. Core wins
// ties, always — installing a plugin must not silently change how a `.ifc`
// loads. Providers fill gaps; they do not override.
// ---------------------------------------------------------------------------

/**
 * A plugin's claim that it can put a stored file in the scene.
 *
 * `claims` must be cheap, synchronous and a pure function of the key: see the
 * block above for why. Throwing from it is a plugin defect and disables the
 * plugin for the session, like every other predicate in this registry.
 *
 * `open` does the work and resolves once the model is visible. Throwing from it
 * surfaces as a load error against THAT FILE and leaves the plugin enabled: a
 * blob that has gone missing or a worker that timed out says nothing about
 * whether the next file will open, and disabling the plugin would take its whole
 * file kind out of every listing for the rest of the session.
 */
export interface RenderableFileProvider {
  id: string; // namespaced on register
  /** Short, user-facing: what opening this file will do ("Build and load the
   * weld model"). Shown by a host that has somewhere to put it; core's own UI
   * currently does not, so it is optional. */
  label?: string;
  /** Does this provider handle this storage key? Pure, synchronous, no I/O. */
  claims: (key: string) => boolean;
  /**
   * Put the file's geometry in the scene. `scope` is the viewer scope the key
   * lives in, resolved by core so a provider cannot open a key against a scope
   * the user is not browsing.
   *
   * REGISTER THE MODEL UNDER `args.key`. That is `SceneHandle.loadModelFromUrl`'s
   * `sourceName`, and it is a requirement rather than a suggestion: core tracks
   * the scene by source name, and every "is this file shown?" affordance — the
   * row's checkbox and eye marker, the bulk actions, the gallery's position
   * anchor — plus the whole unload path are lookups in that set BY FILE KEY. A
   * model registered under some other name is visible in the scene with its row
   * unticked, and the checkbox that opened it cannot close it again.
   *
   * The constraint costs a plugin nothing it should want: a storage key is
   * already unique, stable across reloads, and distinct per revision when the
   * key space carries one — the three properties a scene identity needs. It is
   * also exactly what core's own `overlay_file_in_scene` does with the files it
   * loads, so a plugin-opened file and a core-opened one are the same kind of
   * entry in the same list. A plugin that ALSO loads the same bytes from its own
   * panel, under its own naming, is free to: that is a different entry point
   * making a different claim, and it is not this one.
   */
  open: (ctx: AdaPluginContext, args: { key: string; scope: string }) => Promise<void>;
  /** Tie-break among providers claiming the same key; ties broken by id. */
  order?: number;
}

// ---------------------------------------------------------------------------
// Simulation providers (1.17.0)
//
// A simulation provider is a backend thing: a worker registered it, and the server lists it
// with its domain (`fea`, `robotics`, `cfd`), its engine and a JSON Schema of its parameters.
// Core's Simulations panel can start any of them from that schema and list any run's artefacts
// as downloads, and that is all it can do, because it knows no engine.
//
// This slot is where a plugin adds what core cannot: a better form than the schema gives (a
// file picker for an input deck, a robot chosen from the scene), and a way to OPEN a result
// (load the animated model and its timeline, hand a result file to the FEA view). An entry is
// matched by `providerId` (one provider) or by `domain` (every provider of that kind, since a
// domain's artefact roles are the same whatever the engine). A provider-id match beats a domain
// match; among equals the lower `order` wins, then the id.
// ---------------------------------------------------------------------------

/** What a parameter form section is given. */
export interface SimulationParameterFormArgs {
  provider: SimulationProviderInfo;
  /** The scope the run will be started in. */
  scope: string;
  /** The parameters so far (the schema's defaults to begin with). */
  value: Readonly<Record<string, unknown>>;
  /** Replace the parameters. */
  onChange: (next: Record<string, unknown>) => void;
  /** The storage keys of the run's input files so far, and how to replace them. */
  inputs: readonly string[];
  onInputsChange: (next: string[]) => void;
  /**
   * The subject the run will be started on (e.g. a detail the connection builder offered), when one
   * is chosen and this provider takes its kind; null otherwise. A form whose own fields name the part
   * another way (a connection picked from a list) should leave those out while there is one: the run
   * already says what it works on. Absent on a core before it was passed.
   */
  subject?: SimulationFormSubject | null;
}

/** The subject a parameter form is told about: what it is and what a person calls it. */
export interface SimulationFormSubject {
  /** One of the server's subject kinds, e.g. `"weld-assembly"`. */
  kind: string;
  /** Storage key of its file, in the run's scope. */
  key: string;
  label: string;
}

/** What an "open result" handler is given. */
export interface SimulationOpenResultArgs {
  run: SimulationRun;
  /** The scope the run (and every artefact `key`) is in. */
  scope: string;
  /** The provider as the server lists it now; null when it is no longer listed. */
  provider: SimulationProviderInfo | null;
}

/** A plugin's UI for one simulation provider, or for every provider of a domain. */
export interface SimulationProviderUi {
  id: string; // namespaced on register
  /** Match one provider by its id. */
  providerId?: string;
  /** Match every provider of this kind (`"fea"`, `"robotics"`, `"cfd"`). One of the two is needed. */
  domain?: string;
  /** Tie-break among entries matching equally well; ties broken by id. */
  order?: number;
  /**
   * The start form's parameter section, in place of the one core builds from the provider's
   * JSON Schema. The server still checks what is sent against the schema.
   */
  renderParameters?: (ctx: AdaPluginContext, args: SimulationParameterFormArgs) => React.ReactNode;
  /**
   * Open a run's result (also offered while the run is going, for a handler that follows
   * partial artefacts). Throwing shows the error against that run and leaves the plugin
   * enabled, as for a renderable file: one result that will not open says nothing of the next.
   */
  openResult?: (ctx: AdaPluginContext, args: SimulationOpenResultArgs) => void | Promise<void>;
  /** The button's text; "Open" when omitted. */
  openResultLabel?: string;
  /**
   * (1.19.0) The button's text while the run is still going, for a handler that follows it as
   * it happens ("Live"); `openResultLabel` when omitted. See `openResultButtonLabel`.
   */
  openResultLiveLabel?: string;
}

// ---------------------------------------------------------------------------
// Storage asset types (1.26.0)
//
// The storage browser lists a scope's files. Some of what a scope has is not a file a person
// uploaded and opens: it is an asset of several stored files under one name and version, kept
// under `assets/` (which the browser's file list leaves out) or reached through a grant, and
// only the plugin that defines the kind can say which there are and what one is. This slot is
// where it says so: core gives the kind a section of the browser and an entry of its "+" menu,
// and the plugin fills both. Core never learns what the kind is.
// ---------------------------------------------------------------------------

/** What an asset type's callbacks are told. */
export interface StorageAssetTypeArgs {
  /** The scope the storage browser shows. */
  scope: string;
}

/** A form of the plugin's, opened from the storage browser's "+" menu. */
export interface StorageAssetUpload {
  /** The menu entry's text ("Upload a material library…"). */
  label: string;
  /** The dialog's title; `label` without a trailing ellipsis when omitted. */
  title?: string;
  /** The form. `close` closes the dialog; the plugin calls it when it is done. */
  render: (ctx: AdaPluginContext, args: StorageAssetTypeArgs & { close: () => void }) => React.ReactNode;
}

/** A kind of stored asset a plugin adds to the storage browser. */
export interface StorageAssetType {
  id: string; // namespaced on register
  /** The section's heading: what the kind is called, in the plural. */
  label: string;
  /** Sort key among the asset types; ties broken by id. */
  order?: number;
  /**
   * Whether the open scope has this kind at all. False leaves out the section and the "+" entry.
   * Asked on every render of the browser: cheap, synchronous, no I/O. A plugin whose answer
   * arrives later says so with `notifyPluginActivationChanged`, and the browser asks again.
   */
  available?: (ctx: AdaPluginContext, args: StorageAssetTypeArgs) => boolean;
  /** The section's body: the scope's assets of this kind, as the plugin lists them. */
  render: (ctx: AdaPluginContext, args: StorageAssetTypeArgs) => React.ReactNode;
  /** Adding one: an entry of the "+" menu that opens the plugin's form in a dialog. */
  upload?: StorageAssetUpload;
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
  // File kinds this plugin can render (1.5.0). Read by core's own
  // "can this file go in the scene?" predicate, not just by a UI host.
  renderableFileProviders?: RenderableFileProvider[];
  urlParamHandlers?: UrlParamHandler[];
  // UI for simulation providers, by provider id or by domain (1.17.0). Read by core's
  // Simulations panel.
  simulationProviders?: SimulationProviderUi[];
  // Kinds of stored asset in the storage browser, each with its own list and upload form (1.26.0).
  storageAssetTypes?: StorageAssetType[];
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
  renderableFileProviders: RenderableFileProvider[];
  urlParamHandlers: UrlParamHandler[];
  simulationProviders: SimulationProviderUi[];
  storageAssetTypes: StorageAssetType[];
  modes: PluginModeSpec[];
  // Ids of the UI shells this plugin contributed (the shells themselves live in
  // the shell registry); kept for introspection / audit.
  uiShellIds: string[];
  // Set when a slot callback threw during the session; a disabled plugin
  // contributes no slots until reload (failure isolation).
  disabled?: string;
}
