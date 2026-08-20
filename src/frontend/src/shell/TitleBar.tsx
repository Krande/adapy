import React from "react";
import {Button, Icon, IconButton, cn} from "@/components/ui";
import {MODES, useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {PluginPanelRegion, PluginTopBarButtons} from "@/plugins";
import ScopePicker from "./ScopePicker";
import MenuBar from "./MenuBar";
import ModeToolbar from "./ModeToolbar";
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
    /** Menus + command palette. Off for the single-purpose pages — see profiles.ts. */
    showMenus?: boolean;
    /** Names the page in the reduced bar, e.g. "Convert files". */
    pageTitle?: string;
    /** Offer the way back. Off for pop-out windows — see profiles.ts. */
    backToViewer?: boolean;
}

export default function TitleBar({showModeSwitcher, showMenus = true, pageTitle, backToViewer = false}: TitleBarProps) {
    // A page (/convert, /admin) gets a deliberately thin bar: who we are, what this page
    // is, and the way back.
    //
    // The way back is the whole reason these routes were folded into the shell. They were
    // reachable by URL and by a button in Preferences, and once you were there the only
    // exits were the browser's Back button or editing the address bar — which is how a
    // page stops feeling like part of the application.
    if (!showMenus) {
        return (
            <header
                style={{gridArea: "titlebar", zIndex: Z.dock}}
                className="flex items-center gap-2 min-w-0 px-2 h-9 bg-surface-0 border-b border-edge"
            >
                <span className="shrink-0 px-1 text-sm font-semibold tracking-tight select-none">ada</span>
                {pageTitle && (
                    <>
                        <span aria-hidden="true" className="shrink-0 w-px h-5 mx-1 bg-edge" />
                        <span className="shrink-0 text-sm text-content">{pageTitle}</span>
                    </>
                )}

                <span className="flex-1 min-w-0" />

                <ScopePicker />

                {backToViewer && (
                <Button
                    size="sm"
                    variant="secondary"
                    iconLeft={<Icon name="chevron" size="sm" className="rotate-180" />}
                    onClick={() => {
                        // A real navigation, not history.back(): the page may have been
                        // opened directly from a link or a bookmark, in which case there
                        // is nothing behind it to go back to.
                        window.location.href = "/";
                    }}
                >
                    Back to the viewer
                </Button>
                )}
            </header>
        );
    }

    const mode = useModeStore((s) => s.mode);
    const setMode = useModeStore((s) => s.setMode);
    const badges = useModeStore((s) => s.badges);

    return (
        <header
            style={{gridArea: "titlebar", zIndex: Z.dock}}
            className="flex flex-col min-w-0 bg-surface-0 border-b border-edge"
        >
            {/* Row 1 — application chrome: identity and menus at the left, where you
                are in the middle, session controls at the right.

                A three-column grid, not a flex row with spacers. Centring the modes by
                putting them after the menus would anchor them to wherever the menus
                happen to end — so the group would shift sideways when a menu title is
                renamed, and again on a narrow window. `1fr auto 1fr` centres them on the
                window regardless of what flanks them, which is what makes a fixed
                landmark actually fixed. */}
            <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 min-w-0 px-2 h-9">
                <div className="flex items-center gap-2 min-w-0">
                <span className="shrink-0 px-1 text-sm font-semibold tracking-tight select-none">ada</span>

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
                </div>

            <div className="flex justify-center min-w-0">
            {showModeSwitcher && (
                <>
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
                                        "ada-focus relative inline-flex items-center gap-1.5 px-2 h-6 rounded-sm",
                                        "text-xs font-medium whitespace-nowrap transition-colors duration-(--ada-dur-fast)",
                                        // Green for the active mode, from the theme's semantic
                                        // palette rather than a literal, so it moves with the
                                        // preset and stays legible on the light one.
                                        //
                                        // White-on-raised said "this button is pressed", which
                                        // every toggle in the app also says. Colour says "you are
                                        // HERE" — a different claim, and the one worth making:
                                        // this is the only place in the chrome that answers which
                                        // of four applications you are looking at.
                                        active
                                            ? "bg-pass-subtle text-pass"
                                            : "text-content-muted pointer-fine:hover:text-content",
                                    )}
                                >
                                    <Icon name={m.icon} size="sm" />
                                    {m.label}
                                    {badge != null && (
                                        // Passive signal only — the shell tells you something
                                        // happened without taking you there.
                                        <span
                                            aria-label={`${m.label} has updates`}
                                            className="absolute top-0.5 right-0.5 w-1.5 h-1.5 rounded-full bg-accent"
                                        />
                                    )}
                                </button>
                            );
                        })}
                    </nav>
                </>
            )}
            </div>

            <div className="flex items-center justify-end gap-1 min-w-0">

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

                </div>
            </div>

            {/* Row 2 — the mode's own tools.

                Only the tools. The mode BUTTONS moved up to row 1 beside the menus: they
                are a persistent statement of where you are, which belongs with the other
                persistent chrome, and giving them their own row made them shout louder
                than anything else on screen. What changes with the mode is the strip; the
                switch itself does not need to be the biggest thing in the window. */}
            {showModeSwitcher && (
                <div className="flex items-center gap-2 min-w-0 px-2 h-9 border-t border-edge">
                    <ModeToolbar />
                </div>
            )}
        </header>
    );
}
