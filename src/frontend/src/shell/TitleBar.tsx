import React from "react";
import {Badge, Icon, IconButton, cn} from "@/components/ui";
import {MODES, useModeStore, type ModeId} from "./modeStore";
import {useLayoutStore} from "./layoutStore";
import {panelsForMode} from "./panelRegistry";
import {useShellPrefs} from "./shellPrefs";
import {PluginPanelRegion, PluginTopBarButtons} from "@/plugins";
import {Z} from "./zIndex";

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

            {/* Menu set: the panels THIS mode offers. Maya's menu sets — the app-level
                chrome stays put while the discipline-specific contents swap. */}
            <PanelMenu mode={mode} />

            <span className="flex-1 min-w-0" />

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

/** Toggles for the current mode's panels. Generated from the registry, so a new panel
 *  appears here without anyone editing a menu. */
function PanelMenu({mode}: {mode: ModeId}) {
    const [open, setOpen] = React.useState(false);
    const togglePanel = useLayoutStore((s) => s.togglePanel);
    const layout = useLayoutStore((s) => s.perMode[mode]);
    const ref = React.useRef<HTMLDivElement | null>(null);

    const panels = panelsForMode(mode);

    React.useEffect(() => {
        if (!open) return;
        const onDown = (e: PointerEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
        };
        const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
        window.addEventListener("pointerdown", onDown);
        window.addEventListener("keydown", onKey);
        return () => {
            window.removeEventListener("pointerdown", onDown);
            window.removeEventListener("keydown", onKey);
        };
    }, [open]);

    const isOpen = (id: string) =>
        Boolean(
            layout &&
                (Object.values(layout.docks).some((d) => d.tabs.includes(id as never)) ||
                    id in layout.floats ||
                    layout.overlays[id as never] === true),
        );

    return (
        <div ref={ref} className="relative shrink-0">
            <button
                type="button"
                aria-expanded={open}
                aria-haspopup="menu"
                onClick={() => setOpen((v) => !v)}
                className={cn(
                    "ada-focus inline-flex items-center gap-1 px-2 h-7 rounded-sm text-sm",
                    "text-content-muted pointer-fine:hover:text-content pointer-fine:hover:bg-surface-2",
                )}
            >
                Panels
                <Icon name="chevron" size="sm" className="rotate-90" />
            </button>

            {open && (
                <div
                    role="menu"
                    style={{zIndex: Z.contextMenu}}
                    className="absolute left-0 top-full mt-1 min-w-56 p-1 bg-surface-1 border border-edge rounded-md shadow-popover"
                >
                    {panels.map((p) => (
                        <button
                            key={p.id}
                            role="menuitemcheckbox"
                            aria-checked={isOpen(p.id)}
                            type="button"
                            onClick={() => togglePanel(mode, p.id, p.defaultDock)}
                            className={cn(
                                "ada-focus flex items-center gap-2 w-full px-2 h-7 rounded-sm text-sm text-left",
                                "pointer-fine:hover:bg-surface-2",
                            )}
                        >
                            <span className="w-3 shrink-0 text-accent">{isOpen(p.id) ? "✓" : ""}</span>
                            <Icon name={p.icon} size="sm" />
                            <span className="flex-1 truncate">{p.title}</span>
                            {p.shortcut && <span className="text-xs text-content-subtle font-mono">{p.shortcut}</span>}
                        </button>
                    ))}
                    {panels.length === 0 && (
                        <p className="px-2 py-1.5 text-xs text-content-subtle">No panels available in this mode.</p>
                    )}
                </div>
            )}
        </div>
    );
}
