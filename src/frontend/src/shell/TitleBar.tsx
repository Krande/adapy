import React from "react";
import {Badge, Icon, IconButton, cn} from "@/components/ui";
import {MODES, useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {useShellPrefs} from "./shellPrefs";
import {PluginPanelRegion, PluginTopBarButtons} from "@/plugins";
import ScopePicker from "./ScopePicker";
import MenuBar from "./MenuBar";
import {Z} from "./zIndex";
import {openCommandPalette} from "./CommandPalette";
import {keysFor} from "./shortcuts";

/** Chrome for a plugin's top-bar button, in the shell's idiom rather than the classic
 *  Menu.tsx's. Plugins supply an icon and a label; the shape is core's to decide. */
const pluginNavBtnClass = (active: boolean) =>
    cn(
        "ada-focus inline-flex items-center justify-center w-control-md h-control-md rounded-sm",
        "transition-colors duration-(--ada-dur-fast)",
        active
            ? "bg-accent-subtle text-accent"
            : "text-content-muted pointer-fine:hover:text-content pointer-fine:hover:bg-surface-2",
    );

// Top bar: identity, the mode switcher, the mode's panel menu, global actions.
//
// The mode switcher is the Cinema 4D pattern — modes along the top, and a tool palette
// down the left whose contents follow the selection. Four entries, task-named, always
// in the same place, so switching becomes muscle memory rather than a hunt.
//
// A mode button NEVER activates itself. A finished FEA load puts a dot here; it does
// not move you. See the non-modality contract in modeStore.ts.

export interface TitleBarProps {
    showModeSwitcher: boolean;
}

export default function TitleBar({showModeSwitcher}: TitleBarProps) {
    const mode = useModeStore((s) => s.mode);
    const setMode = useModeStore((s) => s.setMode);
    const badges = useModeStore((s) => s.badges);

    return (
        <header
            style={{gridArea: "titlebar", zIndex: Z.dock}}
            className="flex items-center gap-2 min-w-0 px-2 h-10 bg-surface-0 border-b border-edge"
        >
            <span className="shrink-0 px-1 text-sm font-semibold tracking-tight select-none">ada</span>

            {showModeSwitcher && (
                <nav
                    aria-label="Workspace mode"
                    className="flex items-center gap-0.5 shrink-0 p-0.5 bg-surface-2 border border-edge rounded-md"
                >
                    {MODES.map((m) => {
                        const active = m.id === mode;
                        const badge = badges[m.id];
                        return (
                            <button
                                key={m.id}
                                type="button"
                                aria-current={active ? "page" : undefined}
                                title={`${m.label} — ${m.hint}`}
                                onClick={() => setMode(m.id)}
                                className={cn(
                                    "ada-focus relative inline-flex items-center gap-1.5 px-2.5 h-7 rounded-sm",
                                    "text-sm font-medium whitespace-nowrap transition-colors duration-(--ada-dur-fast)",
                                    active
                                        ? "bg-surface-1 text-content shadow-panel"
                                        : "text-content-muted pointer-fine:hover:text-content",
                                )}
                            >
                                <Icon name={m.icon} size="sm" />
                                {m.label}
                                {badge != null && (
                                    // Passive signal only — the shell tells you
                                    // something happened without taking you there.
                                    <span
                                        aria-label={`${m.label} has updates`}
                                        className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-accent"
                                    />
                                )}
                            </button>
                        );
                    })}
                </nav>
            )}

            {/* The application menu bar: one fixed, complete index of every command.

                This replaces the per-mode "Panels" dropdown, which followed Maya's
                menu-set idea — app chrome stays put, discipline-specific contents swap.
                That works in Maya because its disciplines share most of their tools. Here
                the four modes are genuinely different applications, so the contents turned
                over almost completely and nothing had a fixed address. A menu you cannot
                learn is not a menu.

                So: same menus, same order, in every mode. Commands that cannot act right
                now are greyed with a reason rather than removed. */}
            <MenuBar />

            <span className="flex-1 min-w-0" />

            {/* A visible way in. The palette's job is discoverability, so hiding it
                behind a shortcut you must already know would defeat it. */}
            <IconButton
                size="sm"
                tooltip={`Search commands (${keysFor("command-palette") ?? "Ctrl+K"})`}
                icon={<Icon name="search" size="sm" />}
                onClick={openCommandPalette}
            />

            {/* Scope is the most consequential context in a multi-project deployment —
                every file, conversion and job belongs to one. It was buried in the
                Options drawer; persistent chrome is where it belongs. */}
            <ScopePicker />

            {/* Plugin contributions.
                These are hosted ONLY in the classic Menu.tsx, which the shell never
                renders — so without this a plugin's top-bar button silently disappeared
                the moment the shell was enabled. That is inventory row B11, and exactly
                the kind of quiet loss the parity checklist exists to catch.
                `fem-sidebar` needs no equivalent: SimulationControls hosts it and the
                shell mounts that in the Results dock. */}
            <PluginTopBarButtons navBtnClass={pluginNavBtnClass} />
            <PluginPanelRegion region="top-panel" />

            <Badge tone="accent">new shell</Badge>
            {/* Always offer the way back while the two UIs coexist. The classic pages
                (/convert, /admin) are a dead end today precisely because they lack this. */}
            <IconButton
                size="sm"
                tooltip="Return to the classic UI"
                icon={<Icon name="pop-out" size="sm" />}
                onClick={() => {
                    useShellPrefs.getState().setEnabled(false);
                    window.location.search = "";
                }}
            />
        </header>
    );
}
