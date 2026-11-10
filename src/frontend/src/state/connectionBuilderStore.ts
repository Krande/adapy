// State of the connection builder (`ada dev connections`, the Scene panel's Connection tab): the served
// library's latest state, the selected spec, the per-spec member inputs, and
// the latest build. Pure state -- the session in
// services/devConnections/connectionBuilderSession.ts fetches, builds and
// loads the result into the scene.
//
// Inputs are kept per spec, and in localStorage per library, so switching
// specs or reloading the page keeps what was typed.

import { create } from "zustand";

import type {
  ConnectionInputs,
  DevBuildResult,
  DevConnectionsState,
} from "@/services/api/devConnections";

export type ConnectionBuildState =
  | { status: "idle" }
  | { status: "building"; specName: string }
  | { status: "done"; result: DevBuildResult }
  | {
      status: "error";
      specName: string;
      error: string;
      traceback: string | null;
      /** The builder raised NotApplicable: it refused these members (not a crash). */
      declined: boolean;
    };

type ConnectionBuilderState = {
  /** The library's latest state, from the event stream. Null until the first. */
  library: DevConnectionsState | null;
  setLibrary: (s: DevConnectionsState) => void;
  /** False while the event stream is down (the viewer stopped or restarts). */
  connected: boolean;
  setConnected: (v: boolean) => void;

  selectedSpec: string | null;
  selectSpec: (name: string | null) => void;

  /** What the user changed, per spec: role -> field -> value. Unset fields take the spec's defaults. */
  inputsBySpec: Record<string, ConnectionInputs>;
  setRoleField: (spec: string, role: string, field: string, value: unknown) => void;
  resetInputs: (spec: string) => void;

  build: ConnectionBuildState;
  setBuild: (b: ConnectionBuildState) => void;
  /** The latest SUCCESSFUL build: its joint (clash, members, welds) stays listed while the next
   *  one builds or fails, until a build of another spec replaces it. */
  lastResult: DevBuildResult | null;
};

const STORAGE_PREFIX = "ada-connection-builder:";

function storageKey(library: DevConnectionsState | null): string | null {
  return library ? STORAGE_PREFIX + library.library.config : null;
}

function loadSaved(key: string | null): { selectedSpec: string | null; inputsBySpec: Record<string, ConnectionInputs> } {
  const empty = { selectedSpec: null, inputsBySpec: {} };
  if (!key) return empty;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return empty;
    const parsed = JSON.parse(raw) as Partial<typeof empty>;
    return { selectedSpec: parsed.selectedSpec ?? null, inputsBySpec: parsed.inputsBySpec ?? {} };
  } catch {
    return empty;
  }
}

function save(s: ConnectionBuilderState): void {
  const key = storageKey(s.library);
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify({ selectedSpec: s.selectedSpec, inputsBySpec: s.inputsBySpec }));
  } catch {
    // Storage full or blocked: the inputs just do not outlive the page.
  }
}

export const useConnectionBuilderStore = create<ConnectionBuilderState>((set, get) => ({
  library: null,
  setLibrary: (library) => {
    const first = get().library === null;
    set({ library });
    // The first state names the library: restore what was typed for it last time.
    if (first) set(loadSaved(storageKey(library)));
  },
  connected: false,
  setConnected: (connected) => set({ connected }),

  selectedSpec: null,
  selectSpec: (selectedSpec) => {
    set({ selectedSpec });
    save(get());
  },

  inputsBySpec: {},
  setRoleField: (spec, role, field, value) => {
    set((s) => {
      const specInputs = s.inputsBySpec[spec] ?? {};
      return {
        inputsBySpec: {
          ...s.inputsBySpec,
          [spec]: { ...specInputs, [role]: { ...(specInputs[role] ?? {}), [field]: value } },
        },
      };
    });
    save(get());
  },
  resetInputs: (spec) => {
    set((s) => {
      const next = { ...s.inputsBySpec };
      delete next[spec];
      return { inputsBySpec: next };
    });
    save(get());
  },

  build: { status: "idle" },
  setBuild: (build) => set(build.status === "done" ? { build, lastResult: build.result } : { build }),
  lastResult: null,
}));

/** The inputs a build of `spec` sends: the spec's defaults, overridden by what the user changed. */
export function effectiveInputs(
  defaults: ConnectionInputs,
  changed: ConnectionInputs | undefined,
): ConnectionInputs {
  const out: ConnectionInputs = {};
  for (const role of new Set([...Object.keys(defaults), ...Object.keys(changed ?? {})])) {
    out[role] = { ...(defaults[role] ?? {}), ...(changed?.[role] ?? {}) };
  }
  return out;
}
