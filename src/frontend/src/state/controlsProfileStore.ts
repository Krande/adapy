// The controls profiles (utils/camera/controlProfiles.ts): the user's own, and which profile is chosen,
// kept across reloads. The settings a profile holds stay where they always were -- the camera mapping in
// the camera controls store, the keys in the layout prefs -- so everything that reads them is unchanged:
// choosing a profile writes them there. While one of the user's own profiles is chosen, changing the
// camera controls (or, when the profile has keys, a shortcut) changes the profile: that is how a profile is
// edited. A built-in profile never changes; edited, it shows as modified, to be saved as a new profile.
import {create} from "zustand";
import {persist} from "zustand/middleware";

import {layoutStore} from "@/layout/layoutStore";
import {useCameraControlsStore} from "@/state/cameraControlsStore";
import {cloneMapping} from "@/utils/camera/cameraMapping";
import {
    BUILT_IN_PROFILES, DEFAULT_PROFILE_ID, cleanProfileName, importProfiles, sanitizeProfile, uniqueProfileName,
    type ControlsProfile, type KeyOverrides,
} from "@/utils/camera/controlProfiles";

interface ControlsProfileState {
    /** The user's own profiles, in the order made. */
    custom: ControlsProfile[];
    /** The profile chosen last: a built-in's id or one of `custom`'s. */
    activeId: string;
    /** Choose a profile: its mapping and (when it has them) its keys take effect. */
    choose: (id: string) => void;
    /** A new profile from the settings in force, chosen. `withKeys`: it holds the keys too. Returns its id. */
    createFromCurrent: (name: string, withKeys: boolean) => string;
    /** Rename one of the user's profiles. Returns why not, or null. */
    rename: (id: string, name: string) => string | null;
    /** Delete one of the user's profiles. Deleting the chosen one chooses Default (and applies it). */
    remove: (id: string) => void;
    /** Add the profiles of an exported file. Returns how many; throws with what is wrong with the file. */
    importText: (text: string) => number;
}

const newId = (): string => `p-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

/** Every profile: the built-in ones first. */
export function allProfiles(custom: readonly ControlsProfile[] = useControlsProfileStore.getState().custom): ControlsProfile[] {
    return [...BUILT_IN_PROFILES, ...custom];
}

/** The profile with this id, or null. */
export function profileById(id: string, custom?: readonly ControlsProfile[]): ControlsProfile | null {
    return allProfiles(custom).find((p) => p.id === id) ?? null;
}

/** The keys in force now (the shortcut overrides). */
export function currentKeys(): KeyOverrides {
    return {...(layoutStore.getState().prefs.keyOverrides ?? {})};
}

/** Write a profile's settings where they are read from. */
function applyProfile(p: ControlsProfile): void {
    useCameraControlsStore.getState().setMapping(cloneMapping(p.mapping));
    if (p.keys !== undefined) layoutStore.getState().replaceKeyOverrides(p.keys);
}

export const useControlsProfileStore = create<ControlsProfileState>()(
    persist(
        (set, get) => ({
            custom: [],
            activeId: DEFAULT_PROFILE_ID,
            choose: (id) => {
                const p = profileById(id, get().custom);
                if (!p) return;
                set({activeId: id});
                applyProfile(p);
            },
            createFromCurrent: (name, withKeys) => {
                const taken = allProfiles(get().custom).map((p) => p.name);
                const clean = uniqueProfileName(cleanProfileName(name) ?? "My controls", taken);
                const profile: ControlsProfile = {
                    id: newId(),
                    name: clean,
                    mapping: cloneMapping(useCameraControlsStore.getState().mapping),
                    ...(withKeys ? {keys: currentKeys()} : {}),
                };
                set((s) => ({custom: [...s.custom, profile], activeId: profile.id}));
                return profile.id;
            },
            rename: (id, name) => {
                const clean = cleanProfileName(name);
                if (!clean) return "A profile needs a name.";
                const custom = get().custom;
                if (!custom.some((p) => p.id === id)) return "Only your own profiles can be renamed.";
                if (allProfiles(custom).some((p) => p.id !== id && p.name.toLowerCase() === clean.toLowerCase())) return `There is already a profile called "${clean}".`;
                set({custom: custom.map((p) => (p.id === id ? {...p, name: clean} : p))});
                return null;
            },
            remove: (id) => {
                const custom = get().custom;
                if (!custom.some((p) => p.id === id)) return;
                set({custom: custom.filter((p) => p.id !== id)});
                if (get().activeId === id) get().choose(DEFAULT_PROFILE_ID);
            },
            importText: (text) => {
                const incoming = importProfiles(text, newId);
                const taken = allProfiles(get().custom).map((p) => p.name);
                const added = incoming.map((p) => {
                    const name = uniqueProfileName(p.name, taken);
                    taken.push(name);
                    return {...p, name};
                });
                set((s) => ({custom: [...s.custom, ...added]}));
                return added.length;
            },
        }),
        {
            name: "ada-controls-profiles",
            version: 1,
            partialize: (s) => ({custom: s.custom, activeId: s.activeId}),
            merge: (persisted, current) => {
                const p = (persisted ?? {}) as {custom?: unknown; activeId?: unknown};
                const custom = Array.isArray(p.custom)
                    ? p.custom.map((x) => sanitizeProfile(x, newId())).filter((x): x is ControlsProfile => x !== null)
                    : [];
                const activeId = typeof p.activeId === "string" && profileById(p.activeId, custom) ? p.activeId : DEFAULT_PROFILE_ID;
                return {...current, custom, activeId};
            },
        },
    ),
);

// Editing: while one of the user's own profiles is chosen, it follows the settings.
function follow(change: (p: ControlsProfile) => ControlsProfile | null): void {
    const {custom, activeId} = useControlsProfileStore.getState();
    const i = custom.findIndex((p) => p.id === activeId);
    if (i < 0) return;
    const next = change(custom[i]);
    if (!next) return;
    useControlsProfileStore.setState({custom: custom.map((p, k) => (k === i ? next : p))});
}
useCameraControlsStore.subscribe((s, prev) => {
    if (s.mapping !== prev.mapping) follow((p) => ({...p, mapping: cloneMapping(s.mapping)}));
});
layoutStore.subscribe((s, prev) => {
    if (s.prefs.keyOverrides !== prev.prefs.keyOverrides) follow((p) => (p.keys === undefined ? null : {...p, keys: {...(s.prefs.keyOverrides ?? {})}}));
});
