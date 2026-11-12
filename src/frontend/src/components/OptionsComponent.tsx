import {PanelCard} from "@/components/ui/PanelFrame";
import {CollapsibleSection} from "@/components/ui/CollapsibleSection";
import React, {Suspense} from "react";
import {useIsNarrow} from "@/utils/useIsMobile";
import {runtime} from "@/runtime/config";
import {CORE_PANEL_IDS, hide, reveal} from "@/layout/panelActions";
import ShortcutsModal from "./options/ShortcutsModal";
import {UiShellSwitcher} from "@/plugins";
import {BuildLine} from "./settings/BuildLine";
import {SETTINGS_SECTIONS, type SettingsSectionId} from "./settings/settingsSections";

// REST-only controls (scope picker, signed-in row, convert and admin buttons) live
// here so they're reachable on phones and don't crowd the top bar.
// Lazy-loaded so the desktop bundle stays slim.
const RestSection = React.lazy(() => import("./options/RestSection"));

// Each section starts closed; click the header to toggle.
const Section: React.FC<{title: string; children: React.ReactNode}> = ({title, children}) => (
    <CollapsibleSection title={title} variant="caret" defaultOpen={false}>
        {children}
    </CollapsibleSection>
);

// Two layouts:
// * Mobile: full-height slide-in drawer from the left edge.
// * Desktop: an inline panel in the menu's info-box column, styled like the other info boxes.
//
// The settings themselves live in the Settings window (`core:settings`, one tab per section); the
// panel keeps the account controls, the button that opens Settings, and the shortcut keys.
/**
 * The Options panel's sections, by id. `build` is the build line; `account` is the hosted sign-in /
 * scope / convert / admin block; `interface` is the UI shell switcher (nothing unless the build
 * carries more than one shell); `settings` is the button that opens the Settings window; the rest
 * are the settings sections themselves, drawn collapsible here when a shell picks them.
 */
export type OptionsSectionId = "build" | "account" | "interface" | "settings" | "shortcuts" | SettingsSectionId;

export interface OptionsBodyProps {
    /** The sections, in this order; others are left out. Default: account, interface, settings, shortcuts. */
    sections?: readonly OptionsSectionId[];
    /** Sections left out (after `sections`). */
    hiddenSections?: readonly OptionsSectionId[];
}

const DEFAULT_SECTIONS: readonly OptionsSectionId[] = ["account", "interface", "settings", "shortcuts"];

/** Opens the Settings window. */
const SettingsButton: React.FC = () => (
    <button
        type="button"
        onClick={() => void reveal(CORE_PANEL_IDS.settings)}
        className="block w-full bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold py-1 px-2 rounded-sm"
    >
        Settings
    </button>
);

/** The Options panel's content: the account controls, Settings and the shortcut keys (or the sections a shell picks). */
export function OptionsBody({sections: picked, hiddenSections}: OptionsBodyProps) {
    const rest = runtime.isRestMode();
    // Each section's content; null where it does not apply here.
    const content: Partial<Record<OptionsSectionId, React.ReactNode>> = {
        build: <BuildLine/>,
        account: rest ? (
            <Suspense fallback={null}>
                <RestSection/>
            </Suspense>
        ) : null,
        interface: <UiShellSwitcher/>,
        settings: <SettingsButton/>,
        shortcuts: <ShortcutsModal/>,
    };
    const settingsIds = new Set<OptionsSectionId>();
    for (const s of SETTINGS_SECTIONS) {
        settingsIds.add(s.id);
        content[s.id] = s.available() ? <Section title={s.title}>{s.render()}</Section> : null;
    }
    const hidden = new Set(hiddenSections ?? []);
    const order = (picked ?? DEFAULT_SECTIONS).filter((id) => !hidden.has(id) && content[id] != null);
    // A rule only after a collapsible settings section (a shell's pick); the account block, the
    // switcher and the buttons sit together.
    const ruled = (id: OptionsSectionId) => settingsIds.has(id);
    return (
        <>
            {order.map((id, i) => (
                <React.Fragment key={id}>
                    {content[id]}
                    {ruled(id) && i < order.length - 1 && <hr className="border-gray-600"/>}
                </React.Fragment>
            ))}
        </>
    );
}

// The classic frame around `OptionsBody`, in two layouts (see above).
function OptionsComponent() {
    const isMobile = useIsNarrow();

    if (isMobile) {
        // Drawer pinned to the left edge, full visible-viewport height.
        return (
            <div className="fixed inset-y-0 left-0 z-40 w-[85vw] max-w-sm bg-gray-800 text-white text-sm shadow-xl flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-gray-700">
                    <span className="font-bold text-base">Options</span>
                    <button
                        type="button"
                        onClick={() => void hide(CORE_PANEL_IDS.options)}
                        className="text-gray-300 hover:text-white text-xl leading-none px-2"
                        aria-label="Close options"
                        title="Close"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto p-4 flex flex-col space-y-4">
                    <OptionsBody/>
                </div>
            </div>
        );
    }

    // Desktop: matches the other info boxes (Info / Group / Storage) visually.
    return (
        <PanelCard className="min-w-80 max-w-sm text-sm space-y-3 max-h-[70vh] overflow-y-auto" heading="Options">
            <OptionsBody/>
        </PanelCard>
    );
}

export default OptionsComponent;
