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

import { registerUiShell } from "./uiShells";
import { panelSpecFromSlot } from "@/layout/pluginPanels";
import { registerPanel, removePanelsOwnedBy } from "@/layout/registry";
import { isPlacedElsewhere } from "@/layout/placedElsewhere";
import type { AdaPluginContext, PanelSlot, PluginModeSpec, PluginRegion, PluginSpec, RegisteredPlugin, RenderableFileProvider, ResultSidecarLoader, SceneColorFieldProvider, SimulationProviderUi, StorageAssetType, StorageAssetTypeArgs, UrlParamHandler } from "./registryTypes/slots";

export { type PluginRegion, type PluginDockId, type PluginModeId, type PluginModeSpec, type PluginLogLevel, type PluginApiClient, type PluginTheme, type AdaPluginContext, type ActivationPredicate, type TopBarButtonSpec, type PanelSlot, type SceneColorFieldProvider, type ResultSidecarLoader, type RenderableFileProvider, type SimulationParameterFormArgs, type SimulationFormSubject, type SimulationOpenResultArgs, type SimulationProviderUi, type UrlParamHandler, type PluginSpec, type RegisteredPlugin, type StorageAssetTypeArgs, type StorageAssetUpload, type StorageAssetType } from "./registryTypes/slots";
export { type SceneColorFieldResult, type SceneColor, type SceneOverlay, type SceneLines, type SceneElementOutline, type SceneLabel, type SceneLabelOptions, type SceneElementPaint, type SceneLayerRef, type SceneLayerLook, type SceneLineLayerSpec, type SceneLineLayer, type ScenePointLayerSpec, type ScenePointLayer, type SceneImageRect, type SceneImageSpec, type SceneImage, type SceneModelSource, type SceneModelPose, type SidecarFetcher, type SceneHandle, type SceneBox, type SceneFace, type SceneObjectMatch } from "./registryTypes/scene";
export { type PluginPlaybackTrack, type PluginPlaybackTrackHandle, type PluginPlaybackState, type PluginPlayback, type PluginLayerState, type PluginLayers, type PluginSimulationSubject, type PluginSimulationSubjectHandle, type PluginSimulations } from "./registryTypes/playback";
export type { ScenePick, ScenePickOptions, ScenePickSession } from "./scenePicks";
export type { SceneHighlight, SceneHighlightOptions, SceneHighlightRef } from "./sceneHighlights";
export type {
  SceneContact,
  SceneContactAnswer,
  SceneContactOptions,
  SceneContactPatch,
  SceneObjectRef,
} from "./sceneContact";
export type { PlaceBoxOptions, PlacedBox, PlacedBoxMode, PlacedBoxPose } from "./sceneBox";

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
//   1.3.0  `trackJob` — hand a worker job to the global toast instead of
//          watching it from inside a panel
//   1.4.0  mode-owned scene colouring (`PluginModeSpec.ownsSceneColor`,
//          honoured via `notifyActiveModeSceneColor`), and kept in step with
//          viewer-core 1.4.0 (compute settings store, set isolation on the
//          scene facade) -- which is what a shell's `coreApiRange` is checked
//          against, so a shell built for those exports can name a core that
//          has them.
//
//          This entry was 1.3.0 on this branch. main published 1.3.0 as
//          `trackJob` first, and an out-of-tree plugin already depends on
//          1.3.0 meaning `trackJob` under `plugin_api >= 1.3.0`, so the
//          number could not be reused for a different capability. Moving up
//          costs nothing here, and keeps the viewer-core parity above intact.
//   1.5.0  `renderableFileProviders` — a plugin declares that it can turn a
//          stored file of some kind into scene content core has no converter
//          for. This is the first slot core's OWN behaviour reads outside a
//          render path: `canOpenInScene` (@/utils/scene/fileKinds) asks the
//          registry whether any plugin claims a key, so the storage browser
//          and the gallery offer exactly the files something can actually
//          open. A plugin built against this and loaded into a 1.4.0 core
//          would register a slot nothing reads — its files would be filtered
//          out of every list by 1.4.0's own predicate, silently — which is
//          precisely the "fails from inside the plugin with nothing naming
//          the mismatch" case a minor bump exists to turn into one log line.
//   1.6.0  `registerAssetTreeClient` — a plugin supplies a BROWSER-SIDE client for one asset
//          provider, and core's reading paths dispatch to it per call instead of the REST
//          route. It exists for a provider that must be read as the SIGNED-IN USER: a
//          service authenticating the person cannot be proxied by a worker without either
//          handing the worker that identity or flattening every user to one service account.
//          The same reasoning already produced `registerExternalModelClient`
//          (`services/externalModelClients.ts:117`); this is that shape for assets.
//          A minor bump by the criterion above: it is a registry a plugin DEPENDS on, and a
//          plugin built against it and loaded into 1.5.0 core would register into nothing —
//          its collections would simply not appear, with no line naming the mismatch.
//   1.7.0  kept in step with viewer-core 1.7.0 (`stopLocalViewer` on
//          `@/viewer-core/app`), which is what a shell's `coreApiRange` is
//          checked against — the same reason as 1.4.0.
//   1.8.0  `@/services/assetScopeCollections` — the pure reader for which of an asset
//          provider's collections may be REQUESTED per scope (`parseScopeCollections`,
//          `enabledFor`, the `public.assets.scope_collections` setting the admin Providers tab
//          writes). `enabledFor` returns `null` for unrestricted and `[]` for nothing enabled,
//          and the type keeps them apart. A minor bump by the criterion above: a plugin that
//          imports the module and is built into a 1.7.0 core fails at an undefined import from
//          inside itself; with the bump it is skipped with one line naming the mismatch.
//          This entry was 1.7.0 on its branch; main published 1.7.0 first, for viewer-core
//          parity, so -- as with 1.4.0 -- the number moved up rather than meaning two things.
//          Plugin-side only, so viewer-core stays at 1.7.0, as it did through 1.6.0.
// 1.9.0 -- renderer-neutral scene overlays on `SceneHandle`: `addLines`, `addLabels`,
//          `paintFeaElements` (each returns a `SceneOverlay`), `hasFeaModel`, `showColorScale`
//          (a plugin's banded scale in core's colour legend), `trackJob` `kind` / `data` /
//          `persist` (with `registerJobFinisher` on @/viewer-core/app), FEA element isolation
//          (`isolateFeaElements`, `hideFeaElements`, `hiddenFeaElements`) and
//          `setFeaElementEdgesVisible`. A plugin states what to draw in plain data and core
//          builds it, so it draws the same under three.js and the WebGPU renderer and moves
//          and clips with the model. `add` (a raw Object3D) and `getActiveFeaMesh` stay, now
//          deprecated: they tie a plugin to one renderer and to the FEA mesh's internals.
//          Also `addElementOutlines` (an element set's outline, following the warp),
//          `paintFeaElements` `on: "beamSolids"`, lines `opacity` / `throughModel`, and labels
//          with several lines, `atElements` (pinned to a warping element set) and an `anchor`.
//          Additive, so a minor bump.
// 1.10.0 — the view, for a UI shell's modes and searches: `setFeaDeformationShown` (hold the FEA
//          model undeformed, beside the user's warp settings), `frameFeaElements` / `frameBox` /
//          `frameAll` (the camera), `findFaces` / `focusFace` (a CAD model's faces by source face
//          id). Additive, so a minor bump.
// 1.11.0 — `setFeaUnpaintedColor` (the plain FEA model's colour where nothing paints it), and
//          `addLabels` labels cut whole at their anchor under section planes (they lost part of
//          their text at the cut). Additive, so a minor bump.
// 1.12.0 — kept in step with viewer-core 1.10.0: the panel registry (`registerPanel`,
//          `listPanels`, `getPanel`) and the panel intents (`reveal`, `hide`, `toggle`,
//          `isPanelOpen`) on `@/viewer-core`. Every accepted `PanelSlot` is also registered
//          there as a panel, under the same namespaced id, so a host lists plugin panels
//          beside core's. Slots themselves are unchanged and still mounted by region.
//          Additive, so a minor bump.
// 1.13.0 — kept in step with viewer-core 1.13.0: a UI shell without `load` is composed from
//          core's layout (`UiShellSpec.layout` / `chrome` / `themeTokens`). An older core
//          rejects such a shell, so a shell built on it names this version in `coreApiRange`.
//          Additive, so a minor bump.
// 1.14.0 — kept in step with viewer-core 1.14.0: core binds Ctrl+A (select all) and Ctrl+O
//          (open a file from disk) itself, so `registerShortcut` refuses those two chords to a
//          plugin, as it does core's other keys (`RESERVED_SHORTCUTS`). Nothing else changes for
//          a plugin; the new core commands are named by id like the others. Also reserved:
//          Shift+E (core's marking menu, `core:marking-menu`). A shell's `chrome.rail` may be
//          core's rail as data (`{tools}`) and `chrome.markingMenu` turns core's marking menu on.
// 1.15.0 — kept in step with viewer-core 1.15.0. All additive:
//          - a composed shell's mode may name core's toolbars (`UiShellMode.toolbars`: playback,
//            section, representation, colorBy, build) for its toolbar slot;
//          - number labels (`core:labels-nodes`, `core:labels-elements`; `chrome.overlays.labels`)
//            and colour by property (`registerColorBySource`);
//          - `UiShellMode.requires` (`"builder"`), `chrome.overlays.builder` and the
//            `core:builder-*` commands;
//          - the mirror topics `core:fea-controls`, `core:fea-table` and `core:scene-info`,
//            `feaControlActions`, and the new `SceneActions` (models, groups, selection boxes, related).
// 1.16.0 — kept in step with viewer-core 1.16.0. The node editor was removed (its panel,
//          `enableNodeEditor`, its exports on `@/viewer-core*`). Otherwise additive: core commands for the tree
//          (`core:select-parent` / `-child` / `-prev-sibling` / `-next-sibling`), the result view
//          (`core:color-legend`, `core:fem-concepts`, `core:data-dock`, `core:beam-sections`,
//          `core:warp`, `core:labels-values`) and the builder (`core:builder-new`, placement, gizmos,
//          views); `UiShellChrome.statusItems` (core's status bar, composed); the `core:component-list`
//          panel; property value labels; the Options "compute" section; the split button, status bar,
//          page header, glyphs, `PositionedMenu` and `ModalFrame` on `@/viewer-core/layout`, and
//          `@/viewer-core/theme`; `CommandSpec.keywords`, `core:option.*` commands for every Options
//          setting, and user-editable keyboard shortcuts (a `keys` is a default the user may override).
// 1.17.0 — `simulationProviders`: a plugin's UI for simulation providers, matched by provider id
//          or by domain (the kind of simulation: `fea`, `robotics`, `cfd`). Each entry may give a
//          parameter form section (`renderParameters`) and an "open result" handler
//          (`openResult`) for the Simulations panel (`core:simulations`); without one the panel
//          builds the form from the provider's JSON Schema and lists the artefacts as downloads.
//          Additive, so a minor bump: a plugin built against it and loaded into 1.16.0 would
//          register into nothing, and its results would open as a list of files with no line
//          naming the mismatch. viewer-core moves to 1.17.0 with it (the panel id, the types).
// 1.18.0 — what replaying something recorded over time needs: a moving model with layers of
//          data that change with it. All additive, on both viewports.
//          - `ctx.scene.addLineLayer`, `addPointLayer`, `addImage`: lines, points and an image
//            lying in the world, each returned as a handle whose data is replaced in place (another
//            number of segments or points every frame without a new buffer every frame, new pixels
//            in the same texture) and whose look (`visible`, `opacity`) changes without making it
//            again. Coordinates are the scene's own, z-up. `imageUnavailable` says when the
//            viewport on screen cannot draw an image of a size.
//          - `layer` on each of them: a row in core's Layers panel (`core:layers`) with a switch
//            and an opacity slider, remembered per layer id. `ctx.layers` reads and sets them.
//          - `ctx.playback`: the viewer's one playback clock. `addTrack({id, duration, show})`
//            registers something timed; `play` / `pause` / `seek` / `setSpeed` / `setLoop`,
//            `followEnd` for a recording that is still growing, `state` / `time` / `subscribe`.
//            A loaded model's node animation is a track of the same clock, so it and a plugin's
//            layers show the same moment.
//          - `ctx.scene.loadModel`: a GLB from bytes or a URL as the scene's model, on whichever
//            viewport is on screen (`loadModelFromUrl` built a three.js scene only until 1.26; it now
//            takes the same path).
//          - `simulationsApi.fetchArtefact` (@/viewer-core/app): a run artefact's bytes, whole or
//            from a byte offset, for one that is still being written.
//          `ctx.scene.remove(owner)` takes all of it away, tracks included. Draco-compressed
//          GLBs load.
// 1.19.0 — `openResultLiveLabel` on a simulation provider's UI: the open-result button's text
//          while the run is still going ("Live" beside a "Replay"), so a handler that follows
//          a run as it happens does not offer to replay what has not ended. Additive: without
//          it the button reads `openResultLabel` throughout, as before, and an older core
//          ignores it.
// 1.20.0 — a model whose parts the plugin moves itself, for showing something while it is still
//          happening (a file's own animation can only replay what was recorded). Additive, on
//          both viewports.
//          - `posable` on `SceneModelSource`: load the model for its nodes to be placed.
//          - `ctx.scene.poseModel(owner, sourceName, names)`: the nodes of that model called
//            `names`, as a handle whose `place(matrices)` puts each where a matrix says, in the
//            model's own coordinates. What hangs on a node in the file moves with it.
//          An older core has neither: a plugin asks for `poseModel` before it relies on it.
// 1.21.0 — simulation subjects: a run can be started ON a stored file (a part with its welds),
//          produced by one plugin and run by a provider of another, who never know each other.
//          Additive.
//          - `ctx.simulations.offerSubject({kind, key, label, scope?, source?})` offers the file to
//            the Simulations panel and returns `{withdraw}`. `kind` is one of the server's subject
//            kinds (`weld-assembly`); the panel starts a run on it with a provider whose
//            `accepts` lists the kind (`SimulationProviderInfo.accepts`), sending
//            `subject: {kind, key}`; the provider reads it as `case.subject`.
//          - `SimulationRun.subject`, `StartSimulationBody.subject` on `/app`.
//          An older core has no `ctx.simulations`: ask for it before relying on it.
// 1.22.0 — `registerToastSlot` / `unregisterToastSlot` (@/viewer-core): a plugin's own toasts
//          in the app's one toast stack.
// 1.23.0 — `setBuilderDefaults` (@/viewer-core): a shell's default Builder engine/blueprint.
//          `notifyPluginActivationChanged()`: a plugin whose activation predicate reads its own
//          state says when that state changed, and a placed panel showing "Nothing to show
//          here yet" re-asks it (the panel also re-asks on every loaded-model change).
// 1.24.0 — additive, both optional: an older core has neither.
//          - `SimulationParameterFormArgs.subject` ({kind, key, label} or null): the subject the
//            run will be started on, so a provider's form can leave out fields that name the part.
//          - `ctx.scene.clearScene()`: empties the scene as the Scene tab's Remove all does, for a
//            plugin about to show something of its own (a replay).
// 1.25.0 — `keepCamera` on `SceneModelSource`: the camera stays where it is when the model is
//          loaded, instead of framing it as the user's auto-fit setting says. For a model loaded
//          again in another form (a finer mesh of the same thing). Additive: an older core
//          ignores it and frames the model as before.
// 1.26.0 — what a tool of a plugin's own needs of the viewer. All additive: an older core has
//          none of it, so a plugin asks for a verb first, or names this version where a slot must
//          be seen.
//          - `ctx.scene.pickObjects(owner, {onPick})`: a plugin takes the scene's clicks on
//            objects for a while (a tool that asks the user to point at things), on either
//            viewport. A click it takes selects nothing and is no undo step; `end()` (or
//            `scene.remove(owner)`) gives the clicks back. `ScenePick` names what was clicked:
//            model, object id, name, source file, and the point in the model's own coordinates.
//          - `ctx.scene.placeBox(owner, {box, position, yawDeg?, constrain?, snap?, onChange, ...})`:
//            a plugin puts a simple box in the scene with a transform gizmo on it, for a tool that
//            asks the user where something stands, and is told the box's pose (position and turn
//            about Z, in the scene's own coordinates) as the user moves it. The gizmo answers the
//            cell builder's keys by the builder's rules -- G move, R turn, X / Y / Z lock an axis,
//            Escape / Enter -- and snaps as it does; its handles are drawn through the viewport
//            interface, so it works on either viewport. `set` moves the box from the plugin's
//            side; `end()` (or `scene.remove(owner)`) takes it away.
//          - `ctx.scene.loadModelFromUrl` goes through the loader that knows both viewports (it
//            built a three.js scene only).
//          - `registerPlaybackAction` / `unregisterPlaybackAction` / `notifyPlaybackActionsChanged`
//            (@/viewer-core): a shell's or plugin's buttons in the bar with play, the scrub bar and
//            the run's Console -- what shows and hides the things that follow the clock.
//          - `PanelSlot.toggled`: a panel with no top-bar button that code opens and closes;
//            `PanelSlot.title` and `floatSize`; a slot with `dock: "float"` starts in a window of
//            the layout (once; after that it is where the user left it), in core's own UI too.
//          - `storageAssetTypes`: a plugin adds a kind of stored asset to the storage browser,
//            beside the scope's files: a section the plugin fills with what the open scope has of
//            that kind, and, when it gives `upload`, an entry of the browser's "+" menu that opens
//            a form of the plugin's own in a dialog. `available` says per scope whether the kind
//            is there at all.
//          - the `connection-builder` region: a plugin's tab in the connection builder panel
//            (`core:component-controls`), after the builder's own. Every panel of the region is a
//            tab; `asTab` gives its label, order and badge. With no such panel the builder panel
//            has no tab strip and is what it was.
// 1.27.0 — `ctx.scene.contactBetween(a, b, options?)`: where two objects of the loaded models
//          touch -- the nearest points, the normal, the distance and the contact patches (the
//          polygons where their convex hulls meet), in the scene's own coordinates -- measured by
//          the viewer's clash module on the models' stored GLBs, so it answers the same on either
//          viewport. For a tool that places something where two objects meet. Additive: an older
//          core has no `contactBetween`, so a plugin asks for it first.
// 1.28.0 — `ctx.scene.highlightObjects(owner, refs, {color})`: a plugin draws objects of the loaded
//          models in a colour of its own -- over their own colour and over the selection's -- until
//          `remove()` (or `scene.remove(owner)`); `set(refs)` changes which. For a tool that takes
//          the scene's clicks (`pickObjects`) and shows what it has taken. Not the selection, not a
//          colour the user gave, and no undo step; on either viewport. Additive: an older core has
//          no `highlightObjects`, so a plugin asks for it first.
//          - the `joint-review` region: a plugin's section of the Connections panel's Review tab,
//            after core's produced and identified joints, headed by the panel's `title` and ordered
//            by `order` (`getJointReviewSections`). The Review tab is there while any section is
//            active, joints or not. An older core mounts the region nowhere.
//          - `ctx.scene.findObjects(names, {sourceName?})`: the objects of the loaded models called
//            one of `names`, as a `ScenePick` names them (model, id, name, source file), read from
//            the Scene tree; for a plugin that knows objects by name and wants to highlight them.
//          Both additive: an older core has neither, so a plugin asks for `findObjects` first.
//          Not an API change, but seen by a `connection-builder` tab: the Connections panel has
//          core's Clashes, Overlays and Review tabs too now (they were the Scene panel's Joints
//          group), after Builder and before the plugins' tabs, and is offered everywhere.
export const PLUGIN_API_VERSION = "1.28.0";

/** What the Simulations panel writes on a run's open-result button. */
export function openResultButtonLabel(
  ui: Pick<SimulationProviderUi, "openResultLabel" | "openResultLiveLabel">,
  active: boolean,
): string {
  return (active ? ui.openResultLiveLabel : undefined) ?? ui.openResultLabel ?? "Open";
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
  const renderableFileProviders = (spec.renderableFileProviders ?? []).map((r) => ({
    ...r,
    id: namespaced(id, r.id),
  }));
  const urlParamHandlers = spec.urlParamHandlers ?? [];
  const simulationProviders = (spec.simulationProviders ?? [])
    .filter((u) => {
      if (u?.providerId || u?.domain) return true;
      console.warn(
        `[plugins] plugin "${id}": simulation provider UI "${u?.id}" names neither a providerId nor a domain; ignored`,
      );
      return false;
    })
    .map((u) => ({ ...u, id: namespaced(id, u.id) }));
  const storageAssetTypes = (spec.storageAssetTypes ?? [])
    .filter((t) => {
      if (t?.id && t.label && typeof t.render === "function") return true;
      console.warn(`[plugins] plugin "${id}": storage asset type "${t?.id}" has no id, label or render; ignored`);
      return false;
    })
    .map((t) => ({ ...t, id: namespaced(id, t.id) }));
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
    renderableFileProviders,
    urlParamHandlers,
    simulationProviders,
    storageAssetTypes,
    // NOT namespaced, unlike panels and colour fields. A mode id is user-facing —
    // a panel writes `modes: ["capacity"]` and a shell may accept `?mode=capacity`
    // — so it has to be the word the author chose. Collisions are the same
    // first-writer-wins as everywhere else in this registry.
    modes: spec.modes ?? [],
    uiShellIds,
  });

  // The same panels, in the panel registry's terms (see @/layout/pluginPanels).
  // Additive: the region hosts above still read `panels` directly.
  // Unavailable while disabled, or while the plugin's own activation predicate is
  // false (when the bootstrap gave a context to ask it with).
  const isUnavailable = () => {
    const p = _registry.get(id);
    if (!p || p.disabled) return true;
    if (!p.activationPredicate || !_contextFactory) return false;
    return !isActive(p, _contextFactory(id));
  };
  for (const slot of panels) registerPanel(panelSpecFromSlot(id, slot, isUnavailable), id);
}

let _contextFactory: ((pluginId: string) => AdaPluginContext) | null = null;

/**
 * Hand the registry a way to build a plugin's context outside a mounted host, so
 * a plugin's panels in the panel registry are unavailable while the plugin's own
 * activation predicate is false (a dormant plugin's panels are not placed or
 * listed). Called once by the bootstrap; without it only disabling counts.
 */
// What a plugin's activation predicate reads that core cannot watch -- its own results,
// say -- changes when the plugin says so. A placed panel re-asks its predicate then.
let _activationVersion = 0;
const _activationListeners = new Set<() => void>();

/** Tell core that what a plugin's activation predicate reads has changed (1.23.0), so a
 *  panel waiting on it ("Nothing to show here yet") asks again. Cheap; call it freely. */
export function notifyPluginActivationChanged(): void {
  _activationVersion += 1;
  for (const cb of [..._activationListeners]) cb();
}

export function subscribePluginActivation(cb: () => void): () => void {
  _activationListeners.add(cb);
  return () => _activationListeners.delete(cb);
}

export const pluginActivationVersion = (): number => _activationVersion;

export function setPluginContextFactory(factory: ((pluginId: string) => AdaPluginContext) | null): void {
  _contextFactory = factory;
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
  for (const id of _registry.keys()) removePanelsOwnedBy(id);
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
  opts: { includePlacedElsewhere?: boolean } = {},
): Array<{ pluginId: string; panel: PanelSlot }> {
  const out: Array<{ pluginId: string; panel: PanelSlot; order: number }> = [];
  for (const p of _registry.values()) {
    const pctx: AdaPluginContext = { ...ctx, pluginId: p.id };
    if (!isActive(p, pctx)) continue;
    for (const panel of p.panels) {
      if (panel.region !== region) continue;
      // Shown by the layout in a group or window of its own, not in its region
      // (its top-bar button stays: `includePlacedElsewhere`).
      if (!opts.includePlacedElsewhere && isPlacedElsewhere(panel.id)) continue;
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

/** `connection-builder` panels (1.26.0): a plugin's tabs in the connection builder panel,
 * filtered by activation exactly like `getPanelsForRegion` and ordered by
 * `(asTab.order ?? order ?? 0, id)`. Core appends these after the builder's own tab. Every
 * panel of the region is a tab; one without `asTab` is labelled by its `title`, else its id.
 * Empty when no plugin has one (the panel then has no tab strip, as before the region). */
export function getConnectionBuilderTabs(ctx: AdaPluginContext): SimulationTabEntry[] {
  const entries = getPanelsForRegion("connection-builder", ctx).map(({ pluginId, panel }) => ({
    pluginId,
    panel,
    asTab: panel.asTab ?? { label: panel.title ?? panel.id, order: panel.order },
  }));
  entries.sort(
    (a, b) =>
      (a.asTab.order ?? a.panel.order ?? 0) - (b.asTab.order ?? b.panel.order ?? 0) ||
      a.panel.id.localeCompare(b.panel.id),
  );
  return entries;
}

/** One plugin-contributed section of the Review tab. */
export interface JointReviewSectionEntry {
  pluginId: string;
  panel: PanelSlot;
  /** What heads the section: the panel's `title`, else its id. */
  title: string;
}

/** `joint-review` panels (1.28.0): a plugin's sections of the Connections panel's Review tab,
 * filtered by activation exactly like `getPanelsForRegion` and ordered by `(order ?? 0, id)`.
 * Core renders these after its own sections (the produced and the identified joints). Empty when
 * no plugin has an active one (the tab then shows core's sections only, as before the region). */
export function getJointReviewSections(ctx: AdaPluginContext): JointReviewSectionEntry[] {
  return getPanelsForRegion("joint-review", ctx).map(({ pluginId, panel }) => ({
    pluginId,
    panel,
    title: panel.title ?? panel.asTab?.label ?? panel.id,
  }));
}

/** Whether a plugin that is not disabled has a `connection-builder` panel at all. Asked where
 * there is no plugin context to ask the activation predicates with (a panel's `available`): the
 * connections panel is offered where the builder is, and wherever a plugin has a tab for it. */
export function hasConnectionBuilderTabs(): boolean {
  for (const p of _registry.values()) {
    if (!p.disabled && p.panels.some((panel) => panel.region === "connection-builder")) return true;
  }
  return false;
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

/**
 * Does ANY enabled plugin claim it can render this key?
 *
 * CONTEXT-FREE, and that is the whole reason it exists next to
 * `getRenderableFileProviders` rather than being expressed through it. The
 * caller is core's file-kind predicate: it runs per file over a whole listing,
 * during render, in modules (`@/utils/scene/fileKinds`) that are deliberately
 * free of stores and scene state. Building an `AdaPluginContext` there — which
 * reaches into the mounted viewer's runtime — to answer a question about a
 * string would invert that dependency for no gain.
 *
 * So `activationPredicate` is NOT consulted here, for the same reason
 * `getPluginModes` does not consult it: what a plugin CAN render is a property
 * of the plugin, not of the current scene. A file that appears and disappears
 * from the storage list as the scene changes underneath it would be a worse
 * failure than offering one file whose provider then declines. `open` runs the
 * real, context-filtered resolution.
 *
 * A throwing `claims` disables its plugin for the session (failure isolation,
 * Decision 4) and is treated as "does not claim" — a predicate that cannot
 * answer must not make every file in the scope unopenable.
 */
export function isRenderableByPlugin(key: string): boolean {
  for (const p of _registry.values()) {
    if (p.disabled) continue;
    for (const provider of p.renderableFileProviders) {
      try {
        if (provider.claims(key)) return true;
      } catch (err) {
        disablePlugin(p.id, `renderable provider "${provider.id}" claims() threw: ${String(err)}`);
        break;
      }
    }
  }
  return false;
}

/** Renderable-file providers across active plugins, ordered by `(order ?? 0,
 * id)`. Unlike `isRenderableByPlugin` this DOES honour activation predicates:
 * there is a context by the time a file is actually being opened, and a plugin
 * that says it is inactive should not be handed work. */
export function getRenderableFileProviders(
  ctx: AdaPluginContext,
): Array<{ pluginId: string; provider: RenderableFileProvider }> {
  const out: Array<{ pluginId: string; provider: RenderableFileProvider; order: number }> = [];
  for (const p of _registry.values()) {
    const pctx: AdaPluginContext = { ...ctx, pluginId: p.id };
    if (!isActive(p, pctx)) continue;
    for (const provider of p.renderableFileProviders) {
      out.push({ pluginId: p.id, provider, order: provider.order ?? 0 });
    }
  }
  out.sort((a, b) => a.order - b.order || a.provider.id.localeCompare(b.provider.id));
  return out.map(({ pluginId, provider }) => ({ pluginId, provider }));
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

/**
 * The storage asset types of the active plugins that the scope has, ordered by `(order ?? 0, id)`.
 * Activation predicates are honoured, and each type's own `available`. A throwing `available`
 * disables its plugin for the session, as every other predicate of this registry does.
 */
export function getStorageAssetTypes(
  ctx: AdaPluginContext,
  args: StorageAssetTypeArgs,
): Array<{ pluginId: string; type: StorageAssetType }> {
  const out: Array<{ pluginId: string; type: StorageAssetType; order: number }> = [];
  for (const p of _registry.values()) {
    const pctx: AdaPluginContext = { ...ctx, pluginId: p.id };
    if (!isActive(p, pctx)) continue;
    for (const type of p.storageAssetTypes) {
      try {
        if (type.available && !type.available(pctx, args)) continue;
      } catch (err) {
        disablePlugin(p.id, `storage asset type "${type.id}" available() threw: ${String(err)}`);
        break;
      }
      out.push({ pluginId: p.id, type, order: type.order ?? 0 });
    }
  }
  out.sort((a, b) => a.order - b.order || a.type.id.localeCompare(b.type.id));
  return out.map(({ pluginId, type }) => ({ pluginId, type }));
}

/** One capability of the simulation seam, resolved for a provider. */
export interface ResolvedSimulationUi {
  pluginId: string;
  ui: SimulationProviderUi;
}

/**
 * The plugin UI for one simulation provider: who renders its parameter form and who opens its
 * results, each resolved on its own -- one plugin may supply the form for a provider while
 * another opens every result of its domain. `null` for either means core's own (the form from
 * the JSON Schema, the artefacts as downloads).
 *
 * A provider-id match beats a domain match; then `order`, then id. Activation predicates are
 * honoured, as for the other slots a context is at hand for.
 */
export function resolveSimulationProviderUi(
  ctx: AdaPluginContext,
  provider: { id: string; domain: string },
): { parameters: ResolvedSimulationUi | null; openResult: ResolvedSimulationUi | null } {
  const matches: Array<ResolvedSimulationUi & { rank: number; order: number }> = [];
  for (const p of _registry.values()) {
    if (!isActive(p, { ...ctx, pluginId: p.id })) continue;
    for (const ui of p.simulationProviders) {
      // An entry naming both must match both; the provider id is what makes it specific.
      if (ui.providerId && ui.providerId !== provider.id) continue;
      if (ui.domain && ui.domain !== provider.domain) continue;
      matches.push({ pluginId: p.id, ui, rank: ui.providerId ? 0 : 1, order: ui.order ?? 0 });
    }
  }
  matches.sort((a, b) => a.rank - b.rank || a.order - b.order || a.ui.id.localeCompare(b.ui.id));
  const pick = (has: (ui: SimulationProviderUi) => boolean): ResolvedSimulationUi | null => {
    const m = matches.find((x) => has(x.ui));
    return m ? { pluginId: m.pluginId, ui: m.ui } : null;
  };
  return {
    parameters: pick((ui) => typeof ui.renderParameters === "function"),
    openResult: pick((ui) => typeof ui.openResult === "function"),
  };
}

// Re-export the range checker so tooling / tests can reuse the exact grammar.
export { versionSatisfies as _versionSatisfies };
