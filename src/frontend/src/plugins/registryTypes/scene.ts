// The plugin API's scene handle (`AdaPluginContext.scene`) and the overlays, layers, images and
// models it draws. Types only: part of the plugin API `../registry` versions and re-exports.

import type { ColorScale } from "@/utils/colorScale";
import type { ScenePickOptions, ScenePickSession } from "../scenePicks";
import type { PlaceBoxOptions, PlacedBox } from "../sceneBox";

/** Values a color-field provider returns for core to paint + legend. */
export interface SceneColorFieldResult {
  // Per-entity scalar values. A typed array (index-aligned to the mesh) or a
  // Map keyed by entity id — the same two shapes the FEA paint path accepts.
  values: Float32Array | Map<string, number>;
  range: [number, number];
  // Optional explicit colour mapper; when omitted core uses the active colormap.
  colorFor?: (v: number) => [number, number, number];
}

/** sRGB, as CSS: "#ffaa00", or [r, g, b] in 0..1. */
export type SceneColor = string | readonly [number, number, number];

/** Something core drew for a plugin; `remove` takes it out of the scene. */
export interface SceneOverlay {
  remove: () => void;
}

/** Line segments in the FEA model's coordinates (world coordinates without an FEA model). */
export interface SceneLines {
  // 6 numbers per segment: x0, y0, z0, x1, y1, z1.
  segments: Float32Array | readonly number[];
  // One colour for all, or `colors`: 3 numbers (sRGB, 0..1) per segment.
  color?: SceneColor;
  colors?: Float32Array | readonly number[];
  widthPx?: number; // default 2
  dashed?: { dash: number; gap: number }; // in model units
  opacity?: number; // 0..1; default 1
  // Drawn over the model instead of hidden behind it (a selection behind a deck is still where
  // it is). Of several, the one added last draws on top.
  throughModel?: boolean;
}

/** The outline of FEA elements' faces: the edges no two of them share, following the warp. */
export interface SceneElementOutline {
  elements: Iterable<number>; // element labels of the active FEA mesh
  color?: SceneColor; // default white
  widthPx?: number; // default 2
  dashed?: { dash: number; gap: number }; // in model units
  opacity?: number; // 0..1; default 1
  throughModel?: boolean; // as on `SceneLines`
}

/** A screen-space label pinned to a point (FEA model coordinates, as `SceneLines`). */
export interface SceneLabel {
  // Where it is pinned: a point, or `atElements`, the centre of those FEA elements' faces, which
  // moves with the warp. One of the two.
  position?: readonly [number, number, number];
  atElements?: Iterable<number>;
  // Several: a line each, the first bold.
  text: string | readonly string[];
  color?: string; // text, CSS; default dark slate
  background?: string; // CSS; none by default
  border?: string; // CSS outline around the background; none by default
  shape?: "rect" | "rounded" | "circle";
  // "center" (default) centres it on the point; "above-right" puts its lower-left corner just
  // above it, so it does not cover what it names.
  anchor?: "center" | "above-right";
}

export interface SceneLabelOptions {
  heightPx?: number; // on-screen height, CSS px; default 18
}

/** Colours for elements of the active FEA mesh, drawn over the model and following its warp. */
export interface SceneElementPaint {
  colors: ReadonlyMap<number, SceneColor>; // element label -> colour
  // Colour for elements in `only` (or every element, without `only`) that `colors` leaves out;
  // omitted / null: those are not drawn.
  neutral?: SceneColor | null;
  only?: Iterable<number>;
  // Which of the FEA model's meshes: "model" (default), its shells and lines as drawn, or
  // "beamSolids", the swept beam sections when they are shown (same element labels).
  on?: "model" | "beamSolids";
}

// ── Layers whose data changes over time (1.18.0) ──────────────────────────────
//
// Coordinates are the model's own: those of the loaded model's file (the recording's frame), z-up,
// whatever the viewer does to place the model. The three.js viewport recentres what it loads, and
// core moves these layers by the same offset, again whenever the offset changes (a model loaded
// later); the three.js-free viewport draws both where the file puts them. So a point given at a
// model coordinate is drawn on that point of the model, on either viewport. (`addLines` and the
// other 1.9.0 verbs are unchanged: the FEA model's coordinates when one is loaded, the viewport's
// world otherwise.) A handle draws in the viewport that was on screen when it was made. Arrays handed in are read during the call and not kept, so one buffer
// can be filled and handed over again every frame.

/** A row of core's Layers panel. Several things drawn with the same `id` are one row. */
export interface SceneLayerRef {
  id: string; // unique within the plugin
  label: string;
  group?: string; // rows with the same group are listed together
  // How the row starts: its switch and its slider, when the user has not left a choice for this
  // id in the browser (a stored choice wins). The first thing drawn in a row decides; default on
  // and fully opaque. This is the ROW's look, the user's to change. A spec's own `visible` and
  // `opacity` (and `setLook`) are the plugin's, a second factor: a thing is drawn when both say
  // so, at the product of the two opacities.
  visible?: boolean;
  opacity?: number; // 0..1
}

/** What changes of a drawn thing without making it again. A member left out stays as it is. */
export interface SceneLayerLook {
  visible?: boolean;
  opacity?: number; // 0..1
}

export interface SceneLineLayerSpec {
  // What it draws to begin with (either may be empty or left out): `segments`, 6 numbers each
  // (x0, y0, z0, x1, y1, z1), or `polyline`, 3 numbers per vertex of one open line.
  segments?: Float32Array | readonly number[];
  polyline?: Float32Array | readonly number[];
  color?: SceneColor; // default white
  widthPx?: number; // default 2
  opacity?: number; // 0..1; default 1
  visible?: boolean; // default true
  throughModel?: boolean; // drawn over the model instead of hidden behind it
  layer?: SceneLayerRef; // a row in the Layers panel
}

export interface SceneLineLayer extends SceneOverlay {
  // Replace what it draws with `count` segments (all of `segments` when left out). Any number of
  // them: what the viewport holds is reused while they fit.
  setSegments: (segments: Float32Array, count?: number) => void;
  // The same from the vertices of one open line: `count` vertices, 3 numbers each.
  setPolyline: (points: Float32Array, count?: number) => void;
  setLook: (look: SceneLayerLook & { color?: SceneColor }) => void;
}

export interface ScenePointLayerSpec {
  positions?: Float32Array | readonly number[]; // 3 numbers per point; may be empty or left out
  // A colour per point (sRGB 0..1, 3 numbers each) instead of `color`. Decided here: a layer made
  // without them takes none later.
  colors?: Float32Array | readonly number[];
  color?: SceneColor; // default white
  sizePx?: number; // the diameter; default 6
  opacity?: number;
  visible?: boolean;
  throughModel?: boolean;
  layer?: SceneLayerRef;
}

export interface ScenePointLayer extends SceneOverlay {
  // Replace the points with `count` of them (all of `positions` when left out), and their colours
  // when the layer was made with colours.
  setPoints: (positions: Float32Array, count?: number, colors?: Float32Array) => void;
  setLook: (look: SceneLayerLook & { color?: SceneColor }) => void;
}

/** Where an image lies: from `origin` along `u` (its rows) and `v` (across them). */
export interface SceneImageRect {
  origin: readonly [number, number, number];
  u: readonly [number, number, number]; // the whole width
  v: readonly [number, number, number]; // the whole height
}

export interface SceneImageSpec extends SceneImageRect {
  width: number; // pixels per row
  height: number; // rows
  // RGBA, 4 bytes per pixel, sRGB, straight alpha. Row 0 lies on the edge at the origin that runs
  // along `u`, column 0 on the edge at the origin that runs along `v`: pixel (column i, row j) covers
  // origin + (i..i+1)/width*u + (j..j+1)/height*v. With the origin at the rectangle's least x and y,
  // `u` along +x and `v` along +y, that is row 0 on the min-y edge and column 0 on the min-x edge.
  // Data stored top row first: give the top-left corner as origin and `v` pointing down.
  pixels: Uint8Array | Uint8ClampedArray;
  opacity?: number; // times each pixel's alpha; default 1
  visible?: boolean;
  throughModel?: boolean;
  // Each pixel a flat square, not blended into its neighbours: for a grid of cells.
  nearest?: boolean;
  layer?: SceneLayerRef;
}

export interface SceneImage extends SceneOverlay {
  // New pixels, written in place when the size is the one it has; `width` and `height` change it.
  setPixels: (pixels: Uint8Array | Uint8ClampedArray, width?: number, height?: number) => void;
  setRect: (rect: SceneImageRect) => void;
  setLook: (look: SceneLayerLook) => void;
  // Why the viewport on screen does not draw it at the size it has now, in words for the user;
  // null when it is drawn.
  unavailable: () => string | null;
}

/** A GLB to load as the scene's model. One of `bytes` and `url`. */
export interface SceneModelSource {
  bytes?: Uint8Array | ArrayBuffer;
  url?: string;
  // The name the model is loaded under: its identity, and what `unloadModel` takes.
  sourceName: string;
  // What the model is called to a person: its root row in the Scene tree. Left out, the model's
  // own root name, else `sourceName`.
  displayName?: string;
  // "replace" (default) shows it instead of what is loaded; "overlay" beside it.
  placement?: "replace" | "overlay";
  // The up axis of the file's own coordinates; "z" (default) or "y" (the glTF convention). Read by
  // the three.js viewport; the three.js-free one draws the file as it is.
  sourceUpAxis?: "z" | "y";
  // (1.20.0) The model's nodes are placed by the plugin (`poseModel`) instead of by an animation
  // of its own. Needed for `poseModel` to find its nodes on the three.js-free viewport, which
  // otherwise merges them; an animation the file has is then not played there.
  posable?: boolean;
  // (1.25.0) Leave the camera where it is instead of framing the model (auto-fit), on both
  // viewports: for the same thing loaded again, so the view the user has is kept.
  keepCamera?: boolean;
}

/** (1.20.0) The named nodes of a loaded model, placed by the plugin. See `SceneHandle.poseModel`. */
export interface SceneModelPose {
  // Put each node where its matrix says. `matrices` holds sixteen numbers per name, in the order
  // the names were given: the node's place in the model's own coordinates (those of its file),
  // column-major as in glTF. What hangs on a node in the file moves with it. A matrix of zeros
  // hides the node. The last placement is kept and applied again to a model that is loaded under
  // the same name later, or when the viewport is switched. The array is read during the call
  // and not kept.
  place: (matrices: Float32Array) => void;
  // Per name: whether the model on screen has a node of that name that can be placed. Null while
  // no such model is on screen.
  found: () => readonly boolean[] | null;
  // Stop placing. The model stays as it was last placed.
  remove: () => void;
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
  /** @deprecated A raw three.js object draws only as the renderer in use happens to treat it; use
   *  `addLines` / `addLabels` / `paintFeaElements`, or ask core for the verb you need. */
  add: (owner: string, obj: unknown) => void;
  // Removes `obj`, or everything `owner` added (by `add` and the overlay verbs below).
  remove: (owner: string, obj?: unknown) => void;
  requestRender: () => void;
  // Route a named color field through core's paint + legend path
  // (applyField / colorLegendStore). Core owns the single active-field arbiter.
  paintField: (fieldId: string, result: SceneColorFieldResult) => void;
  // The active FEA mesh (a custom-batch mesh with per-element ``drawRanges``), or
  // null when no FEA model is loaded. Lets a result plugin drive element-level
  // scene ops (isolate / highlight / attach overlays) off the same mesh core
  // deforms. Structural ``unknown`` — no three import in this dependency-free core.
  /** @deprecated Reaches into the FEA mesh's internals; use `paintFeaElements`,
   *  `isolateFeaElements`, `hideFeaElements` and the selection verbs. */
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
  //
  // `displayName` is what the model is called to a person -- its root row in the Scene tree.
  // Defaults to the model's own root name, else to `sourceName`; the tree can be switched to show
  // the unique `sourceName` instead.
  loadModelFromUrl: (
    owner: string,
    url: string,
    opts?: {
      sourceName?: string;
      displayName?: string;
      headers?: Record<string, string>;
      translate?: boolean;
      sourceUpAxis?: "z" | "y";
    },
  ) => Promise<void>;
  // (1.18.0) Load a GLB from bytes or a URL as the scene's model, through core's own loader: on
  // whichever viewport is on screen, where `loadModelFromUrl` builds a three.js scene only. A model
  // with node animation becomes a track of the playback clock. Resolves when the viewport has it.
  loadModel: (owner: string, source: SceneModelSource) => Promise<void>;
  // (1.20.0) The nodes called `names` (as the file names them) of the model loaded as
  // `sourceName`, for the plugin to place: a rig that follows something still going on. Load the
  // model with `posable`. The model need not be loaded yet; `scene.remove(owner)` stops placing.
  poseModel: (owner: string, sourceName: string, names: readonly string[]) => SceneModelPose;
  // Remove a model previously loaded through `loadModelFromUrl` (or any source
  // registered under that name), disposing its GPU resources. No-op when the
  // name is not loaded.
  unloadModel: (sourceName: string) => void;
  // Empty the scene before showing something of the plugin's own (a replay): every loaded model,
  // and what core drew over them (the connection builder's member axes and weld labels, a clash
  // run's markers, an FEA session), as "clear the scene" does. Overlays plugins added with `add` /
  // `addLines` stay theirs to remove. Additive and optional: an older core has no `clearScene`, so
  // ask for it before calling it.
  clearScene?: () => Promise<void>;

  // ── Renderer-neutral overlays (1.9.0). Core builds and owns the scene objects; they draw
  // the same under three.js and the WebGPU renderer, move and clip with the model, and go
  // with `remove(owner)`. Coordinates are the FEA model's own when one is loaded. ──
  addLines: (owner: string, lines: SceneLines) => SceneOverlay;
  addLabels: (owner: string, labels: SceneLabel[], opts?: SceneLabelOptions) => SceneOverlay;
  // Whether an FEA model is loaded (the frame the coordinates above are in).
  hasFeaModel: () => boolean;
  // Null when no FEA model is loaded (or, `on: "beamSolids"`, its beam solids are not shown).
  paintFeaElements: (owner: string, paint: SceneElementPaint) => SceneOverlay | null;
  // Null when no FEA model is loaded or none of `elements` is in it.
  addElementOutlines: (owner: string, outline: SceneElementOutline) => SceneOverlay | null;
  // Show only these elements of the active FEA model; null shows all again.
  isolateFeaElements: (elements: Iterable<number> | null) => void;
  hideFeaElements: (elements: Iterable<number>) => void;
  hiddenFeaElements: () => number[];
  setFeaElementEdgesVisible: (visible: boolean) => void;
  // Show `scale` (see `ColorScale`, `scaleColor` on @/viewer-core) in core's colour legend,
  // in place of the result colormap, while the plugin paints with it; null takes it away.
  // `remove(owner)` takes it away too.
  showColorScale: (owner: string, scale: ColorScale | null) => void;

  // ── Layers whose data changes over time (1.18.0): see `SceneLineLayerSpec` and the types
  // beside it. Each handle replaces its data in place; `remove(owner)` takes them all away. ──
  addLineLayer: (owner: string, spec?: SceneLineLayerSpec) => SceneLineLayer;
  addPointLayer: (owner: string, spec?: ScenePointLayerSpec) => ScenePointLayer;
  addImage: (owner: string, image: SceneImageSpec) => SceneImage;
  // Why the viewport on screen cannot draw an image of this size; null when it can.
  imageUnavailable: (width: number, height: number) => string | null;

  // ── View (1.10.0): what a UI shell's modes and searches ask of the view, without the camera,
  // the scene or the FEA mesh's morph weights. ──
  // `false` holds the FEA model at its undeformed shape (beam solids and overlays with it) until
  // this owner says `true`, whatever the user's warp settings; `remove(owner)` lets go too. The
  // user's settings are left alone and apply again once no owner holds it.
  setFeaDeformationShown: (owner: string, shown: boolean) => void;
  // (1.11.0) The colour of the FEA model (and its beam solids) wherever nothing paints it: its own
  // base colour, so a result, a property or a colour field still draws over it as before. null
  // gives the model its own colour back; `remove(owner)` does too. The latest ask wins.
  setFeaUnpaintedColor: (owner: string, color: SceneColor | null) => void;
  // Frame the camera on elements of the FEA model where they are drawn now; false when none is in it.
  frameFeaElements: (elements: Iterable<number>) => boolean;
  // Frame the camera on a world-space box; false before the viewer has a camera.
  frameBox: (box: SceneBox) => boolean;
  // Frame everything loaded (the toolbar's zoom-to-all).
  frameAll: () => boolean;
  // The loaded CAD models' faces, by source face id then occurrence (empty without face regions).
  findFaces: () => Promise<SceneFace[]>;
  // Highlight `face`, name it in the info box and frame it (unless `frame: false`); null clears
  // the highlight. False when its model is no longer loaded.
  focusFace: (face: SceneFace | null, opts?: { frame?: boolean }) => boolean;
  // (1.26.0) Take the scene's clicks on objects until `end()`: each is handed to `onPick` instead of
  // selecting what was clicked (return false to let one through). For a tool that asks the user to
  // point at objects. Works on either viewport; `remove(owner)` ends it too.
  pickObjects: (owner: string, options: ScenePickOptions) => ScenePickSession;
  // (1.26.0) Put a simple box in the scene with a transform gizmo on it until `end()`: the user moves
  // and turns it with the cell builder's gizmo keys (G, R, X / Y / Z, Escape / Enter), and each change
  // is handed to `onChange` as the box's pose in the scene's own coordinates. `set` moves it from the
  // plugin's side. For a tool that asks where something stands. Works on either viewport;
  // `remove(owner)` ends it too.
  placeBox: (owner: string, options: PlaceBoxOptions) => PlacedBox;
}

/** An axis-aligned world-space box. */
export interface SceneBox {
  min: [number, number, number];
  max: [number, number, number];
}

/** A face of a loaded CAD model: its source face id and occurrence. Pass it back to `focusFace` as
 *  it came from `findFaces`; the rest of it says where it is drawn and is not for reading. */
export interface SceneFace {
  faceId: number;
  seq: number;
  model: string;
  start: number;
  length: number;
}
