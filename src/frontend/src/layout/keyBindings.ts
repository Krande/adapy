// Editing the keyboard shortcuts: the rules and the model behind the shortcuts dialog.
//
// What a key IS lives in `./shortcuts` (the registry with the user's overrides applied in one place);
// this module is what an editor needs on top: which chords the browser or the OS will not deliver
// (refused), turning a key press into a chord, who already holds a chord (a conflict), and the
// rows of commands with their effective and default chords. The overrides are saved in the layout
// prefs (`LayoutPrefs.keyOverrides`, not per shell).
//
// Chord SEQUENCES (two chords in a row) are not supported: the registry matches one key event
// against one chord, and `CommandSpec.chordLabel` is display-only text, so there is nothing to capture.

import { listCommands, type RegisteredCommand } from "./commands";
import { layoutStore } from "./layoutStore";
import type { PanelId } from "./panel";
import { CORE_SHORTCUTS } from "./coreShortcutTable";
import { isPanelOpen, toggle } from "./reveal";
import {
  defaultCoreShortcutKeys,
  listDefaultShortcuts,
  listShortcuts,
  parseShortcut,
  shortcutOverrides,
  type RegisteredShortcut,
  type ShortcutKeys,
} from "./shortcuts";

const MODIFIER_KEYS = new Set(["control", "shift", "alt", "meta", "altgraph", "os", "capslock", "dead"]);

/** Chords the browser or the OS keeps for itself (tabs, windows, reload, developer tools): never delivered to the page, or not ours to take. */
const UNSAFE = new Set(
  [
    "ctrl+w", "ctrl+t", "ctrl+n", "ctrl+shift+n", "ctrl+shift+t", "ctrl+shift+w", "ctrl+tab", "ctrl+shift+tab",
    "ctrl+pageup", "ctrl+pagedown", "ctrl+q", "ctrl+l", "ctrl+r", "ctrl+shift+r", "ctrl+f5", "f5", "f11", "f12",
    "ctrl+shift+i", "ctrl+shift+j", "ctrl+shift+c", "ctrl+shift+delete", "ctrl+u", "ctrl+d", "ctrl+h", "ctrl+j",
    "alt+f4", "alt+tab", "alt+arrowleft", "alt+arrowright", "alt+home", "ctrl+1", "ctrl+2", "ctrl+3", "ctrl+4",
    "ctrl+5", "ctrl+6", "ctrl+7", "ctrl+8", "ctrl+9", "ctrl+0", "ctrl+plus", "ctrl+minus", "ctrl+=", "ctrl+-",
  ].map((c) => c.toLowerCase()),
);

/** Keys that would break the page itself if a command took them. */
const NEVER = new Set(["escape", "tab", "enter", " ", "space", "backspace", "delete", "shift", "control", "alt", "meta"]);

/** A chord in the registry's spelling, normalised for comparison (`ctrl+shift+k`). */
export function normaliseChord(keys: ShortcutKeys): string {
  const c = parseShortcut(keys);
  if (!c) return "";
  return [c.ctrl ? "ctrl" : "", c.alt ? "alt" : "", c.shift ? "shift" : "", c.key].filter(Boolean).join("+");
}

/** Why `keys` may not be assigned, or null when it may. */
export function refuseChord(keys: ShortcutKeys): string | null {
  const c = parseShortcut(keys);
  if (!c || !c.key) return "That is not a key combination.";
  if (MODIFIER_KEYS.has(c.key)) return "A modifier key alone cannot be a shortcut.";
  const norm = normaliseChord(keys);
  if (!c.ctrl && !c.alt && NEVER.has(c.key)) return `${keys} is needed for the page itself (closing dialogs, moving focus, typing) and cannot be a shortcut.`;
  if (UNSAFE.has(norm)) return `${keys} is reserved by the browser or the operating system, which does not hand it to the page.`;
  return null;
}

/**
 * The chord a key press stands for, in the registry's spelling: modifiers then the key
 * (`Ctrl+Shift+K`). Null while only modifiers are held.
 */
export function chordFromEvent(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; shiftKey: boolean }): ShortcutKeys | null {
  if (MODIFIER_KEYS.has(e.key.toLowerCase())) return null;
  const key = e.key === " " ? "Space" : e.key.length === 1 ? e.key.toUpperCase() : e.key;
  return [e.ctrlKey || e.metaKey ? "Ctrl" : "", e.altKey ? "Alt" : "", e.shiftKey ? "Shift" : "", key].filter(Boolean).join("+");
}

/** The shortcut that holds `keys` now (live ones only; a widget's local keys do not count), other than `exceptId`. */
export function chordHolder(keys: ShortcutKeys, exceptId?: string, list: readonly RegisteredShortcut[] = listShortcuts()): RegisteredShortcut | null {
  const want = normaliseChord(keys);
  return list.find((s) => !s.local && s.id !== exceptId && normaliseChord(s.keys) === want) ?? null;
}

/** The id the user's key for command `c` is kept under: the core shortcut it shows, its own registered one, or `cmd:<id>`. */
export function shortcutIdOf(c: Pick<RegisteredCommand, "id" | "shortcut">, base: readonly RegisteredShortcut[] = listDefaultShortcuts()): string {
  if (c.shortcut && CORE_SHORTCUTS.some((d) => d.id === c.shortcut)) return c.shortcut;
  if (base.some((s) => s.id === `cmd:${c.id}`)) return `cmd:${c.id}`;
  if (base.some((s) => s.id === c.id)) return c.id;
  return `cmd:${c.id}`;
}

/** One line of the editor. */
export interface KeyRow {
  /** The key the override is stored under. */
  shortcutId: string;
  /** The command it runs, when it is one. */
  commandId: string | null;
  label: string;
  group: string;
  keywords: readonly string[];
  /** The chord in force now (the user's, else the default); undefined: none. */
  keys: ShortcutKeys | undefined;
  /** The chord with no override. */
  defaultKeys: ShortcutKeys | undefined;
  /** The user changed it (or unbound it). */
  overridden: boolean;
  /** A widget's own key: listed, not editable. */
  locked: boolean;
}

/** Every command (and every core key with no command of its own) as editor rows, with their effective and default chords. */
export function keyRows(
  toggleFn: (id: PanelId) => void = (id) => void toggle(id),
  isOpen: (id: PanelId) => boolean = isPanelOpen,
): KeyRow[] {
  const base = listDefaultShortcuts();
  const overrides = shortcutOverrides();
  const defaults = new Map(base.map((s) => [s.id, s]));
  const rows = new Map<string, KeyRow>();
  const keysOf = (sid: string): ShortcutKeys | undefined => {
    const o = overrides[sid];
    if (o === undefined) return defaults.get(sid)?.keys;
    return o === null || !parseShortcut(o) ? undefined : o;
  };
  const dflt = (sid: string, c?: RegisteredCommand): ShortcutKeys | undefined =>
    defaults.get(sid)?.keys ?? (c?.shortcut ? defaultCoreShortcutKeys(c.shortcut) : undefined) ?? c?.keys;
  for (const c of listCommands({ toggle: toggleFn, isOpen })) {
    const sid = shortcutIdOf(c, base);
    if (rows.has(sid)) continue;
    rows.set(sid, {
      shortcutId: sid,
      commandId: c.id,
      label: c.label,
      group: c.group ?? "Commands",
      keywords: c.keywords ?? [],
      keys: defaults.get(sid)?.local ? defaults.get(sid)?.keys : keysOf(sid),
      defaultKeys: dflt(sid, c),
      overridden: !defaults.get(sid)?.local && sid in overrides,
      locked: !!defaults.get(sid)?.local,
    });
  }
  // Keys that have no command: core's own table (the tree walk is also a command, the rest are keys only), shell and plugin shortcuts.
  for (const s of base) {
    if (rows.has(s.id)) continue;
    rows.set(s.id, {
      shortcutId: s.id,
      commandId: null,
      label: s.label,
      group: s.group ?? "Shell and plugins",
      keywords: [],
      keys: s.local ? s.keys : keysOf(s.id),
      defaultKeys: s.keys,
      overridden: !s.local && s.id in overrides,
      locked: !!s.local,
    });
  }
  return [...rows.values()];
}

/** Bind `shortcutId` to `keys` (the caller has dealt with a conflict), leave it unbound (`null`), or back to its default (`undefined`). */
export function setKeyBinding(shortcutId: string, keys: ShortcutKeys | null | undefined): void {
  layoutStore.getState().setKeyOverride(shortcutId, keys);
}

/** Every key back to its default. */
export function resetAllKeyBindings(): void {
  layoutStore.getState().resetKeyOverrides();
}

/**
 * Assign `keys` to `shortcutId`: refused with a reason for an unsafe chord, reported as a conflict (the
 * holder) unless `replace`, in which case the holder is left unbound. The editor's one entry point.
 */
export function assignKeys(
  shortcutId: string,
  keys: ShortcutKeys,
  opts: { replace?: boolean } = {},
): { ok: true } | { ok: false; reason: string; conflict?: RegisteredShortcut } {
  const refusal = refuseChord(keys);
  if (refusal) return { ok: false, reason: refusal };
  const holder = chordHolder(keys, shortcutId);
  if (holder && !opts.replace) return { ok: false, reason: `${keys} is already used by "${holder.label}".`, conflict: holder };
  if (holder) setKeyBinding(holder.id, null);
  setKeyBinding(shortcutId, keys);
  return { ok: true };
}
