// Commands: named actions a shell's menu bar, its command palette and its keys
// all run, so they agree. A shell or a plugin registers its own; core adds one
// per panel (show or hide it) and one per mode of a composed shell (switch to it).
// A command with `keys` is also a shortcut (`./shortcuts`).
//
// Dependency-free, so the facade root can export it.

import type React from "react";

import type { PanelId } from "./panel";
import { listPanels } from "./registry";
import { activeShellMode, listShellModes, setShellMode } from "./shellModes";
import { registerShortcut, unregisterShortcut, type ShortcutKeys } from "./shortcuts";

export interface CommandSpec {
  /** Unique, e.g. `"my-shell:file.open"`. A later registration under the same id replaces it. */
  id: string;
  label: string;
  /** Where the palette lists it, and the menu a shell may put it in. Default "Commands". */
  group?: string;
  icon?: React.FC;
  /** Also a shortcut (core's own keys are refused, see `RESERVED_SHORTCUTS`). */
  keys?: ShortcutKeys;
  run(): void;
  /** Offered at all right now; default always. */
  when?(): boolean;
  /** Offered but not runnable now, and why (shown greyed, with this as the reason). */
  disabledReason?(): string | null;
  /** Ticked in a menu (a toggle's state). */
  checked?(): boolean;
}

export interface RegisteredCommand extends CommandSpec {
  owner: string;
}

const _commands = new Map<string, RegisteredCommand>();
let version = 0;
const listeners = new Set<() => void>();
const changed = () => {
  version++;
  for (const l of listeners) l();
};

export function registerCommand(spec: CommandSpec, owner: string): void {
  if (!spec.id) {
    console.warn("[layout] registerCommand called without an id; ignored");
    return;
  }
  const had = _commands.get(spec.id);
  if (had?.keys) unregisterShortcut(`cmd:${spec.id}`);
  _commands.set(spec.id, { ...spec, owner });
  if (spec.keys) {
    registerShortcut(
      { id: `cmd:${spec.id}`, keys: spec.keys, label: spec.label, group: spec.group, run: () => void runCommand(spec.id), when: spec.when },
      owner,
    );
  }
  changed();
}

export function unregisterCommand(id: string): void {
  const had = _commands.get(id);
  if (!had) return;
  if (had.keys) unregisterShortcut(`cmd:${id}`);
  _commands.delete(id);
  changed();
}

/** Core's commands: one per panel with a title (show or hide it) and one per mode of the active shell. */
function coreCommands(toggle?: (id: PanelId) => void, isOpen?: (id: PanelId) => boolean): RegisteredCommand[] {
  const panels: RegisteredCommand[] = listPanels({})
    .filter((p) => !p.placement || Object.values(p.placement).some((pl) => pl && !pl.parent))
    .map((p) => ({
      id: `panel:${p.id}`,
      label: p.title,
      group: "Panels",
      icon: p.icon,
      owner: p.owner,
      run: () => toggle?.(p.id),
      checked: isOpen ? () => isOpen(p.id) : undefined,
    }));
  const modes: RegisteredCommand[] = listShellModes().map((m) => ({
    id: `mode:${m.id}`,
    label: m.label,
    group: "Modes",
    icon: m.icon,
    owner: m.pluginId ?? "shell",
    run: () => void setShellMode(m.id),
    checked: () => activeShellMode() === m.id,
  }));
  return [...panels, ...modes];
}

/**
 * Every command offered now: the registered ones whose `when` holds, then core's.
 * `toggle` / `isOpen` give panel commands their effect and state (the layout's `toggle`).
 */
export function listCommands(opts: { toggle?: (id: PanelId) => void; isOpen?: (id: PanelId) => boolean } = {}): RegisteredCommand[] {
  const own = [..._commands.values()].filter((c) => {
    try {
      return !c.when || c.when();
    } catch {
      return false;
    }
  });
  return [...own, ...coreCommands(opts.toggle, opts.isOpen)];
}

export function getCommand(id: string): RegisteredCommand | undefined {
  return _commands.get(id);
}

/** Run a registered command when it is offered and runnable. False otherwise (or when it threw). */
export function runCommand(id: string): boolean {
  const c = _commands.get(id);
  if (!c) return false;
  try {
    if (c.when && !c.when()) return false;
    if (c.disabledReason?.()) return false;
    c.run();
    return true;
  } catch (err) {
    console.error(`[layout] command "${id}" threw`, err);
    return false;
  }
}

/** Ranks `label` for a palette query: lower is better, null when it does not match. */
export function matchCommand(query: string, label: string): number | null {
  const q = query.trim().toLowerCase();
  if (!q) return 0;
  const l = label.toLowerCase();
  const at = l.indexOf(q);
  if (at === 0) return 0;
  if (at > 0) return 1 + at / 100;
  // The query's letters in order (initials and the like).
  let i = 0;
  for (const ch of l) if (ch === q[i]) i++;
  return i === q.length ? 3 : null;
}

export function subscribeCommands(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

export function commandsVersion(): number {
  return version;
}

/** Clear the registry -- unit tests only. */
export function resetCommands(): void {
  for (const c of _commands.values()) if (c.keys) unregisterShortcut(`cmd:${c.id}`);
  _commands.clear();
  changed();
}
