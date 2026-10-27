import {create} from "zustand";
import {persist} from "zustand/middleware";

// 3D-mouse (SpaceMouse) options and connection status.
//
// The options persist to localStorage like the other Options-panel stores; the
// connection status does not (`partialize`), since it describes this page's
// session with the device, not a preference.
//
// OFF BY DEFAULT. Off means no WebHID listener, no device open and nothing asked
// of the browser, so a user without the device sees no change at all. The
// adapter in utils/spacemouse/spaceMouseDevice follows `enabled`.

export type SpaceMouseButtonAction = "none" | "fitAll" | "fitSelection";

export type SpaceMouseConnection =
    | "off"           // support switched off in Options
    | "unsupported"   // this browser has no WebHID
    | "disconnected"  // on, but no granted device present
    | "connecting"
    | "connected"
    | "error";        // the device was found but could not be opened

export interface SpaceMouseState {
    enabled: boolean;
    /** Overall speed multiplier, 0.1..3. */
    sensitivity: number;
    /** Fraction of full deflection ignored around rest. */
    deadZone: number;
    /** Per axis, tx ty tz rx ry rz (view frame): flip its direction. */
    invert: boolean[];
    dominantAxis: boolean;
    lockRotation: boolean;
    lockPan: boolean;
    /** Use the cap's roll axis: the view turns about its own direction, off level.
     *  Off (the default) keeps the camera level; switching it off levels the view again. */
    enableRoll: boolean;
    button1: SpaceMouseButtonAction;
    button2: SpaceMouseButtonAction;

    connection: SpaceMouseConnection;
    deviceName: string | null;
    error: string | null;

    setEnabled: (v: boolean) => void;
    setSensitivity: (v: number) => void;
    setDeadZone: (v: number) => void;
    setInvert: (axis: number, v: boolean) => void;
    setDominantAxis: (v: boolean) => void;
    setLockRotation: (v: boolean) => void;
    setLockPan: (v: boolean) => void;
    setEnableRoll: (v: boolean) => void;
    setButton1: (v: SpaceMouseButtonAction) => void;
    setButton2: (v: SpaceMouseButtonAction) => void;
    setConnection: (connection: SpaceMouseConnection, deviceName?: string | null, error?: string | null) => void;
}

export const useSpaceMouseStore = create<SpaceMouseState>()(
    persist(
        (set) => ({
            enabled: false,
            sensitivity: 1,
            deadZone: 0.08,
            invert: [false, false, false, false, false, false],
            dominantAxis: false,
            lockRotation: false,
            lockPan: false,
            enableRoll: false,
            button1: "fitAll",
            button2: "fitSelection",

            connection: "off",
            deviceName: null,
            error: null,

            setEnabled: (v) => set({enabled: v}),
            setSensitivity: (v) => set({sensitivity: v}),
            setDeadZone: (v) => set({deadZone: v}),
            setInvert: (axis, v) =>
                set((s) => {
                    const invert = s.invert.slice();
                    invert[axis] = v;
                    return {invert};
                }),
            setDominantAxis: (v) => set({dominantAxis: v}),
            // The two locks together would leave nothing to move, so turning one
            // on turns the other off.
            setLockRotation: (v) => set(v ? {lockRotation: true, lockPan: false} : {lockRotation: false}),
            setLockPan: (v) => set(v ? {lockPan: true, lockRotation: false} : {lockPan: false}),
            setEnableRoll: (v) => set({enableRoll: v}),
            setButton1: (v) => set({button1: v}),
            setButton2: (v) => set({button2: v}),
            setConnection: (connection, deviceName = null, error = null) => set({connection, deviceName, error}),
        }),
        {
            name: "ada-spacemouse",
            partialize: (s) => ({
                enabled: s.enabled,
                sensitivity: s.sensitivity,
                deadZone: s.deadZone,
                invert: s.invert,
                dominantAxis: s.dominantAxis,
                lockRotation: s.lockRotation,
                lockPan: s.lockPan,
                enableRoll: s.enableRoll,
                button1: s.button1,
                button2: s.button2,
            }),
        },
    ),
);
