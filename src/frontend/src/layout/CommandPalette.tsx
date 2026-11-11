// The command palette and the menu bar: two ways into the same commands
// (`./commands`), for a shell's chrome. The palette opens over everything,
// filters as you type and runs the chosen command; the menu bar draws a shell's
// menus, each item a command id, greyed with its reason when it cannot run.

import React, {useEffect, useMemo, useRef, useState, useSyncExternalStore} from "react";

import {OverlayPortal} from "@/components/ui/OverlayPortal";

import {activeUiShellId, activeUiShellVersion, getUiShell, subscribeActiveUiShell, type UiShellSpec} from "@/plugins/uiShells";

import {
  commandChord,
  commandsVersion,
  getCommand,
  listCommands,
  commandRank,
  matchCommand,
  registerCommand,
  runCommand,
  subscribeCommands,
  unregisterCommand,
  type RegisteredCommand,
} from "./commands";
import {installShortcutDispatcher} from "./shortcutDispatch";
import {bindCoreShortcuts, coreShortcutKeys} from "./shortcuts";
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

/** Draws the palette while it is open. Core mounts one under its root (`CorePaletteHost`); a shell mounts none of its own. */
export function CommandPaletteHost() {
  const open = usePaletteOpen();
  if (!open) return null;
  return <CommandPalette onClose={closeCommandPalette} />;
}

/** The palette's command (View → Command palette), registered by core while the palette is on. */
export const COMMAND_PALETTE_COMMAND = "core:command-palette";
/** Core's shortcut that opens it (Ctrl+K, `./coreShortcutTable`). */
export const COMMAND_PALETTE_SHORTCUT = "core:command-palette";

const normChord = (k: string) => k.replace(/\s+/g, "").toLowerCase();

/**
 * What shell `spec` asks of the palette: on unless `chrome.commandPalette` is
 * `false`, and the extra chord its `keys` adds to core's Ctrl+K (none when they
 * are Ctrl+K anyway).
 */
export function paletteConfig(spec: Pick<UiShellSpec, "chrome"> | undefined): {on: boolean; extraKeys?: string} {
  const cfg = spec?.chrome?.commandPalette;
  if (cfg === false) return {on: false};
  const keys = cfg?.keys?.trim();
  return keys && normChord(keys) !== normChord(coreShortcutKeys(COMMAND_PALETTE_SHORTCUT) ?? "") ? {on: true, extraKeys: keys} : {on: true};
}

/**
 * The command palette as a core feature, in every UI: mounted once under core's
 * root (so an in-place shell switch keeps it), it binds core's Ctrl+K, registers
 * the palette's command and draws the palette. The active shell may add a chord
 * (`chrome.commandPalette.keys`) or turn it off (`chrome.commandPalette: false`).
 */
export function CorePaletteHost() {
  useSyncExternalStore(subscribeActiveUiShell, activeUiShellVersion, activeUiShellVersion);
  const {on, extraKeys} = paletteConfig(getUiShell(activeUiShellId()));
  useEffect(() => {
    if (!on) return;
    // Core's commands, in a UI that has not registered them (a shell with a root of its own).
    void import("./coreCommands").then((m) => m.registerCoreCommands()).catch(() => {});
    registerCommand(
      {id: COMMAND_PALETTE_COMMAND, label: "Command palette", group: "View", shortcut: COMMAND_PALETTE_SHORTCUT, keys: extraKeys, run: openCommandPalette},
      "core",
    );
    const unbind = bindCoreShortcuts({[COMMAND_PALETTE_SHORTCUT]: {run: () => void openCommandPalette()}});
    // The listener, also where no layout or canvas installs it (a shell with a root of its own).
    const release = installShortcutDispatcher();
    return () => {
      release();
      unbind();
      unregisterCommand(COMMAND_PALETTE_COMMAND);
      closeCommandPalette();
    };
  }, [on, extraKeys]);
  return on ? <CommandPaletteHost /> : null;
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
        .map((c) => ({c, rank: commandRank(query, c)}))
        .filter((x): x is {c: RegisteredCommand; rank: number} => x.rank !== null)
        .sort((a, b) => a.rank - b.rank || a.c.label.localeCompare(b.c.label))
        .slice(0, 50)
        .map((x) => x.c),
    [all, query],
  );
  /** Where the mouse was last seen over the list; null until it has been seen once. */
  const pointer = useRef<{x: number; y: number} | null>(null);
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
                  // Only a mouse that actually moved picks a row. A cursor resting where the list
                  // appears must not: the browser reports a move for it as rows are laid out under
                  // it, and Enter would then run that row instead of the best match.
                  onMouseMove={(e) => {
                    const last = pointer.current;
                    pointer.current = {x: e.clientX, y: e.clientY};
                    if (last && (last.x !== e.clientX || last.y !== e.clientY)) setAt(i);
                  }}
                  onClick={() => choose(c)}
                >
                  <span className="w-20 shrink-0 truncate text-xs text-gray-400">{c.group ?? "Commands"}</span>
                  <span className="flex-1 truncate">{c.label}</span>
                  {commandChord(c) && <kbd className="text-xs text-gray-400">{commandChord(c)}</kbd>}
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

/**
 * An item of a menu: a command by id, a line between items, an action of the
 * menu's own, or a submenu of further items (which may nest).
 */
export type MenuItemDef = string | {separator: true} | {label: string; run(): void; disabled?: boolean} | {label: string; items: readonly MenuItemDef[]};

const isSubmenu = (it: MenuItemDef): it is {label: string; items: readonly MenuItemDef[]} => typeof it === "object" && "items" in it;

const isSeparator = (it: MenuItemDef): boolean => typeof it === "object" && "separator" in it;

/**
 * The items of a menu that are offered: a command id nothing offers is dropped,
 * and so is a submenu left with nothing to run (a separator alone is nothing).
 * Separators left at either end, or next to another, go too.
 */
export function offeredMenuItems(items: readonly MenuItemDef[], offers: (commandId: string) => boolean): MenuItemDef[] {
  const out: MenuItemDef[] = [];
  for (const it of items) {
    if (typeof it === "string") {
      if (offers(it)) out.push(it);
    } else if (isSubmenu(it)) {
      const sub = offeredMenuItems(it.items, offers);
      if (sub.some((s) => !isSeparator(s))) out.push({...it, items: sub});
    } else if (isSeparator(it)) {
      if (out.length && !isSeparator(out[out.length - 1])) out.push(it);
    } else out.push(it);
  }
  while (out.length && isSeparator(out[out.length - 1])) out.pop();
  return out;
}

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
            <MenuList
              label={m.label}
              items={m.items}
              byId={byId}
              close={() => setOpenMenu(null)}
              className="absolute left-0 top-full z-[80] mt-0.5"
            />
          )}
        </div>
      ))}
    </div>
  );
}

const MENU_LIST =
  "min-w-48 rounded-sm border border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-bg,#111827)] py-1 text-xs text-[var(--ada-window-header-text,#f3f4f6)] shadow-xl";

/** One level of a menu: its items, and the submenus they open (to the side, one open at a time). */
export function MenuList({
  label,
  items,
  byId,
  close,
  className,
}: {
  label: string;
  items: readonly MenuItemDef[];
  byId: ReadonlyMap<string, RegisteredCommand>;
  close(): void;
  className: string;
}) {
  const shown = useMemo(() => offeredMenuItems(items, (id) => byId.has(id)), [items, byId]);
  const [sub, setSub] = useState<number | null>(null);
  return (
    <ul role="menu" aria-label={label} className={`${className} ${MENU_LIST}`}>
      {shown.map((it, j) => {
        if (typeof it === "object" && "separator" in it) {
          return <li key={`sep${j}`} role="separator" className="my-1 border-t border-[var(--ada-window-border,#374151)]" />;
        }
        if (typeof it === "object" && isSubmenu(it)) {
          const isOpen = sub === j;
          return (
            <li key={`sub${j}`} role="none" className="relative" onPointerEnter={() => setSub(j)}>
              <button
                type="button"
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={isOpen}
                className={`flex w-full items-center gap-2 px-3 py-1 text-left ${isOpen ? "bg-white/10" : "hover:bg-white/10"}`}
                onClick={() => setSub(isOpen ? null : j)}
                onKeyDown={(e) => {
                  // Right opens the submenu, Left closes it again (Escape closes the whole menu, as for any item).
                  if (e.key === "ArrowRight") setSub(j);
                  else if (e.key === "ArrowLeft" && isOpen) setSub(null);
                  else return;
                  e.preventDefault();
                }}
              >
                <span className="w-3 shrink-0" />
                <span className="flex-1">{it.label}</span>
                <span aria-hidden="true" className="text-gray-400">
                  ›
                </span>
              </button>
              {isOpen && <MenuList label={it.label} items={it.items} byId={byId} close={close} className="absolute left-full top-0 z-[81]" />}
            </li>
          );
        }
        const c = typeof it === "string" ? byId.get(it) : null;
        const own = typeof it === "string" ? null : (it as {label: string; run(): void; disabled?: boolean});
        const text = c ? c.label : own!.label;
        const why = c ? (c.disabledReason?.() ?? null) : own!.disabled ? "" : null;
        const checked = c?.checked?.();
        const chord = c ? commandChord(c) : undefined;
        return (
          <li key={c ? c.id : `own${j}`} role="none" onPointerEnter={() => setSub(null)}>
            <button
              type="button"
              role={checked === undefined ? "menuitem" : "menuitemcheckbox"}
              aria-checked={checked}
              aria-disabled={why !== null}
              title={why || undefined}
              className={`flex w-full items-center gap-2 px-3 py-1 text-left ${why !== null ? "opacity-50" : "hover:bg-white/10"}`}
              onClick={() => {
                if (why !== null) return;
                close();
                if (c) run(c);
                else own!.run();
              }}
            >
              <span className="w-3 shrink-0">{checked ? "✓" : ""}</span>
              <span className="flex-1">{text}</span>
              {chord && <kbd className="text-gray-400">{chord}</kbd>}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
