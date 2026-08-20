import {create} from "zustand";
import {useSectionStore} from "@/state/sectionStore";

// Section / clip tools: the state that says whether they are showing, and the actions
// they run.
//
// They live on the RIGHT of the mode toolbar rather than replacing it, and the rail's
// section button toggles them. That arrangement says the true thing: clipping is a
// second activity you layer ON TOP of whatever mode you are in — you clip a model you are
// inspecting, or one you are building — so the mode's own tools must not go away while
// you do it. Replacing the row would have implied the mode had changed, which it has not.
//
// Everything here delegates to sectionStore, which the Scene panel's Clip tab also
// drives. The toolbar is a second entry point, never a second implementation.

interface SectionToolsState {
    /** Are the section tools showing in the mode toolbar? */
    shown: boolean;
    setShown: (shown: boolean) => void;
    toggle: () => void;
}

export const useSectionTools = create<SectionToolsState>((set, get) => ({
    shown: false,
    setShown: (shown) => set({shown}),
    toggle: () => {
        const next = !get().shown;
        set({shown: next});
        // Putting the tools away also puts the drag gizmo away. Leaving a manipulator on
        // screen after its toolbar is gone strands a control with no visible owner —
        // you can still drag the plane and nothing explains what you are dragging.
        if (!next) useSectionStore.getState().setGizmoVisible(false);
        else useSectionStore.getState().setGizmoVisible(true);
    },
}));

export const addPlane = (axis: "x" | "y" | "z") => () => useSectionStore.getState().addPlane(axis);

/** Flip the plane the gizmo is attached to. */
export function flipActivePlane(): void {
    const s = useSectionStore.getState();
    if (s.activeId) s.flip(s.activeId);
}

export function toggleGizmo(): void {
    const s = useSectionStore.getState();
    s.setGizmoVisible(!s.gizmoVisible);
}

export function clearPlanes(): void {
    useSectionStore.getState().clearAll();
}

/** Null when usable, else why the control is greyed. */
export function needsPlane(): string | null {
    return useSectionStore.getState().planes.length > 0 ? null : "No section plane yet";
}

/** Null when usable, else why. Flip needs a plane the gizmo is actually on. */
export function needsActivePlane(): string | null {
    const s = useSectionStore.getState();
    if (s.planes.length === 0) return "No section plane yet";
    return s.activeId ? null : "No plane selected";
}

export const gizmoShown = () => useSectionStore.getState().gizmoVisible;
