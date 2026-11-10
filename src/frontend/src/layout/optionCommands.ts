// Every setting of the Options panel as a command, from ONE declarative list.
//
// A user searching the palette (Ctrl+K) for "spacemouse" or "antialias" finds the setting and can
// change it without opening Options. Each descriptor registers `core:option.<id>`:
//
//   toggle   a checked item: running it flips the setting
//   choice   one command per value, `core:option.<id>.<value>` ("Theme: Dark"), checked on the current one
//   open     a free-form value (a slider, a number): the command opens Options and focuses it
//
// `setters` names the store setters a descriptor stands for, and `EXISTING_COVERAGE` names the
// settings core already had a command for; the audit test (`__tests__/layout/optionCommands.test.ts`)
// reads every Options component and fails when it uses a setter that neither lists, so a new
// option cannot ship without a command. Keywords let the palette find a setting by what a person
// calls it ("3dconnexion", "msaa", "dpr").

import {useCameraControlsStore} from "@/state/cameraControlsStore";
import {useComputeStore, COMPUTE_MODES, type ComputeMode} from "@/state/computeStore";
import {CAMERA_ACTIONS, CAMERA_PRESETS, CAMERA_PRESET_IDS, MOUSE_BUTTONS, ONE_FINGER_ACTIONS, TWO_FINGER_ACTIONS, presetOf} from "@/utils/camera/cameraMapping";
import {useExperimentalStore} from "@/state/experimentalStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useMeStore} from "@/state/meStore";
import {useModelState} from "@/state/modelState";
import {useOptionsStore} from "@/state/optionsStore";
import {usePerfStore, requestRender} from "@/state/perfStore";
import {useSceneRestoreStore} from "@/state/sceneRestoreStore";
import {useSpaceMouseStore, type SpaceMouseButtonAction} from "@/state/spaceMouseStore";
import {useThemeStore} from "@/state/themeStore";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {feaControlActions} from "@/utils/scene/fea/feaControls";
import {COLORMAP_NAMES} from "@/utils/scene/fea/colormaps";
import {isSpaceMouseSupported, isWebHidSupported, requestSpaceMouse} from "@/utils/spacemouse/spaceMouseDevice";

import {registerCommand, type CommandSpec} from "./commands";
import {CORE_COMMAND_IDS as ID} from "./coreCommandIds";
import {customisablePanels, customisablePanelsRequired, setCustomisablePanels} from "./customisable";
import {CORE_PANEL_IDS, reveal as revealPanel} from "./panelActions";

/** The id of the command for option `id`: `core:option.<id>`; a choice's values add `.<value>`. */
export const optionCommandId = (id: string, value?: string): string => `core:option.${id}${value === undefined ? "" : `.${value}`}`;

interface Base {
  /** Unique within the list; the command id is `core:option.<id>`. */
  id: string;
  title: string;
  keywords?: readonly string[];
  /** The palette group. */
  group: string;
  /** The store setters (by name) this descriptor stands for: what the audit test looks for. */
  setters: readonly string[];
  when?: () => boolean;
  disabledReason?: () => string | null;
}

export interface ToggleOption extends Base {
  kind: "toggle";
  get(): boolean;
  set(on: boolean): void;
}

export interface ChoiceOption extends Base {
  kind: "choice";
  choices: readonly {value: string; label: string}[];
  get(): string;
  set(value: string): void;
}

export interface OpenOption extends Base {
  kind: "open";
  /** The `data-option` attribute of the control the command focuses in Options. */
  focus: string;
}

export type OptionDescriptor = ToggleOption | ChoiceOption | OpenOption;

const opts = () => useOptionsStore.getState();
const perf = () => usePerfStore.getState();
const sm = () => useSpaceMouseStore.getState();

const needWebHid = () => (isWebHidSupported() ? null : "Needs WebHID: use Chrome, Edge or another Chromium-based browser");
// The option itself also works over the Gamepad API; only the explicit device chooser needs WebHID.
const needSpaceMouse = () => (isSpaceMouseSupported() ? null : "Needs WebHID or the Gamepad API: use Chrome or Edge");
const GEN = ["options", "settings", "preferences"] as const;

/** A perf-store switch. `invert`: the setting is the opposite of what the store holds ("Disable antialias"). */
function perfToggle(id: string, title: string, key: keyof ReturnType<typeof perf>, setter: string, keywords: readonly string[] = []): ToggleOption {
  return {
    kind: "toggle", id, title, keywords: [...keywords, "performance", ...GEN], group: "Performance", setters: [setter],
    get: () => !!perf()[key],
    set: (on) => {
      (perf() as unknown as Record<string, (v: boolean) => void>)[setter](on);
      requestRender();
    },
  };
}

const AXES = ["Pan X", "Pan Y", "Zoom", "Tilt", "Spin", "Roll"] as const;
const BUTTON_CHOICES: {value: SpaceMouseButtonAction; label: string}[] = [
  {value: "fitAll", label: "Fit all"},
  {value: "fitSelection", label: "Fit selection"},
  {value: "none", label: "Nothing"},
];
const CAM_KEYS = ["camera", "mouse", "orbit", "pan", "zoom", "rotate", "navigation", "controls"] as const;
const cam = () => useCameraControlsStore.getState();
const SM_KEYS = ["3d mouse", "3dconnexion", "spacemouse", "space mouse", "6dof", "navigation", "hid", "gamepad", "joystick"] as const;

/** Every Options setting, as data. */
export function optionDescriptors(): OptionDescriptor[] {
  const list: OptionDescriptor[] = [
    // --- Scene config / display ---
    {kind: "toggle", id: "mesh-stats", title: "Mesh stats in Properties", group: "Display", keywords: ["tessellation", "triangles", ...GEN],
      setters: ["setShowMeshStats"], get: () => opts().showMeshStats, set: (v) => opts().setShowMeshStats(v)},
    {kind: "toggle", id: "hide-tessellation-edges", title: "Hide tessellation lines", group: "Display", keywords: ["edges", "triangulation", "wireframe", ...GEN],
      setters: ["setHideTessellationEdges"], get: () => opts().hideTessellationEdges,
      set: (v) => {
        opts().setHideTessellationEdges(v);
        void import("@/utils/scene/refreshEdgeOverlays").then((m) => m.refreshEdgeOverlays());
      }},
    {kind: "toggle", id: "auto-convert", title: "Auto-convert uploads to GLB", group: "Display", keywords: ["upload", "conversion", ...GEN],
      setters: ["setAutoConvertOnUpload"], get: () => opts().autoConvertOnUpload, set: (v) => opts().setAutoConvertOnUpload(v)},
    {kind: "toggle", id: "auto-fit", title: "Auto fit to view", group: "Display", keywords: ["zoom to all", "camera", "frame", ...GEN],
      setters: ["setAutoFit"], get: () => opts().autoFit, set: (v) => opts().setAutoFit(v)},
    {kind: "toggle", id: "lock-translation", title: "Lock translation", group: "Display", keywords: ["move", "offset", "camera", ...GEN],
      setters: ["setLockTranslation"], get: () => opts().lockTranslation, set: (v) => opts().setLockTranslation(v)},
    {kind: "toggle", id: "websocket", title: "Enable websocket", group: "Display", keywords: ["connection", "server", "live", ...GEN],
      setters: ["setEnableWebsocket"], get: () => opts().enableWebsocket, set: (v) => opts().setEnableWebsocket(v)},
    {kind: "toggle", id: "z-is-up", title: "Z is up", group: "Display", keywords: ["axis", "orientation", "y up", "camera", ...GEN],
      setters: ["setZIsUp"], get: () => useModelState.getState().zIsUp, set: (v) => useModelState.getState().setZIsUp(v)},
    {kind: "toggle", id: "default-orbit", title: "Use default orbit controller", group: "Display", keywords: ["camera", "controls", "rotate", ...GEN],
      setters: ["setDefaultOrbitController"], get: () => useModelState.getState().defaultOrbitController,
      set: (v) => useModelState.getState().setDefaultOrbitController(v)},
    {kind: "toggle", id: "point-size-absolute", title: "Absolute point size (world units)", group: "Display", keywords: ["points", "cloud", ...GEN],
      setters: ["setPointSizeAbsolute"], get: () => opts().pointSizeAbsolute, set: (v) => opts().setPointSizeAbsolute(v)},
    {kind: "open", id: "point-size", title: "Point size…", group: "Display", keywords: ["points", "cloud", "slider", ...GEN],
      setters: ["setPointSize"], focus: "point-size"},
    {kind: "toggle", id: "face-picking", title: "Face-level picking", group: "Display", keywords: ["pick", "select", "face", "step", "ifc", ...GEN],
      setters: ["setFaceLevelPicking"], get: () => opts().faceLevelPicking, set: (v) => opts().setFaceLevelPicking(v),
      disabledReason: () => (opts().faceRegionsAvailable ? null : "The loaded model has no per-face regions")},
    // --- Session ---
    {kind: "toggle", id: "scene-restore", title: "Restore the scene on reload", group: "Session", keywords: ["session", "reopen", "remember", ...GEN],
      setters: ["setEnabled"], get: () => useSceneRestoreStore.getState().enabled, set: (v) => useSceneRestoreStore.getState().setEnabled(v)},
    // --- Theme ---
    {kind: "open", id: "panel-opacity", title: "Panel opacity…", group: "Theme", keywords: ["transparency", "slider", "panels", ...GEN],
      setters: ["setBgOpacity"], focus: "panel-opacity"},
    {kind: "open", id: "panel-colour", title: "Panel colour…", group: "Theme", keywords: ["color", "background", "panels", "custom", ...GEN],
      setters: ["setCustomBg"], focus: "panel-colour"},
    {kind: "open", id: "text-colour", title: "Panel text colour…", group: "Theme", keywords: ["color", "foreground", "panels", "custom", ...GEN],
      setters: ["setCustomText"], focus: "text-colour"},
    // --- Performance ---
    {kind: "choice", id: "renderer", title: "Renderer", group: "Performance", keywords: ["webgl", "webgpu", "backend", "three", "adacpp", ...GEN],
      setters: ["setRenderBackend"], choices: [{value: "three", label: "three.js (WebGL)"}, {value: "adacpp-webgpu", label: "adacpp WebGPU (experimental)"},
        {value: "adacpp-native", label: "adacpp WebGPU, no three.js (experimental)"}],
      get: () => perf().renderBackend, set: (v) => {
        perf().setRenderBackend(v as ReturnType<typeof perf>["renderBackend"]);
        requestRender();
      }},
    perfToggle("cluster-frustum-cull", "Cluster frustum culling", "adacppFrustumCull", "setAdacppFrustumCull", ["webgpu", "adacpp"]),
    perfToggle("cluster-cone-cull", "Cluster back-face cone culling", "adacppConeCull", "setAdacppConeCull", ["webgpu", "adacpp"]),
    perfToggle("occlusion-cull", "Occlusion culling (GPU)", "adacppOcclusionCull", "setAdacppOcclusionCull", ["webgpu", "adacpp"]),
    perfToggle("webgpu-labels", "WebGPU: labels and markers drawn by adacpp", "adacppLabels", "setAdacppLabels", ["webgpu", "adacpp", "sprites", "icons"]),
    perfToggle("webgpu-point-pick", "WebGPU: point picking through adacpp", "adacppPointPick", "setAdacppPointPick", ["webgpu", "adacpp", "points", "pick"]),
    perfToggle("webgpu-capture", "WebGPU: screenshots and animation export from the WebGPU image", "adacppCapture", "setAdacppCapture", ["webgpu", "adacpp", "screenshot", "export"]),
    perfToggle("webgpu-no-webgl", "WebGPU only: no WebGL renderer (reload required)", "adacppNoWebGL", "setAdacppNoWebGL", ["webgpu", "adacpp", "webgl"]),
    {kind: "choice", id: "material", title: "Material", group: "Performance", keywords: ["pbr", "lambert", "standard", "shading", ...GEN],
      setters: ["setMaterialMode"], choices: [{value: "standard", label: "MeshStandard (PBR)"}, {value: "lambert", label: "MeshLambert (cheap)"}],
      get: () => perf().materialMode, set: (v) => perf().setMaterialMode(v as ReturnType<typeof perf>["materialMode"])},
    perfToggle("beam-solids-backface-cull", "Backface-cull beam solids", "solidsBackfaceCull", "setSolidsBackfaceCull", ["fea"]),
    perfToggle("beam-solids-smooth", "Smooth-shade beam solids", "solidsSmoothShading", "setSolidsSmoothShading", ["fea"]),
    perfToggle("shadow-map-off", "Disable shadow map", "disableShadowMap", "setDisableShadowMap", ["shadows"]),
    {...perfToggle("antialias", "Antialiasing (MSAA)", "antialias", "setAntialias", ["msaa", "smoothing", "edges", "reload"]), title: "Antialiasing (MSAA, reload to apply)"},
    {kind: "open", id: "pixel-ratio", title: "Pixel ratio cap…", group: "Performance", keywords: ["dpr", "resolution", "hidpi", "slider", ...GEN],
      setters: ["setPixelRatioCap"], focus: "pixel-ratio"},
    perfToggle("adaptive-dpr", "Adaptive DPR while orbiting", "adaptivePixelRatio", "setAdaptivePixelRatio", ["dpr", "resolution"]),
    perfToggle("on-demand-render", "On-demand render", "onDemandRender", "setOnDemandRender", ["idle", "battery", "redraw"]),
    perfToggle("time-sliced-load", "Time-sliced (non-blocking) load", "timeSlicedLoad", "setTimeSlicedLoad", ["loading", "responsive"]),
    perfToggle("skip-beam-solids", "Skip beam-solid load", "hideBeamSolids", "setHideBeamSolids", ["fea", "faster"]),
    perfToggle("flat-picker", "Flat-varying GPU picker", "useFlatPicker", "setUseFlatPicker", ["pick", "select"]),
    perfToggle("gpu-face-picking", "GPU face picking", "gpuFacePicking", "setGpuFacePicking", ["pick", "select"]),
    perfToggle("skip-element-edges", "Skip element-edge wireframe", "hideElementEdges", "setHideElementEdges", ["fea", "faster"]),
    // --- Admin metrics ---
    ...(["collectLoadMetrics", "profileCalls", "collectRenderMetrics"] as const).map((key): ToggleOption => {
      const label = {collectLoadMetrics: "Record model-load metrics", profileCalls: "Profile calls during load", collectRenderMetrics: "Record render metrics"}[key];
      const setter = `set${key[0].toUpperCase()}${key.slice(1)}`;
      return {
        kind: "toggle", id: `metrics-${key}`, title: label, group: "Performance", keywords: ["admin", "metrics", "profiling", "fps", ...GEN],
        setters: [setter], when: () => useMeStore.getState().isAdmin,
        get: () => useViewMetricsStore.getState()[key],
        set: (v) => (useViewMetricsStore.getState() as unknown as Record<string, (b: boolean) => void>)[setter](v),
      };
    }),
    // --- 3D mouse ---
    // --- Camera controls ---
    {kind: "choice", id: "camera-preset", title: "Camera controls", group: "Camera controls", keywords: [...CAM_KEYS, "preset"],
      setters: ["setCameraPreset"], choices: CAMERA_PRESET_IDS.map((id) => ({value: id, label: CAMERA_PRESETS[id].label})),
      get: () => presetOf(cam().mapping), set: (v) => { const id = CAMERA_PRESET_IDS.find((x) => x === v); if (id) cam().setCameraPreset(id); }},
    ...MOUSE_BUTTONS.map((button): ChoiceOption => ({
      kind: "choice", id: `camera-drag-${button}`, title: `Camera: ${button} drag`, group: "Camera controls", keywords: [...CAM_KEYS, "button", "drag"],
      setters: ["setDragAction"], choices: CAMERA_ACTIONS.map((a) => ({value: a, label: a === "none" ? "Nothing" : a[0].toUpperCase() + a.slice(1)})),
      get: () => cam().mapping.drag[button].none,
      set: (v) => { const a = CAMERA_ACTIONS.find((x) => x === v); if (a) cam().setDragAction(button, "none", a); },
    })),
    {kind: "open", id: "camera-drag-modifiers", title: "Camera: drags with Shift, Ctrl or Alt…", group: "Camera controls", keywords: [...CAM_KEYS, "modifier"],
      setters: ["setDragAction"], focus: "camera-drag"},
    {kind: "toggle", id: "camera-wheel-zoom", title: "Camera: wheel zooms", group: "Camera controls", keywords: [...CAM_KEYS, "scroll"],
      setters: ["setWheelZoom"], get: () => cam().mapping.wheel.zoom, set: (v) => cam().setWheelZoom(v)},
    {kind: "toggle", id: "camera-wheel-inverted", title: "Camera: reverse the wheel's direction", group: "Camera controls", keywords: [...CAM_KEYS, "scroll", "invert"],
      setters: ["setWheelInverted"], get: () => cam().mapping.wheel.inverted, set: (v) => cam().setWheelInverted(v)},
    {kind: "toggle", id: "camera-wheel-to-cursor", title: "Camera: zoom towards the cursor", group: "Camera controls", keywords: [...CAM_KEYS, "scroll", "dolly"],
      setters: ["setWheelToCursor"], get: () => cam().mapping.wheel.toCursor, set: (v) => cam().setWheelToCursor(v)},
    {kind: "toggle", id: "camera-damping", title: "Camera: glide to a stop", group: "Camera controls", keywords: [...CAM_KEYS, "damping", "inertia", "smooth"],
      setters: ["setCameraDamping"], get: () => cam().mapping.damping, set: (v) => cam().setCameraDamping(v)},
    {kind: "choice", id: "camera-touch-one", title: "Camera: one finger", group: "Camera controls", keywords: [...CAM_KEYS, "touch", "gesture"],
      setters: ["setOneFingerAction"], choices: ONE_FINGER_ACTIONS.map((a) => ({value: a, label: a === "none" ? "Nothing" : a[0].toUpperCase() + a.slice(1)})),
      get: () => cam().mapping.touch.one, set: (v) => { const a = ONE_FINGER_ACTIONS.find((x) => x === v); if (a) cam().setOneFingerAction(a); }},
    {kind: "choice", id: "camera-touch-two", title: "Camera: two fingers", group: "Camera controls", keywords: [...CAM_KEYS, "touch", "gesture", "pinch"],
      setters: ["setTwoFingerAction"], choices: [{value: "zoom-pan", label: "Pinch zooms, move pans"}, {value: "zoom-orbit", label: "Pinch zooms, move orbits"}, {value: "none", label: "Nothing"}],
      get: () => cam().mapping.touch.two, set: (v) => { const a = TWO_FINGER_ACTIONS.find((x) => x === v); if (a) cam().setTwoFingerAction(a); }},
    {kind: "open", id: "camera-orbit-speed", title: "Camera: orbit speed…", group: "Camera controls", keywords: [...CAM_KEYS, "sensitivity", "slider"],
      setters: ["setOrbitSpeed"], focus: "camera-orbit-speed"},
    {kind: "open", id: "camera-zoom-speed", title: "Camera: zoom speed…", group: "Camera controls", keywords: [...CAM_KEYS, "sensitivity", "slider"],
      setters: ["setZoomSpeed"], focus: "camera-zoom-speed"},
    {kind: "toggle", id: "spacemouse", title: "3D mouse (SpaceMouse)", group: "3D mouse", keywords: [...SM_KEYS, "enable", ...GEN],
      setters: ["setEnabled"], disabledReason: needSpaceMouse,
      get: () => sm().enabled && isSpaceMouseSupported(),
      set: (on) => {
        sm().setEnabled(on);
        // Turning it on asks the browser for the device, here and now: the chooser needs the user's key press
        // or click, so nothing is awaited and the palette closes after this has run. Cancelling the chooser changes nothing.
        if (on && sm().connection !== "connected") void requestSpaceMouse();
      }},
    {kind: "toggle", id: "spacemouse-dominant", title: "3D mouse: dominant axis only", group: "3D mouse", keywords: SM_KEYS, setters: ["setDominantAxis"],
      get: () => sm().dominantAxis, set: (v) => sm().setDominantAxis(v)},
    {kind: "toggle", id: "spacemouse-lock-rotation", title: "3D mouse: lock rotation (pan and zoom only)", group: "3D mouse", keywords: SM_KEYS, setters: ["setLockRotation"],
      get: () => sm().lockRotation, set: (v) => sm().setLockRotation(v)},
    {kind: "toggle", id: "spacemouse-lock-pan", title: "3D mouse: lock pan and zoom (rotate only)", group: "3D mouse", keywords: SM_KEYS, setters: ["setLockPan"],
      get: () => sm().lockPan, set: (v) => sm().setLockPan(v)},
    {kind: "toggle", id: "spacemouse-roll", title: "3D mouse: enable roll (all six axes)", group: "3D mouse", keywords: SM_KEYS, setters: ["setEnableRoll"],
      get: () => sm().enableRoll, set: (v) => sm().setEnableRoll(v)},
    ...AXES.map((axis, i): ToggleOption => ({
      kind: "toggle", id: `spacemouse-invert-${axis.toLowerCase().replace(" ", "-")}`, title: `3D mouse: invert ${axis}`, group: "3D mouse", keywords: SM_KEYS,
      setters: i === 0 ? ["setInvert"] : [], get: () => !!sm().invert[i], set: (v) => sm().setInvert(i, v),
    })),
    {kind: "choice", id: "spacemouse-button1", title: "3D mouse: button 1", group: "3D mouse", keywords: SM_KEYS, setters: ["setButton1"],
      choices: BUTTON_CHOICES, get: () => sm().button1, set: (v) => sm().setButton1(v as SpaceMouseButtonAction)},
    {kind: "choice", id: "spacemouse-button2", title: "3D mouse: button 2", group: "3D mouse", keywords: SM_KEYS, setters: ["setButton2"],
      choices: BUTTON_CHOICES, get: () => sm().button2, set: (v) => sm().setButton2(v as SpaceMouseButtonAction)},
    {kind: "open", id: "spacemouse-sensitivity", title: "3D mouse: sensitivity…", group: "3D mouse", keywords: SM_KEYS, setters: ["setSensitivity"], focus: "spacemouse-sensitivity"},
    {kind: "open", id: "spacemouse-dead-zone", title: "3D mouse: dead zone…", group: "3D mouse", keywords: SM_KEYS, setters: ["setDeadZone"], focus: "spacemouse-dead-zone"},
    // --- Conversion engine ---
    {kind: "toggle", id: "pyodide-converter", title: "In-browser conversion engine (WASM)", group: "Conversion", keywords: ["pyodide", "convert", "experimental", ...GEN],
      setters: ["setPyodideConverter"], get: () => useExperimentalStore.getState().pyodideConverter, set: (v) => useExperimentalStore.getState().setPyodideConverter(v)},
    // --- Calculations ---
    {kind: "choice", id: "compute-mode", title: "Calculations", group: "Calculations", keywords: ["compute", "cores", "workers", "performance mode", ...GEN],
      setters: ["setMode"], choices: COMPUTE_MODES.map((m) => ({value: m, label: {interactive: "Interactive", balanced: "Balanced", throughput: "All cores"}[m]})),
      get: () => useComputeStore.getState().mode, set: (v) => useComputeStore.getState().setMode(v as ComputeMode)},
    {kind: "open", id: "compute-workers", title: "Calculations: worker processes…", group: "Calculations", keywords: ["compute", "cores", "threads", ...GEN],
      setters: ["setMaxWorkers"], focus: "compute-workers"},
    {kind: "open", id: "compute-memory", title: "Calculations: memory limit…", group: "Calculations", keywords: ["compute", "ram", "gb", ...GEN],
      setters: ["setMemoryLimitGb"], focus: "compute-memory"},
    // --- Layout ---
    {kind: "toggle", id: "customisable-panels", title: "Customisable panels", group: "Layout", keywords: ["float", "dock", "windows", "layout", ...GEN],
      setters: ["setCustomisablePanels"], get: () => customisablePanels(), set: (v) => setCustomisablePanels(v),
      when: () => !customisablePanelsRequired()},
  ];
  return list;
}

/** Settings core already had a command for before the list above, by the setter the Options panel calls. Read by the audit test. */
export const EXISTING_COVERAGE: Readonly<Record<string, string>> = {
  setShowPerf: ID.stats,
  setShowEdges: ID.edges,
  setGalleryEnabled: ID.gallery,
  setPreset: "core:theme.<preset>",
  setMarkingMenuOn: ID.toggleMarkingMenu,
  setUiFrozen: ID.toggleUiFreeze,
  toggleColorLegend: ID.colorLegend,
};

/** Setters the Options panel calls that are not settings: reset buttons and local UI state. */
export const NOT_SETTINGS: readonly string[] = ["reset", "resetCustom", "setSceneRestoreProgress"];

/** Open the Options panel and focus the control marked `data-option="<focus>"` (when it is on screen). */
export function openOption(focus: string): void {
  void revealPanel(CORE_PANEL_IDS.options);
  const find = () => document.querySelector<HTMLElement>(`[data-option="${focus}"]`);
  const go = () => {
    const el = find();
    if (!el) return;
    el.scrollIntoView?.({block: "center"});
    el.focus?.();
  };
  // The panel mounts after the intent; its sections open on the user's click, so a control inside a closed one is not found.
  setTimeout(go, 60);
}

/** The commands of the descriptors, as specs. */
export function optionCommandSpecs(): CommandSpec[] {
  const out: CommandSpec[] = [];
  for (const d of optionDescriptors()) {
    const common = {group: d.group, keywords: [...(d.keywords ?? []), "setting", d.id.replace(/-/g, " ")], when: d.when, disabledReason: d.disabledReason};
    if (d.kind === "toggle") {
      out.push({...common, id: optionCommandId(d.id), label: d.title, checked: () => d.get(), run: () => d.set(!d.get())});
    } else if (d.kind === "choice") {
      for (const c of d.choices) {
        out.push({...common, id: optionCommandId(d.id, c.value), label: `${d.title}: ${c.label}`, checked: () => d.get() === c.value, run: () => d.set(c.value)});
      }
    } else {
      out.push({...common, id: optionCommandId(d.id), label: d.title, run: () => openOption(d.focus)});
    }
  }
  // Commands that are not a setting's value but belong with them.
  const mouse = ["3d mouse", "3dconnexion", "spacemouse", "space mouse", "hid", "gamepad", "joystick"];
  out.push(
    {
      id: "core:option.spacemouse-connect", label: "Connect 3D mouse (SpaceMouse)…", group: "3D mouse", keywords: [...mouse, "device", "pair", "choose"],
      disabledReason: needWebHid,
      // The device chooser needs this key press or click: enable (synchronously) and ask in the same call.
      run: () => {
        if (!sm().enabled) sm().setEnabled(true);
        void requestSpaceMouse();
      },
    },
    {
      id: "core:option.compute-reset", label: "Calculations: reset to no limits", group: "Calculations", keywords: ["compute", "cores", "memory", "workers"],
      run: () => useComputeStore.getState().reset(),
    },
    {
      id: "core:option.theme-reset-custom", label: "Theme: reset custom panel colours", group: "Theme", keywords: ["color", "colour", "panels"],
      disabledReason: () => (useThemeStore.getState().customBg || useThemeStore.getState().customText ? null : "No custom colours are set"),
      run: () => useThemeStore.getState().resetCustom(),
    },
    {
      id: "core:storage-refresh", label: "Refresh the file list", group: "File", keywords: ["storage", "reload", "files", "server"],
      run: () => void import("@/utils/server_info/handlers/request_list_of_files_from_server").then((m) => m.request_list_of_files_from_server()),
    },
    {
      id: "core:clear-scene", label: "Clear scene (unload every model)", group: "File", keywords: ["remove all", "unload", "empty", "reset"],
      run: () => void import("./sceneActions").then((m) => m.sceneActions().clearScene()),
    },
    {
      id: "core:fea-nodal-average", label: "Result: nodal average", group: "Results", keywords: ["fea", "smoothing", "average"],
      checked: () => useFeaAnimationStore.getState().nodalAverage,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => feaControlActions().setNodalAverage(!useFeaAnimationStore.getState().nodalAverage),
    },
    {
      id: "core:fea-envelope", label: "Result: envelope over all steps", group: "Results", keywords: ["fea", "max", "min", "envelope"],
      checked: () => useFeaAnimationStore.getState().envelopeMode,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => feaControlActions().setEnvelope(!useFeaAnimationStore.getState().envelopeMode),
    },
    {
      id: "core:fea-beam-solid-edges", label: "Result: beam solid edges", group: "Results", keywords: ["fea", "beam", "section", "edges"],
      checked: () => useFeaAnimationStore.getState().beamSolidEdgesVisible,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => feaControlActions().setBeamSolidEdges(!useFeaAnimationStore.getState().beamSolidEdgesVisible),
    },
    {
      id: "core:fea-result-points", label: "Result: result point markers", group: "Results", keywords: ["fea", "points", "markers"],
      checked: () => useFeaAnimationStore.getState().resultPointMarkersVisible,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => feaControlActions().setResultPointMarkers(!useFeaAnimationStore.getState().resultPointMarkersVisible),
    },
  );
  for (const name of COLORMAP_NAMES) {
    out.push({
      id: `core:fea-colormap.${name}`, label: `Result colour map: ${name}`, group: "Results", keywords: ["fea", "colormap", "palette", "legend", "colour", "color"],
      checked: () => useFeaAnimationStore.getState().colormap === name,
      disabledReason: () => (useFeaAnimationStore.getState().sessionActive ? null : "No result is loaded"),
      run: () => feaControlActions().setColormap(name),
    });
  }
  return out;
}

let registered = false;

/** Register the option commands (idempotent; `registerCoreCommands` calls it). */
export function registerOptionCommands(): void {
  for (const spec of optionCommandSpecs()) registerCommand(spec, "core");
  registered = true;
}

export const optionCommandsRegistered = (): boolean => registered;
