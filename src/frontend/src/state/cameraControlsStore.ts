// The user's camera input mapping (utils/camera/cameraMapping.ts), kept across reloads. Read by the
// three.js controllers' binding (sceneHelpers/bindCameraMapping.ts) and by the three.js-free viewport, so
// a change here takes effect on whichever is on screen, without a reload. No three.js in this module.
import {create} from "zustand";
import {persist} from "zustand/middleware";
import {
    CAMERA_PRESETS, DEFAULT_CAMERA_MAPPING, SPEED_MAX, SPEED_MIN, cloneMapping, sanitizeMapping,
    type CameraAction, type CameraMapping, type CameraPresetId, type DragModifier, type MouseButton, type OneFingerAction, type TwoFingerAction,
} from "@/utils/camera/cameraMapping";

interface CameraControlsState {
    mapping: CameraMapping;
    setCameraPreset: (id: CameraPresetId) => void;
    setDragAction: (button: MouseButton, modifier: DragModifier, action: CameraAction) => void;
    setWheelZoom: (on: boolean) => void;
    setWheelInverted: (on: boolean) => void;
    setWheelToCursor: (on: boolean) => void;
    setOneFingerAction: (action: OneFingerAction) => void;
    setTwoFingerAction: (action: TwoFingerAction) => void;
    setOrbitSpeed: (v: number) => void;
    setZoomSpeed: (v: number) => void;
    setCameraDamping: (on: boolean) => void;
}

const clamp = (v: number) => Math.min(SPEED_MAX, Math.max(SPEED_MIN, v));

export const useCameraControlsStore = create<CameraControlsState>()(
    persist(
        (set) => {
            /** Every change makes a new mapping object, so subscribers can compare by identity. */
            const edit = (change: (m: CameraMapping) => void) =>
                set((s) => {
                    const mapping = cloneMapping(s.mapping);
                    change(mapping);
                    return {mapping};
                });
            return {
                mapping: cloneMapping(DEFAULT_CAMERA_MAPPING),
                setCameraPreset: (id) => set({mapping: cloneMapping(CAMERA_PRESETS[id].mapping)}),
                setDragAction: (button, modifier, action) => edit((m) => void (m.drag[button][modifier] = action)),
                setWheelZoom: (on) => edit((m) => void (m.wheel.zoom = on)),
                setWheelInverted: (on) => edit((m) => void (m.wheel.inverted = on)),
                setWheelToCursor: (on) => edit((m) => void (m.wheel.toCursor = on)),
                setOneFingerAction: (action) => edit((m) => void (m.touch.one = action)),
                setTwoFingerAction: (action) => edit((m) => void (m.touch.two = action)),
                setOrbitSpeed: (v) => edit((m) => void (m.orbitSpeed = clamp(v))),
                setZoomSpeed: (v) => edit((m) => void (m.zoomSpeed = clamp(v))),
                setCameraDamping: (on) => edit((m) => void (m.damping = on)),
            };
        },
        {
            name: "ada-camera-controls",
            version: 1,
            partialize: (s) => ({mapping: s.mapping}),
            // Whatever is stored is repaired against the current shape: a later version only has to add
            // its new fields to the defaults.
            migrate: (persisted) => ({mapping: sanitizeMapping((persisted as {mapping?: unknown} | null)?.mapping)}),
            merge: (persisted, current) => ({...current, mapping: sanitizeMapping((persisted as {mapping?: unknown} | null)?.mapping)}),
        },
    ),
);
