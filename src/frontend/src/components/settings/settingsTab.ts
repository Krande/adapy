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
