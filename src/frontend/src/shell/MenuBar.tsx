import React from "react";
import {createPortal} from "react-dom";
import {Icon} from "@/components/icons";
import {cn} from "@/components/ui";
import {buildCommands} from "./commands";
import {MENUS, resolveMenus, type ResolvedItem, type ResolvedMenu} from "./menuModel";
import {Z} from "./zIndex";

// The application menu bar.
//
// Behaviours people expect from a menu bar and notice the absence of:
//
//   * click a title to open, click again to close;
//   * once one is open, HOVERING another switches to it without a second click;
//   * ←/→ move between menus, ↑/↓ within one, Enter runs, Escape closes;
//   * clicking anywhere else closes it.
//
// The panels are portalled to <body>. The title bar is a grid item carrying
// z-index: dock, and a z-index on a grid item applies even at position: static — it
// brings a stacking context with it, which traps descendants regardless of their own
// z-index. Floating panels at a lower z were drawing over the Panels dropdown until it
// was portalled; every popover in docked chrome has this problem.

const ITEM = "ada-focus flex w-full items-center gap-2 rounded-sm px-2 py-1 text-left text-sm";

function MenuItems({items, onRun, depth = 0}: {items: ResolvedItem[]; onRun: () => void; depth?: number}) {
    return (
        <>
            {items.map((it, i) => {
                if (it.kind === "separator") return <div key={i} className="my-1 border-t border-edge" />;

                if (it.kind === "submenu") {
                    // Nested one level only, and rendered inline (indented) rather than as
                    // a fly-out. Fly-outs need hover intent handling to not slam shut when
                    // the pointer cuts a corner, and this menu has exactly one submenu —
                    // not worth the machinery or the bug surface.
                    return (
                        <div key={i}>
                            <div className="px-2 pb-0.5 pt-1.5 text-xs font-semibold uppercase tracking-wide text-content-subtle">
                                {it.label}
                            </div>
                            <MenuItems items={it.items} onRun={onRun} depth={depth + 1} />
                        </div>
                    );
                }

                const c = it.command;
                const disabled = c.enabled === false;
                return (
                    <button
                        key={c.id}
                        type="button"
                        role="menuitem"
                        disabled={disabled}
                        // The reason is the tooltip: "why is that greyed out" should be
                        // answerable by pointing at it.
                        title={disabled ? c.disabledReason : undefined}
                        onClick={() => {
                            if (disabled) return;
                            c.run();
                            onRun();
                        }}
                        className={cn(
                            ITEM,
                            depth > 0 && "pl-6",
                            disabled
                                ? "cursor-default text-content-subtle"
                                : "text-content pointer-fine:hover:bg-surface-2",
                        )}
                    >
                        {c.icon ? (
                            <Icon name={c.icon} size="sm" className="shrink-0 opacity-70" />
                        ) : (
                            <span className="w-4 shrink-0" />
                        )}
                        <span className="flex-1 truncate">{c.title}</span>
                        {/* The mode a panel belongs to, when it is not this one — so
                            choosing it is not a surprise mode switch. */}
                        {c.context && !["Panel", "Action", "Layout", "Help"].includes(c.context) && (
                            <span className="shrink-0 text-xs text-content-subtle">{c.context}</span>
                        )}
                        {c.keys && <span className="shrink-0 font-mono text-xs text-content-subtle">{c.keys}</span>}
                    </button>
                );
            })}
        </>
    );
}

export default function MenuBar() {
    const [open, setOpen] = React.useState<string | null>(null);
    const [anchor, setAnchor] = React.useState<{left: number; top: number} | null>(null);
    const barRef = React.useRef<HTMLDivElement | null>(null);
    const panelRef = React.useRef<HTMLDivElement | null>(null);
    const titleRefs = React.useRef<Record<string, HTMLButtonElement | null>>({});

    // Titles come from the static structure, so the bar is the same width and the same
    // order on every render — including before anything has been opened. Only the OPEN
    // menu's contents are resolved, and they are resolved fresh each time it opens:
    // enablement is a snapshot of live store state (is anything selected, is a result set
    // loaded), and a menu that greys the wrong things is worse than one that greys
    // nothing.
    const titles = MENUS.map((m) => ({id: m.id, label: m.label}));
    const menuOrder = React.useRef<string[]>(titles.map((t) => t.id));
    menuOrder.current = titles.map((t) => t.id);

    const current: ResolvedMenu | undefined = React.useMemo(() => {
        if (!open) return undefined;
        return resolveMenus(buildCommands("menu")).find((m) => m.id === open);
    }, [open]);

    const place = React.useCallback((id: string) => {
        const r = titleRefs.current[id]?.getBoundingClientRect();
        if (r) setAnchor({left: r.left, top: r.bottom + 2});
    }, []);

    const openMenu = (id: string) => {
        place(id);
        setOpen(id);
    };
    const close = React.useCallback(() => {
        setOpen(null);
        setAnchor(null);
    }, []);

    React.useEffect(() => {
        if (!open) return;
        const onDown = (e: PointerEvent) => {
            const t = e.target as Node;
            if (barRef.current?.contains(t)) return;
            if (panelRef.current?.contains(t)) return;
            close();
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") {
                e.stopPropagation();
                close();
                titleRefs.current[open]?.focus();
                return;
            }
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                // Wrap around: at the last menu, → goes back to the first. A menu bar
                // that dead-ends under the arrow keys feels broken.
                const ids = menuOrder.current;
                const i = ids.indexOf(open);
                if (i < 0) return;
                e.preventDefault();
                const next = ids[(i + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length];
                openMenu(next);
                return;
            }
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                const items = panelRef.current?.querySelectorAll<HTMLButtonElement>(
                    "button[role='menuitem']:not([disabled])",
                );
                if (!items?.length) return;
                const list = Array.from(items);
                const at = list.indexOf(document.activeElement as HTMLButtonElement);
                const step = e.key === "ArrowDown" ? 1 : list.length - 1;
                list[(at < 0 ? (e.key === "ArrowDown" ? 0 : list.length - 1) : (at + step) % list.length)].focus();
            }
        };
        window.addEventListener("pointerdown", onDown);
        window.addEventListener("keydown", onKey);
        const onResize = () => place(open);
        window.addEventListener("resize", onResize);
        return () => {
            window.removeEventListener("pointerdown", onDown);
            window.removeEventListener("keydown", onKey);
            window.removeEventListener("resize", onResize);
        };
    }, [open, close, place]);


    return (
        <div ref={barRef} className="flex shrink-0 items-center">
            {titles.map((m) => (
                <button
                    key={m.id}
                    ref={(el) => {
                        titleRefs.current[m.id] = el;
                    }}
                    type="button"
                    aria-expanded={open === m.id}
                    aria-haspopup="menu"
                    onClick={() => (open === m.id ? close() : openMenu(m.id))}
                    // Once any menu is open, sliding across the bar switches menus — the
                    // single most missed menu-bar behaviour when it is absent.
                    onPointerEnter={() => open && open !== m.id && openMenu(m.id)}
                    className={cn(
                        "ada-focus h-7 rounded-sm px-2 text-sm",
                        open === m.id
                            ? "bg-surface-2 text-content"
                            : "text-content-muted pointer-fine:hover:bg-surface-2 pointer-fine:hover:text-content",
                    )}
                >
                    {m.label}
                </button>
            ))}

            {current && anchor &&
                createPortal(
                    <div
                        ref={panelRef}
                        role="menu"
                        aria-label={current.label}
                        style={{zIndex: Z.contextMenu, position: "fixed", left: anchor.left, top: anchor.top}}
                        // Two layers, on purpose.
                        //
                        // Panel themes are rgba — "slate glass" is 62% opaque — which is
                        // right for a panel you park beside the model and wrong for a menu
                        // you read in a fraction of a second over arbitrary 3D content.
                        // CSS cannot flatten that alpha (color-mix over an opaque colour
                        // still yields alpha), so the opaque surface-0 layer blocks the
                        // background and the tinted surface-1 layer inside supplies the
                        // theme's colour. Composite: opaque, and still themed.
                        className="max-h-[70vh] min-w-64 overflow-y-auto scrollbar rounded-md border border-edge bg-surface-0 shadow-popover"
                    >
                        <div className="rounded-md bg-surface-1 p-1">
                            <MenuItems items={current.items} onRun={close} />
                        </div>
                    </div>,
                    document.body,
                )}
        </div>
    );
}
