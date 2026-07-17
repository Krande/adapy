import { create } from 'zustand';

// `GroupInfo` keeps its semantic name — it still describes a single group.
// The store name moved to `SceneInfo*` because the panel that consumes it
// now spans both Stats and Groups sub-sections; groups are one part of the
// scene metadata, not the whole panel.
export interface GroupInfo {
  name: string;
  description?: string;
  members?: string[];
  type: 'design' | 'simulation';
  fe_object_type?: 'node' | 'element';
  parent_name: string;
  /** Storage key of the loaded file this group came from. Disambiguates
   * groups across multi-model overlays (shown in the picker, and scopes
   * the member-mesh lookup to the owning model's scene group). Absent
   * for streaming-FEA groups pushed by load_fea_streaming — there's
   * only ever one streaming model in the scene. */
  source?: string;
}

// One kwarg in a utility's input form (mirrors the worker @utility spec).
export interface UtilityKwarg {
  // 'ref' renders a published-build (branch/commit) picker in the frontend.
  name: string;
  type: 'string' | 'int' | 'float' | 'bool' | 'enum' | 'ref';
  default?: string | number | boolean | null;
  description?: string;
  enum?: string[];
}

// A worker-advertised utility (from GET /api/config["utilities"]).
export interface UtilitySpec {
  name: string;
  description: string;
  kwargs: UtilityKwarg[];
  inputs: string[];
  affects: string[];
  returns: string;
}

// Result summary shown in the panel after a utility run.
export interface UtilityResult {
  legend?: { label: string; color: string; count?: number }[];
  summary?: Record<string, unknown>;
}

export type SceneInfoMode = 'info' | 'source' | 'utilities' | 'section' | 'fem' | 'mesh';

interface SceneInfoState {
  show_scene_info_box: boolean;
  mode: SceneInfoMode;
  selectedGroup: GroupInfo | null;
  availableGroups: GroupInfo[];
  // Utilities panel state
  utilities: UtilitySpec[];
  selectedUtility: string | null;
  utilityKwargs: Record<string, string | number | boolean | null>;
  running: boolean;
  lastResult: UtilityResult | null;
  setShowSceneInfoBox: (show: boolean) => void;
  setMode: (mode: SceneInfoMode) => void;
  setSelectedGroup: (group: GroupInfo | null) => void;
  setAvailableGroups: (groups: GroupInfo[]) => void;
  setUtilities: (u: UtilitySpec[]) => void;
  setSelectedUtility: (name: string | null) => void;
  setUtilityKwargs: (kw: Record<string, string | number | boolean | null>) => void;
  setRunning: (r: boolean) => void;
  setLastResult: (r: UtilityResult | null) => void;
  toggle: () => void;
}

export const useSceneInfoStore = create<SceneInfoState>((set, get) => ({
  show_scene_info_box: false,
  mode: 'info',
  selectedGroup: null,
  availableGroups: [],
  utilities: [],
  selectedUtility: null,
  utilityKwargs: {},
  running: false,
  lastResult: null,
  setShowSceneInfoBox: (show) => set({ show_scene_info_box: show }),
  setMode: (mode) => set({ mode }),
  setSelectedGroup: (group) => set({ selectedGroup: group }),
  setAvailableGroups: (groups) => set({ availableGroups: groups }),
  setUtilities: (utilities) => set({ utilities }),
  setSelectedUtility: (selectedUtility) => set({ selectedUtility }),
  setUtilityKwargs: (utilityKwargs) => set({ utilityKwargs }),
  setRunning: (running) => set({ running }),
  setLastResult: (lastResult) => set({ lastResult }),
  toggle: () => set((state) => ({ show_scene_info_box: !state.show_scene_info_box })),
}));
