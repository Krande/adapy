// The joints of a loaded model drawn with overlays of their own (Connections > Review, "Joint
// overlays"): a weld's "Show joint" in the Selected Object panel, or a produced joint's row, adds
// its joint here, and only these joints are annotated in 3D -- each with its own switches (member
// axes, weld labels, symbol, name and throat, show welds, weld colour), so turning one joint's
// labels on does not label every joint of the model. Connections > Overlays' switches stay the
// joint being developed's (`ada dev connections`); a joint added here starts from them.
//
// A joint is its connection's name in the loaded source it was found in. A source that leaves the
// scene takes its joints with it. Pure state: `utils/scene/jointFocusOverlay.ts` draws them. For the
// page only.

import { create } from "zustand";

import { useConnectionBuilderStore, type ConnectionOverlays } from "./connectionBuilderStore";
import { useModelState } from "./modelState";

export interface FocusedJoint {
  /** The loaded source whose GLB lists the joint's connection. */
  file: string;
  /** The connection's name (an ada.Connection part). */
  name: string;
  /** How this joint, and only this joint, is drawn. */
  overlays: ConnectionOverlays;
}

interface JointFocusState {
  /** Newest first. */
  joints: FocusedJoint[];
  /** Draw the joint `name` of `file` with overlays of its own (the global switches to start); one
   *  already drawn moves to the top and keeps its switches. */
  focus: (file: string, name: string) => void;
  setOverlay: <K extends keyof ConnectionOverlays>(file: string, name: string, which: K, value: ConnectionOverlays[K]) => void;
  remove: (file: string, name: string) => void;
  clear: () => void;
}

const same = (j: FocusedJoint, file: string, name: string) => j.file === file && j.name === name;

export const useJointFocusStore = create<JointFocusState>((set) => ({
  joints: [],
  focus: (file, name) =>
    set((s) => {
      const had = s.joints.find((j) => same(j, file, name));
      const joint = had ?? { file, name, overlays: { ...useConnectionBuilderStore.getState().overlays } };
      return { joints: [joint, ...s.joints.filter((j) => j !== had)] };
    }),
  setOverlay: (file, name, which, value) =>
    set((s) => ({
      joints: s.joints.map((j) => (same(j, file, name) ? { ...j, overlays: { ...j.overlays, [which]: value } } : j)),
    })),
  remove: (file, name) => set((s) => ({ joints: s.joints.filter((j) => !same(j, file, name)) })),
  clear: () => set({ joints: [] }),
}));

/** Whether the joint `name` of `file` is drawn with overlays of its own. */
export function isFocusedJoint(joints: readonly FocusedJoint[], file: string | null | undefined, name: string): boolean {
  return !!file && joints.some((j) => same(j, file, name));
}

// A source that leaves the scene, by whichever route, takes its joints with it.
useModelState.subscribe((s, prev) => {
  if (s.loadedSourceNames === prev.loadedSourceNames) return;
  const { joints } = useJointFocusStore.getState();
  const kept = joints.filter((j) => !prev.loadedSourceNames.has(j.file) || s.loadedSourceNames.has(j.file));
  if (kept.length !== joints.length) useJointFocusStore.setState({ joints: kept });
});
