// The command palette and the menu bar: two ways into the same commands
// (`./commands`), for a shell's chrome. The palette opens over everything,
// filters as you type and runs the chosen command; the menu bar draws a shell's
// menus, each item a command id, greyed with its reason when it cannot run.

import React, {useEffect, useMemo, useRef, useState, useSyncExternalStore} from "react";

import {OverlayPortal} from "@/components/ui/OverlayPortal";

import {commandsVersion, getCommand, listCommands, matchCommand, runCommand, subscribeCommands, type RegisteredCommand} from "./commands";
import {isPanelOpen, toggle} from "./reveal";
import {shellModesVersion, subscribeShellModes} from "./shellModes";

// ---------------------------------------------------------------------------
// Open / closed, as a tiny external store: a header button, a key and a menu all open it.
// ---------------------------------------------------------------------------

let paletteOpen = false;
const openListeners = new Set<() => void>();
const setOpen = (v: boolean) => {
  if (paletteOpen === v) return;
  paletteOpen = v;
  for (const l of openListeners) l();
};

export function openCommandPalette(): void {
  setOpen(true);
}

export function closeCommandPalette(): void {
  setOpen(false);
}

function usePaletteOpen(): boolean {
  return useSyncExternalStore(
    (cb) => {
      openListeners.add(cb);
      return () => openListeners.delete(cb);
    },
    () => paletteOpen,
    () => false,
  );
}

/** Every command offered now, re-read when commands or modes change. */
function useCommands(): RegisteredCommand[] {
  const v = useSyncExternalStore(subscribeCommands, commandsVersion, commandsVersion);
  const m = useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => listCommands({toggle: (id) => void toggle(id), isOpen: isPanelOpen}), [v, m]);
}

/** Run one command, registered or core's (a panel's or a mode's). */
function run(c: RegisteredCommand): void {
  if (getCommand(c.id)) runCommand(c.id);
  else c.run();
}

/** Mount once (the composed layout does); draws the palette while it is open. */
export function CommandPaletteHost() {
  const open = usePaletteOpen();
  if (!open) return null;
  return <CommandPalette onClose={closeCommandPalette} />;
}

function CommandPalette({onClose}: {onClose(): void}) {
  const all = useCommands();
  const [query, setQuery] = useState("");
  const [at, setAt] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  const hits = useMemo(
    () =>
      all
        .map((c) => ({c, rank: matchCommand(query, `${c.group ?? ""} ${c.label}`) ?? matchCommand(query, c.label)}))
        .filter((x): x is {c: RegisteredCommand; rank: number} => x.rank !== null)
        .sort((a, b) => a.rank - b.rank || a.c.label.localeCompare(b.c.label))
        .slice(0, 50)
        .map((x) => x.c),
    [all, query],
  );
  const choose = (c: RegisteredCommand | undefined) => {
    if (!c || c.disabledReason?.()) return;
    onClose();
    run(c);
  };
  return (
    <OverlayPortal>
      <div className="fixed inset-0 z-[90] flex items-start justify-center bg-black/50 p-4 pt-[12vh]" onClick={onClose}>
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Command palette"
          className="flex w-full max-w-xl flex-col overflow-hidden rounded-md border border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-bg,#111827)] text-[var(--ada-panel-text)] shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          <input
            ref={input}
            value={query}
            placeholder="Type a command"
            aria-label="Command"
            className="border-b border-[var(--ada-window-border,#374151)] bg-transparent px-3 py-2 text-sm outline-none"
            onChange={(e) => {
              setQuery(e.target.value);
              setAt(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") setAt((i) => Math.min(hits.length - 1, i + 1));
              else if (e.key === "ArrowUp") setAt((i) => Math.max(0, i - 1));
              else if (e.key === "Enter") choose(hits[at]);
              else return;
              e.preventDefault();
            }}
          />
          <ul role="listbox" aria-label="Commands" className="max-h-[50vh] overflow-y-auto py-1">
            {hits.length === 0 && <li className="px-3 py-2 text-xs text-gray-400">No command matches.</li>}
            {hits.map((c, i) => {
              const why = c.disabledReason?.() ?? null;
              return (
                <li
                  key={c.id}
                  role="option"
                  aria-selected={i === at}
                  aria-disabled={!!why}
                  title={why ?? undefined}
                  className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm ${i === at ? "bg-[var(--ada-tab-active-bg,#374151)]" : ""} ${why ? "opacity-50" : ""}`}
                  onMouseEnter={() => setAt(i)}
                  onClick={() => choose(c)}
                >
                  <span className="w-20 shrink-0 truncate text-xs text-gray-400">{c.group ?? "Commands"}</span>
                  <span className="flex-1 truncate">{c.label}</span>
                  {c.keys && <kbd className="text-xs text-gray-400">{c.keys}</kbd>}
                </li>
              );
            })}
          </ul>
        </div>
      </div>
    </OverlayPortal>
  );
}

// ---------------------------------------------------------------------------
// The menu bar.
// ---------------------------------------------------------------------------

/** An item of a menu: a command by id, a line between items, or an action of the menu's own. */
export type MenuItemDef = string | {separator: true} | {label: string; run(): void; disabled?: boolean};

export interface MenuDef {
  label: string;
  items: readonly MenuItemDef[];
}

export interface MenuBarProps {
  menus: readonly MenuDef[];
  className?: string;
}

/** A shell's menus over the commands: an item that is not offered is left out; one that cannot run is greyed with its reason. */
export function MenuBar({menus, className = "flex items-center gap-0.5 text-[var(--ada-window-header-text,#f3f4f6)]"}: MenuBarProps) {
  const all = useCommands();
  const byId = useMemo(() => new Map(all.map((c) => [c.id, c])), [all]);
  const [open, setOpenMenu] = useState<number | null>(null);
  const bar = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open === null) return;
    const away = (e: PointerEvent) => {
      if (!bar.current?.contains(e.target as Node)) setOpenMenu(null);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpenMenu(null);
    window.addEventListener("pointerdown", away);
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("pointerdown", away);
      window.removeEventListener("keydown", esc);
    };
  }, [open]);
  return (
    <div ref={bar} role="menubar" className={className}>
      {menus.map((m, i) => (
        <div key={m.label} className="relative">
          <button
            type="button"
            role="menuitem"
            aria-haspopup="menu"
            aria-expanded={open === i}
            className={`rounded-sm px-2 py-1 text-xs ${open === i ? "bg-[var(--ada-tab-active-bg,#374151)]" : "hover:bg-white/10"}`}
            onClick={() => setOpenMenu(open === i ? null : i)}
            onPointerEnter={() => open !== null && setOpenMenu(i)}
          >
            {m.label}
          </button>
          {open === i && (
            <ul
              role="menu"
              aria-label={m.label}
              className="absolute left-0 top-full z-[80] mt-0.5 min-w-48 rounded-sm border border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-bg,#111827)] py-1 text-xs text-[var(--ada-window-header-text,#f3f4f6)] shadow-xl"
            >
              {m.items.map((it, j) => {
                if (typeof it === "object" && "separator" in it) {
                  return <li key={`sep${j}`} role="separator" className="my-1 border-t border-[var(--ada-window-border,#374151)]" />;
                }
                const c = typeof it === "string" ? byId.get(it) : null;
                if (typeof it === "string" && !c) return null;
                const label = c ? c.label : (it as {label: string}).label;
                const why = c ? (c.disabledReason?.() ?? null) : (it as {disabled?: boolean}).disabled ? "" : null;
                const checked = c?.checked?.();
                return (
                  <li key={c ? c.id : `own${j}`} role="none">
                    <button
                      type="button"
                      role={checked === undefined ? "menuitem" : "menuitemcheckbox"}
                      aria-checked={checked}
                      aria-disabled={why !== null}
                      title={why || undefined}
                      className={`flex w-full items-center gap-2 px-3 py-1 text-left ${why !== null ? "opacity-50" : "hover:bg-white/10"}`}
                      onClick={() => {
                        if (why !== null) return;
                        setOpenMenu(null);
                        if (c) run(c);
                        else (it as {run(): void}).run();
                      }}
                    >
                      <span className="w-3 shrink-0">{checked ? "✓" : ""}</span>
                      <span className="flex-1">{label}</span>
                      {c?.keys && <kbd className="text-gray-400">{c.keys}</kbd>}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
