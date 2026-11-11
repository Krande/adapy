// Which mouse button, modifier, wheel or touch gesture does what to the camera. This is the one
// description of camera input the viewer has: the three.js controllers are configured from it, and the
// three.js-free viewport reads it directly. Input is mapped to SEMANTIC actions (orbit, pan, zoom), so a
// camera controller never needs to know about buttons or keys. No three.js and no DOM here.

export const CAMERA_ACTIONS = ["orbit", "pan", "zoom", "none"] as const;
export type CameraAction = (typeof CAMERA_ACTIONS)[number];

export const MOUSE_BUTTONS = ["left", "middle", "right"] as const;
export type MouseButton = (typeof MOUSE_BUTTONS)[number];

/** A drag with no modifier held, or with exactly this one. Ctrl also stands for Cmd on a Mac. */
export const DRAG_MODIFIERS = ["none", "shift", "ctrl", "alt"] as const;
export type DragModifier = (typeof DRAG_MODIFIERS)[number];

export const ONE_FINGER_ACTIONS = ["orbit", "pan", "none"] as const;
export type OneFingerAction = (typeof ONE_FINGER_ACTIONS)[number];
/** Two fingers always pinch to zoom; moving them together pans or orbits. */
export const TWO_FINGER_ACTIONS = ["zoom-pan", "zoom-orbit", "none"] as const;
export type TwoFingerAction = (typeof TWO_FINGER_ACTIONS)[number];

export interface CameraMapping {
    drag: Record<MouseButton, Record<DragModifier, CameraAction>>;
    wheel: {
        zoom: boolean;
        /** Rolling the wheel towards you zooms in. */
        inverted: boolean;
        /** Zoom towards the point under the cursor, not towards the centre of the view. */
        toCursor: boolean;
    };
    touch: {one: OneFingerAction; two: TwoFingerAction};
    /** Multipliers on the controller's own speeds; 1 = as shipped. */
    orbitSpeed: number;
    zoomSpeed: number;
    /** The camera glides to a stop instead of stopping dead. */
    damping: boolean;
}

export const SPEED_MIN = 0.2;
export const SPEED_MAX = 3;

const drag = (none: CameraAction, shift: CameraAction, ctrl: CameraAction, alt: CameraAction): Record<DragModifier, CameraAction> =>
    ({none, shift, ctrl, alt});

const COMMON = {
    wheel: {zoom: true, inverted: false, toCursor: false},
    touch: {one: "orbit", two: "zoom-pan"},
    orbitSpeed: 1,
    zoomSpeed: 1,
    damping: true,
} satisfies Omit<CameraMapping, "drag">;

/** Named starting points, described by what they do. `default` is how the viewer has always behaved, plus
 *  Shift / Ctrl + left to pan on both controllers (one of the two already did that). */
export const CAMERA_PRESETS = {
    default: {
        label: "Left orbits, right pans, middle zooms",
        mapping: {...COMMON, drag: {
            left: drag("orbit", "pan", "pan", "none"),
            middle: drag("zoom", "zoom", "zoom", "zoom"),
            right: drag("pan", "orbit", "orbit", "pan"),
        }},
    },
    "middle-orbit": {
        label: "Middle orbits, Shift + middle pans, left only selects",
        mapping: {...COMMON, wheel: {...COMMON.wheel, toCursor: true}, drag: {
            left: drag("none", "none", "none", "none"),
            middle: drag("orbit", "pan", "zoom", "orbit"),
            right: drag("pan", "pan", "pan", "pan"),
        }},
    },
    "right-orbit": {
        label: "Right orbits, middle pans, left only selects",
        mapping: {...COMMON, wheel: {...COMMON.wheel, toCursor: true}, drag: {
            left: drag("none", "none", "none", "none"),
            middle: drag("pan", "pan", "pan", "pan"),
            right: drag("orbit", "pan", "zoom", "orbit"),
        }},
    },
} satisfies Record<string, {label: string; mapping: CameraMapping}>;

export type CameraPresetId = keyof typeof CAMERA_PRESETS;
export const CAMERA_PRESET_IDS = Object.keys(CAMERA_PRESETS) as CameraPresetId[];
export const DEFAULT_CAMERA_MAPPING: CameraMapping = CAMERA_PRESETS.default.mapping;

/** A deep copy: presets are shared constants and a mapping is edited in place by nobody. */
export function cloneMapping(m: CameraMapping): CameraMapping {
    return {
        ...m,
        drag: {left: {...m.drag.left}, middle: {...m.drag.middle}, right: {...m.drag.right}},
        wheel: {...m.wheel},
        touch: {...m.touch},
    };
}

/** The preset a mapping equals, or "custom". */
export function presetOf(mapping: CameraMapping): CameraPresetId | "custom" {
    const key = JSON.stringify(sortedCopy(mapping));
    return CAMERA_PRESET_IDS.find((id) => JSON.stringify(sortedCopy(CAMERA_PRESETS[id].mapping)) === key) ?? "custom";
}

function sortedCopy(m: CameraMapping) {
    const row = (r: Record<DragModifier, CameraAction>) => DRAG_MODIFIERS.map((k) => r[k]);
    return [MOUSE_BUTTONS.map((b) => row(m.drag[b])), m.wheel.zoom, m.wheel.inverted, m.wheel.toCursor, m.touch.one, m.touch.two,
        m.orbitSpeed, m.zoomSpeed, m.damping];
}

export interface DragModifiers {
    shiftKey?: boolean;
    ctrlKey?: boolean;
    metaKey?: boolean;
    altKey?: boolean;
}

/** The one modifier a drag counts as held with. Shift wins over Ctrl / Cmd, which wins over Alt. */
export function dragModifier(e: DragModifiers): DragModifier {
    if (e.shiftKey) return "shift";
    if (e.ctrlKey || e.metaKey) return "ctrl";
    return e.altKey ? "alt" : "none";
}

/** DOM `MouseEvent.button` (0, 1, 2) as a button name; null for the others. */
export function mouseButtonOf(button: number): MouseButton | null {
    return MOUSE_BUTTONS[button] ?? null;
}

/** What a drag with this button and these modifiers does. */
export function dragAction(mapping: CameraMapping, button: MouseButton, modifiers: DragModifiers = {}): CameraAction {
    return mapping.drag[button][dragModifier(modifiers)];
}

/** The zoom factor of one wheel event (below 1 zooms in), or null when the wheel does not zoom. */
export function wheelZoomFactor(mapping: CameraMapping, deltaY: number, sensitivity = 0.001): number | null {
    if (!mapping.wheel.zoom || deltaY === 0) return null;
    const direction = mapping.wheel.inverted ? -1 : 1;
    return Math.exp(direction * deltaY * sensitivity * mapping.zoomSpeed);
}

const isOneOf = <T extends string>(list: readonly T[], v: unknown): v is T => typeof v === "string" && list.some((x) => x === v);
const clampSpeed = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? Math.min(SPEED_MAX, Math.max(SPEED_MIN, v)) : 1);

/** A mapping from stored or foreign data: every field that is missing or not one of the allowed values
 *  falls back to the default, so an old or hand-edited setting can never leave the camera unusable. */
export function sanitizeMapping(raw: unknown): CameraMapping {
    const out = cloneMapping(DEFAULT_CAMERA_MAPPING);
    if (!raw || typeof raw !== "object") return out;
    const r = raw as {drag?: unknown; wheel?: unknown; touch?: unknown; orbitSpeed?: unknown; zoomSpeed?: unknown; damping?: unknown};
    if (r.drag && typeof r.drag === "object") {
        for (const button of MOUSE_BUTTONS) {
            const row = (r.drag as Record<string, unknown>)[button];
            if (!row || typeof row !== "object") continue;
            for (const modifier of DRAG_MODIFIERS) {
                const action = (row as Record<string, unknown>)[modifier];
                if (isOneOf(CAMERA_ACTIONS, action)) out.drag[button][modifier] = action;
            }
        }
    }
    if (r.wheel && typeof r.wheel === "object") {
        const w = r.wheel as Record<string, unknown>;
        for (const key of ["zoom", "inverted", "toCursor"] as const) if (typeof w[key] === "boolean") out.wheel[key] = w[key];
    }
    if (r.touch && typeof r.touch === "object") {
        const t = r.touch as Record<string, unknown>;
        if (isOneOf(ONE_FINGER_ACTIONS, t.one)) out.touch.one = t.one;
        if (isOneOf(TWO_FINGER_ACTIONS, t.two)) out.touch.two = t.two;
    }
    out.orbitSpeed = clampSpeed(r.orbitSpeed);
    out.zoomSpeed = clampSpeed(r.zoomSpeed);
    if (typeof r.damping === "boolean") out.damping = r.damping;
    return out;
}
