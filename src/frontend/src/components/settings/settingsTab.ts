// Which tab the Settings window shows. Kept outside the component so the window reopens where the
// user left it (this page load), and so a command can ask for the tab that holds a setting, whether
// the window is open or not.

import type {SettingsSectionId} from "./settingsSections";

let current: SettingsSectionId | null = null;
const listeners = new Set<() => void>();

/** The tab last open or asked for; null before either. */
export function settingsTab(): SettingsSectionId | null {
    return current;
}

/** Show this tab: now if the Settings window is open, and when it next opens. */
export function showSettingsTab(id: SettingsSectionId): void {
    current = id;
    listeners.forEach((listener) => listener());
}

/** Called when another tab is asked for. Returns the unsubscribe. */
export function onSettingsTab(listener: () => void): () => void {
    listeners.add(listener);
    return () => {
        listeners.delete(listener);
    };
}

/**
 * Who shows the Settings window. Core's own is the `core:settings` panel; a shell that draws Settings its
 * own way (another layout, its own window) registers itself here, and then the commands that open a
 * setting (`openOption`) and the Options panel's Settings button open the shell's window on that tab.
 */
export type SettingsHost = (tab: SettingsSectionId | null) => void;

let host: SettingsHost | null = null;

/** Show Settings through `h` from now on. Returns the undo (back to core's window). */
export function setSettingsHost(h: SettingsHost): () => void {
    host = h;
    return () => {
        if (host === h) host = null;
    };
}

/** The shell's Settings window, when one registered (`setSettingsHost`); null: core's own. */
export function settingsHost(): SettingsHost | null {
    return host;
}
