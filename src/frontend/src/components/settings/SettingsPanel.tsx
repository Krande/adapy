// The Settings window: every viewer setting, one tab per section, as the Admin panel lays out its
// tabs. Opened from the Options panel's "Settings" button (or Ctrl+K "Settings"); the Options panel
// itself keeps only the account controls, this button and the shortcut keys.

import React, {useState} from "react";

import {BuildLine} from "./BuildLine";
import {availableSettingsSections, type SettingsSectionId} from "./settingsSections";

/** The tab last open, so reopening the window lands where the user left it (this page load). */
let lastTab: SettingsSectionId | null = null;

export interface SettingsBodyProps {
    /** Tab to open on; default the one last open, else the first. */
    initialTab?: SettingsSectionId;
}

export const SettingsBody: React.FC<SettingsBodyProps> = ({initialTab}) => {
    const sections = availableSettingsSections();
    const pick = (id: SettingsSectionId | null | undefined) =>
        sections.find((s) => s.id === id)?.id ?? sections[0]?.id ?? null;
    const [tab, setTab] = useState<SettingsSectionId | null>(() => pick(initialTab ?? lastTab));
    const active = sections.find((s) => s.id === tab) ?? null;
    const select = (id: SettingsSectionId) => {
        lastTab = id;
        setTab(id);
    };

    return (
        <div className="h-full flex flex-col bg-gray-900 text-white overflow-hidden">
            <header className="flex items-center gap-2 border-b border-gray-800 px-3 py-2 sm:px-4 shrink-0">
                <div role="tablist" aria-label="Settings" className="flex-1 min-w-0 overflow-x-auto flex gap-1 text-sm">
                    {sections.map((s) => (
                        <button
                            key={s.id}
                            type="button"
                            role="tab"
                            aria-selected={s.id === tab}
                            className={
                                "px-3 py-2 rounded-sm text-sm whitespace-nowrap " +
                                (s.id === tab ? "bg-gray-700 text-white" : "text-gray-300 hover:bg-gray-800")
                            }
                            onClick={() => select(s.id)}
                        >
                            {s.title}
                        </button>
                    ))}
                </div>
            </header>
            <main role="tabpanel" aria-label={active?.title} className="flex-1 min-h-0 overflow-y-auto">
                <div className="max-w-2xl p-4 text-sm space-y-4">{active?.render()}</div>
            </main>
            <footer className="shrink-0 border-t border-gray-800 px-4 py-2">
                <BuildLine className="text-xs text-gray-400"/>
            </footer>
        </div>
    );
};

export default SettingsBody;
