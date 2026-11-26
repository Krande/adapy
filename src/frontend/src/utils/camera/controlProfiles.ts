// Controls profiles: a named set of the camera mapping (cameraMapping.ts) and, optionally, the keyboard
// shortcuts (the shortcut overrides the Shortcut Keys dialog edits, `LayoutPrefs.keyOverrides`). Choosing a
// profile applies both; a profile without keys leaves the keys as they are. Two are built in -- the
// viewer's own controls and DNV GeniE's -- and the user makes, edits, renames, deletes, exports and
// imports the others. No three.js and no DOM here; the store is state/controlsProfileStore.ts.

import {CAMERA_PRESETS, DEFAULT_CAMERA_MAPPING, cloneMapping, sanitizeMapping, type CameraMapping} from "./cameraMapping";

/** Shortcut id -> its chord (`"Alt+S"`), or null for no key: the same shape as `LayoutPrefs.keyOverrides`. */
export type KeyOverrides = Record<string, string | null>;

export interface ControlsProfile {
    id: string;
    name: string;
    mapping: CameraMapping;
    /** The keyboard shortcuts the profile sets: every shortcut not named here is on its default. Absent:
     *  the profile does not touch the keys. */
    keys?: KeyOverrides;
    /** Shipped with the viewer: cannot be renamed, edited or deleted (a copy can). */
    builtIn?: boolean;
}

export const DEFAULT_PROFILE_ID = "default";
export const GENIE_PROFILE_ID = "genie";

/**
 * DNV GeniE's keys, where the viewer has the same command (GeniE V9.3 User Documentation, 2.2.18
 * Customize Keyboard Shortcuts, its table of predefined shortcuts; 3.3 View Manipulations; 2.2.13 Working
 * with Visible Model). GeniE's "View from Y / Z" looks along -Y / -Z: the viewer's back and top views. Not
 * here, because a browser does not hand the key to the page, the viewer keeps it, or the viewer has no
 * such command: View from X (F6 moves the focus between the viewer's areas; the right view keeps Alt + 6),
 * Isometric View (F5 reloads the page), Camera Back / Forward (Alt + arrows go back and forward in the
 * browser's history), Rotate / Zoom / Pan as tools (F2 / F3 / F4: the right button does all three) and
 * Spin Model (F10). Add selection (Alt + Plus) is the viewer's Show selected, and Show complement
 * (Alt + Q, `core:show-complement`) is listed explicitly below even though it is also every profile's
 * BASE default (`coreShortcutTable.ts`) -- GeniE never had a key free for it before. Undo (Ctrl + Z) and
 * Redo (Ctrl + Y) are the viewer's keys already.
 */
export const GENIE_KEYS: Readonly<KeyOverrides> = {
    "core:fit-all": "F9",
    "core:fit-selection": "Alt+Z",
    "core:view.back": "F7",
    "core:view.top": "F8",
    "core:hide-selected": "Alt+-",
    "core:hide-others": "Alt+S",
    "core:show-all": "Alt+A",
    "core:show-selected": "Alt+Plus",
    "core:show-complement": "Alt+Q",
};

export const BUILT_IN_PROFILES: readonly ControlsProfile[] = [
    {id: DEFAULT_PROFILE_ID, name: "Default", mapping: cloneMapping(DEFAULT_CAMERA_MAPPING), keys: {}, builtIn: true},
    {id: GENIE_PROFILE_ID, name: "GeniE", mapping: cloneMapping(CAMERA_PRESETS.genie.mapping), keys: {...GENIE_KEYS}, builtIn: true},
];

export const PROFILE_NAME_MAX = 60;
/** What an exported file says it is. */
export const PROFILE_FILE_FORMAT = "ada-controls-profiles";
export const PROFILE_FILE_VERSION = 1;

/** A name as a profile keeps it: trimmed, one line, not too long; null when nothing is left. */
export function cleanProfileName(raw: unknown): string | null {
    if (typeof raw !== "string") return null;
    const name = raw.replace(/\s+/g, " ").trim().slice(0, PROFILE_NAME_MAX);
    return name || null;
}

/** `name`, or `name (2)`, `name (3)` ... : the first that none of `taken` has (compared ignoring case). */
export function uniqueProfileName(name: string, taken: Iterable<string>): string {
    const used = new Set([...taken].map((n) => n.toLowerCase()));
    if (!used.has(name.toLowerCase())) return name;
    for (let i = 2; ; i++) {
        const next = `${name} (${i})`;
        if (!used.has(next.toLowerCase())) return next;
    }
}

/** Key overrides from stored or foreign data: only string ids with a chord or null survive. */
export function sanitizeKeys(raw: unknown): KeyOverrides | undefined {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return undefined;
    const out: KeyOverrides = {};
    for (const [id, chord] of Object.entries(raw as Record<string, unknown>)) {
        if (!id.trim()) continue;
        if (chord === null) out[id] = null;
        else if (typeof chord === "string" && chord.trim()) out[id] = chord.trim();
    }
    return out;
}

/** A profile from stored or foreign data, or null when it has no usable name. Its mapping is repaired as a
 *  stored mapping is; an unknown id gets `fallbackId`. */
export function sanitizeProfile(raw: unknown, fallbackId: string): ControlsProfile | null {
    if (!raw || typeof raw !== "object") return null;
    const r = raw as {id?: unknown; name?: unknown; mapping?: unknown; keys?: unknown};
    const name = cleanProfileName(r.name);
    if (!name) return null;
    const id = typeof r.id === "string" && r.id.trim() ? r.id : fallbackId;
    const keys = sanitizeKeys(r.keys);
    return {id, name, mapping: sanitizeMapping(r.mapping), ...(keys ? {keys} : {})};
}

/** The exported file of `profiles`: their names, mappings and keys, not their ids. */
export function exportProfiles(profiles: readonly ControlsProfile[]): string {
    const body = {
        format: PROFILE_FILE_FORMAT,
        version: PROFILE_FILE_VERSION,
        profiles: profiles.map((p) => ({name: p.name, mapping: p.mapping, ...(p.keys ? {keys: p.keys} : {})})),
    };
    return JSON.stringify(body, null, 2);
}

/** The profiles in an exported file (or a single exported profile), with new ids from `newId`. Throws an
 *  Error whose message says what is wrong with the file. */
export function importProfiles(text: string, newId: () => string): ControlsProfile[] {
    let data: unknown;
    try {
        data = JSON.parse(text);
    } catch {
        throw new Error("The file is not JSON.");
    }
    const list = Array.isArray((data as {profiles?: unknown})?.profiles)
        ? ((data as {profiles: unknown[]}).profiles)
        : data && typeof data === "object" && "mapping" in (data as object)
          ? [data]
          : null;
    if (!list) throw new Error(`The file holds no controls profiles (expected "${PROFILE_FILE_FORMAT}").`);
    const out = list.map((p) => sanitizeProfile({...(p as object), id: undefined}, newId())).filter((p): p is ControlsProfile => p !== null);
    if (!out.length) throw new Error("No profile in the file has a name.");
    return out;
}

const sameKeys = (a: KeyOverrides, b: KeyOverrides): boolean => {
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every((k) => k in b && a[k] === b[k]);
};

/** Whether the settings in force are the profile's: its mapping, and its keys when it has any. */
export function profileMatches(profile: ControlsProfile, mapping: CameraMapping, keys: KeyOverrides | undefined): boolean {
    if (JSON.stringify(sanitizeMapping(profile.mapping)) !== JSON.stringify(sanitizeMapping(mapping))) return false;
    return profile.keys === undefined || sameKeys(profile.keys, keys ?? {});
}

