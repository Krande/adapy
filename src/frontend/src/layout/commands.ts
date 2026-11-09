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
import { modeAvailable } from "./modeRequirements";
import { isPanelOpen, toggle as togglePanel } from "./reveal";
import { coreShortcutKeys, registerShortcut, registerShortcutSource, shortcutOverrides, unregisterShortcut, type ShortcutKeys } from "./shortcuts";

export interface CommandSpec {
  /** Unique, e.g. `"my-shell:file.open"`. A later registration under the same id replaces it. */
  id: string;
  label: string;
  /** Extra words the palette finds the command by ("spacemouse", "3dconnexion"): matched beside the label, the group and the id. */
  keywords?: readonly string[];
  /** Where the palette lists it, and the menu a shell may put it in. Default "Commands". */
  group?: string;
  icon?: React.FC;
  /** Also a shortcut (core's own keys are refused, see `RESERVED_SHORTCUTS`). */
  keys?: ShortcutKeys;
  /**
   * The id of a core shortcut (`core:...`, see `CORE_SHORTCUTS`) that does what this command does:
   * its chord is shown beside the command, read from the registry, so the menu, the dialog and the
   * binding share one definition. Binds nothing itself. Ignored when `keys` is given.
   */
  shortcut?: string;
  /**
   * A display-only chord, bound to nothing, for a key handled outside the registry. Prefer `shortcut`.
   * Ignored when `keys` or a known `shortcut` is given.
   */
  chordLabel?: string;
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

/** The chord to show beside a command: its `keys`, else its core `shortcut`'s, else its display-only `chordLabel`. */
export function commandChord(c: Pick<CommandSpec, "keys" | "chordLabel" | "shortcut">): string | undefined {
  return c.keys || (c.shortcut ? coreShortcutKeys(c.shortcut) : undefined) || c.chordLabel || undefined;
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
  const modes: RegisteredCommand[] = listShellModes().filter(modeAvailable).map((m) => ({
    id: `mode:${m.id}`,
    label: m.label,
    group: "Modes",
    icon: m.icon,
    owner: m.pluginId ?? "shell",
    run: () => void setShellMode(m.id),
    checked: () => activeShellMode() === m.id,
    when: () => modeAvailable(m),
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

/** The registered commands of `owner` (every one, offered now or not). */
export function commandsOwnedBy(owner: string): RegisteredCommand[] {
  return [..._commands.values()].filter((c) => c.owner === owner);
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

/**
 * Ranks command `c` for a palette query: lower is better, null when it does not match.
 * The query is split on spaces and every word must match: the label (as a prefix, a substring or
 * its letters in order, `matchCommand`), or else as a substring of the group, the keywords or the
 * id. Case-insensitive; an empty query matches everything.
 */
export function commandRank(query: string, c: {id: string; label: string; group?: string; keywords?: readonly string[]}): number | null {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return 0;
  const hay = [c.group ?? "", ...(c.keywords ?? []), c.id].join(" ").toLowerCase();
  let total = 0;
  for (const w of words) {
    const inLabel = matchCommand(w, c.label);
    const inHay = hay.includes(w) ? 2 : null;
    const best = inLabel === null ? inHay : inHay === null ? inLabel : Math.min(inLabel, inHay);
    if (best === null) return null;
    total += best;
  }
  return total / words.length;
}

// A key the user bound to a command that had none (`cmd:<command id>` in the overrides) is a shortcut
// from then on: this is where it comes from. It runs the command as the palette does.
registerShortcutSource(() => {
  const out: import("./shortcuts").RegisteredShortcut[] = [];
  for (const [sid, chord] of Object.entries(shortcutOverrides())) {
    if (chord === null || !sid.startsWith("cmd:")) continue;
    const id = sid.slice(4);
    const own = _commands.get(id);
    if (own) {
      out.push({ id: sid, keys: chord, label: own.label, group: own.group, owner: own.owner, run: () => runCommand(id), when: own.when });
      continue;
    }
    const core = coreCommands(togglePanel, isPanelOpen).find((c) => c.id === id);
    if (core) out.push({ id: sid, keys: chord, label: core.label, group: core.group, owner: core.owner, run: () => core.run(), when: core.when });
  }
  return out;
});
